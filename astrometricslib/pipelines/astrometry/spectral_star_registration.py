"""Match stars in a spectroscopy image to stars in a normal image.

Spectroscopy images (the ones that spread star light into rainbows)
can't be mapped directly because the stars aren't just dots anymore.
So, we can't look up their names in the database.

To fix this, we take a normal image of the same target where we DO
know the names of all the stars, and we try to line up the two images.
If we can figure out how the two images overlap, we can copy the star
names from the normal image to the spectroscopy image.
"""

import logging

import numpy as np

from astrometricslib.models.stellar_source import StellarObject

logger = logging.getLogger(__name__)

# astroalign's own asterism-matching needs at least 3 stars on each side
# to find a candidate transform at all; requiring one more than that
# here avoids running (and logging) a doomed attempt on a field with
# only a handful of detections, where 3 points can't disambiguate a
# unique transform from noise.
_MIN_CONTROL_POINTS = 4

# Default nearest-neighbour cutoff, in pixels, for accepting a
# registered spectral star as the same star as a reference-field match.
# Generous relative to a single star's PSF/centroid so real matches
# with the tracking-mount jitter this codebase actually sees aren't
# rejected, tight enough that projecting through a poorly-constrained
# transform (few control points) doesn't confidently pair two
# unrelated stars.
_DEFAULT_MAX_MATCH_DISTANCE_PX = 15.0

# Default search window, in pixels, for the translation-only offset
# vote. Generous relative to typical guiding RMS (sub-pixel to a few
# pixels) to also absorb a modest, consistent mechanical shift from
# swapping the grism accessory in/out between sessions, while still
# far more constrained than a blind full-field search.
_DEFAULT_MAX_TRANSLATION_OFFSET_PX = 300.0

# Bin width, in pixels, for the offset-vote histogram. Wide enough to
# absorb per-star centroid noise (a spectral zero-order centroid is
# noisier than a normal point-source centroid) without splitting one
# true offset across adjacent bins, narrow enough that an unrelated
# star field wouldn't coincidentally pile up in a single bin.
_TRANSLATION_BIN_PX = 6.0


def _pixel_position(obj: StellarObject) -> tuple[float, float] | None:
    """Get the X and Y coordinates of a star in the image.

    Returns
    -------
    position : `tuple[float, float]` or `None`
        The (X, Y) pixel location, or None if it's missing.
    """
    star_data = obj.star_data if isinstance(obj.star_data, dict) else {}
    x = star_data.get("xcentroid", star_data.get("x_centroid"))
    y = star_data.get("ycentroid", star_data.get("y_centroid"))
    if x is None or y is None:
        return None
    return float(x), float(y)


def _estimate_translation_offset(
    source_points: np.ndarray,
    target_points: np.ndarray,
    max_offset_px: float,
    bin_px: float,
) -> tuple[float, float] | None:
    """Figure out how far we need to slide one image to match the other.

    This assumes the telescope didn't rotate, it just bumped slightly
    left, right, up, or down.

    Returns
    -------
    offset : `tuple[float, float]` or `None`
        How many pixels to slide the image (X, Y), or None if we couldn't
        find a clear match.
    """
    diffs = (target_points[np.newaxis, :, :] - source_points[:, np.newaxis, :]).reshape(-1, 2)
    within_window = (np.abs(diffs[:, 0]) <= max_offset_px) & (np.abs(diffs[:, 1]) <= max_offset_px)
    diffs = diffs[within_window]
    if len(diffs) == 0:
        return None

    bin_edges = np.arange(-max_offset_px, max_offset_px + bin_px, bin_px)
    histogram, x_edges, y_edges = np.histogram2d(diffs[:, 0], diffs[:, 1], bins=[bin_edges, bin_edges])
    peak_x_index, peak_y_index = np.unravel_index(np.argmax(histogram), histogram.shape)
    peak_votes = histogram[peak_x_index, peak_y_index]
    if peak_votes < _MIN_CONTROL_POINTS:
        return None

    peak_x_center = (x_edges[peak_x_index] + x_edges[peak_x_index + 1]) / 2.0
    peak_y_center = (y_edges[peak_y_index] + y_edges[peak_y_index + 1]) / 2.0
    near_peak = diffs[
        (np.abs(diffs[:, 0] - peak_x_center) <= bin_px) & (np.abs(diffs[:, 1] - peak_y_center) <= bin_px)
    ]
    dx, dy = np.median(near_peak, axis=0)
    return float(dx), float(dy)


def _apply_matches(
    spectral_objs: list[StellarObject],
    reference_objs: list[StellarObject],
    transformed_points: np.ndarray,
    target_points: np.ndarray,
    max_match_distance_px: float,
) -> int:
    """Once the images are lined up, copy the star names over.

    We pair up stars that are physically very close to each other
    after sliding the images, assuming they must be the same star.

    Returns
    -------
    matched_count : `int`
        How many stars we successfully copied names to.
    """
    from scipy.spatial import cKDTree

    reference_tree = cKDTree(target_points)
    distances, nearest_indices = reference_tree.query(transformed_points)

    matched_count = 0
    for spectral_obj, distance, reference_index in zip(
        spectral_objs, distances, nearest_indices, strict=True
    ):
        if distance > max_match_distance_px:
            continue
        reference_star = reference_objs[reference_index]
        spectral_obj.id = f"{reference_star.id}::spectroscopy"
        spectral_obj.name = reference_star.name
        spectral_obj.right_ascension = reference_star.right_ascension
        spectral_obj.declination = reference_star.declination
        spectral_obj.spectral_type = reference_star.spectral_type
        spectral_obj.stellar_spectral_type = reference_star.stellar_spectral_type
        spectral_obj.magnitude = reference_star.magnitude
        spectral_obj.is_catalog_identified = reference_star.is_catalog_identified
        matched_count += 1
    return matched_count


def identify_spectral_stars_via_registration(
    spectral_stellar_objects: list[StellarObject],
    reference_stellar_objects: list[StellarObject],
    max_match_distance_px: float = _DEFAULT_MAX_MATCH_DISTANCE_PX,
    max_translation_offset_px: float = _DEFAULT_MAX_TRANSLATION_OFFSET_PX,
) -> int:
    """Give names to the stars in the spectroscopy image.

    We do this by lining up the spectroscopy image with a normal image
    where we already know all the star names. We try just sliding the
    images first, and if that doesn't work, we try rotating and scaling
    them too.

    Parameters
    ----------
    spectral_stellar_objects : `list` [`StellarObject`]
        The unnamed stars from the spectroscopy image.
    reference_stellar_objects : `list` [`StellarObject`]
        The named stars from the normal image.
    max_match_distance_px : `float`, optional
        How close the stars have to line up to be considered a match.
    max_translation_offset_px : `float`, optional
        The furthest we will try to slide the images to make them fit.

    Returns
    -------
    matched_count : `int`
        How many unnamed stars we successfully gave names to.
    """
    spectral_positions = {id(obj): pos for obj in spectral_stellar_objects if (pos := _pixel_position(obj))}
    reference_positions = {id(obj): pos for obj in reference_stellar_objects if (pos := _pixel_position(obj))}

    if len(spectral_positions) < _MIN_CONTROL_POINTS or len(reference_positions) < _MIN_CONTROL_POINTS:
        logger.info(
            "Not enough positioned stars to register the spectral field against a reference "
            f"field ({len(spectral_positions)} spectral, {len(reference_positions)} reference; "
            f"need >= {_MIN_CONTROL_POINTS} each) -- leaving spectroscopy stars unidentified."
        )
        return 0

    spectral_objs = [obj for obj in spectral_stellar_objects if id(obj) in spectral_positions]
    reference_objs = [obj for obj in reference_stellar_objects if id(obj) in reference_positions]
    source_points = np.array([spectral_positions[id(obj)] for obj in spectral_objs])
    target_points = np.array([reference_positions[id(obj)] for obj in reference_objs])

    offset = _estimate_translation_offset(
        source_points, target_points, max_translation_offset_px, _TRANSLATION_BIN_PX
    )
    if offset is not None:
        matched_count = _apply_matches(
            spectral_objs,
            reference_objs,
            source_points + np.array(offset),
            target_points,
            max_match_distance_px,
        )
        logger.info(
            f"Spectral field registration matched {matched_count} / {len(spectral_objs)} spectroscopy "
            f"stars via translation-only offset (dx={offset[0]:.2f}, dy={offset[1]:.2f}) px."
        )
        return matched_count

    logger.info(
        "No confident translation-only offset found; falling back to astroalign "
        "similarity-transform registration."
    )

    import astroalign

    try:
        transform, _ = astroalign.find_transform(source_points, target_points)
    except (astroalign.MaxIterError, ValueError) as e:
        logger.warning(f"Could not register spectral field against reference star field: {e}")
        return 0

    matched_count = _apply_matches(
        spectral_objs, reference_objs, transform(source_points), target_points, max_match_distance_px
    )
    logger.info(
        f"Spectral field registration matched {matched_count} / {len(spectral_objs)} "
        "spectroscopy stars to catalog-identified reference stars "
        f"(rotation={np.degrees(transform.rotation):.3f} deg, scale={transform.scale:.4f})."
    )
    return matched_count
