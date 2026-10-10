"""Tools for running photometry across a target's observing sessions.

Brightness tracking only works within one session at a time (consistent
framing and rotation), so `PhotometryPipelineAdapter.run` runs one
`VariabilityAnalyzer` pass per session
(`_run_variability_analysis_for_session`) and then, when there is more
than one session, matches each star's light curve across sessions by
its sky position (`_match_and_merge_across_sessions`) so a star seen on
two different nights ends up as one combined record instead of two
unrelated ones.

The merge keeps each session's normalized level as measured (it never
rescales one session to another) and records a per-session summary of
the comparison ensemble, so a later step can measure between-night
brightness change and a reader can judge whether it is real.
"""

import logging
import math
from typing import Any

from astrometricslib.drivers.catalog_access import POSITION_ONLY_STAR_ID_PREFIX
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target
from astrometricslib.pipelines.photometry.pre_processing.observation_times import (
    TIME_BASIS_BJD_TDB_GEOCENTRIC,
)
from astrometricslib.pipelines.shared.target_center_hint import resolve_target_center_hint
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

# How many of a target's brightest stars get a period search automatically,
# on top of the target's own star. The two searches take about 8 seconds per
# star (measured on a 139-point light curve: 1.1 s for the smooth-cycle
# search and 6.9 s for the repeating-dip search), and a target's field holds
# up to about 40,000 stars, so searching them all would take days. Ten
# brightest plus the main star is about 90 seconds and matches the ten stars
# the spectral analysis follows (`MAXIMUM_TRACKED_STARS_PER_FRAME`). The
# brightest stars are chosen because their light curves are the least noisy.
# Any other star can still be searched from the Astronomy Manager.
MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH = 10


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
        from astrometricslib.drivers.image import AstrometricsImage
        from astrometricslib.pipelines.shared.session_identification import (
            identify_session_stars,
        )

        center_ra, center_dec = resolve_target_center_hint(target)

        reference_image = AstrometricsImage(session.frame_paths[0])
        if reference_image.wcs is None and target and target.stacking.stacked_image:
            stacked_img = AstrometricsImage(target.stacking.stacked_image)
            swcs = stacked_img.wcs
            if swcs is not None and (swcs.is_celestial or swcs.has_celestial):
                reference_image.wcs = swcs

        identify_result = identify_session_stars(
            reference_image, star_identifier, center_ra=center_ra, center_dec=center_dec
        )
        seed_stars = identify_result.stellar_objects

    from astrometricslib.foundation.config import get_configuration

    analyzer = VariabilityAnalyzer()
    analyzer.process(
        session.frame_paths,
        max_workers=max_workers,
        id_prefix=id_prefix,
        seed_stars=seed_stars,
        target_position_deg=_field_position_deg(target, identify_result),
        observer_site=get_configuration().get_observatory_site(),
    )
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
    from astrometricslib.drivers.image import AstrometricsImage
    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
    from astrometricslib.pipelines.shared.session_identification import resolve_frame_wcs

    reference_path = session.frame_paths[0]
    try:
        center_ra, center_dec = resolve_target_center_hint(target)

        wcs, _reused_existing_wcs, _solve_attempted = resolve_frame_wcs(
            AstrometricsImage(reference_path),
            StarIdentifier(),
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


def _summarize_session_for_star(session_id: str, analyzer: Any, light_curve: Any) -> Any:
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
        The star's light curve from this session, before any merge.

    Returns
    -------
    summary : `SessionPhotometrySummary`
        The record for this star and session.
    """
    import numpy as np

    from astrometricslib.models.stellar_source import SessionPhotometrySummary

    normalized = np.array(light_curve.fluxes_normalized, dtype=float)
    normalized = normalized[normalized > 0]
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
    )


def _merge_light_curves(canonical: Any, new: Any) -> Any:
    """Merge a star's brightness data from two different nights.

    The two segments are concatenated and sorted by timestamp. No flux
    value is rescaled. Each session's normalized flux is the star's flux
    divided by that session's comparison ensemble, so the level of a
    session is the physical comparison between nights, provided the
    comparison stars behave alike. Rescaling the new session to the old
    one's median would remove exactly the between-night brightness change
    that `identify_long_term_variable_candidates` looks for.

    `fluxes_detrended` is concatenated the same way. The airmass detrend
    runs on one session at a time and keeps the session's mean level, so
    the merged values keep each session's trend removal and each
    session's level.

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
    `periodogram`, `transit_candidate` and the between-session fields are
    single computed results, not per-timestamp arrays, and are dropped
    instead of carrying a stale value onto the merged curve.

    Returns
    -------
    merged : `PhotometryResult`
        A new `PhotometryResult` combining both segments, sorted by timestamp.
    """
    from astrometricslib.models.stellar_source import PhotometryResult

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

    return PhotometryResult(
        timestamps=_reordered(combined_timestamps),
        fluxes=_reordered(combined_fluxes),
        fluxes_normalized=_reordered(combined_fluxes_normalized),
        fluxes_detrended=_reordered(combined_fluxes_detrended),
        airmasses=_reordered(combined_airmasses),
        is_saturated=_reordered(combined_is_saturated),
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

        for star in session_stars_with_sky:
            if star.photometry is not None:
                star.photometry.session_summaries = [
                    _summarize_session_for_star(session.id, analyzer, star.photometry)
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
            canonical_star.photometry = _merge_light_curves(canonical_star.photometry, new_star.photometry)
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


def select_period_search_stars(
    stellar_objects: list[StellarObject],
    center_ra: float | None,
    center_dec: float | None,
    limit: int = MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH,
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
    searchable = [star for star in stellar_objects if star.can_run_period_search]

    target_star = None
    if center_ra is not None and center_dec is not None:
        identified = [
            star
            for star in searchable
            if star.is_catalog_identified
            and not star.id.startswith(POSITION_ONLY_STAR_ID_PREFIX)
            and star.right_ascension not in (None, "")
            and star.declination not in (None, "")
        ]
        if identified:
            target_star = min(
                identified,
                key=lambda star: _separation_degrees(
                    float(star.right_ascension), float(star.declination), center_ra, center_dec
                ),
            )

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
    """
    from astrometricslib.pipelines.photometry.processing.family_wise_correction import (
        apply_family_wise_correction,
        needs_repeat,
        shuffles_needed,
    )

    shuffles = shuffles_needed(family_size)
    for star in searched:
        try:
            if needs_repeat(star.photometry.periodogram, family_size):
                analyzer.run_lomb_scargle_periodogram(star, shuffle_count=shuffles)
            if needs_repeat(star.photometry.transit_candidate, family_size):
                analyzer.run_bls_transit_search(star, shuffle_count=shuffles)
        except (AstrometricsError, *DATA_ERRORS) as search_error:
            logger.warning("[%s] Repeating the search for %s failed: %s", target_id, star.id, search_error)
    apply_family_wise_correction(all_results(), family_size)
    logger.info("[%s] Judged %s period searches together.", target_id, family_size)


def search_periods_and_save(
    stellar_objects: list[StellarObject],
    target: Target,
    catalog_access: Any,
    limit: int = MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH,
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
    """
    from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

    center_ra, center_dec = resolve_target_center_hint(target)
    chosen = select_period_search_stars(stellar_objects, center_ra, center_dec, limit)
    if not chosen:
        return 0

    analyzer = VariabilityAnalyzer()
    searched: list[StellarObject] = []
    for star in chosen:
        try:
            periodogram = analyzer.run_lomb_scargle_periodogram(star)
            transit_candidate = analyzer.run_bls_transit_search(star)
        except (AstrometricsError, *DATA_ERRORS) as search_error:
            logger.warning("[%s] Period search failed for %s: %s", target.id, star.id, search_error)
            continue
        if periodogram is not None or transit_candidate is not None:
            searched.append(star)

    _correct_for_the_number_of_searches(analyzer, searched, target.id)

    if searched:
        catalog_access.merge_and_record("stellar_catalog", searched, _add_period_results_to_saved_star)
    logger.info("[%s] Searched %s star(s) for repeating patterns.", target.id, len(searched))
    return len(searched)
