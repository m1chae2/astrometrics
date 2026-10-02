"""Match stars in a spectroscopy image to stars in a normal image.

Spectroscopy images (the ones that spread star light into rainbows)
can't be mapped directly because the stars aren't just dots anymore.
So, their names can't be looked up in the database.

To fix this, a normal image of the same target where the names of all
the stars are known is taken, and an attempt is made to line up the two images.
If how the two images overlap can be figured out, the star
names can be copied from the normal image to the spectroscopy image.
"""

import logging
from typing import Any

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

# Search window, in pixels, for the shift between a target's solved standard
# stack and its spectral stack when the plate solution places the stars. The
# two stacks are registered separately, so how far apart their pixel grids
# sit depends on where the mount pointed for each set of frames: 2026-09-25
# gave about 170 px for Deneb, 280 px for Albireo and 500 px for Mirfak. The
# solution places every catalog star exactly, and a shift needs at least
# `_MIN_CONTROL_POINTS` pairs to agree on it, so a wide window is safe.
# 1000 px is a third of the sensor.
_SOLUTION_MAX_TRANSLATION_OFFSET_PX = 1000.0

# Two catalog stars at least this far apart are resolved (two separate zero
# orders); closer ones are one blob to the detector. Navi's primary and
# companion are 4 px apart and stay one star. Albireo A and B are 17 px apart
# and the detector found a single detection for both, which took B's name and
# left A unnamed. 8 px is about twice a zero order's width (FWHM about 4 px),
# a judgement, not tuned.
_RESOLVED_PAIR_MINIMUM_SEPARATION_PX = 8.0

# How far from a named detection a second catalog star may be to be counted
# as part of the same blob. A little more than a bright star's zero order
# wings reach; Albireo's pair is 17 px apart.
_BLENDED_PAIR_SEARCH_RADIUS_PX = 25.0

# A companion is only split out if it is at most this many magnitudes
# fainter than the star that took the detection: a much fainter star is not
# what makes the blob and would have no zero order of its own to extract.
# Albireo B is 2 magnitudes fainter than A.
_BLENDED_PAIR_MAXIMUM_MAGNITUDE_DIFFERENCE = 4.0


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
    """Figure out how far one image needs to be slid to match the other.

    This assumes the telescope didn't rotate, it just bumped slightly
    left, right, up, or down.

    Returns
    -------
    offset : `tuple[float, float]` or `None`
        How many pixels to slide the image (X, Y), or None if a clear
        match couldn't be found.
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


def _copy_identity(spectral_obj: StellarObject, reference_star: StellarObject) -> None:
    """Give a spectroscopy detection the name and catalog data of a star.

    The detection gets the same id as the reference star, so the catalog
    merge adds its spectrum to that star's existing row instead of creating
    a second row for the same star.

    This also carries over the star's own measured photometry (its light
    curve from the photometry stage, which the batch pipeline already runs
    before spectroscopy), not just its external catalog magnitude. A fresh
    spectroscopy detection otherwise only has whatever brightness the
    spectral image's own point-source detector measured for it -- a single,
    uncalibrated, one-frame estimate -- while `reference_star.photometry`
    is the star's own calibrated brightness, measured across many frames.
    Nothing downstream writes this back to the catalog (the merge step
    keeps the existing row's own photometry untouched), so this is purely
    for use during this processing run, such as comparing two stars'
    relative brightness (see `find_neighbor_contamination_windows`).

    Parameters
    ----------
    spectral_obj : `StellarObject`
        The detection in the spectroscopy image. Changed in place.
    reference_star : `StellarObject`
        The named catalog star it is the same star as.
    """
    spectral_obj.id = reference_star.id
    spectral_obj.name = reference_star.name
    spectral_obj.right_ascension = reference_star.right_ascension
    spectral_obj.declination = reference_star.declination
    spectral_obj.spectral_type = reference_star.spectral_type
    spectral_obj.stellar_spectral_type = reference_star.stellar_spectral_type
    spectral_obj.magnitude = reference_star.magnitude
    spectral_obj.b_minus_v = reference_star.b_minus_v
    spectral_obj.is_catalog_identified = reference_star.is_catalog_identified
    spectral_obj.photometry = reference_star.photometry


def _apply_matches(
    spectral_objs: list[StellarObject],
    reference_objs: list[StellarObject],
    transformed_points: np.ndarray,
    target_points: np.ndarray,
    max_match_distance_px: float,
) -> int:
    """Once the images are lined up, copy the star names over.

    Stars that are physically very close to each other after sliding
    the images are paired up, assuming they must be the same star.

    Returns
    -------
    matched_count : `int`
        How many stars successfully had their names copied.
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
        _copy_identity(spectral_obj, reference_star)
        matched_count += 1
    return matched_count


def identify_spectral_stars_via_registration(
    spectral_stellar_objects: list[StellarObject],
    reference_stellar_objects: list[StellarObject],
    max_match_distance_px: float = _DEFAULT_MAX_MATCH_DISTANCE_PX,
    max_translation_offset_px: float = _DEFAULT_MAX_TRANSLATION_OFFSET_PX,
) -> int:
    """Give names to the stars in the spectroscopy image.

    This is done by lining up the spectroscopy image with a normal image
    where all the star names are already known. Sliding the images is
    tried first, and if that doesn't work, rotating and scaling them is
    tried too.

    Parameters
    ----------
    spectral_stellar_objects : `list` [`StellarObject`]
        The unnamed stars from the spectroscopy image.
    reference_stellar_objects : `list` [`StellarObject`]
        The named stars from the normal image.
    max_match_distance_px : `float`, optional
        How close the stars have to line up to be considered a match.
    max_translation_offset_px : `float`, optional
        The furthest the images will be slid to try to make them fit.

    Returns
    -------
    matched_count : `int`
        How many unnamed stars were successfully given names.
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


def identify_spectral_stars_via_solution(
    spectral_stellar_objects: list[StellarObject],
    reference_stellar_objects: list[StellarObject],
    reference_wcs: Any,
    max_match_distance_px: float = _DEFAULT_MAX_MATCH_DISTANCE_PX,
) -> int | None:
    """Name a spectroscopy image's stars from a plate solution's sky positions.

    The plate solution of the target's solved standard stack turns every
    catalog star's sky position into a pixel position, whether or not the
    star was detected in that stack. This matters for the brightest stars:
    they saturate the standard stack, are not detected there, and so keep a
    stale stored pixel position that no geometric match can use (Deneb sat
    170 px from where it was expected and was never named). The two stacks
    are shifted apart by one offset, measured by the pairs of stars that
    agree on it (see `estimate_registration_offset`). Each catalog star is
    then paired with the spectroscopy detection nearest its shifted position.

    A detection is given at most one name and a star at most one detection,
    nearest pairs first (a brighter star wins a near tie), so one bright
    star's trail cannot take a name many times.

    Parameters
    ----------
    spectral_stellar_objects : `list` [`StellarObject`]
        The detections in the spectroscopy image. Renamed in place.
    reference_stellar_objects : `list` [`StellarObject`]
        The named catalog stars. Only their sky positions are used to place
        them, not their stored pixel positions.
    reference_wcs : `astropy.wcs.WCS`
        The plate solution of the solved standard stack.
    max_match_distance_px : `float`, optional
        How far a detection may be from a star's shifted position and still
        be that star.

    Returns
    -------
    matched_count : `int` or `None`
        How many detections were named, or `None` when the two star fields
        cannot be lined up with the solution by a shift or a
        rotation, so the solution cannot be used.
    """
    reference_by_id = {
        star.id: star for star in reference_stellar_objects if star.id and star.right_ascension is not None
    }
    placed = _positions_through_wcs(list(reference_by_id.values()), reference_wcs)
    detections = [(obj, pos) for obj in spectral_stellar_objects if (pos := _pixel_position(obj))]
    if not placed or not detections:
        return 0

    # Both star fields are compared in the solved image's pixels. A pure
    # shift is tried first; when the fields also differ by a small rotation
    # (the grating can turn a little from the luminance filter's angle), a
    # similarity transform between the detections and the placed stars is
    # tried instead.
    detection_points = np.array([pos for _, pos in detections])
    offset = estimate_registration_offset(
        spectral_stellar_objects,
        reference_stellar_objects,
        reference_wcs,
        _SOLUTION_MAX_TRANSLATION_OFFSET_PX,
    )
    if offset is not None:
        in_solved_frame = detection_points - np.array(offset)
        how = f"shift dx={offset[0]:.2f}, dy={offset[1]:.2f} px"
    else:
        import astroalign

        try:
            transform, _ = astroalign.find_transform(detection_points, np.array(list(placed.values())))
        except astroalign.MaxIterError, ValueError:
            return None
        in_solved_frame = transform(detection_points)
        how = f"rotation {np.degrees(transform.rotation):.2f} deg, scale {transform.scale:.4f}"

    # Nearest pairs are made first. Two catalog entries for one bright star
    # (a primary and a companion listed at almost the same place) are
    # within a few pixels of the same detection (Navi's are 4 px apart), so
    # distances are compared in whole five-pixel steps and the brighter entry
    # wins a tie.
    candidate_pairs = []
    for star_id, (x, y) in placed.items():
        magnitude = reference_by_id[star_id].magnitude
        brightness_rank = 99.0 if magnitude is None else float(magnitude)
        for detection_index, (detection_x, detection_y) in enumerate(in_solved_frame):
            distance = float(np.hypot(detection_x - x, detection_y - y))
            if distance <= max_match_distance_px:
                candidate_pairs.append((
                    int(distance // 5.0),
                    brightness_rank,
                    distance,
                    star_id,
                    detection_index,
                ))
    candidate_pairs.sort()

    used_stars: set[str] = set()
    used_detections: set[int] = set()
    matched_count = 0
    for _step, _brightness, _distance, star_id, detection_index in candidate_pairs:
        if star_id in used_stars or detection_index in used_detections:
            continue
        _copy_identity(detections[detection_index][0], reference_by_id[star_id])
        used_stars.add(star_id)
        used_detections.add(detection_index)
        matched_count += 1
    if offset is not None:
        matched_count += _split_blended_pairs(
            spectral_stellar_objects, detections, used_detections, reference_by_id, placed, offset
        )
    logger.info(
        f"Spectral field identified {matched_count} / {len(detections)} detections from the plate "
        f"solution ({how})."
    )
    return matched_count


def _split_blended_pairs(
    spectral_stellar_objects: list[StellarObject],
    detections: list[tuple[StellarObject, tuple[float, float]]],
    used_detections: set[int],
    reference_by_id: dict[str, StellarObject],
    placed: dict[str, tuple[float, float]],
    offset: tuple[float, float],
) -> int:
    """Give each star of a resolved close pair its own place in the image.

    The blind detector finds two zero orders that overlap as one blob.
    That blob was named after the nearest catalog star, so the other star of
    the pair was left unnamed and never extracted. Where a second catalog
    star lies at least `_RESOLVED_PAIR_MINIMUM_SEPARATION_PX` from the star
    that took a detection, and within `_BLENDED_PAIR_SEARCH_RADIUS_PX` of it,
    both are moved to their own projected positions (the plate solution plus
    the shift between the two images) and the second is added as a new
    detection. Pairs closer than the minimum are left as one star.

    Parameters
    ----------
    spectral_stellar_objects : `list` [`StellarObject`]
        The detections, added to in place.
    detections : `list` [`tuple`]
        Each detection with its pixel position, in the same order the
        matching used.
    used_detections : `set` [`int`]
        Indexes into `detections` that were named.
    reference_by_id : `dict` [`str`, `StellarObject`]
        The catalog stars by id.
    placed : `dict` [`str`, `tuple`]
        Each catalog star's pixel position in the solved image.
    offset : `tuple` [`float`, `float`]
        The shift from the solved image to the spectroscopy image.

    Returns
    -------
    added_count : `int`
        How many companions were added as new detections.
    """
    shift = np.array(offset)
    added = 0
    handled: set[str] = set()
    for detection_index in sorted(used_detections):
        detection, _ = detections[detection_index]
        star = reference_by_id.get(detection.id)
        if star is None or star.id in handled:
            continue
        star_position = np.array(placed[star.id]) + shift
        for other_id, other in reference_by_id.items():
            if other_id == star.id or other_id in handled or other_id not in placed:
                continue
            other_position = np.array(placed[other_id]) + shift
            separation = float(np.hypot(*(other_position - star_position)))
            if not _RESOLVED_PAIR_MINIMUM_SEPARATION_PX <= separation <= _BLENDED_PAIR_SEARCH_RADIUS_PX:
                continue
            if any(
                other_id == used.id or float(np.hypot(*(np.array(pos) - other_position))) < 5.0
                for used, pos in detections
                if used.id == other_id
            ):
                continue
            if (
                star.magnitude is not None
                and other.magnitude is not None
                and other.magnitude - star.magnitude > _BLENDED_PAIR_MAXIMUM_MAGNITUDE_DIFFERENCE
            ):
                continue
            companion = detection.model_copy(deep=True)
            _copy_identity(companion, other)
            _set_pixel_position(companion, other_position)
            _set_pixel_position(detection, star_position)
            spectral_stellar_objects.insert(spectral_stellar_objects.index(detection) + 1, companion)
            handled.update({star.id, other_id})
            added += 1
            break
    return added


def _set_pixel_position(obj: StellarObject, position: np.ndarray) -> None:
    """Move a detection to a pixel position.

    Parameters
    ----------
    obj : `StellarObject`
        The detection. Its ``star_data`` centroid is changed.
    position : `numpy.ndarray`
        The new ``(x, y)``.
    """
    star_data = obj.star_data if isinstance(obj.star_data, dict) else {}
    x_key = "xcentroid" if "xcentroid" in star_data else "x_centroid"
    y_key = "ycentroid" if "ycentroid" in star_data else "y_centroid"
    star_data[x_key] = float(position[0])
    star_data[y_key] = float(position[1])
    obj.star_data = star_data


# The fewest identified stars needed to trust an offset between the two star
# fields. Below this, one bad match could move the median.
_MINIMUM_OFFSET_PAIR_COUNT = 4


def _positions_through_wcs(stellar_objects: list[StellarObject], wcs: Any) -> dict[str, tuple[float, float]]:
    """Find where a plate solution puts each star that has a sky position.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The stars to place. Stars without a right ascension and declination
        are skipped.
    wcs : `astropy.wcs.WCS`
        The plate solution that turns a sky position into a pixel position.

    Returns
    -------
    positions : `dict` [`str`, `tuple` [`float`, `float`]]
        Each placed star's `(x, y)` pixel position, keyed by star id.
    """
    positions = {}
    for stellar_object in stellar_objects:
        right_ascension = stellar_object.right_ascension
        declination = stellar_object.declination
        if not stellar_object.id or right_ascension is None or declination is None:
            continue
        x, y = wcs.all_world2pix(float(right_ascension), float(declination), 0)
        if np.isfinite(x) and np.isfinite(y):
            positions[stellar_object.id] = (float(x), float(y))
    return positions


def estimate_registration_offset(
    spectral_stellar_objects: list[StellarObject],
    reference_stellar_objects: list[StellarObject],
    reference_wcs: Any | None = None,
    max_offset_px: float = _DEFAULT_MAX_TRANSLATION_OFFSET_PX,
) -> tuple[float, float] | None:
    """Measure how far the spectroscopy image sits from the reference image.

    After `identify_spectral_stars_via_registration`, every matched
    spectral star carries the same `id` as its reference star, while its
    pixel position is still the spectroscopy image's own. The typical
    difference between the two positions is the shift between the images.

    Only a pure shift is reported: if the paired stars disagree about it by
    more than the offset-vote bin width, the images differ by a rotation or
    scale as well and one shift would be wrong, so `None` is returned.

    Parameters
    ----------
    spectral_stellar_objects : `list` [`StellarObject`]
        The stars found in the spectroscopy image, after identification.
    reference_stellar_objects : `list` [`StellarObject`]
        The named stars of the reference image.
    reference_wcs : `astropy.wcs.WCS`, optional
        The plate solution that will be shifted by the result. When given,
        the star identities are not used at all. Every reference star's
        pixel position is worked out from its sky position with this
        solution, and the shift is the one most (spectral star, reference
        star) pairs agree on. A target's stored stars can come from several
        stacks (a different camera, say) whose pixel frames differ by
        hundreds of pixels, and identities carried over by registration can
        repeat one star many times (M 27 gave one name to eight points on a
        bright star's trail), which would drag a median to the wrong place.
        Counting agreeing pairs is not fooled by either.
    max_offset_px : `float`, optional
        The furthest shift, in pixels along either axis, that is searched
        for when a solution is given.

    Returns
    -------
    offset : `tuple` [`float`, `float`] or `None`
        The `(dx, dy)` to add to a reference-image pixel position to get the
        matching spectroscopy-image position, or `None` when there are too
        few pairs or they do not agree on a single shift.
    """
    if reference_wcs is not None:
        reference_points = np.array(
            list(_positions_through_wcs(reference_stellar_objects, reference_wcs).values())
        )
        spectral_points = np.array(
            list({pos for obj in spectral_stellar_objects if (pos := _pixel_position(obj))})
        )
        if (
            len(reference_points) < _MINIMUM_OFFSET_PAIR_COUNT
            or len(spectral_points) < _MINIMUM_OFFSET_PAIR_COUNT
        ):
            return None
        return _estimate_translation_offset(
            reference_points,
            spectral_points,
            max_offset_px,
            _TRANSLATION_BIN_PX,
        )

    reference_positions = {
        obj.id: pos for obj in reference_stellar_objects if obj.id and (pos := _pixel_position(obj))
    }
    differences = []
    for spectral_obj in spectral_stellar_objects:
        spectral_position = _pixel_position(spectral_obj)
        reference_position = reference_positions.get(spectral_obj.id)
        if spectral_position is None or reference_position is None:
            continue
        differences.append((
            spectral_position[0] - reference_position[0],
            spectral_position[1] - reference_position[1],
        ))
    if len(differences) < _MINIMUM_OFFSET_PAIR_COUNT:
        return None

    differences_array = np.array(differences)
    median_offset = np.median(differences_array, axis=0)
    typical_disagreement = np.median(np.abs(differences_array - median_offset), axis=0)
    if np.any(typical_disagreement > _TRANSLATION_BIN_PX):
        return None
    return float(median_offset[0]), float(median_offset[1])


def shift_wcs_to_frame(reference_wcs: Any, offset_px: tuple[float, float]) -> Any:
    """Move a plate solution onto the spectroscopy image.

    Parameters
    ----------
    reference_wcs : `astropy.wcs.WCS`
        The reference image's plate solution.
    offset_px : `tuple` [`float`, `float`]
        The `(dx, dy)` from `estimate_registration_offset`.

    Returns
    -------
    shifted_wcs : `astropy.wcs.WCS`
        A copy that gives spectroscopy-image pixel positions. The input is
        not changed.
    """
    shifted_wcs = reference_wcs.deepcopy()
    shifted_wcs.wcs.crpix = np.asarray(shifted_wcs.wcs.crpix, dtype=float) + np.asarray(
        offset_px, dtype=float
    )
    return shifted_wcs
