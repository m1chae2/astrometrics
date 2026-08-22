"""Identify spectroscopy-extracted stars against a catalog-identified field.

The spectral (grism/slitless) stack is never plate-solved (see
`AstrometryPipeline.process` called with `attempt_plate_solving=False` for
spectroscopy in `pipeline_tasks.analyze_target`) -- a dispersed star field
doesn't match a point-source catalog the way an undispersed one does, so it
carries no WCS of its own. That leaves every spectroscopy-extracted star
without a real identity: it never goes through SIMBAD/Gaia and keeps a
synthetic ``Star_N`` id/name.

If this target also has an already plate-solved, catalog-identified
luminance (or other undispersed) stack, its stars' pixel positions and the
spectral stack's zero-order star positions describe the *same physical
star field* through two different pixel grids. Two strategies recover the
mapping between them, purely from point-set geometry -- no WCS needed on
either side:

1. **Translation-only offset voting** (tried first): a fixed tracking
   mount with active guiding keeps the same pointing across sessions, so
   the two pixel grids should differ by little more than a constant (X, Y)
   offset -- at most a few pixels of guiding jitter, plus whatever small,
   consistent mechanical shift comes from inserting/removing the grism
   accessory between the luminance and spectral sessions. That's only 2
   degrees of freedom, and finding it by histogram-voting over every
   source-to-target pairwise offset is far more robust with a modest
   number of stars than a full similarity-transform fit.
2. **`astroalign` similarity-transform fit** (fallback): handles rotation
   and scale differences too, for the case where the two stacks' framing
   genuinely isn't just a small translation apart (different session,
   meridian flip, mount reset). Needs more well-distributed control
   points to converge reliably than the translation-only approach.
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
    """Extract a pixel centroid from `obj.star_data`.

    Returns
    -------
    position : `tuple[float, float]` or `None`
        `(x, y)`, or `None` if `obj.star_data` has no centroid.
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
    """Find the dominant (dx, dy) translating `source_points` onto targets.

    Computes every source-to-target pairwise offset within
    `max_offset_px`, bins them into a 2D histogram, and takes the
    median of the offsets falling in (and immediately around) the
    most-voted bin. A true shared translation shows up as a sharp
    peak -- most source stars have *some* target star at very nearly
    the same offset -- while unrelated point sets spread their offsets
    roughly uniformly across the search window.

    Returns
    -------
    offset : `tuple[float, float]` or `None`
        The estimated `(dx, dy)`, or `None` if no offset collected
        enough votes to be trusted (see `_MIN_CONTROL_POINTS`).
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
    """Nearest-neighbour match onto targets, then copy over identity.

    Mutates each matched star in `spectral_objs` in place -- see
    `identify_spectral_stars_via_registration` for exactly which
    fields are copied.

    Returns
    -------
    matched_count : `int`
        Number of `spectral_objs` successfully identified.
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
    """Carry catalog identity from `reference_stellar_objects` onto matches.

    Tries a translation-only offset vote first (see
    `_estimate_translation_offset`) -- the right model for a fixed
    tracking mount with guiding, and far more robust with a modest
    star count since it only has 2 degrees of freedom. Falls back to a
    full `astroalign` similarity-transform fit (rotation + scale +
    translation) if that doesn't find a confident offset, for framing
    that isn't just a small translation apart.

    Stars with no pixel centroid, or left unmatched within
    `max_match_distance_px` of their registered position, are returned
    unmodified and keep whatever placeholder identity
    `SourceDetector`/`SpectroscopyPipeline` gave them.

    Parameters
    ----------
    spectral_stellar_objects : `list` [`StellarObject`]
        Freshly detected/extracted stars from the spectral stack, to
        identify in place.
    reference_stellar_objects : `list` [`StellarObject`]
        Already catalog-identified stars from a plate-solved stack of
        the same field (e.g. the target's luminance `stacked_image`).
    max_match_distance_px : `float`, optional
        Maximum post-registration nearest-neighbour distance, in
        pixels, to accept a match (default 15.0).
    max_translation_offset_px : `float`, optional
        Maximum `(dx, dy)` magnitude, in pixels, considered for the
        translation-only offset vote (default 300.0).

    Returns
    -------
    matched_count : `int`
        Number of `spectral_stellar_objects` successfully identified.
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
