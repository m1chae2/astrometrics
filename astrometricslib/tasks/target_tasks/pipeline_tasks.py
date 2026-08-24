"""Purpose: Target pipeline orchestration.

Description: Free functions implementing the analysis/stacking pipeline
orchestration that used to live as methods directly on
`astrometricslib.models.target.Target`. Each function takes a `Target`
instance as its first argument rather than being a method, so that
`models/target.py` can stay a pure data schema with zero imports of
`tasks/`, `api/`, or `data_access/` -- these functions depend on
`Target`, not the other way around. Exposed to external callers via
`astrometricslib.api.targets.TargetCatalog`.

# REQ: BKD-5: Data Persistence
"""

import logging
import os
import re
import statistics
import threading
import time
from typing import Any, NamedTuple

from astrometricslib.drivers import disk_interface
from astrometricslib.models.moving_object import CascadeStage
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.enums import FilterType

logger = logging.getLogger(__name__)

# Maximum time to wait for a single Siril stacking run before giving up
# on it. Scaled by frame count rather than flat, because stacking cost
# is dominated by per-frame registration and integration and the
# catalog's targets differ by more than an order of magnitude in size
# (2 frames for Alnath, 535 for NGC 7023).
#
# A flat 600s was the previous value and it silently discarded finished
# work. On the 2026-08-24 DSLR pass four targets were declared timed out
# and three of them went on to stack *successfully* seconds later --
# NGC 7000 finished at 06:35:11 after being abandoned at 06:27:21, and
# its 288MB output was left on disk with nothing in the catalog pointing
# at it.
#
# The per-frame figure comes from that same run: NGC 7000's 84 color
# frames took roughly 800s of Siril time, about 9.5s per frame, for
# 288MB three-channel output. Monochrome frames are far cheaper (NGC
# 2403 stacked 35 frames in 22.5s, 0.64s/frame), so a per-frame budget
# sized for color leaves mono with a wide margin rather than needing a
# second constant. 30s/frame is roughly 3x the measured color cost,
# chosen so that a legitimate stack is never killed -- this is a
# hang-detection ceiling, not an expected duration.
STACKING_TIMEOUT_BASE_SECONDS = 300
STACKING_TIMEOUT_PER_FRAME_SECONDS = 30

# Retained for callers that still pass an explicit budget; equivalent to
# the previous flat value and used only as a floor.
STACKING_TIMEOUT_SECONDS = 600

# Poll interval while waiting on a stacking thread. Small relative to
# the minimum 600s budget, so a genuine timeout overshoots by at most
# this much, while still letting the deadline pick up lock waits that
# accrue after the wait began.
_STACKING_TIMEOUT_POLL_SECONDS = 2.0


def compute_stacking_timeout_seconds(frame_count: int) -> int:
    """Return the stacking timeout appropriate to a frame count.

    Parameters
    ----------
    frame_count : `int`
        Number of frames being submitted to this stack.

    Returns
    -------
    timeout_seconds : `int`
        Base overhead plus a per-frame allowance, never less than
        `STACKING_TIMEOUT_SECONDS` so a tiny stack still gets the
        historical budget.
    """
    scaled_timeout = STACKING_TIMEOUT_BASE_SECONDS + STACKING_TIMEOUT_PER_FRAME_SECONDS * max(0, frame_count)
    return int(max(STACKING_TIMEOUT_SECONDS, scaled_timeout))


# Matches the synthetic placeholder id assigned by
# `StarIdentifier._build_stellar_objects_from_sources` and
# `VariabilityAnalyzer.process`'s blind-detection path (optionally
# prefixed, e.g. "sess_20260101:Star_3") to a star that was never
# resolved to a real catalog id (SIMBAD/Gaia) or a position-derived
# one (FIELD_J...). A real catalog or position-derived id never
# matches this pattern.
_UNRESOLVED_STAR_ID_PATTERN = re.compile(r"^(?:.*:)?Star_\d+$")


class StarIdentificationBreakdown(NamedTuple):
    """How a batch of stars resolved; see `_drop_unresolved_stars`."""

    catalog_matched: int
    position_only: int
    unresolved: int


def select_frames_for_camera(target: Any, camera_name: str) -> list:
    """Select the frames on `target` captured with the named camera.

    The single definition of "does this target have work for this
    camera", shared by `run_full_pipeline` (which processes the frames)
    and the batch worker (which decides whether the target is a no-op
    skip rather than a genuine success). Keeping one implementation
    means the two can never disagree about which targets have frames.

    Matching is a case-insensitive substring test, so a caller may pass
    either a full camera name or a distinguishing fragment of one.

    Parameters
    ----------
    target : `Any`
        The target whose `frames` are filtered.
    camera_name : `str`
        Camera name, matched case-insensitively as a substring.

    Returns
    -------
    camera_frames : `list`
        The matching frames, in their original order.
    """
    return [frame for frame in target.frames if camera_name.lower() in (frame.camera or "").lower()]


def frame_configuration_key(frame: Any) -> str | None:
    """Name the camera-and-optic configuration a frame belongs to.

    Frames may only be stacked with others of the same configuration.
    Two optics of different focal length image at different scales --
    this library's 300mm lens and 405mm telescope differ by 1.35x -- so
    a stack blending them has no single pixel scale, cannot be plate
    solved accurately, and yields fluxes that are not comparable between
    frames. Seven targets were being stacked that way, including
    NGC 7023 (424 frames at 300mm with 111 at 405mm).

    Parameters
    ----------
    frame : `Any`
        The frame record to key.

    Returns
    -------
    configuration_key : `str` or `None`
        ``"<camera>@<focal>mm"``, or `None` when the frame records no
        focal length and therefore cannot be safely grouped.
    """
    focal_length = getattr(frame, "focal_length_mm", None)
    if not focal_length or focal_length <= 0:
        return None
    camera = (getattr(frame, "camera", None) or "Unknown").strip()
    # Rounded to whole millimetres so 405.0 and 405 key identically; no
    # real optic is distinguished by a fraction of a millimetre.
    return f"{camera}@{round(float(focal_length))}mm"


def group_frames_by_configuration(target: Any, camera_name: str | None = None) -> dict[str, list]:
    """Group a target's light frames into stackable configurations.

    Parameters
    ----------
    target : `Any`
        The target whose `frames` are grouped.
    camera_name : `str`, optional
        Restrict to this camera, matched case-insensitively as a
        substring. Defaults to every camera.

    Returns
    -------
    frames_by_configuration : `dict` [`str`, `list`]
        Frames keyed by `frame_configuration_key`, largest group first.
        Frames without a focal length are omitted entirely -- see
        `frames_missing_focal_length`, which reports them so they are
        never dropped silently.
    """
    grouped: dict[str, list] = {}
    for frame in target.frames or []:
        if camera_name and camera_name.lower() not in (frame.camera or "").lower():
            continue
        key = frame_configuration_key(frame)
        if key is None:
            continue
        grouped.setdefault(key, []).append(frame)
    return dict(sorted(grouped.items(), key=lambda item: -len(item[1])))


def frames_missing_focal_length(target: Any, camera_name: str | None = None) -> list:
    """Report frames that cannot be assigned to any configuration.

    Omitting a frame is a real loss, so callers are expected to surface
    this rather than let it pass. On this library 602 frames carry no
    FOCALLEN, and three targets consist entirely of them -- including a
    comet that cannot be re-imaged.

    Parameters
    ----------
    target : `Any`
        The target whose `frames` are inspected.
    camera_name : `str`, optional
        Restrict to this camera, matched as a case-insensitive substring.

    Returns
    -------
    unassignable_frames : `list`
        Frames with no usable focal length.
    """
    return [
        frame
        for frame in target.frames or []
        if (not camera_name or camera_name.lower() in (frame.camera or "").lower())
        and frame_configuration_key(frame) is None
    ]


def select_frames_for_configuration(target: Any, configuration_key: str) -> list:
    """Select the frames belonging to one camera-and-optic configuration.

    Parameters
    ----------
    target : `Any`
        The target whose `frames` are filtered.
    configuration_key : `str`
        A key as produced by `frame_configuration_key`.

    Returns
    -------
    configuration_frames : `list`
        The matching frames, in their original order.
    """
    return [frame for frame in target.frames or [] if frame_configuration_key(frame) == configuration_key]


def _drop_unresolved_stars(
    stellar_objects: list, *, target_id: str, pipeline_name: str
) -> tuple[list, StarIdentificationBreakdown]:
    """Filter out stars that were never resolved to a real identity.

    A star that can't be matched to SIMBAD/Gaia and can't even be
    given a stable position-derived id (its sky position couldn't be
    determined) is worthless as a persistent catalog entry -- its
    placeholder id is arbitrary and not reproducible across runs, so
    saving it would only pollute `stellar_catalog` with rows that can
    never be merged back into the real star they came from. Dropping
    it here, right before persistence, keeps this rule in one place
    regardless of which pipeline (astrometry, spectroscopy,
    photometry) produced the star.

    Also logs and returns a breakdown of every star's outcome
    (catalog-matched / position-only / unresolved-and-dropped), so a
    caller worried about spurious detections has a concrete per-run
    number to look at instead of only transient DEBUG-level logging
    from the identification step itself.

    Parameters
    ----------
    stellar_objects : `list`
        Candidate stars to filter.
    target_id : `str`
        The target this batch of stars belongs to, for the log line.
    pipeline_name : `str`
        Which pipeline produced `stellar_objects` ("astrometry",
        "spectroscopy", or "photometry"), for the log line.

    Returns
    -------
    resolved : `list`
        The subset of `stellar_objects` with a real or position-derived
        identity.
    breakdown : `StarIdentificationBreakdown`
        Counts of every star's outcome, computed before filtering.
    """
    resolved = []
    catalog_matched = 0
    position_only = 0
    unresolved = 0
    for stellar_object in stellar_objects:
        if _UNRESOLVED_STAR_ID_PATTERN.match(stellar_object.id):
            unresolved += 1
            continue
        if stellar_object.is_catalog_identified:
            catalog_matched += 1
        else:
            position_only += 1
        resolved.append(stellar_object)

    logger.info(
        f"[{target_id}] {pipeline_name} star identification: {catalog_matched} catalog-matched, "
        f"{position_only} position-only (no catalog match), {unresolved} dropped (no sky position at all)"
    )
    return resolved, StarIdentificationBreakdown(catalog_matched, position_only, unresolved)


def merge_astrometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Merge rule for astrometry-derived stellar object updates.

    Appends any new target ids and carries over freshly solved/identified
    properties, without discarding data other targets may have already
    contributed to this same stellar object.

    Returns
    -------
    stellar_object : `Any`
        `updated_stellar_object` if `existing_stellar_object` is `None`;
        otherwise `existing_stellar_object` merged with the update.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    return existing_stellar_object


def merge_spectroscopy_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Merge rule for spectroscopy-derived stellar object updates.

    Appends any new target ids and carries over every field this run's
    spectroscopy pipeline owns (spectrum data, dispersion geometry,
    trail fit, and -- when registration identified the star against a
    catalog-identified reference field, see
    `identify_spectral_stars_via_registration` -- its catalog identity),
    without discarding data other targets may have already contributed
    to this same stellar object. A prior version of this rule only
    refreshed `spectrum_data_processed`, leaving `dispersion_angle` and
    the trail/rectangle geometry stuck at whatever the *first* run for
    this star computed even after a later run recomputed them.

    Returns
    -------
    stellar_object : `Any`
        `updated_stellar_object` if `existing_stellar_object` is `None`;
        otherwise `existing_stellar_object` merged with the update.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    existing_stellar_object.name = updated_stellar_object.name
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.is_catalog_identified = updated_stellar_object.is_catalog_identified
    existing_stellar_object.star_data = updated_stellar_object.star_data
    existing_stellar_object.detected_angle = updated_stellar_object.detected_angle
    existing_stellar_object.dispersion_angle = updated_stellar_object.dispersion_angle
    existing_stellar_object.trail_centerline_px = updated_stellar_object.trail_centerline_px
    existing_stellar_object.trail_width_px = updated_stellar_object.trail_width_px
    existing_stellar_object.rectangle = updated_stellar_object.rectangle
    existing_stellar_object.spectrum_data_processed = updated_stellar_object.spectrum_data_processed
    return existing_stellar_object


def merge_photometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Merge rule for photometry-derived stellar object updates.

    Carries over the freshly measured light curve and variability
    statistics, and appends any new target ids, without discarding data
    other targets may have already contributed to this same stellar object.

    Returns
    -------
    stellar_object : `Any`
        `updated_stellar_object` if `existing_stellar_object` is `None`;
        otherwise `existing_stellar_object` merged with the update.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    existing_stellar_object.light_curve = updated_stellar_object.light_curve
    if getattr(updated_stellar_object, "mean_flux", None) is not None:
        existing_stellar_object.mean_flux = updated_stellar_object.mean_flux
        existing_stellar_object.coefficient_of_variation = updated_stellar_object.coefficient_of_variation
        existing_stellar_object.variability_score = updated_stellar_object.variability_score
    # Cross-session matching (see _match_and_merge_across_sessions)
    # recomputes both fresh each run, so a full replace keeps a repeat
    # run's result authoritative rather than accumulating stale matches.
    existing_stellar_object.session_matches = updated_stellar_object.session_matches
    if updated_stellar_object.right_ascension:
        existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
        existing_stellar_object.declination = updated_stellar_object.declination
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    return existing_stellar_object


def _run_variability_analysis_for_session(
    session: Any,
    max_workers: int | None,
    id_prefix: str,
    target: Target | None = None,
    star_identifier: Any = None,
    use_astrometry_seed: bool = True,
) -> tuple[Any, list[Any], Any | None]:
    """Run one independent VariabilityAnalyzer against one observing session.

    Pixel-position re-centroiding against a single reference frame only
    holds within frames that share consistent framing/rotation -- i.e.
    one observing session. Mixing frames across sessions into a single
    `VariabilityAnalyzer.process()` call corrupts tracking for most stars
    (see the "photometry" case in `analyze_target`), so each session gets
    its own analyzer run and its own reference frame.

    When `use_astrometry_seed` is `True`, this session's reference frame
    is first run through `session_identification.identify_session_stars`
    -- reusing an existing FITS-header WCS when present, otherwise
    plate-solving -- and the resulting SIMBAD-identified stars are
    tracked instead of `VariabilityAnalyzer`'s own blind detection (see
    `VariabilityAnalyzer.process`'s `seed_stars` parameter), so results
    are tied to real, stable star identities instead of throwaway
    per-run `Star_N` labels.

    Parameters
    ----------
    session : `TargetSession`
        The observing session to analyze.
    max_workers : `int`, optional
        Forwarded to `VariabilityAnalyzer.process`.
    id_prefix : `str`
        Forwarded to `VariabilityAnalyzer.process`; ignored when
        `use_astrometry_seed` is `True` since identified stars already
        carry their own real ids.
    target : `Target`, optional
        Supplies the RA/Dec hint for astrometry identification when
        `use_astrometry_seed` is `True`.
    star_identifier : `StarIdentifier`, optional
        Shared identifier instance (reused across sessions) to run
        identification with when `use_astrometry_seed` is `True`.
    use_astrometry_seed : `bool`, optional
        Whether to seed this session's tracked stars from astrometry
        identification rather than blind detection (default `False`).

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer instance holding this session's `stellar_objects`,
        `rejected_files`, and `frame_ensemble_composition`.
    candidates : `list`
        This session's flagged variable-star candidates.
    identify_result : `SessionIdentificationResult` or `None`
        This session's astrometry identification result (including its
        resolved WCS, if any), if `use_astrometry_seed` was requested;
        `None` if seeding wasn't requested at all.
    """
    from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
        VariabilityAnalyzer,
    )

    seed_stars = None
    identify_result = None
    if use_astrometry_seed and star_identifier is not None and target is not None:
        from astrometricslib.tasks.stellar_tasks.astrometry_tasks.session_identification import (
            identify_session_stars,
        )
        from astrometricslib.utilities.image import AstrometricsImage

        center_ra = None
        center_dec = None
        try:
            center_ra = parse_coordinate_string(str(target.ra), is_ra=True)
            center_dec = parse_coordinate_string(str(target.dec), is_ra=False)
        except Exception as exc:
            # Blind solve (no center hint) if the target has no usable
            # RA/Dec yet.
            logger.debug("Falling back to blind solve, could not parse target RA/Dec: %s", exc)

        reference_image = AstrometricsImage(session.frame_paths[0])
        if reference_image.wcs is None and target and target.stacked_image:
            stacked_img = AstrometricsImage(target.stacked_image)
            swcs = stacked_img.wcs
            if swcs is not None and (swcs.is_celestial or swcs.has_celestial):
                reference_image.wcs = swcs

        identify_result = identify_session_stars(
            reference_image, star_identifier, center_ra=center_ra, center_dec=center_dec
        )
        seed_stars = identify_result.stellar_objects

    analyzer = VariabilityAnalyzer()
    analyzer.process(session.frame_paths, max_workers=max_workers, id_prefix=id_prefix, seed_stars=seed_stars)
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()
    candidates = analyzer.identify_variable_stars()
    return analyzer, candidates, identify_result


def _solve_session_wcs(session: Any, target: Target) -> Any | None:
    """Plate-solve one session's own reference frame for sky coordinates.

    Identifying the same physical star across sessions needs a common
    coordinate system -- pixel positions have no shared meaning across
    sessions with different framing/rotation.

    Fallback path only: when `_run_variability_analysis_for_session`
    already resolved this session's WCS via `use_astrometry_seed` (see
    `session_identification.identify_session_stars`), that result is
    passed to `_match_and_merge_across_sessions` via `session_wcs_map`
    and this function is never called for that session -- calling
    `PlateSolver` directly here, a second time against the same
    reference frame, would be a wasted, redundant solve. This function
    only still runs for sessions that weren't pre-resolved, i.e. the
    still-default `use_astrometry_seed=False` path, or a session whose
    astrometry-seed resolution itself failed (present in the map with
    a `None` value is NOT such a case -- see `session_wcs_map`'s
    docstring -- but a session simply absent from a `None` map is).
    `PlateSolver` is constructed with no API key here, so its online
    fallback tiers are already no-ops (`solve()` returns `None`
    immediately for those without a key); only the local `solve-field`
    attempt ever runs, so this adds no new network dependency. Unlike
    `identify_session_stars`, this never runs SIMBAD identification --
    only the WCS itself is needed for cross-session sky-position
    matching (§ `_stars_to_sky`), independent of whether any star ever
    gets a real catalog identity.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The solved WCS for this session's reference frame, or `None` if
        the solve failed or raised.
    """
    import warnings

    from astropy.wcs import WCS, FITSFixedWarning

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import PlateSolver

    reference_path = session.frame_paths[0]
    try:
        center_ra = None
        center_dec = None
        try:
            center_ra = parse_coordinate_string(str(target.ra), is_ra=True)
            center_dec = parse_coordinate_string(str(target.dec), is_ra=False)
        except Exception as exc:
            # Blind solve (no center hint) if the target has no usable
            # RA/Dec yet.
            logger.debug("Falling back to blind solve, could not parse target RA/Dec: %s", exc)

        solver = PlateSolver()
        header = solver.solve(
            image_path=reference_path,
            center_ra=center_ra,
            center_dec=center_dec,
            radius=2.0,
            # A real M 81 solve with a center hint measured 2.27s; 30s
            # gives >10x margin while still failing fast (vs. the
            # shared 300s default star_identifier.py uses for a real,
            # possibly hint-less solve) when a session's reference
            # frame isn't solvable -- this call is best-effort only,
            # already tolerating a failed solve by skipping cross-
            # session matching for that session (see docstring above).
            solve_timeout=30,
        )
        if header is None:
            logger.warning(
                f"Session {session.id} plate solve failed ({reference_path}); "
                "skipping cross-session star matching for this session."
            )
            return None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            return WCS(header, naxis=2)
    except Exception as solve_error:
        logger.warning(
            f"Session {session.id} plate solve failed ({reference_path}); "
            f"skipping cross-session star matching for this session: {solve_error}"
        )
        return None


def _stars_to_sky(stellar_objects: list[Any], wcs: Any) -> list[Any]:
    """Convert each star's detected pixel position into sky coordinates.

    Mutates `right_ascension`/`declination` on each successfully
    converted star in place, using the same low-level WCSLIB pixel ->
    world conversion already used for SIMBAD identification
    (`star_identifier.py`'s `wcs.wcs_pix2world(x, y, 0)`), applied here
    directly to `VariabilityAnalyzer`'s own detected pixel positions
    rather than running a separate independent detection pass.

    Returns
    -------
    converted : `list`
        The subset of `stellar_objects` that had a usable pixel
        position and were successfully converted. Tracked separately
        rather than re-inspected from the mutated fields afterward,
        since a genuine ra/dec of exactly 0.0 would otherwise read as
        falsy and be miscategorized as unconverted.
    """
    import numpy as np

    x_positions = []
    y_positions = []
    stars_with_position = []
    for star in stellar_objects:
        star_data = star.star_data
        if not isinstance(star_data, dict):
            continue
        x = star_data.get("xcentroid", star_data.get("x_centroid"))
        y = star_data.get("ycentroid", star_data.get("y_centroid"))
        if x is None or y is None:
            continue
        x_positions.append(x)
        y_positions.append(y)
        stars_with_position.append(star)

    if not stars_with_position:
        return []

    ra_array, dec_array = wcs.wcs_pix2world(np.array(x_positions), np.array(y_positions), 0)
    for star, ra, dec in zip(stars_with_position, ra_array, dec_array, strict=True):
        star.right_ascension = float(ra)
        star.declination = float(dec)

    return stars_with_position


def _positive_median_or_none(values: list[float]) -> float | None:
    """Median of the positive values in `values`, or `None` if none exist.

    Returns
    -------
    median : `float` or `None`
        The median of the positive values, or `None` if none exist.
    """
    import numpy as np

    array = np.array(values, dtype=float)
    array = array[array > 0]
    return float(np.median(array)) if array.size else None


def _rescale_flux_segment(
    values: list[float], own_median: float | None, target_median: float | None
) -> list[float]:
    """Rescale a flux segment so its own median matches `target_median`.

    Returns
    -------
    rescaled : `list` [`float`]
        `values` unchanged if either median is unavailable or non-positive;
        otherwise each value scaled by `target_median / own_median`.
    """
    if not own_median or not target_median:
        return list(values)
    factor = target_median / own_median
    return [float(value) * factor for value in values]


def _rescale_and_merge_light_curve(canonical: Any, new: Any) -> Any:
    """Merge a session's light curve into a canonical cross-session curve.

    Different sessions normalize flux against their own local
    comparison-star ensemble median (Eq. 5), so naively concatenating
    two sessions' fluxes would inject a spurious step-change at the
    session boundary that looks like variability but is really just an
    inter-session zero-point offset. The incoming (`new`) segment's
    `fluxes_normalized`/`fluxes_detrended` are each independently
    rescaled so their own median matches the canonical curve's existing
    median before concatenating, then the combined curve is sorted by
    timestamp. `magnitudes` (always empty today) is carried over
    untouched; `periodogram`/`transit_candidate` are single computed
    results, not per-timestamp arrays, and are dropped rather than
    carrying a stale single-session value forward on the merged curve.

    Returns
    -------
    merged : `LightCurve`
        A new `LightCurve` combining both segments, sorted by timestamp.
    """
    from astrometricslib.models.stellar_source import LightCurve

    canonical_median = _positive_median_or_none(canonical.fluxes_normalized)
    new_median = _positive_median_or_none(new.fluxes_normalized)
    rescaled_new_normalized = _rescale_flux_segment(new.fluxes_normalized, new_median, canonical_median)

    canonical_detrended_median = _positive_median_or_none(canonical.fluxes_detrended)
    new_detrended_median = _positive_median_or_none(new.fluxes_detrended)
    rescaled_new_detrended = _rescale_flux_segment(
        new.fluxes_detrended, new_detrended_median, canonical_detrended_median
    )

    combined_timestamps = canonical.timestamps + new.timestamps
    combined_fluxes = canonical.fluxes + new.fluxes
    combined_fluxes_normalized = canonical.fluxes_normalized + rescaled_new_normalized
    combined_fluxes_detrended = canonical.fluxes_detrended + rescaled_new_detrended
    combined_airmasses = canonical.airmasses + new.airmasses
    combined_is_saturated = canonical.is_saturated + new.is_saturated

    sort_order = sorted(range(len(combined_timestamps)), key=lambda i: combined_timestamps[i])

    def _reordered(values):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return [values[i] for i in sort_order] if len(values) == len(sort_order) else list(values)

    return LightCurve(
        timestamps=_reordered(combined_timestamps),
        fluxes=_reordered(combined_fluxes),
        fluxes_normalized=_reordered(combined_fluxes_normalized),
        fluxes_detrended=_reordered(combined_fluxes_detrended),
        airmasses=_reordered(combined_airmasses),
        is_saturated=_reordered(combined_is_saturated),
        magnitudes=canonical.magnitudes,
        periodogram=None,
        transit_candidate=None,
    )


def _match_and_merge_across_sessions(
    photometry_sessions: list[Any],
    per_session_results: list[tuple[Any, list[Any]]],
    target: Target,
    tolerance_arcsec: float = 5.0,
    session_wcs_map: dict[str, Any] | None = None,
) -> tuple[list[Any], list[str], int]:
    """Identify the same physical star across sessions and merge light curves.

    Uses each session's own reference-frame WCS to get real sky
    coordinates for its detected stars, then greedily matches stars
    across sessions within `tolerance_arcsec` and merges matched stars'
    light curves into one canonical, persisted entity per physical star
    (see `_rescale_and_merge_light_curve`). A star observed in only one
    session, or whose session's WCS is unavailable, persists standalone
    exactly as it did before this matching step existed -- this is
    purely additive, never a regression.

    Parameters
    ----------
    photometry_sessions : `list`
        Sessions in the same chronological order `derive_target_sessions`
        returns them in (sorted by night date, then gain, then offset).
    per_session_results : `list` [`tuple`]
        `(analyzer, session_candidates)` pairs, index-aligned with
        `photometry_sessions`, from `_run_variability_analysis_for_session`.
    target : `Target`
        Supplies the RA/Dec hint passed to the plate solver when a
        session's WCS isn't already available via `session_wcs_map`.
    tolerance_arcsec : `float`, optional
        Angular separation below which two sessions' detections are
        considered the same physical star (default 5.0", matching
        `stellar_service.find_or_create_by_position`'s existing
        spatial-match tolerance).
    session_wcs_map : `dict` [`str`, `Any`], optional
        Pre-resolved `{session.id: wcs}` entries, e.g. already obtained
        by `_run_variability_analysis_for_session`'s astrometry-seed
        step. When a session's id is present here (even with a `None`
        value, meaning that session's own resolution already failed),
        its WCS is used as-is and `_solve_session_wcs` is not called
        again for it -- avoiding a second, redundant plate-solve of the
        same reference frame. A session absent from the map (including
        when `session_wcs_map` itself is `None`) falls back to today's
        behavior of solving it here.

    Returns
    -------
    merged_stellar_objects : `list`
        Every session's stellar objects, with matched stars folded into
        one merged canonical entry per physical star (unmatched stars
        unchanged, still one entry each).
    sessions_missing_wcs : `list` [`str`]
        Session ids whose plate solve failed or found no usable stars.
    match_count : `int`
        Number of (star, contributing-session) pairs folded into an
        existing canonical entry -- total merges performed, not the
        count of distinct merged stars.
    """
    from astropy import units as astropy_units
    from astropy.coordinates import SkyCoord, search_around_sky

    from astrometricslib.models.stellar_source import StellarSessionMatch

    sessions_missing_wcs: list[str] = []
    match_count = 0
    # One (star, ra_deg, dec_deg) entry per distinct physical star found
    # so far. Kept as plain floats rather than individual SkyCoord
    # objects so a matching SkyCoord *array* can be built in one call
    # per session below -- vectorized, KD-tree-backed matching instead
    # of a per-pair Python loop, which does not scale to the thousands
    # of stars a dense field like M 81 detects per session.
    canonical_registry: list[tuple[Any, float, float]] = []
    merged_stellar_objects: list[Any] = []

    for session, (analyzer, _session_candidates) in zip(
        photometry_sessions, per_session_results, strict=True
    ):
        if session_wcs_map is not None and session.id in session_wcs_map:
            wcs = session_wcs_map[session.id]
        else:
            wcs = _solve_session_wcs(session, target)
        if wcs is None:
            sessions_missing_wcs.append(session.id)
            merged_stellar_objects.extend(analyzer.stellar_objects)
            continue

        session_stars_with_sky = _stars_to_sky(analyzer.stellar_objects, wcs)
        if not session_stars_with_sky:
            sessions_missing_wcs.append(session.id)
            merged_stellar_objects.extend(analyzer.stellar_objects)
            continue

        stars_with_sky_ids = {id(star) for star in session_stars_with_sky}
        merged_stellar_objects.extend(
            star for star in analyzer.stellar_objects if id(star) not in stars_with_sky_ids
        )

        if not canonical_registry:
            for star in session_stars_with_sky:
                canonical_registry.append((star, star.right_ascension, star.declination))
                merged_stellar_objects.append(star)
            continue

        # Greedy nearest-first one-to-one assignment: find every
        # (canonical, session_star) pair under tolerance via a KD-tree
        # search (`search_around_sky`, not an O(canonical x session)
        # pairwise Python loop -- that does not scale to a dense
        # field's thousands of stars per session), then assign in
        # ascending-separation order while both sides remain unclaimed.
        # A naive "first canonical entry within tolerance wins" per-star
        # loop can double-assign in a crowded field at this tolerance.
        canonical_coords = SkyCoord(
            ra=[entry[1] for entry in canonical_registry] * astropy_units.deg,
            dec=[entry[2] for entry in canonical_registry] * astropy_units.deg,
        )
        session_coords = SkyCoord(
            ra=[star.right_ascension for star in session_stars_with_sky] * astropy_units.deg,
            dec=[star.declination for star in session_stars_with_sky] * astropy_units.deg,
        )
        search_result = search_around_sky(
            canonical_coords, session_coords, tolerance_arcsec * astropy_units.arcsec
        )
        candidate_pairs = sorted(
            zip(
                search_result.angular_separation.arcsecond,
                search_result.indices_to_first_set,
                search_result.indices_to_second_set,
                strict=False,
            ),
            key=lambda pair: pair[0],
        )

        claimed_canonical_indices: set[int] = set()
        claimed_session_star_indices: set[int] = set()
        for separation_arcsec, canonical_index, session_star_index in candidate_pairs:
            canonical_index = int(canonical_index)
            session_star_index = int(session_star_index)
            if (
                canonical_index in claimed_canonical_indices
                or session_star_index in claimed_session_star_indices
            ):
                continue
            claimed_canonical_indices.add(canonical_index)
            claimed_session_star_indices.add(session_star_index)

            canonical_star, _canonical_ra, _canonical_dec = canonical_registry[canonical_index]
            new_star = session_stars_with_sky[session_star_index]
            canonical_star.light_curve = _rescale_and_merge_light_curve(
                canonical_star.light_curve, new_star.light_curve
            )
            canonical_star.session_matches.append(
                StellarSessionMatch(session_id=session.id, angular_separation_arcsec=float(separation_arcsec))
            )
            match_count += 1

        for session_star_index, star in enumerate(session_stars_with_sky):
            if session_star_index in claimed_session_star_indices:
                continue
            canonical_registry.append((star, star.right_ascension, star.declination))
            merged_stellar_objects.append(star)

    return merged_stellar_objects, sessions_missing_wcs, match_count


def analyze_frame_spectroscopy(target: Target, path: str, limit: int = 10) -> tuple[Any, list[Any]]:
    """Run spectroscopy analysis on a single FITS frame for this target.

    Returns
    -------
    result : `tuple[Any, list[Any]]`
        The result of
        `tasks.target_tasks.stacking_tasks.analyze_frame_spectroscopy`.
    """
    from astrometricslib.tasks.target_tasks.stacking_tasks import analyze_frame_spectroscopy as _analyze

    return _analyze(target, path, limit)


def analyze_target(
    target: Target,
    frames: list[FrameRecord] | None = None,
    pipeline_type: str = "astrometry",
    filter_type: str | None = None,
    butler=None,  # ruff: ignore[missing-type-function-argument]
    register_job: bool = True,
    path: str | None = None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Run the requested analysis pipeline on this Target domain object.

    Supports "astrometry", "spectroscopy", "photometry", and
    "asteroid_recovery" analysis types.

    Parameters
    ----------
    register_job : `bool`, optional
        Whether to auto-register a `ProcessingJob` for this run
        (default `True`), so a script/notebook/CLI call shows up in
        the UI's job manager without the caller doing anything extra.
        Callers that already track their own job for this exact call
        -- namely `AnalysisOrchestrator._start_analysis_task`, which
        creates and finalizes its own job around this same call --
        must pass `False` here, otherwise this call would register a
        second, redundant job for the same analysis run: two "started"
        rows appearing for one UI-triggered analysis, both ownerless
        from the orchestrator's perspective (it only tracks the job
        id it itself created).

    Returns
    -------
    result : `dict[str, Any]`
        Analysis-type-specific results and status fields.

    Raises
    ------
    ValueError
        If `pipeline_type` is not one of the supported analysis types, or
        if no frames or stacked image are available for an
        "astrometry"/"spectroscopy" analysis.
    """
    if butler is None:
        from astrometricslib.data_access.butler import DiskButler

        butler = DiskButler()

    # Automatically register processing job in astrometrics_log.db so
    # execution from scripts, notebooks, or CLI automatically appears in
    # the UI job manager. Skipped when register_job=False -- see that
    # parameter's docstring.
    # Mirrors AnalysisOrchestrator._start_analysis_task's job_logger setup (a
    # FileHandler for the job's own log file, plus a DbLogHandler so
    # JobHistoryList's "select job -> view log" pulls the same milestone
    # messages a UI-started analysis run would produce) so a script-started
    # run gets the same terminal output as one started from the UI.
    job_id = None
    logger_if = None
    job_logger = None
    job_log_handlers: list = []
    pipeline_logger = None
    if register_job:
        try:
            import os
            import uuid
            from datetime import datetime

            from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
            from astrometricslib.utilities.config_loader import get_configuration
            from astrometricslib.utilities.pipeline_models import ProcessingJob

            cfg = get_configuration()
            log_db_path = cfg.get_logs_db_path()
            logger_if = LoggerInterface(log_db_path)
            job_id = str(uuid.uuid4())

            safe_target = target.id.replace(" ", "_").replace("/", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = cfg.get_logs_path()
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = str(log_dir / f"analysis_{safe_target}_{timestamp}.log")

            job_logger = logging.getLogger(f"job_{job_id}")
            job_logger.propagate = False
            job_logger.setLevel(logging.INFO)
            file_handler = logging.FileHandler(log_file_path)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            db_handler = DbLogHandler(logger_if, job_id=job_id)
            job_logger.addHandler(file_handler)
            job_logger.addHandler(db_handler)

            # Also attach to the "astrometricslib" package logger -- the
            # common ancestor of every module logger used deeper in the
            # pipeline (star_identifier, plate_solver,
            # variability_analyzer, etc, all via
            # logging.getLogger(__name__) with propagate=True by
            # default). Without this, only this function's own
            # hand-written milestone messages ever reached the job's log
            # file/DB rows -- everything the pipeline itself decides
            # along the way (plate-solve fallback stages, SIMBAD/Gaia
            # query results, per-star identification) was invisible
            # there. Removed in the finally block below once this run is
            # done, since every job attaches its own handler instances
            # here and leaving them would mean this job's file/DB rows
            # keep receiving every *future* job's log lines too.
            job_log_handlers = [file_handler, db_handler]
            pipeline_logger = logging.getLogger("astrometricslib")
            for handler in job_log_handlers:
                pipeline_logger.addHandler(handler)
            pipeline_logger.setLevel(logging.INFO)

            initial_job = ProcessingJob(
                id=job_id,
                target_id=target.id,
                job_type="analysis",
                status="started",
                progress_current=0,
                progress_total=100,
                log_file_path=log_file_path,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
            logger_if.upsert_job(initial_job)
            job_logger.info(
                f"[{target.id}] Analysis job started for {target.id} (type: {pipeline_type}, Job: {job_id})"
            )
        except Exception as job_err:
            logger.warning(f"Could not register job in astrometrics_log.db: {job_err}")

    def _update_job_status(status_val: str, progress_val: int = 100):  # ruff: ignore[missing-return-type-private-function]
        if logger_if and job_id:
            try:
                j = logger_if.get_job(job_id)
                if j:
                    j.status = status_val
                    j.progress_current = progress_val
                    j.updated_at = datetime.now().isoformat()
                    logger_if.upsert_job(j)
            except Exception as exc:
                logger.debug("Failed to persist job status update for job '%s': %s", job_id, exc)
        if job_logger:
            if status_val == "completed":
                job_logger.info(f"[{target.id}] Analysis completed successfully.")
            elif status_val == "failed":
                job_logger.error(f"[{target.id}] Analysis failed.")

    try:
        # Resolve image path for astrometry/spectroscopy modes
        # if not explicitly provided
        if not path and pipeline_type in ("astrometry", "spectroscopy"):
            if frames:
                path = frames[0].path
            elif pipeline_type == "spectroscopy" and target.stacked_spectral_target:
                path = target.stacked_spectral_target
            elif pipeline_type == "astrometry" and target.stacked_image:
                path = target.stacked_image
            elif target.frames:
                path = target.frames[0].path
            else:
                _update_job_status("failed", 0)
                raise ValueError(
                    f"No frames or stacked image available for {pipeline_type} analysis"
                    f" on target {target.id}."
                )

        res_dict = _run_analysis_pipeline_match(
            target, frames, pipeline_type, filter_type, butler, path, **kwargs
        )
        _update_job_status("completed", 100)
        return res_dict
    except Exception as exc:
        _update_job_status("failed", 0)
        raise exc
    finally:
        # See the comment where job_log_handlers/pipeline_logger are
        # built above: every job attaches its own handler instances to
        # the shared "astrometricslib" logger, so they must come off
        # again once this run is done (any exit path), or this job's
        # file/DB rows would keep receiving every *future* job's log
        # lines too.
        if pipeline_logger:
            for handler in job_log_handlers:
                pipeline_logger.removeHandler(handler)


def _run_analysis_pipeline_match(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument]
    pipeline_type,  # ruff: ignore[missing-type-function-argument]
    filter_type,  # ruff: ignore[missing-type-function-argument]
    butler,  # ruff: ignore[missing-type-function-argument]
    path,  # ruff: ignore[missing-type-function-argument]
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    match pipeline_type:
        case "astrometry":
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
                AstrometryPipeline,
            )

            pipeline = AstrometryPipeline()
            context = pipeline.process(
                path, attempt_plate_solving=True, target_ra=target.ra, target_dec=target.dec, **kwargs
            )

            from astrometricslib.models.quality_summary import (
                AstrometryPipelineQualityMetrics,
                AstrometryQualitySummary,
            )

            context.stellar_objects, star_id_breakdown = _drop_unresolved_stars(
                context.stellar_objects, target_id=target.id, pipeline_name="astrometry"
            )
            simbad_matched_count = sum(
                1 for stellar_object in context.stellar_objects if stellar_object.spectral_type
            )
            # Read from the identification and solver modules rather than
            # threaded through the call chain: both keep per-process
            # tallies precisely so a summary can record what the run
            # actually experienced against the remote services.
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import (
                get_plate_solve_attempt_count,
            )
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
                get_gaia_query_statistics,
            )

            gaia_statistics = get_gaia_query_statistics()
            target.astrometry_quality_summary = AstrometryQualitySummary(
                target_id=target.id,
                astrometry_metrics=AstrometryPipelineQualityMetrics(
                    sources_detected=context.sources_detected,
                    solve_attempted=context.solve_attempted,
                    astrometric_residual_rms_arcsec=context.astrometric_residual_rms_arcsec,
                    plate_solve_succeeded=context.wcs is not None,
                    simbad_matched_count=simbad_matched_count,
                    remote_catalog_queries_attempted=int(gaia_statistics["attempted"]),
                    remote_catalog_queries_failed=int(gaia_statistics["failed"]),
                    remote_catalog_circuit_breaker_tripped=bool(gaia_statistics["circuit_breaker_tripped"]),
                    plate_solve_attempts=get_plate_solve_attempt_count(),
                    catalog_matched_star_count=star_id_breakdown.catalog_matched,
                    position_only_star_count=star_id_breakdown.position_only,
                    unresolved_star_count=star_id_breakdown.unresolved,
                ),
            )
            if not target.astrometry_quality_summary.astrometry_metrics.plate_solve_succeeded:
                target.astrometry_quality_summary.flagged = True
                target.astrometry_quality_summary.flag_reasons.append("plate solve failed")

            # If target RA and DEC are not populated or zero, pull
            # coordinates from plate solver
            is_ra_empty = not target.ra or target.ra.strip() in ("", "0", "0.0", "0h 0m 0s")
            is_dec_empty = not target.dec or target.dec.strip() in ("", "0", "0.0", "0° 0′ 0′′")
            is_zero = False
            if not (is_ra_empty or is_dec_empty):
                try:
                    resolved_ra_deg = parse_coordinate_string(str(target.ra), is_ra=True)
                    resolved_dec_deg = parse_coordinate_string(str(target.dec), is_ra=False)
                    # ruff: ignore[float-equality-comparison] -- exact
                    # sentinel check against the unset-coordinate
                    # default ("0h 0m 0s" / "0(deg) 0' 0''"), not a
                    # measured/computed value comparison.
                    if resolved_ra_deg == 0.0 and resolved_dec_deg == 0.0:
                        is_zero = True
                except Exception:
                    is_zero = True

            if is_ra_empty or is_dec_empty or is_zero:
                if context.wcs is not None:
                    try:
                        from astropy import units as u
                        from astropy.coordinates import SkyCoord

                        ra_deg = float(context.wcs.wcs.crval[0])
                        dec_deg = float(context.wcs.wcs.crval[1])
                        solved_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
                        target.ra = solved_coord.ra.to_string(unit=u.hour, sep=" ", precision=2)
                        target.dec = solved_coord.dec.to_string(unit=u.deg, sep=" ", precision=2)
                        logger.info(
                            f"Updated Target {target.id} RA/Dec from plate solver: "
                            f"RA={target.ra}, DEC={target.dec}"
                        )
                    except Exception as wcs_error:
                        logger.warning(
                            f"Failed to extract center coordinate from WCS for target "
                            f"{target.id}: {wcs_error}"
                        )

            if context.wcs is not None and path:
                import os

                if os.path.exists(path):
                    try:
                        from astropy.io import fits

                        with fits.open(path, mode="update") as hdul:
                            wcs_header = context.wcs.to_header()
                            for card in wcs_header.cards:
                                if not card.keyword:
                                    continue
                                hdul[0].header[card.keyword] = (card.value, card.comment)
                            hdul.flush()
                        logger.info(f"Updated FITS file {path} header with solved WCS keywords.")
                    except Exception as wcs_error:
                        logger.warning(f"Failed to update FITS file header with WCS: {wcs_error}")

            for obj in context.stellar_objects:
                if target.id not in obj.target_ids:
                    obj.target_ids.append(target.id)

            butler.merge_and_persist_records(
                "stellar_catalog", context.stellar_objects, merge_astrometry_stellar_object
            )

            return {
                "context": context,
                "stellar_objects": context.stellar_objects,
                "wcs": context.wcs,
                "image_stats": context.image.get_stats() if hasattr(context.image, "get_stats") else {},
            }

        case "spectroscopy":
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
                AstrometryPipeline,
            )
            from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import (
                SpectroscopyPipeline,
            )

            # Use the AstrometryPipeline to identify the stars in the field
            astrometry = AstrometryPipeline()
            context = astrometry.process(path, attempt_plate_solving=False)

            # The spectral stack has no WCS of its own (see the module
            # docstring on spectral_star_registration), so these stars
            # would otherwise stay permanently unidentified. If this
            # target already has a plate-solved, catalog-identified
            # star field (from an earlier astrometry run), register the
            # two point sets purely by their geometry and carry each
            # matched star's real identity over -- automatically,
            # whenever a reference field is available, no caller opt-in
            # needed. Registered against the *full* blind detection set
            # (`context.stellar_objects`, up to ~100 stars) rather than
            # just the handful spectroscopy.process() below goes on to
            # extract a spectrum for -- astroalign's triangle-asterism
            # matching needs a reasonably dense point set to find a
            # reliable transform, and 10ish points was regularly too few
            # to converge at all in practice. Registration only sets
            # identification fields, and these are the same object
            # instances spectroscopy.process() mutates next, so it
            # doesn't matter that most of them won't end up with a
            # spectrum extracted.
            reference_stellar_objects = [
                stellar_object
                for stellar_object in butler.get("stellar_catalog", {})
                if target.id in stellar_object.target_ids
                and stellar_object.is_catalog_identified
                and not stellar_object.id.endswith("::spectroscopy")
            ]
            if reference_stellar_objects:
                from astrometricslib.tasks.stellar_tasks.astrometry_tasks.spectral_star_registration import (
                    identify_spectral_stars_via_registration,
                )

                identify_spectral_stars_via_registration(context.stellar_objects, reference_stellar_objects)

            spectroscopy = SpectroscopyPipeline()
            limit = kwargs.get("limit", 10)
            stellar_objects, star_id_breakdown = _drop_unresolved_stars(
                spectroscopy.process(context, limit=limit), target_id=target.id, pipeline_name="spectroscopy"
            )

            for obj in stellar_objects:
                if target.id not in obj.target_ids:
                    obj.target_ids.append(target.id)

            butler.merge_and_persist_records(
                "stellar_catalog", stellar_objects, merge_spectroscopy_stellar_object
            )

            from astrometricslib.models.quality_summary import (
                SpectroscopyPipelineQualityMetrics,
                SpectroscopyQualitySummary,
            )
            from astrometricslib.tasks.shared.saturation_analysis import is_saturation_significant

            zero_order_fractions = spectroscopy.last_run_zero_order_saturation_fractions
            max_zero_order_fraction = max(zero_order_fractions) if zero_order_fractions else None
            zero_order_flagged = (
                is_saturation_significant(max_zero_order_fraction)
                if max_zero_order_fraction is not None
                else False
            )
            dispersion_angles = [
                obj.dispersion_angle for obj in stellar_objects if obj.dispersion_angle is not None
            ]
            all_trail_widths = [
                width
                for obj in stellar_objects
                if obj.trail_width_px
                for width in obj.trail_width_px
                if width > 0.0  # 0.0 marks a per-position fixed-box fallback, not a real fit
            ]
            trail_width_profile_available = bool(all_trail_widths)
            median_trail_width_px = (
                statistics.median(all_trail_widths) if trail_width_profile_available else None
            )

            target.spectroscopy_quality_summary = SpectroscopyQualitySummary(
                target_id=target.id,
                spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(
                    zero_order_saturated_pixel_fraction=max_zero_order_fraction,
                    zero_order_saturation_flagged=zero_order_flagged,
                    dispersion_angle_deg=dispersion_angles[0] if dispersion_angles else None,
                    trail_width_profile_available=trail_width_profile_available,
                    median_trail_width_px=median_trail_width_px,
                    catalog_matched_star_count=star_id_breakdown.catalog_matched,
                    position_only_star_count=star_id_breakdown.position_only,
                    unresolved_star_count=star_id_breakdown.unresolved,
                ),
            )
            if zero_order_flagged:
                target.spectroscopy_quality_summary.flagged = True
                target.spectroscopy_quality_summary.flag_reasons.append(
                    "zero-order saturated in at least one processed star"
                )

            return {"context": context, "stellar_objects": stellar_objects}

        case "photometry":
            image_paths = []
            photometry_frames = []
            target_frames = frames if frames is not None else target.frames
            for frame in target_frames:
                if not frame.path:
                    continue
                if not filter_type:
                    image_paths.append(frame.path)
                    photometry_frames.append(frame)
                elif frame.filter and (
                    frame.filter.name == filter_type.upper()
                    or (
                        filter_type.upper() in ["L", "LUMINANCE"]
                        and (
                            frame.filter.name in ["L", "LUMINANCE", "NONE", "UNKNOWN"]
                            or getattr(frame.filter, "value", "").upper()
                            in ["L", "LUMINANCE", "NONE", "UNKNOWN"]
                        )
                    )
                ):
                    image_paths.append(frame.path)
                    photometry_frames.append(frame)

            if not image_paths:
                return {
                    "status": "failed",
                    "targetId": target.id,
                    "analysisMode": "photometry",
                    "message": f"No frames found for filter: {filter_type}",
                }

            from astrometricslib.models.stellar_source import VariableCandidate
            from astrometricslib.tasks.target_tasks.target_session_tasks import derive_target_sessions

            # Photometry tracks stars via pixel-position re-centroiding
            # against a single reference frame per analysis run; that only
            # holds within one observing session (consistent framing and
            # rotation). A target's frames can span many separately
            # registered sessions, so each session gets its own
            # VariabilityAnalyzer run rather than one run spanning the
            # target's entire frame history (which corrupts tracking for
            # most stars once sessions mix).
            photometry_frames_with_timestamp = [f for f in photometry_frames if f.timestamp is not None]
            photometry_frames_without_timestamp = [f for f in photometry_frames if f.timestamp is None]
            photometry_sessions = derive_target_sessions(target.id, photometry_frames_with_timestamp)

            if not photometry_sessions:
                return {
                    "status": "failed",
                    "targetId": target.id,
                    "analysisMode": "photometry",
                    "message": (
                        "No frames with a usable capture timestamp to assign a session "
                        f"for filter: {filter_type}"
                    ),
                }

            per_session_results = []
            all_candidates = []
            all_rejected_files = []
            all_frame_ensemble_composition = []
            frames_rejected_for_small_ensemble = 0
            session_empty_reasons = []
            # Only session-prefix ids when there's more than one session,
            # so the common single-session target keeps today's plain
            # Star_N ids and doesn't churn its stellar_catalog rows.
            # Cross-session star matching (below) is likewise skipped
            # entirely for a single session -- there is nothing to
            # cross-match a lone session against.
            id_prefix_enabled = len(photometry_sessions) > 1

            # REQ: Analyze Target should identify the actual stars in an
            # image against a real catalog, not just track anonymous
            # per-run pixel detections. Opt-in for now (see rollout
            # notes) while this is verified against real data before
            # becoming the caller's default.
            use_astrometry_seed = bool(kwargs.get("use_astrometry_seed", True))
            star_identifier = None
            if use_astrometry_seed:
                from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
                    StarIdentifier,
                )

                star_identifier = StarIdentifier()

            session_wcs_map: dict[str, Any] = {}
            astrometry_identified_star_count = 0
            sessions_with_reused_header_wcs: list[str] = []
            sessions_with_replaced_header_wcs: list[str] = []

            for session in photometry_sessions:
                id_prefix = f"{session.id}:" if id_prefix_enabled else ""
                analyzer, session_candidates, identify_result = _run_variability_analysis_for_session(
                    session,
                    kwargs.get("max_workers"),
                    id_prefix,
                    target=target,
                    star_identifier=star_identifier,
                    use_astrometry_seed=use_astrometry_seed,
                )
                if identify_result is not None:
                    session_wcs_map[session.id] = identify_result.wcs
                    astrometry_identified_star_count += identify_result.simbad_matched_count
                    if identify_result.reused_existing_header_wcs:
                        sessions_with_reused_header_wcs.append(session.id)
                    if identify_result.header_wcs_replaced_after_verification:
                        sessions_with_replaced_header_wcs.append(session.id)
                if not analyzer.stellar_objects:
                    session_empty_reasons.append(
                        f"session {session.id}: reference-frame star detection failed, 0 stars processed"
                    )
                per_session_results.append((analyzer, session_candidates))
                all_candidates.extend(session_candidates)
                all_rejected_files.extend(analyzer.rejected_files)
                all_frame_ensemble_composition.extend(analyzer.frame_ensemble_composition)
                frames_rejected_for_small_ensemble += getattr(
                    analyzer, "frames_rejected_for_small_ensemble", 0
                )

            # Captured before cross-session merging/re-flagging below so
            # each candidate reflects its own session's local adaptive
            # cutoff -- VariableCandidate copies plain float values, so
            # later mutating the underlying StellarObjects (merging
            # light curves, recomputing a long-term CV) cannot retroactively
            # change an already-built VariableCandidate.
            candidates_formatted = [
                VariableCandidate(
                    id=star.id,
                    meanFlux=star.mean_flux,
                    coefficientOfVariation=star.coefficient_of_variation,
                    score=min(1.0, star.variability_score / 100.0),
                    ra=float(star.right_ascension) if star.right_ascension else 0.0,
                    dec=float(star.declination) if star.declination else 0.0,
                )
                for star in all_candidates
            ]

            sessions_missing_wcs: list[str] = []
            cross_session_match_count = 0
            long_term_candidates = []

            if id_prefix_enabled:
                all_stellar_objects, sessions_missing_wcs, cross_session_match_count = (
                    _match_and_merge_across_sessions(
                        photometry_sessions, per_session_results, target, session_wcs_map=session_wcs_map
                    )
                )
                if cross_session_match_count > 0:
                    from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
                        identify_long_term_variable_candidates,
                    )

                    long_term_candidates = identify_long_term_variable_candidates(all_stellar_objects)
            else:
                all_stellar_objects = per_session_results[0][0].stellar_objects

            long_term_candidates_formatted = [
                VariableCandidate(
                    id=star.id,
                    meanFlux=star.mean_flux,
                    coefficientOfVariation=star.coefficient_of_variation,
                    score=min(1.0, star.variability_score / 100.0),
                    ra=float(star.right_ascension) if star.right_ascension else 0.0,
                    dec=float(star.declination) if star.declination else 0.0,
                )
                for star in long_term_candidates
            ]

            all_stellar_objects, star_id_breakdown = _drop_unresolved_stars(
                all_stellar_objects, target_id=target.id, pipeline_name="photometry"
            )

            for obj in all_stellar_objects:
                if target.id not in obj.target_ids:
                    obj.target_ids.append(target.id)

            butler.merge_and_persist_records(
                "stellar_catalog", all_stellar_objects, merge_photometry_stellar_object
            )

            from astrometricslib.models.quality_summary import (
                ExcludedFrame,
                PhotometryPipelineQualityMetrics,
                PhotometryQualitySummary,
                TargetSessionContribution,
            )
            from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
                median_light_curve_scatter_mag,
            )

            rejected_paths = set(all_rejected_files)
            photometry_session_breakdown = [
                TargetSessionContribution(
                    session_id=session.id,
                    frames_contributed=len(session.frame_paths),
                    frames_clipped=sum(1 for path in session.frame_paths if path in rejected_paths),
                )
                for session in photometry_sessions
            ]

            frames_processed = sum(len(session.frame_paths) for session in photometry_sessions) - len(
                all_rejected_files
            )

            rejected_frames = [
                ExcludedFrame(path=path, reason="global frame outlier (ensemble median MAD-clipped)")
                for path in all_rejected_files
            ] + [
                ExcludedFrame(
                    path=frame.path,
                    reason="no capture timestamp available; cannot be assigned to a session",
                )
                for frame in photometry_frames_without_timestamp
            ]

            target.photometry_quality_summary = PhotometryQualitySummary(
                target_id=target.id,
                target_session_ids=[session.id for session in photometry_sessions],
                target_session_breakdown=photometry_session_breakdown,
                photometry_metrics=PhotometryPipelineQualityMetrics(
                    stars_processed=len(all_stellar_objects),
                    stars_found=len(all_stellar_objects),
                    frames_processed=frames_processed,
                    rejected_frames=rejected_frames,
                    frame_ensemble_composition=all_frame_ensemble_composition,
                    frames_rejected_for_small_ensemble=frames_rejected_for_small_ensemble,
                    variable_candidate_count=len(all_candidates),
                    cross_session_match_count=cross_session_match_count,
                    sessions_missing_wcs=sessions_missing_wcs,
                    long_term_variable_candidate_count=len(long_term_candidates),
                    astrometry_identified_star_count=astrometry_identified_star_count,
                    sessions_with_reused_header_wcs=sessions_with_reused_header_wcs,
                    sessions_with_replaced_header_wcs=sessions_with_replaced_header_wcs,
                    catalog_matched_star_count=star_id_breakdown.catalog_matched,
                    position_only_star_count=star_id_breakdown.position_only,
                    unresolved_star_count=star_id_breakdown.unresolved,
                    light_curve_scatter_rms_mag=median_light_curve_scatter_mag(all_stellar_objects),
                ),
            )
            if all_rejected_files:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.append(
                    f"{len(all_rejected_files)} frame(s) rejected as global ensemble outliers"
                )
            if frames_rejected_for_small_ensemble:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.append(
                    f"{frames_rejected_for_small_ensemble} frame(s) had fewer than "
                    "the minimum comparison stars and were not normalized"
                )
            if photometry_frames_without_timestamp:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.append(
                    f"{len(photometry_frames_without_timestamp)} frame(s) excluded for missing "
                    "capture timestamp"
                )
            if session_empty_reasons:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.extend(session_empty_reasons)
            if sessions_missing_wcs:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.append(
                    f"{len(sessions_missing_wcs)} session(s) could not be plate-solved for "
                    f"cross-session star matching: {', '.join(sessions_missing_wcs)}"
                )

            return {
                "status": "completed",
                "targetId": target.id,
                "totalImages": len(image_paths),
                "analysisMode": "photometry",
                "starsProcessed": len(all_stellar_objects),
                "spectraExtracted": 0,
                "starsFound": len(all_stellar_objects),
                "framesProcessed": frames_processed,
                "rejectedCount": len(all_rejected_files),
                "rejectedFiles": all_rejected_files,
                "variableCandidates": candidates_formatted,
                "longTermVariableCandidates": long_term_candidates_formatted,
                "crossSessionMatchCount": cross_session_match_count,
            }

        case "asteroid_recovery":
            from astrometricslib.models.quality_summary import (
                AsteroidRecoveryPipelineQualityMetrics,
                AsteroidRecoveryQualitySummary,
                TargetSessionContribution,
            )
            from astrometricslib.tasks.moving_object_tasks.moving_object_pipeline_tasks import (
                AsteroidRecoveryPipeline,
            )
            from astrometricslib.tasks.target_tasks.target_session_tasks import derive_target_sessions

            pipeline = AsteroidRecoveryPipeline()
            all_candidates = pipeline.process(target)
            metrics = pipeline.last_run_metrics
            # Persist only candidates that survived the discrimination
            # cascade (or were matched to a known body) -- `process()`
            # deliberately returns every rejected chain too (cosmic
            # rays, hot pixels, missed stars) so `metrics` above can
            # summarize them, but writing all of those into the
            # target's persisted record is unbounded: a single dense
            # field can produce tens of thousands of single-frame noise
            # chains, each carrying its own frame-detection payload.
            target.asteroid_candidates = [
                candidate
                for candidate in all_candidates
                if candidate.cascade_stage
                in (CascadeStage.RATE_LINEARITY_CONFIRMED, CascadeStage.EPHEMERIS_MATCHED)
            ]

            light_frames = [frame for frame in target.frames if frame.role == "LIGHT"]
            asteroid_recovery_sessions = derive_target_sessions(target.id, light_frames)
            # Per-session frame-exclusion identity isn't tracked by the
            # pipeline today (only the aggregate
            # frames_excluded_missing_pointing_metadata count is), so
            # frames_clipped is left at 0 here rather than fabricating a
            # breakdown.
            asteroid_recovery_session_breakdown = [
                TargetSessionContribution(
                    session_id=session.id,
                    frames_contributed=len(session.frame_paths),
                    frames_clipped=0,
                )
                for session in asteroid_recovery_sessions
            ]

            target.asteroid_recovery_quality_summary = AsteroidRecoveryQualitySummary(
                target_id=target.id,
                target_session_ids=[session.id for session in asteroid_recovery_sessions],
                target_session_breakdown=asteroid_recovery_session_breakdown,
                asteroid_recovery_metrics=AsteroidRecoveryPipelineQualityMetrics(**metrics),
            )
            if metrics.get("frames_excluded_missing_pointing_metadata", 0) > 0:
                target.asteroid_recovery_quality_summary.flagged = True
                target.asteroid_recovery_quality_summary.flag_reasons.append(
                    f"{metrics['frames_excluded_missing_pointing_metadata']} frame(s) excluded for "
                    "missing RA/DEC/NAXIS pointing metadata"
                )
            candidates_awaiting_recovery = sum(
                1
                for candidate in target.asteroid_candidates
                if candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
            )
            if candidates_awaiting_recovery > 0:
                target.asteroid_recovery_quality_summary.flagged = True
                target.asteroid_recovery_quality_summary.flag_reasons.append(
                    f"{candidates_awaiting_recovery} candidate(s) confirmed as movers but not "
                    "matched to a known body -- worth a manual look"
                )

            return {
                "status": "completed",
                "targetId": target.id,
                "analysisMode": "asteroid_recovery",
                "candidatesDetected": metrics.get("candidates_detected", 0),
                "candidatesRateLinearityConfirmed": metrics.get("candidates_rate_linearity_confirmed", 0),
                "candidatesEphemerisMatched": metrics.get("candidates_ephemeris_matched", 0),
                "candidates": target.asteroid_candidates,
            }

        case _:
            raise ValueError(f"Unknown analysis type: {pipeline_type}")


def reindex_frames(
    target: Target,
    prune_missing: bool = False,
    butler=None,  # ruff: ignore[missing-type-function-argument]
    refresh_headers: bool = False,
) -> None:
    """Sync frame records from disk and recompute total exposure time.

    `refresh_headers` also re-reads header-derived acquisition
    conditions on frames already tracked. Scanning alone only builds
    records for files it has not seen, so a field added to
    `FrameRecord` after a frame was first indexed stays `None` on that
    frame until this runs. Costs ~10ms per frame.
    """
    if butler is None:
        from astrometricslib.data_access.butler import DiskButler

        butler = DiskButler()

    if prune_missing:
        target.frames = [
            f
            for f in target.frames
            if butler.exists("raw_frame", {"path": f.path})
            and not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]

    butler.get("raw_frames", {"target": target, "refresh_headers": refresh_headers})


def get_header_information(target: Target, frame_path: str) -> list[dict[str, str]]:
    """Verify ownership and return raw FITS header card listings.

    Parameters
    ----------
    target : `Target`
        The target `frame_path` must belong to.
    frame_path : `str`
        Path to the FITS file to read.

    Returns
    -------
    header_cards : `list[dict[str, str]]`
        The FITS primary header's card entries for `frame_path`.

    Raises
    ------
    ValueError
        If `frame_path` does not belong to this target.
    """
    is_valid = any(f.path == frame_path for f in target.frames)
    if not is_valid:
        if frame_path in [target.processed_image, target.stacked_image, target.stacked_spectral_target]:
            is_valid = True

    if not is_valid:
        raise ValueError(f"Path {frame_path} does not belong to target {target.id}")

    from astrometricslib.data_access import image_conversions

    return image_conversions.get_fits_header(frame_path)


def stack_and_solve(
    target: Target,
    log_file: str | None = None,
    frames_to_stack: list[FrameRecord] | None = None,
    filter_type: Any | None = None,
    rejection_sigma: tuple[float, float] | None = None,
    filter_wfwhm: str | None = None,
    filter_round: str | None = None,
    stack_weight: str | None = None,
    generate_rejmap: bool | None = None,
    register_job: bool = True,
) -> str | None:
    """Run the Siril stacking pipeline on the target's frames.

    Accepts optional frames_to_stack list or filter_type to filter
    frames for stacking, plus overrides for the stack-time rejection
    sigma, FWHM/roundness selection filters, frame weighting, and
    rejection-map generation (each falls back to the configured
    default in astrometrics.config when omitted).

    Parameters
    ----------
    register_job : `bool`, optional
        Whether to auto-register a `ProcessingJob` for this stacking
        run (default `True`), so a script/notebook/CLI call shows up
        in the UI's job manager without the caller doing anything
        extra -- mirrors `analyze_target`'s `register_job` parameter
        and its job-registration shape. The backend's own UI-triggered
        "Stack Frames" button does not route through this function (it
        calls the Siril driver directly and manages its own job via
        `backend.services.processing.job_service`), so there is no
        double-registration risk here. Registration failures are
        logged and swallowed rather than raised, so a database issue
        never blocks the actual stack.

    Returns
    -------
    stacked_path : `str` or `None`
        The path to the stacked output file, or `None` if
        stacking did not produce an output.
    """
    job_id = None
    logger_if = None
    job_logger = None
    job_log_handlers: list = []
    pipeline_logger = None

    if register_job:
        try:
            import os
            import uuid
            from datetime import datetime

            from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
            from astrometricslib.utilities.config_loader import get_configuration
            from astrometricslib.utilities.pipeline_models import ProcessingJob

            cfg = get_configuration()
            logger_if = LoggerInterface(cfg.get_logs_db_path())
            job_id = str(uuid.uuid4())

            safe_target = target.id.replace(" ", "_").replace("/", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = cfg.get_logs_path()
            os.makedirs(log_dir, exist_ok=True)
            job_log_file_path = log_file or str(log_dir / f"stacking_{safe_target}_{timestamp}.log")

            job_logger = logging.getLogger(f"job_{job_id}")
            job_logger.propagate = False
            job_logger.setLevel(logging.INFO)
            file_handler = logging.FileHandler(job_log_file_path)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            db_handler = DbLogHandler(logger_if, job_id=job_id)
            job_logger.addHandler(file_handler)
            job_logger.addHandler(db_handler)

            # Also attach to the "astrometricslib" package logger, the
            # common ancestor of every module logger used deeper in the
            # stacking pipeline (stacking_tasks, siril_interface, etc,
            # all via logging.getLogger(__name__) with propagate=True by
            # default) -- mirrors analyze_target's identical setup.
            job_log_handlers = [file_handler, db_handler]
            pipeline_logger = logging.getLogger("astrometricslib")
            for handler in job_log_handlers:
                pipeline_logger.addHandler(handler)
            pipeline_logger.setLevel(logging.INFO)

            logger_if.upsert_job(
                ProcessingJob(
                    id=job_id,
                    target_id=target.id,
                    job_type="stacking",
                    status="started",
                    progress_current=0,
                    progress_total=100,
                    log_file_path=job_log_file_path,
                    created_at=datetime.now().isoformat(),
                    updated_at=datetime.now().isoformat(),
                )
            )
            job_logger.info(f"[{target.id}] Stacking job started (Job: {job_id}).")
        except Exception as job_err:
            logger.warning(f"Could not register stacking job in astrometrics_log.db: {job_err}")

    def _update_job_status(status_val: str, progress_val: int = 100):  # ruff: ignore[missing-return-type-private-function]
        if logger_if and job_id:
            try:
                j = logger_if.get_job(job_id)
                if j:
                    j.status = status_val
                    j.progress_current = progress_val
                    j.updated_at = datetime.now().isoformat()
                    logger_if.upsert_job(j)
            except Exception as exc:
                logger.debug("Failed to persist stacking job status update for job '%s': %s", job_id, exc)
        if job_logger:
            if status_val == "completed":
                job_logger.info(f"[{target.id}] Stacking completed successfully.")
            elif status_val == "failed":
                job_logger.error(f"[{target.id}] Stacking failed.")

    try:
        from astrometricslib.tasks.target_tasks import stacking_tasks

        stacked_path = stacking_tasks.stack_frames(
            target,
            log_file,
            frames_to_stack,
            filter_type,
            rejection_sigma=rejection_sigma,
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
            stack_weight=stack_weight,
            generate_rejmap=generate_rejmap,
        )
        # analyze_target(pipeline_type="astrometry") plate-solves
        # target.stacked_image
        # specifically (see analyze_target's path-resolution logic) -- it has
        # no notion of a spectral stack, so only run it when this call just
        # produced a *standard* stack. Checking which of
        # stacked_image/stacked_spectral_target now equals stacked_path
        # tells us which one stacking_tasks.stack_frames just set,
        # without needing a separate return value for it. analyze_target
        # builds and assigns target.astrometry_quality_summary itself
        # (including flagging a failed-but-attempted solve) -- the only
        # case it can't cover is a hard solver error, which raises before
        # analyze_target gets to build the summary at all, so that's
        # handled here instead.
        if stacked_path and stacked_path == target.stacked_image:
            try:
                # register_job=False: this stacking run already registered
                # its own job above, and analyze_target's docstring is
                # explicit about why a nested call must suppress its own
                # registration -- otherwise one stack_and_solve(solve=True)
                # call produces two ownerless "started" rows in the UI job
                # manager ("stacking" and "analysis") for what the caller
                # sees as a single action.
                analyze_target(target, pipeline_type="astrometry", register_job=False)
            except Exception as stacking_error:
                logger.warning(f"Astrometry plate solving failed after stacking: {stacking_error}")
                from astrometricslib.models.quality_summary import (
                    AstrometryPipelineQualityMetrics,
                    AstrometryQualitySummary,
                )

                target.astrometry_quality_summary = AstrometryQualitySummary(
                    target_id=target.id,
                    flagged=True,
                    flag_reasons=["plate solve failed"],
                    astrometry_metrics=AstrometryPipelineQualityMetrics(
                        sources_detected=0,
                        solve_attempted=False,
                        plate_solve_succeeded=False,
                        simbad_matched_count=0,
                    ),
                )
        _update_job_status("completed" if stacked_path else "failed", 100)
        return stacked_path
    except Exception as exc:
        _update_job_status("failed", 0)
        raise exc
    finally:
        if pipeline_logger:
            for handler in job_log_handlers:
                pipeline_logger.removeHandler(handler)


def _stack_frames_with_timeout(
    target: Target, frames_to_stack: list[FrameRecord], timeout_seconds: int | None = None
) -> str | None:
    """Run stack_and_solve on a background thread, enforcing a hard timeout.

    Siril can occasionally hang mid-stack (e.g. waiting on a pipe that
    never receives its completion marker), which would otherwise freeze
    the caller indefinitely.

    The budget covers time this stack spends *working*. Time spent
    blocked on the machine-wide Siril lock is added back as it accrues,
    because that queue exists to stop Siril runs competing for the CPU
    and must not convert into a cascade of timeouts for the targets
    waiting their turn.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    frames_to_stack : `list` [`FrameRecord`]
        Frames submitted to this stack; their count sets the budget.
    timeout_seconds : `int`, optional
        Explicit budget override. Defaults to
        `compute_stacking_timeout_seconds` for this frame count.

    Returns
    -------
    stacked_path : `str` or `None`
        The stacked output path, or `None` if stacking timed out.

    Raises
    ------
    Exception
        Whatever exception `stack_and_solve` raised on the
        background thread, re-raised here on the caller's thread.
    """  # ruff: ignore[docstring-extraneous-exception] -- `raise
    # outcome["error"]` re-raises a captured exception object,
    # which pydoclint cannot resolve to a declared type statically.
    from astrometricslib.drivers import siril_interface

    if timeout_seconds is None:
        timeout_seconds = compute_stacking_timeout_seconds(len(frames_to_stack))

    outcome: dict[str, Any] = {}

    def _run_stacking() -> None:
        try:
            outcome["path"] = stack_and_solve(target, frames_to_stack=frames_to_stack)
        except Exception as stacking_error:
            outcome["error"] = stacking_error

    siril_interface.reset_siril_lock_wait_seconds()
    stacking_thread = threading.Thread(target=_run_stacking, daemon=True)
    started_at = time.monotonic()
    stacking_thread.start()

    # Polled rather than a single join so the deadline can absorb lock
    # waits that only become known while this stack is queued. The
    # interval is short enough that the overshoot past a real timeout is
    # negligible against a budget measured in minutes.
    while True:
        stacking_thread.join(_STACKING_TIMEOUT_POLL_SECONDS)
        if not stacking_thread.is_alive():
            break
        elapsed_seconds = time.monotonic() - started_at
        if elapsed_seconds >= timeout_seconds + siril_interface.get_siril_lock_wait_seconds():
            break

    if stacking_thread.is_alive():
        lock_wait_seconds = siril_interface.get_siril_lock_wait_seconds()
        print(
            f"[{target.id}] Stacking timed out after {timeout_seconds} seconds "
            f"of working time ({lock_wait_seconds:.0f}s of Siril-lock wait excluded). "
            f"Abandoning this stack."
        )
        # A timeout is a quality event, not just a log line: recorded on
        # the target's existing stack summary when there is one, so the
        # abandoned stack is queryable rather than only discoverable by
        # reading the run's output. The summary may be absent entirely --
        # stack_frames builds it, and this stack never got that far -- in
        # which case the timeout stays a log-only fact.
        summary = getattr(target, "stack_quality_summary", None)
        metrics = getattr(summary, "stacking_metrics", None) if summary else None
        if metrics is not None:
            metrics.timed_out = True
            summary.flagged = True
            summary.flag_reasons.append(f"stacking timed out after {timeout_seconds}s")
        return None

    if "error" in outcome:
        raise outcome["error"]

    return outcome.get("path")


def run_full_pipeline(
    target: Target,
    astrometrics: Any,
    max_workers: int | None = None,
    *,
    camera_name: str,
    focal_length_mm: float | None = None,
) -> dict[str, str]:
    """Run the full stacking and analysis pipeline sequence.

    Executes stacking, astrometry, photometry, and spectroscopy in
    sequence, then persists this target's own record. Reusable by
    both a single-target caller and a batch orchestrator processing
    many targets concurrently, since persistence only ever touches
    this target's own row (see DiskButler's "target_record" dataset
    type) rather than a full-catalog resync.

    Parameters
    ----------
    target : `Target`
        The target to process.
    astrometrics : `Any`
        the high-level interface providing config and butler access.
    max_workers : `int`, optional
        Number of processes to use for the photometry step's
        per-frame parallel work. Passed through to analyze_target;
        `None` preserves that step's own default.
    camera_name : `str`
        Case-insensitive substring matched against each frame's
        camera name; only matching frames are processed, and every
        other frame on the target -- including from a second camera
        the target was also captured with -- is silently excluded.
        Required, keyword-only, and has no default: a multi-camera
        target silently dropping most of its frames under an
        unnoticed fallback is worse than a caller being forced to say
        which camera they mean. Use
        `TargetCatalog.list_camera_names` to see what's actually
        present in the catalog before choosing one.

    Returns
    -------
    stack_outputs : `dict`
        Mapping of stack type ("standard" and/or "spectral") to its
        output path for whichever stacks were actually produced;
        empty if none.

    Raises
    ------
    ValueError
        If stacking of the standard and/or spectral frame group
        fails to produce a valid output file.
    """
    stack_outputs: dict[str, str] = {}
    print("\n==========================================")
    print(f"STARTING BATCH PROCESSING FOR TARGET: {target.id}")
    print("==========================================")

    # Restrict all processing to frames captured with the requested
    # camera; every other camera's frames on this target are excluded.
    camera_frames = select_frames_for_camera(target, camera_name)

    # Then narrow to a single optic. Frames of different focal length
    # image at different scales -- this library's 300mm and 405mm optics
    # differ by 1.35x -- so a stack blending them has no single pixel
    # scale, cannot be plate solved accurately, and produces fluxes that
    # are not comparable between frames. Seven targets were being
    # stacked that way, NGC 7023 worst at 424 frames of one optic mixed
    # with 111 of the other.
    if focal_length_mm is not None:
        requested_key_suffix = f"@{round(float(focal_length_mm))}mm"
        selected_frames = [
            frame
            for frame in camera_frames
            if (frame_configuration_key(frame) or "").endswith(requested_key_suffix)
        ]
        if not selected_frames:
            print(
                f"[{target.id}] No frames at {focal_length_mm:g}mm for camera '{camera_name}'. "
                "Skipping all processing steps."
            )
            return stack_outputs
        unassignable = frames_missing_focal_length(target, camera_name)
        if unassignable:
            # Never dropped silently: a frame with no FOCALLEN cannot be
            # grouped, and on this library that is 602 frames. See
            # scripts/backfill_focal_length.
            print(
                f"[{target.id}] {len(unassignable)} frame(s) excluded: no FOCALLEN recorded, "
                "so their optic is unknown."
            )
        camera_frames = selected_frames

    if not camera_frames:
        print(
            f"[{target.id}] No frames matching camera '{camera_name}' found for this target. "
            "Skipping all processing steps."
        )
        return stack_outputs

    print(f"[{target.id}] Stacking frames...")
    target_frames = [
        frame
        for frame in camera_frames
        if not any(k in frame.path.lower() for k in ("_stacked", "starless", "starmask"))
    ]

    # Check if there is a mixed set of spectral and standard frames.
    # If so, run standard stacking on standard frames, and spectral
    # stacking on spectral frames.
    standard_frames = []
    spectral_frames = []
    for frame in target_frames:
        is_spectral = (
            frame.filter == FilterType.SPEC
            or getattr(frame.filter, "name", None) == "SPEC"
            or str(frame.filter).upper() in ("SPEC", "STAR ANALYZER 200")
        )
        if is_spectral:
            spectral_frames.append(frame)
        else:
            standard_frames.append(frame)

    # Deliberately no slot acquired here. `siril_interface.siril_process_lock`
    # takes one around the Siril launch itself, and taking a second from the
    # same "siril" semaphore here nests them: with two slots and two workers
    # each takes this outer slot, then both wait forever for an inner slot
    # neither can release. Observed on 2026-08-24 as two workers parked on
    # "Waiting for a free Siril slot" with no Siril process running.
    #
    # The driver is also the better place for it -- it covers every Siril
    # launch, including the backend service's, not just this batch path.
    if standard_frames and spectral_frames:
        print(
            f"[{target.id}] Target contains mixed frames. Stacking standard and spectral frames separately."
        )
        stacked_output = _stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking failed on mixed target.")
        stacked_spectral = _stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking failed on mixed target.")
        print(f"[{target.id}] Stacking succeeded: Standard={stacked_output}, Spectral={stacked_spectral}")
        stack_outputs["standard"] = stacked_output
        stack_outputs["spectral"] = stacked_spectral
    elif standard_frames:
        stacked_output = _stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Standard stacking succeeded: {stacked_output}")
        stack_outputs["standard"] = stacked_output
    elif spectral_frames:
        stacked_spectral = _stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Spectral stacking succeeded: {stacked_spectral}")
        stack_outputs["spectral"] = stacked_spectral
    else:
        print(
            f"[{target.id}] No valid frames matching camera '{camera_name}' found for stacking. "
            "Skipping stacking step."
        )

    # 2. Astrometry Analysis
    print(f"[{target.id}] Running Astrometry Analysis...")
    astrometry_results = analyze_target(target, pipeline_type="astrometry", butler=astrometrics.butler)
    if astrometry_results is None:
        raise ValueError("Astrometry analysis failed.")
    print(
        f"[{target.id}] Astrometry Analysis complete. "
        f"Resolved WCS: {astrometry_results.get('wcs') is not None}"
    )

    # 3. Photometry Analysis
    analysis_concurrency = astrometrics.config.get_analysis_concurrency()
    print(f"[{target.id}] Running Photometry/Variability Analysis...")
    with disk_interface.acquire_resource_slot(astrometrics.config, "analysis", analysis_concurrency):
        photometry_results = analyze_target(
            target,
            pipeline_type="photometry",
            frames=camera_frames,
            butler=astrometrics.butler,
            max_workers=max_workers,
        )
    if photometry_results.get("status") == "failed":
        raise ValueError(f"Photometry analysis failed: {photometry_results.get('message')}")
    print(
        f"[{target.id}] Photometry Analysis complete. Stars found: {photometry_results.get('starsFound', 0)}"
    )

    # 4. Spectroscopy Analysis (only when this target actually has a
    # SPEC stack)
    if spectral_frames:
        print(f"[{target.id}] Running Spectroscopy Analysis...")
        with disk_interface.acquire_resource_slot(astrometrics.config, "analysis", analysis_concurrency):
            spectroscopy_results = analyze_target(
                target, pipeline_type="spectroscopy", limit=10, butler=astrometrics.butler
            )
        if spectroscopy_results is None:
            raise ValueError("Spectroscopy analysis failed.")
        print(f"[{target.id}] Spectroscopy Analysis complete.")
    else:
        print(f"[{target.id}] No SPEC frames found for this target. Skipping spectroscopy analysis.")

    # Save this target's own record (safe under concurrent callers,
    # unlike a full-catalog resync)
    astrometrics.butler.put(target, "target_record", {})
    print(f"[{target.id}] Processing completed and metadata saved successfully.")

    return stack_outputs


def add_frame(
    target: Target, path: str, role: str = "LIGHT", filter_type: str | None = None, camera: str | None = None
) -> FrameRecord:
    """Add a single FrameRecord by parsing its FITS metadata.

    Returns
    -------
    frame_record : `FrameRecord`
        The newly added frame record.
    """
    from astrometricslib.tasks.target_tasks import stacking_tasks

    return stacking_tasks.add_frame(target, path, role, filter_type, camera)
