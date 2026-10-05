"""Catalog Query Operations.

Registers online/local catalog query drivers and queries the local
Astrometrics database and online astronomical catalogs (SIMBAD, GAIA DR3,
bundled Hipparcos bright-star extract) for wayfindinglib.sky.Sky, parsing
results into standard Target/StellarObject domain model instances.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord

from astrometricslib import StellarObject, Target, parse_coordinate_string
from wayfindinglib.drivers.catalog import (
    CatalogDriver,
    DeepStarCatalogDriver,
    DeepStarSource,
    LocalBrightStarCatalogDriver,
)
from wayfindinglib.drivers.catalog.simbad_catalog_driver import (
    format_target_coordinates,
    read_simbad_field,
    resolve_simbad_radec,
)

logger = logging.getLogger(__name__)


def build_catalog_driver_registry(star_source: DeepStarSource | None = None) -> dict[str, CatalogDriver]:
    """Construct the registry of catalog query drivers.

    Every driver reads from this computer; none of them use the internet.

    Parameters
    ----------
    star_source : `DeepStarSource`, optional
        Where the deep-star driver reads the downloaded Gaia stars from.
        Without one, the deep-star driver returns no stars.

    Returns
    -------
    Dict[str, CatalogDriver]
        Registry keyed by driver name: "deep_stars", "hipparcos".
    """
    return {
        # Faint stars from the Gaia DR3 copy downloaded to disk, for the
        # viewport-scoped deep layer. Replaces the live Gaia archive query,
        # which took tens of seconds per view and could not be cached.
        "deep_stars": DeepStarCatalogDriver(star_source=star_source),
        # Locally bundled Hipparcos extract, for the full-sky "Bright
        # Stars" overview layer. GAIA is unsuitable for this layer — its
        # detectors saturate on very bright stars, so it's missing nearly
        # every naked-eye-famous star (Sirius, Vega, Polaris, etc.).
        "hipparcos": LocalBrightStarCatalogDriver(),
    }


def _filter_within_radius(
    candidate_objects: list[Target | StellarObject],
    candidate_coordinates: SkyCoord,
    center: SkyCoord,
    radius_deg: float,
) -> list[Target | StellarObject]:
    """Filter candidates by separation using a batch SkyCoord array.

    Parameters
    ----------
    candidate_objects : List[Union[Target, StellarObject]]
        Objects in the same order as `candidate_coordinates`.
    candidate_coordinates : SkyCoord
        Array SkyCoord holding one coordinate per entry in `candidate_objects`.
    center : SkyCoord
        Scalar search center.
    radius_deg : float
        Search radius in degrees.

    Returns
    -------
    List[Union[Target, StellarObject]]
        The subset of `candidate_objects` whose coordinate falls within
        `radius_deg` of `center`.
    """
    if len(candidate_objects) == 0:
        return []
    within_radius = center.separation(candidate_coordinates).deg <= radius_deg
    return [
        candidate_object
        for candidate_object, is_within_radius in zip(candidate_objects, within_radius, strict=False)
        if is_within_radius
    ]


_DATABASE_FILES = ("astrometrics.db", "astrometrics.db-wal")

_target_position_cache: dict[str, tuple[tuple[int, ...], list[tuple[str, float, float]]]] = {}
"""Parsed target positions per library folder, with the database version."""
_target_position_cache_lock = threading.Lock()


def _database_version(astrometrics) -> tuple[str, tuple[int, ...]] | None:  # ruff: ignore[missing-type-function-argument]
    """Identify the state of the library database file.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle, which knows where the library is.

    Returns
    -------
    version : `tuple` or `None`
        The library folder and the modification times of the database file
        and its write-ahead log. Any write to the library changes it. `None`
        when the files cannot be inspected, which turns caching off.
    """
    try:
        library = Path(astrometrics.config.get_library_path())
    except AttributeError, TypeError:
        return None
    modification_times = []
    for name in _DATABASE_FILES:
        try:
            modification_times.append((library / name).stat().st_mtime_ns)
        except OSError:
            # The write-ahead log exists only while the database is written.
            modification_times.append(0)
    if not modification_times[0]:
        return None
    return str(library), tuple(modification_times)


def _parse_target_positions(targets: list[Target]) -> list[tuple[str, float, float]]:
    """Turn targets into ``(id, ra, dec)`` in degrees, skipping blank ones.

    Coordinates are parsed once per candidate via the canonical
    `parse_coordinate_string` (RA=hourangle, Dec=degrees, unit markers
    stripped). A single malformed target cannot fail the whole batch: it is
    skipped and logged individually.

    Returns
    -------
    positions : `list` of `tuple`
        One entry per target that has a usable position.
    """
    positions: list[tuple[str, float, float]] = []
    for target in targets:
        # Skip default, empty, or uninitialized coordinates to speed
        # up matching
        is_blank = not target.ra or not target.dec or target.ra.isspace() or target.dec.isspace()
        is_placeholder = target.ra in (None, "", "None", "0h 0m 0s") and target.dec in (
            None,
            "",
            "None",
            "0° 0′ 0′′",
            "0d 0m 0s",
            "0° 0′ 0″",
        )
        if is_blank or is_placeholder:
            continue
        try:
            ra_deg = parse_coordinate_string(target.ra, is_ra=True)
            dec_deg = parse_coordinate_string(target.dec, is_ra=False)
        except Exception as parse_error:
            logger.warning("Failed to parse coordinates for local target %s: %s", target.id, parse_error)
            continue
        positions.append((target.id, ra_deg, dec_deg))
    return positions


def _targets_within_radius(astrometrics, center: SkyCoord, radius_deg: float) -> list[Target]:  # ruff: ignore[missing-type-function-argument]
    """Find the library targets inside a circle on the sky.

    Reading and validating every target record from the database took about
    120 ms for 53 targets, and every Planetarium pan or zoom paid it just to
    learn the targets' positions. The parsed positions are kept until the
    database changes, so only the few targets that match are looked up in
    full.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    center : `SkyCoord`
        Scalar search center.
    radius_deg : `float`
        Search radius in degrees.

    Returns
    -------
    targets : `list` [`Target`]
        The targets whose position falls within `radius_deg` of `center`.
    """
    version = _database_version(astrometrics)
    loaded_targets: list[Target] | None = None
    positions: list[tuple[str, float, float]] | None = None
    if version is not None:
        with _target_position_cache_lock:
            cached = _target_position_cache.get(version[0])
        if cached is not None and cached[0] == version[1]:
            positions = cached[1]

    if positions is None:
        loaded_targets = astrometrics.targets.list()
        positions = _parse_target_positions(loaded_targets)
        if version is not None:
            with _target_position_cache_lock:
                _target_position_cache[version[0]] = (version[1], positions)

    if not positions:
        return []
    # Converting to numpy arrays before SkyCoord() matters: given plain
    # Python lists, astropy falls back to constructing one Angle per
    # element in a Python-level loop instead of a vectorized numpy path.
    coordinates = SkyCoord(
        ra=np.asarray([ra for _, ra, _ in positions], dtype=float),
        dec=np.asarray([dec for _, _, dec in positions], dtype=float),
        unit=(u.deg, u.deg),
        frame="icrs",
    )
    within_radius = center.separation(coordinates).deg <= radius_deg
    matching_ids = [
        target_id for (target_id, _, _), is_within in zip(positions, within_radius, strict=False) if is_within
    ]
    if loaded_targets is not None:
        by_id = {target.id: target for target in loaded_targets}
        return [by_id[target_id] for target_id in matching_ids if target_id in by_id]
    matched = (astrometrics.targets.get(target_id) for target_id in matching_ids)
    return [target for target in matched if target is not None]


def astrometrics_catalog(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    include_stars: bool = True,
) -> list[Target | StellarObject]:
    """Query the local Astrometrics database for objects in a region.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the local Astrometrics catalog.
    ra_deg : float
        Right Ascension of the search center in degrees.
    dec_deg : float
        Declination of the search center in degrees.
    radius_deg : float
        Search radius in degrees.
    include_stars : bool
        If False, only targets are searched. The stars branch loads every
        star in the library in full and takes seconds on a large one, so
        a caller that reads the stars another way (see
        `resolution_operations.get_library_star_summaries`) skips it.

    Returns
    -------
    List[Union[Target, StellarObject]]
        List of Target and StellarObject instances within the search radius.
    """
    results: list[Target | StellarObject] = []
    center = SkyCoord(ra=ra_deg, dec=dec_deg, unit=(u.deg, u.deg), frame="icrs")

    # 1. Fetch and filter Targets. Coordinates are parsed once per
    # candidate via the canonical parse_coordinate_string
    # (RA=hourangle, Dec=degrees, unit markers stripped) then
    # matched in a single batched SkyCoord/separation call instead
    # of constructing one SkyCoord per target, which dominated
    # request latency for large catalogs. Parsing here (rather than
    # passing raw strings straight into SkyCoord) also means a
    # single malformed target can't fail the whole batch — it's
    # skipped and logged individually, same as the StellarObjects
    # branch below.
    results.extend(_targets_within_radius(sky._astrometrics, center, radius_deg))

    if not include_stars:
        return results

    # 2. Fetch and filter StellarObjects, batched the same way as
    # Targets above.
    # Only the stars near the search circle are read from the library, using
    # its sky-position index, not every star it holds.
    stars = (
        sky._astrometrics.stars.query(
            ra_deg=ra_deg, dec_deg=dec_deg, radius_deg=radius_deg, detail="objects", limit=None
        ).objects
        or []
    )
    candidate_stars: list[StellarObject] = []
    candidate_star_ra: list[Any] = []
    candidate_star_dec: list[Any] = []
    for star in stars:
        # Skip uninitialized or empty coordinates to prevent massive overhead
        ra_degrees_value = star.right_ascension
        dec_degrees_value = star.declination
        if ra_degrees_value in (None, "", "None", 0.0, 0) and dec_degrees_value in (None, "", "None", 0.0, 0):
            continue
        if ra_degrees_value in (None, "", "None") or dec_degrees_value in (None, "", "None"):
            continue
        candidate_stars.append(star)
        candidate_star_ra.append(ra_degrees_value)
        candidate_star_dec.append(dec_degrees_value)

    if candidate_stars:
        try:
            # See the numpy conversion note on the target branch above --
            # this is the one that matters in practice, since a catalog can
            # hold hundreds of thousands of stars where it holds a handful
            # of targets.
            star_coordinates = SkyCoord(
                ra=np.asarray(candidate_star_ra, dtype=float),
                dec=np.asarray(candidate_star_dec, dtype=float),
                unit=(u.deg, u.deg),
                frame="icrs",
            )
            results.extend(_filter_within_radius(candidate_stars, star_coordinates, center, radius_deg))
        except Exception as batch_error:
            logger.warning(
                "Batch coordinate parsing failed for local stellar objects (%s); "
                "falling back to per-object parsing",
                batch_error,
            )
            zipped_candidates = zip(candidate_stars, candidate_star_ra, candidate_star_dec, strict=False)
            for star, ra_degrees_value, dec_degrees_value in zipped_candidates:
                try:
                    star_coordinate = SkyCoord(
                        ra_degrees_value, dec_degrees_value, unit=(u.deg, u.deg), frame="icrs"
                    )
                    if center.separation(star_coordinate).deg <= radius_deg:
                        results.append(star)
                except Exception as parse_error:
                    logger.warning("Failed to parse coordinates for local star %s: %s", star.id, parse_error)

    return results


def global_catalog(sky, ra_deg: float, dec_deg: float, radius_deg: float) -> list[Target | StellarObject]:  # ruff: ignore[missing-type-function-argument]
    """Query the online SIMBAD catalog for objects in a region.

    Handles network connection issues and timeouts gracefully by logging
    a warning and returning an empty list.

    Parameters
    ----------
    sky : Sky
        The Sky instance (unused directly; kept for call-signature
        consistency).
    ra_deg : float
        Right Ascension of the search center in degrees.
    dec_deg : float
        Declination of the search center in degrees.
    radius_deg : float
        Search radius in degrees.

    Returns
    -------
    List[Union[Target, StellarObject]]
        List of mapped Target and StellarObject instances from SIMBAD.
    """
    results: list[Target | StellarObject] = []

    try:
        from astroquery.simbad import Simbad
    except ImportError:
        logger.error("astroquery is not installed. Cannot query global catalog.")
        return results

    custom_simbad = Simbad()
    custom_simbad.TIMEOUT = 10
    # No V-magnitude column: requesting it hides objects without one,
    # such as clusters and nebulae.
    custom_simbad.add_votable_fields("otype", "sp")

    center = SkyCoord(ra=ra_deg, dec=dec_deg, unit=(u.deg, u.deg), frame="icrs")

    try:
        table = custom_simbad.query_region(center, radius=radius_deg * u.deg)
        if table is None or len(table) == 0:
            return results

        for row in table:
            main_id = str(read_simbad_field(row, "main_id"))
            ra_str = str(read_simbad_field(row, "ra"))
            dec_str = str(read_simbad_field(row, "dec"))
            otype = str(read_simbad_field(row, "otype", "")).upper()
            sp_type = str(read_simbad_field(row, "sp_type", ""))
            magnitude = None

            is_star = "STAR" in otype or "WD" in otype or bool(sp_type)

            try:
                ra_degrees_value, dec_degrees_value = resolve_simbad_radec(ra_str, dec_str)
            except Exception as coordinate_error:
                logger.debug(
                    "Skipping SIMBAD object %s due to invalid coordinates: %s", main_id, coordinate_error
                )
                continue

            if is_star:
                results.append(
                    StellarObject(
                        id=main_id,
                        name=main_id,
                        ra=ra_degrees_value,
                        dec=dec_degrees_value,
                        magnitude=magnitude,
                        spectralType=sp_type or "",
                    )
                )
            else:
                ra_text, dec_text = format_target_coordinates(ra_degrees_value, dec_degrees_value)
                results.append(Target(id=main_id, commonName=main_id, ra=ra_text, dec=dec_text))

    except Exception as query_error:
        logger.warning("SIMBAD online query failed or timed out (offline mode): %s", query_error)

    return results


def list_catalog_driver_metadata(sky) -> list[dict[str, Any]]:  # ruff: ignore[missing-type-function-argument]
    """Return display metadata for all registered online catalog drivers.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the catalog driver registry.

    Returns
    -------
    List[Dict[str, Any]]
        One entry per driver with keys: driver_name, display_name,
        maximum_query_radius_degrees.

    REQ: PLN-3.1
    """
    return [
        {
            "driver_name": driver.driver_name,
            "display_name": driver.display_name,
            "maximum_query_radius_degrees": driver.maximum_query_radius_degrees,
        }
        for driver in sky._catalog_driver_registry.values()
    ]


def query_online_catalogs(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_degrees: float,
    dec_degrees: float,
    radius_degrees: float,
    enabled_driver_names: list[str],
    magnitude_limit: float | None = None,
) -> list[tuple[str, StellarObject]]:
    """Query one or more registered online catalog drivers in parallel.

    Each driver runs concurrently via a thread pool. Failures in individual
    drivers are caught and logged without cancelling other in-flight queries.
    Results are deduplicated by source ID across all drivers.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the catalog driver registry.
    ra_degrees : float
        Center Right Ascension in degrees (ICRS).
    dec_degrees : float
        Center Declination in degrees (ICRS).
    radius_degrees : float
        Search radius in degrees. Clamped per-driver to each driver's
        maximum_query_radius_degrees before dispatch.
    enabled_driver_names : List[str]
        Registry keys of drivers to query (e.g. ['simbad', 'gaia']).
    magnitude_limit : float, optional
        Faintest magnitude the caller wants, passed to every driver. A driver
        that cannot use it ignores it.

    Returns
    -------
    List[Tuple[str, StellarObject]]
        Tagged (driver_name, StellarObject) pairs, deduplicated by source ID.
        Results are transient and must never be recorded to the database.

    REQ: PLN-3.1, PLN-3.2
    """
    active_drivers = [
        sky._catalog_driver_registry[name]
        for name in enabled_driver_names
        if name in sky._catalog_driver_registry
    ]
    if not active_drivers:
        return []

    tagged_results: list[tuple[str, StellarObject]] = []
    seen_ids: set = set()

    def _query_driver(driver: CatalogDriver) -> list[tuple[str, StellarObject]]:
        effective_radius = min(radius_degrees, driver.maximum_query_radius_degrees)
        try:
            objects = driver.query_region(
                ra_degrees, dec_degrees, effective_radius, magnitude_limit=magnitude_limit
            )
            return [(driver.driver_name, obj) for obj in objects]
        except Exception as driver_error:
            logger.warning(
                "Catalog driver '%s' query failed: %s",
                driver.driver_name,
                driver_error,
            )
            return []

    with ThreadPoolExecutor(max_workers=len(active_drivers)) as executor:
        futures = {executor.submit(_query_driver, driver): driver.driver_name for driver in active_drivers}
        for future in as_completed(futures):
            try:
                for driver_name, stellar_object in future.result():
                    if stellar_object.id not in seen_ids:
                        seen_ids.add(stellar_object.id)
                        tagged_results.append((driver_name, stellar_object))
            except Exception as future_error:
                logger.warning(
                    "Unexpected error collecting results from driver '%s': %s",
                    futures[future],
                    future_error,
                )

    return tagged_results
