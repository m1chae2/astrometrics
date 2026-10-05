"""Target Resolution & Region Source Query Operations.

Resolves a named target/star against the local Astrometrics database or
SIMBAD, and retrieves objects within a sky region for wayfindinglib.sky.Sky.
"""

from typing import Any

from astrometricslib import ExternalServiceError, NotFoundError, ProcessingError, StellarObject, Target
from wayfindinglib.drivers.catalog.simbad_catalog_driver import (
    format_target_coordinates,
    read_simbad_field,
    resolve_simbad_radec,
)


def resolve_target_coordinates(sky, target_name: str) -> Target | StellarObject:  # ruff: ignore[missing-type-function-argument]
    """Resolve coordinates for a target by name from local database or SIMBAD.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the local Astrometrics catalog.
    target_name : str
        The name or identifier of the target/star.

    Returns
    -------
    Union[Target, StellarObject]
        Resolved Target or StellarObject containing coordinates.

    Raises
    ------
    NotFoundError
        If the target name cannot be resolved offline or online.
    ExternalServiceError
        If SIMBAD cannot be reached, or `astroquery` is not installed.
    ProcessingError
        If SIMBAD returns coordinates that cannot be read.
    """
    # 1. Search local targets
    targets = sky._astrometrics.targets.list()
    for target in targets:
        is_name_match = target.id == target_name or (
            target.common_name and target.common_name.lower() == target_name.lower()
        )
        if is_name_match:
            # Only return if coordinates are initialized
            has_dec = target.dec != "0° 0′ 0′′" and target.dec != "0d 0m 0s" and target.dec != "0° 0′ 0″"
            if target.ra != "0h 0m 0s" or has_dec:
                return target

    # 2. Search local stellar objects
    # Only the stars that match the name are read from the library, not
    # every star in it.
    for star in sky._astrometrics.stars.find_all_by_id_or_name(target_name):
        # Only return if coordinates are initialized (nonzero)
        if (
            star.right_ascension != 0.0  # ruff: ignore[float-equality-comparison] -- exact sentinel: 0.0 means "uninitialized"
            or star.declination != 0.0  # ruff: ignore[float-equality-comparison] -- exact sentinel: 0.0 means "uninitialized"
        ):
            return star

    # 3. Fallback to SIMBAD query by name
    try:
        from astroquery.simbad import Simbad
    except ImportError as e:
        raise ExternalServiceError(f"Cannot resolve '{target_name}': astroquery not installed.") from e

    # Ask only for the object-type and spectral-type columns. Adding the V
    # magnitude here would make SIMBAD return only objects that have one,
    # which drops clusters and nebulae such as M 52.
    custom_simbad = Simbad()
    custom_simbad.TIMEOUT = 5
    custom_simbad.add_votable_fields("otype", "sp")

    try:
        table = custom_simbad.query_object(target_name)
        if table is not None and len(table) > 0:
            row = table[0]
            main_id = str(read_simbad_field(row, "main_id"))
            ra_str = str(read_simbad_field(row, "ra"))
            dec_str = str(read_simbad_field(row, "dec"))
            otype = str(read_simbad_field(row, "otype", "")).upper()
            sp_type = str(read_simbad_field(row, "sp_type", ""))

            is_star = "STAR" in otype or "WD" in otype or bool(sp_type)

            try:
                ra_degrees_value, dec_degrees_value = resolve_simbad_radec(ra_str, dec_str)
            except Exception as coordinate_error:
                raise ProcessingError(
                    f"Resolved target '{target_name}' coordinates are invalid: {coordinate_error}"
                ) from coordinate_error

            magnitude = _query_visual_magnitude(main_id) if is_star else None

            if is_star:
                return StellarObject(
                    id=main_id,
                    name=main_id,
                    ra=ra_degrees_value,
                    dec=dec_degrees_value,
                    magnitude=magnitude,
                    spectralType=sp_type or "",
                )
            else:
                ra_text, dec_text = format_target_coordinates(ra_degrees_value, dec_degrees_value)
                return Target(id=main_id, commonName=main_id, ra=ra_text, dec=dec_text)

    except Exception as simbad_error:
        raise ExternalServiceError(
            f"Network offline or timeout: Cannot resolve target '{target_name}' via SIMBAD: {simbad_error}"
        ) from simbad_error

    raise NotFoundError(f"Target '{target_name}' could not be resolved in local database or SIMBAD.")


def get_sources(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    include_catalog: bool = False,
    include_stars: bool = True,
) -> list[Target | StellarObject]:
    """Retrieve list of targets and stars in a specific sky region.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the local Astrometrics catalog.
    ra_deg : float
        Center Right Ascension in degrees.
    dec_deg : float
        Center Declination in degrees.
    radius_deg : float
        Field of view radius in degrees.
    include_catalog : bool
        If True, query includes the global SIMBAD catalog. If False,
        returns local database records only.
    include_stars : bool
        If False, the user's own stars are left out and only targets
        (plus the SIMBAD results, if asked for) come back.

    Returns
    -------
    List[Union[Target, StellarObject]]
        List of objects found in the region.
    """
    from wayfindinglib.tasks.planning_tasks import catalog_operations

    # astrometrics_catalog() already restricts local targets/stars to
    # ra_deg/dec_deg/radius_deg via a batched SkyCoord separation check
    # (see its own docstring), so it's used for both branches below --
    # there used to be a separate "not include_catalog" fast path that
    # returned sky._astrometrics.targets.list() and .stellar_objects
    # directly, unfiltered by radius_deg. That meant every viewport-scoped
    # Planetarium query (which always calls this with include_catalog=False)
    # fetched and serialized the entire local catalog instead of just
    # what's in view.
    sources = catalog_operations.astrometrics_catalog(sky, ra_deg, dec_deg, radius_deg, include_stars)
    if include_catalog:
        online_sources = catalog_operations.global_catalog(sky, ra_deg, dec_deg, radius_deg)
        # Avoid duplicating IDs
        local_ids = {source.id for source in sources}
        for online_source in online_sources:
            if online_source.id not in local_ids:
                sources.append(online_source)
    return sources


def get_library_star_summaries(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    magnitude_range: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve quick summaries of the user's own stars in a sky region.

    `get_sources` loads every library star in full and checks each one, so
    it takes seconds on a large library. This reads only the saved columns
    of the stars near the region, which takes milliseconds.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the local Astrometrics catalog.
    ra_deg : float
        Center Right Ascension in degrees.
    dec_deg : float
        Center Declination in degrees.
    radius_deg : float
        Search radius in degrees.
    magnitude_range : Tuple[float, float], optional
        Lowest and highest magnitude to keep, ends included. Stars with no
        saved magnitude are left out. Every star is kept when omitted.

    Returns
    -------
    List[Dict[str, Any]]
        One dict per star inside the region, with keys ``id``, ``name``,
        ``ra``, ``dec``, ``targetIds``, ``hasSpectra``, ``hasPhotometry``,
        ``magnitude`` and ``spectralType``.
    """
    return sky._astrometrics.stars.list_object_summaries_in_region(
        ra_deg, dec_deg, radius_deg, magnitude_range
    )


def get_online_catalog_sources(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    enabled_driver_names: list[str],
    magnitude_limit: float | None = None,
) -> list[tuple[str, StellarObject]]:
    """Query registered online catalog drivers for objects in a sky region.

    Unlike get_sources(), this method performs only online catalog queries and
    never touches the local astrometrics.db. Designed to be called from a
    separate, independently-triggered RPC endpoint so that network latency does
    not block the local source load.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the catalog driver registry.
    ra_deg : float
        Center Right Ascension in degrees (ICRS).
    dec_deg : float
        Center Declination in degrees (ICRS).
    radius_deg : float
        Search radius in degrees.
    enabled_driver_names : List[str]
        Registry keys of drivers to query (e.g. ['simbad', 'gaia']).
    magnitude_limit : float, optional
        Faintest magnitude the caller wants; drivers that can use it fetch
        fewer stars.

    Returns
    -------
    List[Tuple[str, StellarObject]]
        Tagged (driver_name, StellarObject) pairs. Never recorded.

    REQ: PLN-3.1, PLN-3.2
    """
    from wayfindinglib.tasks.planning_tasks import catalog_operations

    return catalog_operations.query_online_catalogs(
        sky,
        ra_degrees=ra_deg,
        dec_degrees=dec_deg,
        radius_degrees=radius_deg,
        enabled_driver_names=enabled_driver_names,
        magnitude_limit=magnitude_limit,
    )


def _query_visual_magnitude(object_name: str) -> float | None:
    """Look up a star's V magnitude in a separate SIMBAD query.

    Kept apart from the main lookup because requesting the V column
    there would drop every object that has no V magnitude.

    Parameters
    ----------
    object_name : `str`
        The SIMBAD main identifier of the star.

    Returns
    -------
    magnitude : `float` or `None`
        The V magnitude, or `None` if SIMBAD has none or the query fails.
    """
    try:
        from astroquery.simbad import Simbad

        magnitude_client = Simbad()
        magnitude_client.TIMEOUT = 5
        magnitude_client.add_votable_fields("V")
        table = magnitude_client.query_object(object_name)
        if table is None or len(table) == 0:
            return None
        value = read_simbad_field(table[0], "V")
        return None if value is None else float(value)
    except Exception:
        return None
