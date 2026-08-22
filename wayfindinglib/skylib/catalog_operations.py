"""Catalog Query Operations.

Registers online/local catalog query drivers and queries the local
Astrometrics database and online astronomical catalogs (SIMBAD, GAIA DR3,
bundled Hipparcos bright-star extract) for wayfindinglib.sky.Sky, parsing
results into standard Target/StellarObject domain model instances.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord

from astrometricslib import StellarObject, Target, parse_coordinate_string
from wayfindinglib.drivers.catalog import (
    CatalogDriver,
    GaiaCatalogDriver,
    LocalBrightStarCatalogDriver,
    SimbadCatalogDriver,
)
from wayfindinglib.drivers.catalog.simbad_catalog_driver import resolve_simbad_radec

logger = logging.getLogger(__name__)


def build_catalog_driver_registry() -> dict[str, CatalogDriver]:
    """Construct the registry of online/local catalog query drivers.

    Returns
    -------
    Dict[str, CatalogDriver]
        Registry keyed by driver name: "simbad", "gaia", "hipparcos".
    """
    return {
        "simbad": SimbadCatalogDriver(),
        # Live TAP-backed driver, for small viewport-scoped deep queries.
        "gaia": GaiaCatalogDriver(),
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


def astrometrics_catalog(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
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
    targets = sky._astrometrics.targets.list()
    candidate_targets: list[Target] = []
    candidate_ra_deg: list[float] = []
    candidate_dec_deg: list[float] = []
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
        candidate_targets.append(target)
        candidate_ra_deg.append(ra_deg)
        candidate_dec_deg.append(dec_deg)

    if candidate_targets:
        target_coordinates = SkyCoord(
            ra=candidate_ra_deg, dec=candidate_dec_deg, unit=(u.deg, u.deg), frame="icrs"
        )
        results.extend(_filter_within_radius(candidate_targets, target_coordinates, center, radius_deg))

    # 2. Fetch and filter StellarObjects, batched the same way as
    # Targets above.
    stars = sky._astrometrics.stellar_objects
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
            star_coordinates = SkyCoord(
                ra=candidate_star_ra, dec=candidate_star_dec, unit=(u.deg, u.deg), frame="icrs"
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
    custom_simbad.add_votable_fields("otype", "sp", "flux(V)")

    center = SkyCoord(ra=ra_deg, dec=dec_deg, unit=(u.deg, u.deg), frame="icrs")

    try:
        table = custom_simbad.query_region(center, radius=radius_deg * u.deg)
        if table is None or len(table) == 0:
            return results

        for row in table:
            main_id = str(row["main_id"] if "main_id" in row.colnames else row["MAIN_ID"])
            ra_str = str(row["ra"] if "ra" in row.colnames else row["RA"])
            dec_str = str(row["dec"] if "dec" in row.colnames else row["DEC"])
            otype = str(row.get("OTYPE", "")).upper()
            sp_type = str(row.get("SP_TYPE", ""))

            magnitude = None
            if "FLUX_V" in row.colnames and not row["FLUX_V"].mask:
                try:
                    magnitude = float(row["FLUX_V"])
                except ValueError, TypeError:
                    pass

            is_star = "STAR" in otype or "WD" in otype or sp_type

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
                        magnitude=magnitude or "",
                        spectralType=sp_type or "",
                    )
                )
            else:
                results.append(Target(id=main_id, commonName=main_id, ra=ra_str, dec=dec_str))

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

    Returns
    -------
    List[Tuple[str, StellarObject]]
        Tagged (driver_name, StellarObject) pairs, deduplicated by source ID.
        Results are transient and must never be persisted to the database.

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
            objects = driver.query_region(ra_degrees, dec_degrees, effective_radius)
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
