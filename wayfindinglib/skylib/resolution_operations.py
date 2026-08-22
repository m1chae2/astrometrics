"""Target Resolution & Region Source Query Operations.

Resolves a named target/star against the local Astrometrics database or
SIMBAD, and retrieves objects within a sky region for wayfindinglib.sky.Sky.
"""

from astrometricslib import StellarObject, Target
from wayfindinglib.drivers.catalog.simbad_catalog_driver import resolve_simbad_radec
from wayfindinglib.exceptions import AstrometryHardwareError


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
    AstrometryHardwareError
        If target name cannot be resolved offline or online.
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
    stars = sky._astrometrics.stellar_objects
    for star in stars:
        if star.id == target_name or (star.name and star.name.lower() == target_name.lower()):
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
        raise AstrometryHardwareError(f"Cannot resolve '{target_name}': astroquery not installed.") from e

    custom_simbad = Simbad()
    custom_simbad.TIMEOUT = 5
    custom_simbad.add_votable_fields("otype", "sp", "flux(V)")

    try:
        table = custom_simbad.query_object(target_name)
        if table is not None and len(table) > 0:
            row = table[0]
            main_id = str(row["MAIN_ID"])
            ra_str = str(row["RA"])
            dec_str = str(row["DEC"])
            otype = str(row.get("OTYPE", "")).upper()
            sp_type = str(row.get("SP_TYPE", ""))

            magnitude = None
            if "FLUX_V" in row.colnames and not row["FLUX_V"].mask:
                magnitude = float(row["FLUX_V"])

            is_star = "STAR" in otype or "WD" in otype or sp_type

            try:
                ra_degrees_value, dec_degrees_value = resolve_simbad_radec(ra_str, dec_str)
            except Exception as coordinate_error:
                raise AstrometryHardwareError(
                    f"Resolved target '{target_name}' coordinates are invalid: {coordinate_error}"
                ) from coordinate_error

            if is_star:
                return StellarObject(
                    id=main_id,
                    name=main_id,
                    ra=ra_degrees_value,
                    dec=dec_degrees_value,
                    magnitude=magnitude or "",
                    spectralType=sp_type or "",
                )
            else:
                return Target(id=main_id, commonName=main_id, ra=ra_str, dec=dec_str)

    except Exception as simbad_error:
        raise AstrometryHardwareError(
            f"Network offline or timeout: Cannot resolve target '{target_name}' via SIMBAD: {simbad_error}"
        ) from simbad_error

    raise AstrometryHardwareError(
        f"Target '{target_name}' could not be resolved in local database or SIMBAD."
    )


def get_sources(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    include_catalog: bool = False,
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

    Returns
    -------
    List[Union[Target, StellarObject]]
        List of objects found in the region.
    """
    from wayfindinglib.skylib import catalog_operations

    if not include_catalog:
        sources = []
        sources.extend(sky._astrometrics.targets.list())
        sources.extend(sky._astrometrics.stellar_objects)
        return sources

    sources = catalog_operations.astrometrics_catalog(sky, ra_deg, dec_deg, radius_deg)
    if include_catalog:
        online_sources = catalog_operations.global_catalog(sky, ra_deg, dec_deg, radius_deg)
        # Avoid duplicating IDs
        local_ids = {source.id for source in sources}
        for online_source in online_sources:
            if online_source.id not in local_ids:
                sources.append(online_source)
    return sources


def get_online_catalog_sources(
    sky,  # ruff: ignore[missing-type-function-argument]
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    enabled_driver_names: list[str],
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

    Returns
    -------
    List[Tuple[str, StellarObject]]
        Tagged (driver_name, StellarObject) pairs. Never persisted.

    REQ: PLN-3.1, PLN-3.2
    """
    from wayfindinglib.skylib import catalog_operations

    return catalog_operations.query_online_catalogs(
        sky,
        ra_degrees=ra_deg,
        dec_degrees=dec_deg,
        radius_degrees=radius_deg,
        enabled_driver_names=enabled_driver_names,
    )
