"""Tracks star brightness over time to find variable stars.

A target's images span many separately registered observing sessions, and
brightness tracking only works within one session at a time (consistent
framing and rotation), so this runs one `VariabilityAnalyzer` pass per
session and then, when there is more than one session, matches each
star's light curve across sessions by its sky position
(`_match_and_merge_across_sessions`) so a star seen on two different
nights ends up as one combined record instead of two unrelated ones.
"""

import logging
from typing import Any

from astrometricslib.models.target import Target
from astrometricslib.tasks.target_tasks.pipelines.star_persistence import (
    merge_photometry_stellar_object,
    persist_pipeline_stars,
)
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

logger = logging.getLogger(__name__)

# The percentage of rejected frames needed to trigger a quality warning flag.
# Normal processing naturally rejects a small number of frames (around 7.2%
# based on past runs). If we trigger a warning for anything less, we get
# too many false alarms. Setting the limit to 0.25 (25%) helps us catch
# real issues (like passing clouds) that need a human to check.
MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG = 0.25

# The minimum number of rejected frames needed to trigger a quality warning.
# This prevents false alarms when dealing with a small number of frames
# (under 20), where a single rejected frame could cause a high percentage.
MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG = 5


def _run_variability_analysis_for_session(
    session: Any,
    max_workers: int | None,
    id_prefix: str,
    target: Target | None = None,
    star_identifier: Any = None,
    use_astrometry_seed: bool = True,
) -> tuple[Any, list[Any], Any | None]:
    """Track star brightness over a single observing session.

    If `use_astrometry_seed` is turned on, this function tries to
    figure out the sky coordinates (plate solve) of the reference image.
    It then looks up the stars in SIMBAD/Gaia databases before tracking
    their brightness. This known identity stays with the star.

    Returns
    -------
    analyzer : VariabilityAnalyzer
        The tool that ran the analysis.
    candidates : list
        Stars that might be changing brightness (variable stars).
    identify_result : IdentifyStarsResult or None
        The result of looking up the stars, if we tried to do it.
        Useful for getting the sky coordinate map (WCS) later.
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
    """Plate-solve a session's reference frame to get its sky coordinates.

    We need this when we want to match stars across different sessions,
    but we haven't already looked up their identities in a database.
    (For example, if we skipped the SIMBAD lookup step earlier).

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The map from pixel to sky position, or None if the solve
        failed.
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
    """Convert star pixel locations into real sky coordinates (RA/Dec).

    Updates the input stars with their new right ascension and declination.

    Returns
    -------
    stars_with_position : `list`
        Only the stars that successfully got sky coordinates.
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
    """Merge a star's brightness data from two different nights.

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
    """Find the same real star in different sessions and combine its data.

    This takes the sky coordinates for stars in each session and pairs
    them up if they are very close to each other (under `tolerance_arcsec`).
    If they match, their light curves are merged into a single star record.
    If a star only appears once, or if we don't have sky coordinates for
    that session, it stays as its own separate record.

    Parameters
    ----------
    photometry_sessions : list
        The list of observing sessions, in chronological order.
    per_session_results : list of tuples
        The analysis tool and variable star candidates for each session.
    target : Target
        The target name and RA/Dec hint used to help the plate solver
        figure out coordinates if they are missing.
    tolerance_arcsec : float, optional
        How close two stars must be in arcseconds to be considered the
        same physical star (default is 5.0").
    session_wcs_map : dict, optional
        A map of session IDs to their known coordinate systems (WCS).
        This stops us from having to run the plate solver twice for the
        same image.

    Returns
    -------
    merged_stellar_objects : list
        The final list of stars, with matching ones combined.
    sessions_missing_wcs : list of str
        Names of sessions where we couldn't figure out the coordinates.
    match_count : int
        The total number of times we merged a star into another one.
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


def run_photometry_analysis(
    target: Target,
    frames,  # ruff: ignore[missing-type-function-argument]
    filter_type,  # ruff: ignore[missing-type-function-argument]
    butler,  # ruff: ignore[missing-type-function-argument]
    path,  # ruff: ignore[missing-type-function-argument] -- unused; photometry works from `frames`/`target.frames`
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Track star brightness across a target's images, session by session.

    Parameters
    ----------
    target : `Target`
        The target being tracked. Its `photometry_quality_summary` is set
        by this call.
    frames : `list` [`FrameRecord`] or `None`
        The frames to use; `target.frames` if not given.
    filter_type : `str` or `None`
        Only frames with this filter are used, if given.
    butler : `Any`
        Saves the stars this run found.
    path : `Any`
        Unused. Present so every pipeline runner shares one call signature.

    Returns
    -------
    result : `dict`
        Either the "failed, no usable frames" shape (``status``,
        ``targetId``, ``analysisMode``, ``message``) or the completed
        shape carrying every brightness-tracking metric.
    """
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
                    or getattr(frame.filter, "value", "").upper() in ["L", "LUMINANCE", "NONE", "UNKNOWN"]
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
                f"No frames with a usable capture timestamp to assign a session for filter: {filter_type}"
            ),
        }

    per_session_results = []
    all_candidates = []
    all_rejected_files = []
    all_frame_ensemble_composition = []
    session_empty_reasons = []
    # Only session-prefix ids when there's more than one session,
    # so the common single-session target keeps today's plain
    # Star_N ids and doesn't churn its stellar_catalog rows.
    # Cross-session star matching (below) is likewise skipped
    # entirely for a single session -- there is nothing to
    # cross-match a lone session against.
    id_prefix_enabled = len(photometry_sessions) > 1

    # Analyze Target should identify the actual stars in an
    # image against a real catalog, not just track anonymous
    # per-run pixel detections. Opt-in for now (see rollout
    # notes) while this is verified against real data before
    # becoming the caller's default.
    use_astrometry_seed = bool(kwargs.get("use_astrometry_seed", True))
    star_identifier = None
    if use_astrometry_seed:
        from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

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

    all_stellar_objects, star_id_breakdown = persist_pipeline_stars(
        all_stellar_objects,
        butler=butler,
        target_id=target.id,
        merge_function=merge_photometry_stellar_object,
        pipeline_name="photometry",
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
    # The rejected frames are recorded in the metrics either way;
    # this only decides whether the count is worth a human's
    # attention, which routine clipping is not.
    frames_contributed_total = sum(
        contribution.frames_contributed for contribution in photometry_session_breakdown
    )
    rejection_fraction = (
        len(all_rejected_files) / frames_contributed_total if frames_contributed_total else 0.0
    )
    if (
        len(all_rejected_files) >= MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG
        and rejection_fraction >= MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG
    ):
        target.photometry_quality_summary.flagged = True
        target.photometry_quality_summary.flag_reasons.append(
            f"{len(all_rejected_files)} of {frames_contributed_total} frame(s) "
            f"({rejection_fraction:.0%}) rejected as global ensemble outliers, which is high "
            "enough to suspect the comparison ensemble or the observing conditions"
        )
    if photometry_frames_without_timestamp:
        target.photometry_quality_summary.flagged = True
        target.photometry_quality_summary.flag_reasons.append(
            f"{len(photometry_frames_without_timestamp)} frame(s) excluded for missing capture timestamp"
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
