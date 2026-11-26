"""Tools for running photometry across a target's observing sessions.

Brightness tracking only works within one session at a time (consistent
framing and rotation), so `PhotometryPipelineAdapter.run` runs one
`VariabilityAnalyzer` pass per session
(`_run_variability_analysis_for_session`) and then, when there is more
than one session, matches each star's light curve across sessions by
its sky position (`_match_and_merge_across_sessions`) so a star seen on
two different nights ends up as one combined record instead of two
unrelated ones.

Each session normalizes by its own comparison ensemble, so before the
light curves are joined every session is tied to the field: scaled by the
one factor that the stars it shares with the reference session give
(`_tie_sessions_to_the_field`, see `session_tie`). The merge itself never
rescales a light curve. It records a per-session summary of the comparison
ensemble and the tie, so a later step can measure between-night brightness
change and a reader can judge whether it is real.
"""

import logging
import math
from collections.abc import Sequence
from typing import Any

from astrometricslib._internal.drivers.catalog_access import POSITION_ONLY_STAR_ID_PREFIX
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.jobs.runner import get_current_job
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_abandonment import RunAbandonedError, raise_if_abandoned
from astrometricslib.foundation.run_bases import RunBasis, factor_for
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.photometry.pre_processing.observation_times import (
    TIME_BASIS_BJD_TDB_GEOCENTRIC,
)
from astrometricslib.pipelines.shared.target_center_hint import resolve_target_center_hint
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

# How close two stars from different sessions must be on the sky to count as
# the same star. It is an angle on the sky, so it follows the plate scale: the
# value is a factor times the plate scale the run measured. The 5 arcsec
# limit was chosen as a sanity check (PHO-14, partly measured: the radius was
# not varied) and is 2.6 pixels of the reference camera, wide enough for the
# plate-solution error between nights and far below the spacing of the
# brightest field stars.
CROSS_SESSION_MATCH_RADIUS_ARCSEC = TunedParameter(
    name="photometry.cross_session_match_radius_arcsec",
    default=factor_for(5.0, RunBasis.PLATE_SCALE_ARCSEC_PER_PX),
    reference_value=5.0,
    unit="× plate scale",
    description="Sky distance within which two sessions' stars count as the same star.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.PLATE_SCALE_ARCSEC_PER_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-14",
)

# How many of a target's brightest stars get a period search automatically,
# on top of the target's own star. The two searches take about 8 seconds per
# star (measured on a 139-point light curve: 1.1 s for the smooth-cycle
# search and 6.9 s for the repeating-dip search), and a target's field holds
# up to about 40,000 stars, so searching them all would take days. Ten
# brightest plus the main star is about 90 seconds and matches the ten stars
# the spectral analysis follows (`MAXIMUM_TRACKED_STARS_PER_FRAME`). The
# brightest stars are chosen because their light curves are the least noisy.
# Any other star can still be searched from the Astronomy Manager.
MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH = TunedParameter(
    name="photometry.maximum_brightest_stars_for_period_search",
    default=10,
    unit="stars",
    description=(
        "How many of a target's brightest stars get a period search automatically, besides the "
        "target's own star."
    ),
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="PHO-D20",
)


def _field_position_deg(target: Target | None, identify_result: Any | None) -> tuple[float, float] | None:
    """Pick the sky position for a session's barycentric time correction.

    The barycentric correction depends on the direction of the object. The
    whole session uses one position, in this order:

    1. The target's own right ascension and declination, when it has them.
    2. The reference point (CRVAL) of the reference frame's plate solution,
       which lies near the middle of the frame.

    Parameters
    ----------
    target : `Target` or `None`
        The target being analyzed.
    identify_result : `IdentifyStarsResult` or `None`
        The star lookup for the session's reference frame, which carries its
        plate solution (`wcs`).

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        Right ascension and declination in degrees (ICRS), or `None` when
        neither source gives a position.
    """
    if target is not None:
        center_ra, center_dec = resolve_target_center_hint(target)
        if center_ra is not None and center_dec is not None:
            return float(center_ra), float(center_dec)
    wcs = getattr(identify_result, "wcs", None)
    if wcs is not None and getattr(wcs, "has_celestial", False):
        reference_ra, reference_dec = (float(value) for value in wcs.celestial.wcs.crval)
        if math.isfinite(reference_ra) and math.isfinite(reference_dec):
            return reference_ra, reference_dec
    return None


def _run_variability_analysis_for_session(
    session: Any,
    max_workers: int | None,
    id_prefix: str,
    target: Target | None = None,
    star_identifier: Any = None,
    use_astrometry_seed: bool = True,
    aperture_diameter_mm: float | None = None,
    aperture_reason: str | None = None,
) -> tuple[Any, list[Any], Any | None]:
    """Track star brightness over a single observing session.

    If `use_astrometry_seed` is turned on, this function tries to
    figure out the sky coordinates (plate solve) of the reference image.
    It then looks up the stars in SIMBAD/Gaia databases before tracking
    their brightness. This known identity stays with the star.

    The session's light curves also get mid-exposure BJD_TDB times. The
    sky position comes from `_field_position_deg` and the observatory from
    the configuration (`get_observatory_site`); without a site the times
    are taken from Earth's center, and without a position they are left
    out.

    The scintillation term of the flux errors needs the telescope's
    aperture (`aperture_diameter_mm`, or `aperture_reason` when it is not
    known) and the site's height from the configuration.

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
    from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
        VariabilityAnalyzer,
    )

    seed_stars = None
    identify_result = None
    if use_astrometry_seed and star_identifier is not None and target is not None:
        from astrometricslib._internal.drivers.image import AstrometricsImage
        from astrometricslib.pipelines.shared.session_identification import (
            header_is_plate_solved,
            identify_session_stars,
        )
        from astrometricslib.pipelines.shared.target_center_hint import resolve_solved_stack_wcs

        center_ra, center_dec = resolve_target_center_hint(target)

        reference_image = AstrometricsImage(session.frame_paths[0])
        borrowed_stack_wcs = False
        header = getattr(reference_image, "header", None)
        if header is None or not header_is_plate_solved(header):
            # The frame has no plate solution of its own (a map INDI writes
            # from the mount's pointing is not one). Borrow the solution of
            # the target's solved stack taken with the same camera and
            # focal length, never the main stack of another setup (a Nikon
            # session must not take the ZWO stack's map). It is checked
            # against the catalog like any reused map.
            stack_wcs = resolve_solved_stack_wcs(target, session.frame_paths[0])
            if stack_wcs is not None and stack_wcs.has_celestial:
                reference_image.wcs = stack_wcs
                borrowed_stack_wcs = True

        identify_result = identify_session_stars(
            reference_image,
            star_identifier,
            center_ra=center_ra,
            center_dec=center_dec,
            trust_image_wcs=borrowed_stack_wcs,
        )
        seed_stars = identify_result.stellar_objects

    from astrometricslib.foundation.config import get_configuration

    analyzer = VariabilityAnalyzer()
    analyzer.aperture_diameter_mm = aperture_diameter_mm
    analyzer.aperture_reason = aperture_reason
    site = get_configuration().get_observatory_site()
    analyzer.site_elevation_m = float(site.elevation_m) if site is not None else 0.0
    analyzer.process(
        session.frame_paths,
        max_workers=max_workers,
        id_prefix=id_prefix,
        seed_stars=seed_stars,
        target_position_deg=_field_position_deg(target, identify_result),
        observer_site=get_configuration().get_observatory_site(),
    )
    _fill_catalog_variability(analyzer.stellar_objects, getattr(star_identifier, "simbad", None))
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()
    candidates = analyzer.identify_variable_stars()
    return analyzer, candidates, identify_result


def _fill_catalog_variability(stellar_objects: list[Any], simbad: Any) -> None:
    """Fill blank catalog answers before comparison stars are chosen.

    A star that SIMBAD, VSX or Gaia DR3 lists as variable is kept out of the
    comparison set. The answers come from the library's stored catalog answers
    (see `pipelines/shared/stored_variability.py`), so a catalog that is down
    does not let a known variable through. The log says how many stars each
    catalog lists.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The session's stars; changed in memory only.
    simbad : `SimbadDriver` or `None`
        The SIMBAD driver whose stored answers are read.
    """
    from astrometricslib.pipelines.shared.stored_variability import (
        count_listed_by_source,
        fill_known_variability,
    )

    try:
        report = fill_known_variability(stellar_objects, simbad)
    except (AstrometricsError, *DATA_ERRORS) as fill_error:
        logger.warning("Catalog variability answers could not be read: %s", fill_error)
        return
    counts = count_listed_by_source(stellar_objects)
    logger.info(
        "  Listed as variable: SIMBAD %s, VSX %s, Gaia DR3 %s (%s stars in all; answers added: %s).",
        counts["SIMBAD"],
        counts["VSX"],
        counts["Gaia DR3"],
        counts["any"],
        dict(report.filled) or "none",
    )


def _solve_session_wcs(session: Any, target: Target, drivers: Drivers | None = None) -> Any | None:
    """Plate-solve a session's reference frame to get its sky coordinates.

    We need this when we want to match stars across different sessions,
    but we haven't already looked up their identities in a database.
    (For example, if we skipped the SIMBAD lookup step earlier).

    Parameters
    ----------
    session : `Any`
        The observing session; its first frame is solved.
    target : `Target`
        The target, whose position helps the solver.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver to use. Any left out is the
        built-in one.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The map from pixel to sky position, or None if the solve
        failed.
    """
    from astrometricslib._internal.drivers.image import AstrometricsImage
    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
    from astrometricslib.pipelines.shared.session_identification import resolve_frame_wcs

    reference_path = session.frame_paths[0]
    try:
        center_ra, center_dec = resolve_target_center_hint(target)

        wcs, _reused_existing_wcs, _solve_attempted = resolve_frame_wcs(
            AstrometricsImage(reference_path),
            StarIdentifier(drivers=drivers),
            center_ra=center_ra,
            center_dec=center_dec,
            write_back=False,
            # A real M 81 solve with a center hint measured 2.27s; 30s
            # gives >10x margin while still failing fast (vs. the
            # 300s default a real, possibly hint-less solve elsewhere
            # uses) when a session's reference frame isn't solvable --
            # this call is best-effort only, already tolerating a
            # failed solve by skipping cross-session matching for
            # that session (see docstring above).
            solve_timeout=30,
        )
        if wcs is None:
            logger.warning(
                "Session %s plate solve failed (%s); skipping cross-session star matching for this session.",
                session.id,
                reference_path,
            )
        return wcs
    except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as solve_error:
        logger.warning(
            "Session %s plate solve failed (%s); skipping cross-session star matching for this session: %s",
            session.id,
            reference_path,
            solve_error,
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


def _summarize_session_for_star(
    session_id: str, analyzer: Any, light_curve: Any, tie: Any | None = None
) -> Any:
    """Record what one session contributed to a star's light curve.

    Each session normalizes against its own comparison stars, so the
    star's level in one session is only comparable to its level in
    another if the two comparison groups behave alike. This summary keeps
    the numbers a reader needs to judge that: the comparison star count
    and ids, how many stars were turned away as comparison stars, the
    scatter of the comparison stars themselves, the typical flux of the
    comparison ensemble, and the star's median and scatter of normalized
    flux.

    Parameters
    ----------
    session_id : `str`
        The session the light curve came from.
    analyzer : `VariabilityAnalyzer`
        The analyzer that normalized the session. Its
        `frame_ensemble_composition` and `frame_reference_flux` supply the
        comparison star count and the ensemble flux, and `comparison_set`
        supplies the comparison star ids, their scatter and the rejected
        count. Any may be absent on a stand-in object; the matching
        fields are then empty or `None`.
    light_curve : `PhotometryResult`
        The star's light curve from this session, before any merge and
        after the session was tied to the field (see `session_tie`), so the
        median and the scatter are on the field's scale.
    tie : `SessionTieRecord`, optional
        How the session was tied. Its status, factor and uncertainty are
        copied to the summary. Left out, the tie fields stay `None`.

    Returns
    -------
    summary : `SessionPhotometrySummary`
        The record for this star and session.
    """
    import numpy as np

    from astrometricslib.models.stellar_source import SessionPhotometrySummary

    normalized = np.array(light_curve.fluxes_normalized, dtype=float)
    # A faint star's point at or below zero is a real measurement and stays.
    normalized = normalized[np.isfinite(normalized)]
    median_level = float(np.median(normalized)) if normalized.size else None
    scatter = None
    if normalized.size >= 2:
        # 1.4826 x the median absolute deviation equals the standard
        # deviation for normally distributed noise and ignores outliers.
        scatter = float(1.4826 * np.median(np.abs(normalized - np.median(normalized))))

    ensemble_sizes = [
        composition.ensemble_size
        for composition in getattr(analyzer, "frame_ensemble_composition", None) or []
    ]
    ensemble_fluxes = [
        float(flux) for flux in (getattr(analyzer, "frame_reference_flux", None) or {}).values() if flux > 0
    ]
    comparison_set = getattr(analyzer, "comparison_set", None)
    return SessionPhotometrySummary(
        session_id=session_id,
        point_count=int(normalized.size),
        median_normalized_flux=median_level,
        normalized_flux_scatter=scatter,
        comparison_star_count=int(np.median(ensemble_sizes)) if ensemble_sizes else None,
        ensemble_median_flux=float(np.median(ensemble_fluxes)) if ensemble_fluxes else None,
        comparison_star_ids=list(comparison_set.star_ids) if comparison_set is not None else [],
        comparison_scatter_mag=comparison_set.scatter_mag if comparison_set is not None else None,
        comparison_rejected_count=comparison_set.rejected_count if comparison_set is not None else None,
        tie_status=None if tie is None else tie.status,
        tie_factor=None if tie is None else tie.factor,
        tie_uncertainty=None if tie is None else tie.uncertainty,
    )


def _merged_crowding(canonical: Any, new: Any) -> dict[str, Any]:
    """Combine the crowding fields of two segments of one star's light curve.

    Each session measures crowding on its own reference frame, so the
    segments can disagree: seeing, focus and the frame's rotation change how
    close and how bright a neighbor looks. The merged record keeps the worst
    case, because the fields exist to warn about light from a neighbor, and
    points from a night on which a neighbor reached the aperture are in the
    merged curve too:

    * ``crowded`` is `True` when either segment is crowded, `False` when
      both recorded it and neither is, and `None` when neither recorded it.
    * ``nearest_neighbor_distance_px`` and ``nearest_neighbor_flux_ratio``
      come together from the segment with the closer neighbor, so the pair
      still describes one neighbor seen on one night.
    * ``masked_sky_fraction`` is the larger of the two.

    Parameters
    ----------
    canonical, new : `PhotometryResult`
        The two segments.

    Returns
    -------
    fields : `dict` [`str`, `Any`]
        The four crowding fields of the merged record.
    """
    flags = [segment.crowded for segment in (canonical, new) if segment.crowded is not None]
    with_neighbor = [
        segment for segment in (canonical, new) if segment.nearest_neighbor_distance_px is not None
    ]
    closest = min(with_neighbor, key=lambda segment: segment.nearest_neighbor_distance_px, default=None)
    masked = [
        segment.masked_sky_fraction for segment in (canonical, new) if segment.masked_sky_fraction is not None
    ]
    return {
        "crowded": any(flags) if flags else None,
        "nearest_neighbor_distance_px": None if closest is None else closest.nearest_neighbor_distance_px,
        "nearest_neighbor_flux_ratio": None if closest is None else closest.nearest_neighbor_flux_ratio,
        "masked_sky_fraction": max(masked) if masked else None,
    }


def _joined_reasons(first: str | None, second: str | None) -> str | None:
    """Join two "not judged" reasons, keeping each once.

    Parameters
    ----------
    first, second : `str` or `None`
        The reason each session's light curve gave, or `None`.

    Returns
    -------
    reason : `str` or `None`
        Both reasons separated by a semicolon, one reason when only one
        session gave one or both are the same, or `None` when neither did.
    """
    reasons = [reason for reason in (first, second) if reason]
    return "; ".join(dict.fromkeys(reasons)) or None


def _merge_light_curves(canonical: Any, new: Any) -> Any:
    """Merge a star's brightness data from two different nights.

    The two segments are concatenated and sorted by timestamp. This
    function rescales nothing. Each session's normalized flux is the star's
    flux divided by that session's comparison ensemble, and the sessions
    were put on one scale before this call by the tie to the field
    (`_tie_sessions_to_the_field`), which uses the stars the sessions share
    and not this star's own median. Rescaling the new session to this
    star's median instead would remove exactly the between-night brightness
    change that `identify_long_term_variable_candidates` looks for.

    `fluxes_detrended` is concatenated the same way. The airmass detrend
    runs on one session at a time and keeps the session's mean level, so
    the merged values keep each session's trend removal and each
    session's (tied) level. A session left untied keeps its own level, and
    the pooled statistics of a star seen in it include the difference.

    `session_summaries` from both segments are joined, in that order, so
    a later reader can see how each session's comparison ensemble looked.
    The per-measurement uncertainties (`flux_errors`,
    `fluxes_normalized_errors`, `fluxes_detrended_errors`) and the
    mid-exposure `time_bjd_tdb` times are joined and sorted the same way. A
    merged array is kept only when it has one value per point of the array it
    belongs to, so it is dropped when only one of the two segments has it. The
    flags saying the errors assumed unit gain or zero read noise are `True`
    when either segment's errors assumed it. The merged `time_basis` is the
    shared one, or the less exact geocentric one when the segments differ.
    `magnitudes` (always empty today) is carried over untouched.
    `saturated_points_removed` is the sum of the two segments' counts, so
    the merged curve still says how many saturated points were taken out.
    When neither segment has a point left, the merged curve keeps the
    reason the star has no light curve.
    The crowding fields keep the worst case of the two segments (see
    `_merged_crowding`), so a star crowded on any night stays crowded.
    `periodogram`, `transit_candidate` and the between-session fields are
    single computed results, not per-timestamp arrays, and are dropped
    instead of carrying a stale value onto the merged curve.

    Returns
    -------
    merged : `PhotometryResult`
        A new `PhotometryResult` combining both segments, sorted by timestamp.
    """
    from astrometricslib.models.stellar_source import PhotometryResult
    from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
        flux_mean_and_coefficient_of_variation,
    )

    combined_timestamps = canonical.timestamps + new.timestamps
    combined_fluxes = canonical.fluxes + new.fluxes
    combined_fluxes_normalized = canonical.fluxes_normalized + new.fluxes_normalized
    combined_fluxes_detrended = canonical.fluxes_detrended + new.fluxes_detrended
    combined_airmasses = canonical.airmasses + new.airmasses
    combined_is_saturated = canonical.is_saturated + new.is_saturated
    combined_flux_errors = canonical.flux_errors + new.flux_errors
    combined_normalized_errors = canonical.fluxes_normalized_errors + new.fluxes_normalized_errors
    combined_detrended_errors = canonical.fluxes_detrended_errors + new.fluxes_detrended_errors
    combined_bjd_times = canonical.time_bjd_tdb + new.time_bjd_tdb

    sort_order = sorted(range(len(combined_timestamps)), key=lambda i: combined_timestamps[i])

    def _reordered(values: list[Any]) -> list[Any]:
        """Put per-frame values in timestamp order.

        Returns
        -------
        ordered : `list`
            The values reordered, or a plain copy when the array is not
            one value per frame (an empty or truncated array stays as it is).
        """
        return [values[i] for i in sort_order] if len(values) == len(sort_order) else list(values)

    def _reordered_if_complete(values: list[Any], companion: list[Any]) -> list[Any]:
        """Put an uncertainty or time array in order, or drop it if incomplete.

        Parameters
        ----------
        values : `list`
            The joined array.
        companion : `list`
            The joined array it belongs to, which has one entry per point.

        Returns
        -------
        ordered : `list`
            The values in timestamp order, or an empty list when `values`
            does not have one entry per point of `companion`.
        """
        if not values or len(values) != len(companion) or len(companion) != len(sort_order):
            return []
        return [values[i] for i in sort_order]

    merged_flux_errors = _reordered_if_complete(combined_flux_errors, combined_fluxes)
    merged_normalized_errors = _reordered_if_complete(combined_normalized_errors, combined_fluxes_normalized)
    merged_detrended_errors = _reordered_if_complete(combined_detrended_errors, combined_fluxes_detrended)
    merged_bjd_times = _reordered_if_complete(combined_bjd_times, combined_timestamps)
    segments_with_errors = [light_curve for light_curve in (canonical, new) if light_curve.flux_errors]
    errors_kept = bool(merged_flux_errors or merged_normalized_errors or merged_detrended_errors)
    shared_bases = {light_curve.time_basis for light_curve in (canonical, new) if light_curve.time_basis}
    if not merged_bjd_times or not shared_bases:
        merged_basis = None
    elif len(shared_bases) == 1 and canonical.time_basis and new.time_basis:
        merged_basis = canonical.time_basis
    else:
        merged_basis = TIME_BASIS_BJD_TDB_GEOCENTRIC

    merged = PhotometryResult(
        timestamps=_reordered(combined_timestamps),
        fluxes=_reordered(combined_fluxes),
        fluxes_normalized=_reordered(combined_fluxes_normalized),
        fluxes_detrended=_reordered(combined_fluxes_detrended),
        airmasses=_reordered(combined_airmasses),
        is_saturated=_reordered(combined_is_saturated),
        saturated_points_removed=canonical.saturated_points_removed + new.saturated_points_removed,
        light_curve_withheld_reason=None
        if combined_timestamps
        else (canonical.light_curve_withheld_reason or new.light_curve_withheld_reason),
        not_judged_reason=_joined_reasons(canonical.not_judged_reason, new.not_judged_reason),
        flux_errors=merged_flux_errors,
        fluxes_normalized_errors=merged_normalized_errors,
        fluxes_detrended_errors=merged_detrended_errors,
        errors_assume_unit_gain=(
            any(light_curve.errors_assume_unit_gain for light_curve in segments_with_errors)
            if errors_kept
            else None
        ),
        errors_assume_zero_read_noise=(
            any(light_curve.errors_assume_zero_read_noise for light_curve in segments_with_errors)
            if errors_kept
            else None
        ),
        time_bjd_tdb=merged_bjd_times,
        time_basis=merged_basis,
        magnitudes=canonical.magnitudes,
        periodogram=None,
        transit_candidate=None,
        session_summaries=list(canonical.session_summaries) + list(new.session_summaries),
        **_merged_crowding(canonical, new),
    )
    # The merged curve has its own mean and scatter, measured the same way as
    # a session's. Left empty, the first star flagged by its change between
    # sessions stopped the run, because a variable candidate needs both. The
    # CV of a merged curve includes the change between sessions.
    statistics = flux_mean_and_coefficient_of_variation(merged)
    if statistics is not None:
        merged.mean_flux, merged.coefficient_of_variation = statistics
    return merged


def _pair_stars_by_position(
    first_positions: Sequence[tuple[float, float]],
    second_positions: Sequence[tuple[float, float]],
    tolerance_arcsec: float,
) -> list[tuple[int, int, float]]:
    """Pair the stars of two lists that lie within a tolerance on the sky.

    The pairs are one to one. A KD-tree search (`search_around_sky`, not a
    pairwise Python loop, which does not scale to the thousands of stars a
    dense field detects per session) finds every pair under the tolerance.
    The pairs are then assigned in ascending order of separation while both
    stars are unclaimed. A naive "first star within tolerance wins" loop can
    give one star to two partners in a crowded field at this tolerance.

    Parameters
    ----------
    first_positions, second_positions : `Sequence` [`tuple` [`float`, `float`]]
        The (right ascension, declination) of each star, in degrees.
    tolerance_arcsec : `float`
        The largest separation of a pair, in arcseconds.

    Returns
    -------
    pairs : `list` [`tuple` [`int`, `int`, `float`]]
        One entry per pair: the index in the first list, the index in the
        second list and the separation in arcseconds, nearest pair first.
    """
    from astropy import units as astropy_units
    from astropy.coordinates import SkyCoord, search_around_sky

    if not first_positions or not second_positions:
        return []
    first_coords = SkyCoord(
        ra=[position[0] for position in first_positions] * astropy_units.deg,
        dec=[position[1] for position in first_positions] * astropy_units.deg,
    )
    second_coords = SkyCoord(
        ra=[position[0] for position in second_positions] * astropy_units.deg,
        dec=[position[1] for position in second_positions] * astropy_units.deg,
    )
    search_result = search_around_sky(first_coords, second_coords, tolerance_arcsec * astropy_units.arcsec)
    candidate_pairs = sorted(
        zip(
            search_result.angular_separation.arcsecond,
            search_result.indices_to_first_set,
            search_result.indices_to_second_set,
            strict=False,
        ),
        key=lambda pair: pair[0],
    )
    claimed_first: set[int] = set()
    claimed_second: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for separation_arcsec, first_index, second_index in candidate_pairs:
        first_index = int(first_index)
        second_index = int(second_index)
        if first_index in claimed_first or second_index in claimed_second:
            continue
        claimed_first.add(first_index)
        claimed_second.add(second_index)
        pairs.append((first_index, second_index, float(separation_arcsec)))
    return pairs


def _tie_level(star: Any, minimum_points: int) -> float | None:
    """Give a star's median level in one session if it can be a tie star.

    Parameters
    ----------
    star : `StellarObject`
        The star as one session measured it.
    minimum_points : `int`
        The fewest normalized points the median needs.

    Returns
    -------
    level : `float` or `None`
        The median of the star's normalized fluxes (no units). `None` when
        the median is not above zero, the star has too few points, lost a
        saturated point, or a catalog lists it as variable (it cannot be
        assumed constant).
    """
    import numpy as np

    from astrometricslib.pipelines.photometry.post_processing.known_variability_labels import (
        is_listed_as_variable,
    )

    photometry = star.photometry
    if photometry is None or photometry.saturated_points_removed > 0 or is_listed_as_variable(star):
        return None
    values = np.asarray(photometry.fluxes_normalized, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < minimum_points:
        return None
    level = float(np.median(values))
    return level if level > 0 else None


def _tie_sessions_to_the_field(
    photometry_sessions: Sequence[Any],
    per_session_results: Sequence[tuple[Any, list[Any]]],
    sky_stars_by_session: Sequence[list[Any] | None],
    tolerance_arcsec: float,
    target_id: str,
) -> list[Any]:
    """Scale every session onto the reference session's scale, in place.

    Each session normalizes by its own comparison ensemble, so two sessions
    that share no comparison star place every star at a different level
    (on M 57, by a factor of about 1.97). The reference session is the one
    with the most measured stars (the earliest when several tie). For each
    other session this finds the stars both sessions measured, keeps the
    ones that can serve as tie stars, and finds the factor that brings the
    session onto the reference (`session_tie.measure_session_tie`). A tied
    session's normalized and detrended fluxes and their errors are
    multiplied by the factor. A session with too few tie stars is left as
    measured, its reason is recorded and a warning is logged.

    A star is a tie star when both sessions measured it with at least the
    points the between-session search needs, no saturated point was removed
    in either session, no catalog lists it as variable and neither session
    flagged it as a possible variable. A real change of one star cannot
    move the factor, because the factor is the clipped median of the
    ratios of all the tie stars.

    Parameters
    ----------
    photometry_sessions : `Sequence`
        The sessions, in chronological order.
    per_session_results : `Sequence` [`tuple`]
        The analyzer and the variable-star candidates of each session, in the
        same order.
    sky_stars_by_session : `Sequence` [`list` or `None`]
        Each session's stars that have a sky position, or `None` for a
        session with no plate solution.
    tolerance_arcsec : `float`
        The matching radius of the cross-session match.
    target_id : `str`
        The target, named in warnings.

    Returns
    -------
    records : `list` [`SessionTieRecord`]
        One record per session, in the order of `photometry_sessions`. Empty
        when fewer than two sessions have sky positions, because there is
        nothing to tie.
    """
    from astrometricslib.models.quality.quality_summary import SessionTieRecord
    from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
        MINIMUM_POINTS_PER_SESSION_FOR_LONG_TERM_SEARCH,
    )
    from astrometricslib.pipelines.photometry.session_tie import (
        TIE_STATUS_REFERENCE,
        TIE_STATUS_UNTIED,
        log_tied_session,
        measure_session_tie,
        scale_session_stars,
        warn_about_untied_session,
    )

    solved_indices = [index for index, stars in enumerate(sky_stars_by_session) if stars]
    if len(solved_indices) < 2:
        return []
    minimum_points = int(resolve(MINIMUM_POINTS_PER_SESSION_FOR_LONG_TERM_SEARCH).value)

    levels_by_session: dict[int, list[float | None]] = {}
    for index in solved_indices:
        levels_by_session[index] = [
            _tie_level(star, minimum_points) for star in sky_stars_by_session[index] or []
        ]
    # `max` returns the first of equal values, so a tie goes to the
    # earliest session.
    reference_index = max(
        solved_indices, key=lambda index: sum(level is not None for level in levels_by_session[index])
    )
    reference_id = photometry_sessions[reference_index].id
    reference_stars = sky_stars_by_session[reference_index] or []
    flagged_by_session = {
        index: {candidate.id for candidate in per_session_results[index][1]} for index in solved_indices
    }

    records: list[Any] = []
    for index, session in enumerate(photometry_sessions):
        if index == reference_index:
            records.append(
                SessionTieRecord(
                    session_id=session.id,
                    reference_session_id=reference_id,
                    status=TIE_STATUS_REFERENCE,
                    tie_star_count=sum(level is not None for level in levels_by_session[index]),
                )
            )
            continue
        if index not in levels_by_session:
            record = SessionTieRecord(
                session_id=session.id,
                reference_session_id=reference_id,
                status=TIE_STATUS_UNTIED,
                reason=(
                    "the session has no plate solution, so its stars cannot be "
                    "matched to the reference session"
                ),
            )
            warn_about_untied_session(record, target_id)
            records.append(record)
            continue
        session_stars = sky_stars_by_session[index] or []
        pairs = _pair_stars_by_position(
            [(star.right_ascension, star.declination) for star in reference_stars],
            [(star.right_ascension, star.declination) for star in session_stars],
            tolerance_arcsec,
        )
        reference_levels: list[float] = []
        session_levels: list[float] = []
        for reference_star_index, session_star_index, _separation in pairs:
            reference_level = levels_by_session[reference_index][reference_star_index]
            session_level = levels_by_session[index][session_star_index]
            if reference_level is None or session_level is None:
                continue
            if (
                reference_stars[reference_star_index].id in flagged_by_session[reference_index]
                or session_stars[session_star_index].id in flagged_by_session[index]
            ):
                continue
            reference_levels.append(reference_level)
            session_levels.append(session_level)
        record = measure_session_tie(session.id, reference_id, reference_levels, session_levels)
        if record.factor is not None:
            scale_session_stars(per_session_results[index][0].stellar_objects, record.factor)
            log_tied_session(record, target_id)
        else:
            warn_about_untied_session(record, target_id)
        records.append(record)
    return records


def _match_and_merge_across_sessions(
    photometry_sessions: list[Any],
    per_session_results: list[tuple[Any, list[Any]]],
    target: Target,
    tolerance_arcsec: float | None = None,
    session_wcs_map: dict[str, Any] | None = None,
    drivers: Drivers | None = None,
    session_tie_records: list[Any] | None = None,
) -> tuple[list[Any], list[str], int]:
    """Find the same real star in different sessions and combine its data.

    This takes the sky coordinates for stars in each session and pairs
    them up if they are very close to each other (under `tolerance_arcsec`).
    Before the light curves are joined, every session is put on one
    brightness scale (`_tie_sessions_to_the_field`), because each session's
    comparison ensemble sets its own level. If stars match, their light
    curves are merged into a single star record. If a star only appears
    once, or if we don't have sky coordinates for that session, it stays as
    its own separate record.

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
        same physical star. By default `CROSS_SESSION_MATCH_RADIUS_ARCSEC`,
        which follows the plate scale (5.0" on the reference setup).
    session_wcs_map : dict, optional
        A map of session IDs to their known coordinate systems (WCS).
        This stops us from having to run the plate solver twice for the
        same image.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver for any solve this needs.
    session_tie_records : `list` [`SessionTieRecord`], optional
        A list the function fills with one record per session: the factor
        that tied it to the reference session, or why it was left untied.
        Left empty when fewer than two sessions have sky positions.

    Returns
    -------
    merged_stellar_objects : list
        The final list of stars, with matching ones combined.
    sessions_missing_wcs : list of str
        Names of sessions where we couldn't figure out the coordinates.
    match_count : int
        The total number of times we merged a star into another one.
    """
    from astrometricslib.models.stellar_source import StellarSessionMatch

    if tolerance_arcsec is None:
        tolerance_arcsec = float(resolve(CROSS_SESSION_MATCH_RADIUS_ARCSEC).value)
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

    # First give every session its stars' sky positions. The tie needs all
    # sessions at once, so it cannot wait for the one-at-a-time merge below.
    sky_stars_by_session: list[list[Any] | None] = []
    for session, (analyzer, _session_candidates) in zip(
        photometry_sessions, per_session_results, strict=True
    ):
        if session_wcs_map is not None and session.id in session_wcs_map:
            wcs = session_wcs_map[session.id]
        else:
            wcs = _solve_session_wcs(session, target, drivers)
        session_stars_with_sky = [] if wcs is None else _stars_to_sky(analyzer.stellar_objects, wcs)
        sky_stars_by_session.append(session_stars_with_sky or None)

    tie_records = _tie_sessions_to_the_field(
        photometry_sessions, per_session_results, sky_stars_by_session, tolerance_arcsec, target.id
    )
    if session_tie_records is not None:
        session_tie_records.extend(tie_records)
    tie_by_session_id = {record.session_id: record for record in tie_records}

    for session, (analyzer, _session_candidates), session_stars_with_sky in zip(
        photometry_sessions, per_session_results, sky_stars_by_session, strict=True
    ):
        if session_stars_with_sky is None:
            sessions_missing_wcs.append(session.id)
            merged_stellar_objects.extend(analyzer.stellar_objects)
            continue

        for star in session_stars_with_sky:
            if star.photometry is not None:
                star.photometry.session_summaries = [
                    _summarize_session_for_star(
                        session.id, analyzer, star.photometry, tie_by_session_id.get(session.id)
                    )
                ]

        stars_with_sky_ids = {id(star) for star in session_stars_with_sky}
        merged_stellar_objects.extend(
            star for star in analyzer.stellar_objects if id(star) not in stars_with_sky_ids
        )

        if not canonical_registry:
            for star in session_stars_with_sky:
                canonical_registry.append((star, star.right_ascension, star.declination))
                merged_stellar_objects.append(star)
            continue

        pairs = _pair_stars_by_position(
            [(entry[1], entry[2]) for entry in canonical_registry],
            [(star.right_ascension, star.declination) for star in session_stars_with_sky],
            tolerance_arcsec,
        )
        claimed_session_star_indices: set[int] = set()
        for canonical_index, session_star_index, separation_arcsec in pairs:
            claimed_session_star_indices.add(session_star_index)
            canonical_star, _canonical_ra, _canonical_dec = canonical_registry[canonical_index]
            new_star = session_stars_with_sky[session_star_index]
            canonical_star.photometry = _merge_light_curves(canonical_star.photometry, new_star.photometry)
            canonical_star.session_matches.append(
                StellarSessionMatch(session_id=session.id, angular_separation_arcsec=separation_arcsec)
            )
            match_count += 1

        for session_star_index, star in enumerate(session_stars_with_sky):
            if session_star_index in claimed_session_star_indices:
                continue
            canonical_registry.append((star, star.right_ascension, star.declination))
            merged_stellar_objects.append(star)

    return merged_stellar_objects, sessions_missing_wcs, match_count


def _separation_degrees(
    ra_degrees: float, dec_degrees: float, other_ra_degrees: float, other_dec_degrees: float
) -> float:
    """Measure the angle on the sky between two points.

    Parameters
    ----------
    ra_degrees, dec_degrees : `float`
        The first point, in degrees.
    other_ra_degrees, other_dec_degrees : `float`
        The second point, in degrees.

    Returns
    -------
    separation_degrees : `float`
        The angle between them, in degrees.
    """
    ra = math.radians(ra_degrees)
    dec = math.radians(dec_degrees)
    other_ra = math.radians(other_ra_degrees)
    other_dec = math.radians(other_dec_degrees)
    # The haversine formula stays accurate for very small angles.
    haversine = (
        math.sin((other_dec - dec) / 2.0) ** 2
        + math.cos(dec) * math.cos(other_dec) * math.sin((other_ra - ra) / 2.0) ** 2
    )
    return math.degrees(2.0 * math.asin(math.sqrt(min(1.0, haversine))))


def find_target_star(
    stellar_objects: list[StellarObject], center_ra: float | None, center_dec: float | None
) -> StellarObject | None:
    """Find the target's own star: the identified star nearest its position.

    Stars known only by their position (ids starting with ``FIELD_J``) are
    never chosen.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The stars to choose from.
    center_ra : `float` or `None`
        The target's right ascension in degrees, if known.
    center_dec : `float` or `None`
        The target's declination in degrees, if known.

    Returns
    -------
    target_star : `StellarObject` or `None`
        The nearest identified star, or `None` when the target's position is
        unknown or no star is identified. The star may lie far from the
        target; the caller judges the distance (see `separation_arcsec`).
    """
    if center_ra is None or center_dec is None:
        return None
    identified = [
        star
        for star in stellar_objects
        if star.is_catalog_identified
        and not star.id.startswith(POSITION_ONLY_STAR_ID_PREFIX)
        and star.right_ascension not in (None, "")
        and star.declination not in (None, "")
    ]
    if not identified:
        return None
    return min(
        identified,
        key=lambda star: _separation_degrees(
            float(star.right_ascension), float(star.declination), center_ra, center_dec
        ),
    )


def _normalized_name(name: str) -> str:
    """Make a star or target name comparable.

    Parameters
    ----------
    name : `str`
        A name such as ``"Mirach"``, ``"* bet And"`` or ``"NAME Mirach"``.

    Returns
    -------
    key : `str`
        The name in lower case, with a leading catalog prefix (``NAME `` or
        ``* ``) removed and every run of spaces and underscores turned into
        one space.
    """
    key = " ".join(name.replace("_", " ").replace("\u00a0", " ").split()).casefold()
    for prefix in ("name ", "* "):
        if key.startswith(prefix):
            key = key[len(prefix) :]
    return key


def find_star_named(
    stellar_objects: list[StellarObject], target_names: Sequence[str]
) -> StellarObject | None:
    """Find the catalog-identified star that carries a target's name.

    A target that is a star is matched by its catalog identity, not by how
    near a star lies to the target's position, so a bright neighbor cannot
    stand in for it. The match compares the target's id and common name with
    each star's id, name and aliases (SIMBAD's common name when it has one,
    else its main identifier; a star saved under its Gaia number keeps its
    SIMBAD main id as an alias). Stars known only by their position (ids
    starting with ``FIELD_J``) are never chosen.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The stars to choose from.
    target_names : `Sequence` [`str`]
        The names the target is known by: its id and common name.

    Returns
    -------
    star : `StellarObject` or `None`
        The first star whose id or name equals one of the names, or `None`.
    """
    wanted = {_normalized_name(name) for name in target_names if name}
    if not wanted:
        return None
    for star in stellar_objects:
        if not star.is_catalog_identified or star.id.startswith(POSITION_ONLY_STAR_ID_PREFIX):
            continue
        if {
            _normalized_name(star.id),
            _normalized_name(star.name),
            *map(_normalized_name, star.aliases),
        } & wanted:
            return star
    return None


def separation_arcsec(star: StellarObject, center_ra: float, center_dec: float) -> float:
    """Find how far a star lies from a position on the sky.

    Parameters
    ----------
    star : `StellarObject`
        A star with a right ascension and a declination, in degrees.
    center_ra, center_dec : `float`
        The position, in degrees.

    Returns
    -------
    separation : `float`
        The angular distance, in arcseconds.
    """
    return 3600.0 * _separation_degrees(
        float(star.right_ascension), float(star.declination), center_ra, center_dec
    )


def select_period_search_stars(
    stellar_objects: list[StellarObject],
    center_ra: float | None,
    center_dec: float | None,
    limit: int | None = None,
) -> list[StellarObject]:
    """Choose the stars that get a period search automatically.

    A period search takes several seconds per star, so a target's tens of
    thousands of stars cannot all be searched. The ones chosen are:

    1. The target's own star: the catalog-identified star closest to the
       target's coordinates.
    2. The brightest other stars (the highest mean flux), whose light
       curves are the least noisy.

    Only stars with enough measurements for a search are considered, and
    stars known only by their position (ids starting with ``FIELD_J``) are
    never chosen as the target's star.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The stars found by the photometry run.
    center_ra : `float` or `None`
        The target's right ascension in degrees, if known.
    center_dec : `float` or `None`
        The target's declination in degrees, if known.
    limit : `int`, optional
        How many bright stars to add after the target's own star.

    Returns
    -------
    chosen : `list` [`StellarObject`]
        The target's star first (when it can be found), then up to `limit`
        stars from brightest to faintest.
    """
    if limit is None:
        limit = resolve(MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH).value
    searchable = [star for star in stellar_objects if star.can_run_period_search]
    target_star = find_target_star(searchable, center_ra, center_dec)

    brightest = sorted(
        (star for star in searchable if star is not target_star and (star.photometry.mean_flux or 0.0) > 0.0),
        key=lambda star: star.photometry.mean_flux,
        reverse=True,
    )[:limit]
    return ([target_star] if target_star is not None else []) + brightest


def _add_period_results_to_saved_star(
    existing_star: StellarObject | None, updated_star: StellarObject
) -> StellarObject:
    """Copy a star's new period-search results onto its saved row.

    Nothing else on the saved row is touched, so the search cannot undo
    anything the photometry save just wrote.

    Parameters
    ----------
    existing_star : `StellarObject` or `None`
        The saved row, or `None` if there is none.
    updated_star : `StellarObject`
        The star carrying the new results.

    Returns
    -------
    star : `StellarObject`
        The row to save.
    """
    if existing_star is None:
        return updated_star
    if existing_star.photometry is None or updated_star.photometry is None:
        return existing_star
    existing_star.photometry.periodogram = updated_star.photometry.periodogram
    existing_star.photometry.transit_candidate = updated_star.photometry.transit_candidate
    return existing_star


def _correct_for_the_number_of_searches(analyzer: Any, searched: list[StellarObject], target_id: str) -> None:
    """Judge the run's period-search verdicts against every search made.

    Each search's false-alarm probability holds for one search; the run made
    one cycle search and one dip search per star. A "detected" or "possible"
    result at the floor of its noise comparison is repeated with enough
    noise-only versions to be resolved, then every verdict is recomputed on
    its family-wise probability (see `family_wise_correction`).

    Parameters
    ----------
    analyzer : `VariabilityAnalyzer`
        Runs the repeated searches.
    searched : `list` [`StellarObject`]
        The stars that were searched; their results are corrected in place.
    target_id : `str`
        The target, for the log.
    """
    from astrometricslib.pipelines.photometry.processing.family_wise_correction import count_family
    from astrometricslib.pipelines.photometry.processing.period_checks import apply_verdict_checks

    def all_results() -> list[Any]:
        """Collect the run's cycle and dip results.

        Returns
        -------
        results : `list`
            Every non-empty result, two at most per star.
        """
        collected = []
        for star in searched:
            collected.extend(
                result
                for result in (star.photometry.periodogram, star.photometry.transit_candidate)
                if result
            )
        return collected

    family_size = count_family(all_results())
    if family_size >= 2:
        _repeat_and_correct_family(analyzer, searched, family_size, target_id, all_results)
    # The held-out and alias checks apply to every "detected" or "possible"
    # result, whether or not the run searched several stars.
    for star in searched:
        time_days, fluxes = analyzer.light_curve_arrays(star)
        apply_verdict_checks(star.photometry.periodogram, time_days, fluxes)
        apply_verdict_checks(star.photometry.transit_candidate, time_days, fluxes)


def _repeat_and_correct_family(
    analyzer: Any, searched: list[StellarObject], family_size: int, target_id: str, all_results: Any
) -> None:
    """Repeat searches that need it, then judge every verdict on the family.

    Parameters
    ----------
    analyzer : `VariabilityAnalyzer`
        Runs the repeated searches.
    searched : `list` [`StellarObject`]
        The stars that were searched.
    family_size : `int`
        How many searches the run made.
    target_id : `str`
        The target, for the log.
    all_results : `Callable`
        Collects the run's current results.

    Raises
    ------
    RunAbandonedError
        If the job was cancelled; the step stops at the next star.
    """
    from astrometricslib.pipelines.photometry.processing.family_wise_correction import (
        apply_family_wise_correction,
        needs_repeat,
        shuffles_needed,
        single_search_limit,
    )

    shuffles = shuffles_needed(family_size)
    # A repeated search whose probability is certain to end above this limit
    # can only be "not detected" once it is judged on the family, so it stops
    # there instead of searching every one of its many versions.
    stop_above = single_search_limit(family_size)
    to_repeat = [
        star
        for star in searched
        if needs_repeat(star.photometry.periodogram, family_size)
        or needs_repeat(star.photometry.transit_candidate, family_size)
    ]
    for position, star in enumerate(to_repeat, start=1):
        _report_search_progress(
            target_id,
            position - 1,
            len(to_repeat),
            f"Repeating the strongest searches (star {position} of {len(to_repeat)})",
        )
        try:
            if needs_repeat(star.photometry.periodogram, family_size):
                analyzer.run_lomb_scargle_periodogram(star, shuffle_count=shuffles, stop_above=stop_above)
            if needs_repeat(star.photometry.transit_candidate, family_size):
                analyzer.run_bls_transit_search(star, shuffle_count=shuffles, stop_above=stop_above)
        except RunAbandonedError:
            raise
        except (AstrometricsError, *DATA_ERRORS) as search_error:
            logger.warning("[%s] Repeating the search for %s failed: %s", target_id, star.id, search_error)
    apply_family_wise_correction(all_results(), family_size)
    logger.info("[%s] Judged %s period searches together.", target_id, family_size)


def _report_search_progress(target_id: str, done: int, total: int, message: str) -> None:
    """Tell the log and the running job how far the period searches are.

    A search takes seconds to minutes per star, so the job would otherwise
    say nothing for the whole step. The job's progress shows the counts
    (``done`` of ``total``); the log gets the message. Also stops the step
    if the job was cancelled.

    Parameters
    ----------
    target_id : `str`
        The target, for the log.
    done : `int`
        How many stars are finished.
    total : `int`
        How many stars the step has.
    message : `str`
        What the step is doing now.
    """
    raise_if_abandoned()
    text = f"[{target_id}] {message}."
    job = get_current_job()
    if job is None:
        logger.info(text)
    else:
        job.report_count(done, total, text)


def search_periods_and_save(
    stellar_objects: list[StellarObject],
    target: Target,
    catalog_access: Any,
    limit: int | None = None,
) -> int:
    """Search the main and brightest stars for repeating patterns, and save.

    Runs after the photometry itself has been saved, so a slow or failing
    search never delays or loses the light curves. A star whose search
    fails is skipped with a warning and the rest carry on.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The stars the photometry run found and saved.
    target : `Target`
        The target that was analyzed; its coordinates pick the main star.
    catalog_access : `Any`
        Provides `merge_and_record` to save the results.
    limit : `int`, optional
        How many bright stars to search after the target's own star.

    Returns
    -------
    searched_count : `int`
        How many stars had a search result saved.

    Raises
    ------
    RunAbandonedError
        If the job was cancelled; the step stops at the next star.
    """
    if limit is None:
        limit = resolve(MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH).value
    from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

    center_ra, center_dec = resolve_target_center_hint(target)
    chosen = select_period_search_stars(stellar_objects, center_ra, center_dec, limit)
    if not chosen:
        return 0

    analyzer = VariabilityAnalyzer()
    searched: list[StellarObject] = []
    for position, star in enumerate(chosen, start=1):
        _report_search_progress(
            target.id,
            position - 1,
            len(chosen),
            f"Searching star {position} of {len(chosen)} for repeating patterns ({star.id})",
        )
        try:
            periodogram = analyzer.run_lomb_scargle_periodogram(star)
            transit_candidate = analyzer.run_bls_transit_search(star)
        except RunAbandonedError:
            raise
        except (AstrometricsError, *DATA_ERRORS) as search_error:
            logger.warning("[%s] Period search failed for %s: %s", target.id, star.id, search_error)
            continue
        if periodogram is not None or transit_candidate is not None:
            searched.append(star)

    _correct_for_the_number_of_searches(analyzer, searched, target.id)

    if searched:
        catalog_access.merge_and_record("stellar_catalog", searched, _add_period_results_to_saved_star)
    _report_search_progress(
        target.id, len(chosen), len(chosen), f"Searched {len(searched)} star(s) for repeating patterns"
    )
    return len(searched)
