"""Tools for running photometry across a target's observing sessions.

Brightness tracking only works within one session at a time (consistent
framing and rotation), so `PhotometryPipelineAdapter.run` runs one
`VariabilityAnalyzer` pass per session
(`_run_variability_analysis_for_session`) and then, when there is more
than one session, matches each star's light curve across sessions by
its sky position (`_match_and_merge_across_sessions`) so a star seen on
two different nights ends up as one combined record instead of two
unrelated ones.
"""

import logging
import math
from typing import Any

from astrometricslib.drivers.catalog_access import POSITION_ONLY_STAR_ID_PREFIX
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.target_center_hint import resolve_target_center_hint

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

# Fewest measurements the smooth-cycle search accepts (the repeating-dip
# search needs 8). A star with fewer is not worth choosing, because its
# search would return nothing.
MINIMUM_POINTS_FOR_PERIOD_SEARCH = 5


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
    from astrometricslib.pipelines.photometry.variability_analyzer import (
        VariabilityAnalyzer,
    )

    seed_stars = None
    identify_result = None
    if use_astrometry_seed and star_identifier is not None and target is not None:
        from astrometricslib.drivers.image import AstrometricsImage
        from astrometricslib.pipelines.astrometry.session_identification import (
            identify_session_stars,
        )

        center_ra, center_dec = resolve_target_center_hint(target)

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
    from astrometricslib.drivers.image import AstrometricsImage
    from astrometricslib.pipelines.astrometry.session_identification import resolve_frame_wcs
    from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier

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
                f"Session {session.id} plate solve failed ({reference_path}); "
                "skipping cross-session star matching for this session."
            )
        return wcs
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
    merged : `PhotometryResult`
        A new `PhotometryResult` combining both segments, sorted by timestamp.
    """
    from astrometricslib.models.stellar_source import PhotometryResult

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

    return PhotometryResult(
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
            canonical_star.photometry = _rescale_and_merge_light_curve(
                canonical_star.photometry, new_star.photometry
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
    searchable = [
        star
        for star in stellar_objects
        if star.photometry is not None and len(star.photometry.timestamps) >= MINIMUM_POINTS_FOR_PERIOD_SEARCH
    ]

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
    from astrometricslib.pipelines.photometry.variability_analyzer import VariabilityAnalyzer

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
        except Exception as search_error:
            logger.warning("[%s] Period search failed for %s: %s", target.id, star.id, search_error)
            continue
        if periodogram is not None or transit_candidate is not None:
            searched.append(star)

    if searched:
        catalog_access.merge_and_record("stellar_catalog", searched, _add_period_results_to_saved_star)
    logger.info(f"[{target.id}] Searched {len(searched)} star(s) for repeating patterns.")
    return len(searched)
