"""Purpose: Gather the stars and targets the Planetarium draws in a sky region.

Description: `ObservationPlanning.get_sources` and
`ObservationPlanning.get_online_catalog_sources` call these functions. They
turn the library's targets, the library's own stars, and the stars found by
online catalogs into `SkySource` records.

The rules applied here:

* A target is drawn with its stacked image. A target with no stack uses its
  longest LIGHT frame instead (or its longest frame of any kind when it has
  no LIGHT frames), so the map still has a picture to show.
* A target or star whose position cannot be read, or whose right ascension
  or declination is exactly zero (never set), is left out.
* A star with a real catalog magnitude fainter than ``limiting_magnitude`` is
  left out. A star with no catalog magnitude (missing, zero or instrumental)
  is kept only when ``include_stars_without_catalog_magnitude`` is `True`.
  Targets are never filtered by magnitude.

The library's own stars are read as indexed summary rows, never as full
records, because loading every star in a wide region in full takes seconds on
a large library.
"""

import logging
from typing import Any

from astrometricslib import InvalidArgumentError, StellarObject, Target, parse_coordinate_string
from wayfindinglib.models.planning.sky_source import SkySource

logger = logging.getLogger(__name__)

__all__ = ["collect_sky_sources", "online_catalog_sources", "target_source"]


def _frame_seconds(frame: Any) -> float:
    """Read a frame's exposure time as a number.

    Returns
    -------
    float
        The exposure in seconds, or 0 when it is missing or not a number.
    """
    try:
        return float(getattr(frame, "exposure", 0) or 0.0)
    except ValueError, TypeError:
        return 0.0


def _display_image(target: Target) -> str | None:
    """Choose the image the map shows for a target.

    Returns
    -------
    path : `str` or `None`
        The stacked image, else the longest LIGHT frame (or the longest
        frame of any kind when there are no LIGHT frames), else `None`.
    """
    if target.stacking.stacked_image:
        return target.stacking.stacked_image
    if not target.frames:
        return None
    light_frames = [frame for frame in target.frames if frame.role == "LIGHT"]
    longest = max(light_frames or target.frames, key=_frame_seconds)
    return longest.path or None


def target_source(target: Target, is_global: bool = False) -> SkySource | None:
    """Describe a target as a sky source.

    Parameters
    ----------
    target : `Target`
        The target.
    is_global : `bool`, optional
        Whether it came from an online catalog rather than the library.

    Returns
    -------
    source : `SkySource` or `None`
        The target, or `None` when its coordinates cannot be read or are
        not set.
    """
    if not target.ra or not target.dec:
        return None
    try:
        ra_deg = parse_coordinate_string(str(target.ra), is_ra=True)
        dec_deg = parse_coordinate_string(str(target.dec), is_ra=False)
    except (InvalidArgumentError, ValueError, TypeError) as error:
        logger.warning("Failed to parse coordinates for target '%s': %s", target.id, error)
        return None
    stacked_image = _display_image(target)
    name = target.common_name or target.id
    return SkySource(
        id=target.id,
        ra=ra_deg,
        dec=dec_deg,
        name=name,
        common_name=name,
        has_spectra=bool(target.spectral_stacking.stacked_image),
        has_photometry=bool(stacked_image or target.stacking.processed_image),
        type="target",
        is_global=is_global,
        stacked_image=stacked_image,
        field_of_view=target.field_of_view,
    )


def _keeps_star(
    magnitude: float | None,
    known_magnitude: bool,
    limiting_magnitude: float | None,
    include_stars_without_catalog_magnitude: bool,
) -> bool:
    """Apply the magnitude limits to one star.

    Returns
    -------
    bool
        `True` when the star should be drawn.
    """
    if known_magnitude:
        return limiting_magnitude is None or magnitude <= limiting_magnitude
    return include_stars_without_catalog_magnitude


def _library_star_sources(
    sky: Any,
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    limiting_magnitude: float | None,
    include_stars_without_catalog_magnitude: bool,
) -> list[SkySource]:
    """Read the library's own stars in a region as sky sources.

    Returns
    -------
    sources : `list` [`SkySource`]
        The stars that pass the magnitude limits. Single-frame photometry
        detections are never included.
    """
    only_catalog_magnitudes = not include_stars_without_catalog_magnitude
    rows = (
        sky._astrometrics.stars.query(
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            radius_deg=radius_deg,
            # Without uncataloged stars, the database can skip every star that
            # is too faint or has no catalog magnitude.
            magnitude_max=limiting_magnitude if only_catalog_magnitudes else None,
            has_catalog_magnitude=True if only_catalog_magnitudes else None,
            limit=None,
        ).stars
        or []
    )
    sources = []
    for row in rows:
        # A star with either coordinate exactly zero has no position saved
        # yet, so there is nowhere to draw it.
        if not row["ra"] or not row["dec"]:
            continue
        if not _keeps_star(
            row["magnitude"],
            row["hasCatalogMagnitude"],
            limiting_magnitude,
            include_stars_without_catalog_magnitude,
        ):
            continue
        name = row["name"] or row["id"]
        sources.append(
            SkySource(
                id=row["id"],
                ra=float(row["ra"]),
                dec=float(row["dec"]),
                name=name,
                common_name=name,
                spectral_type=row["spectralType"],
                magnitude=row["magnitude"],
                has_spectra=row["hasSpectra"],
                has_photometry=row["hasPhotometry"],
                type="star",
            )
        )
    return sources


def collect_sky_sources(
    sky: Any,
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    include_stars: bool,
    include_online: bool,
    limiting_magnitude: float | None,
    include_stars_without_catalog_magnitude: bool,
) -> list[SkySource]:
    """Gather the targets, and optionally stars, inside a sky region.

    Parameters
    ----------
    sky : `wayfindinglib.sky.Sky`
        The sky engine, which holds the `Astrometrics` handle.
    ra_deg, dec_deg : `float`
        The region's center, in degrees.
    radius_deg : `float`
        The region's radius, in degrees.
    include_stars : `bool`
        Add the library's own stars.
    include_online : `bool`
        Add SIMBAD's objects in the region that the library does not hold.
    limiting_magnitude : `float` or `None`
        Faintest catalog magnitude to keep. `None` keeps every star.
    include_stars_without_catalog_magnitude : `bool`
        Keep stars that have no real catalog magnitude.

    Returns
    -------
    sources : `list` [`SkySource`]
        Library targets first, then library stars, then online objects
        (marked ``is_global``).
    """
    from wayfindinglib.tasks.planning_tasks import resolution_operations

    found = resolution_operations.get_sources(
        sky, ra_deg, dec_deg, radius_deg, include_catalog=include_online, include_stars=False
    )
    local_target_ids = {target.id for target in sky._astrometrics.targets.list()} if include_online else set()

    targets: list[SkySource] = []
    online: list[Any] = []
    for item in found:
        if isinstance(item, Target) and (not include_online or item.id in local_target_ids):
            source = target_source(item)
            if source is not None:
                targets.append(source)
        else:
            online.append(item)

    stars = (
        _library_star_sources(
            sky, ra_deg, dec_deg, radius_deg, limiting_magnitude, include_stars_without_catalog_magnitude
        )
        if include_stars
        else []
    )

    known_ids = {source.id for source in targets} | {source.id for source in stars}
    online_sources: list[SkySource] = []
    for item in online:
        if item.id in known_ids:
            continue
        if isinstance(item, Target):
            source = target_source(item, is_global=True)
        else:
            source = _online_star_source(item, None)
            if source is not None and not _keeps_star(
                item.magnitude,
                item.has_catalog_magnitude,
                limiting_magnitude,
                include_stars_without_catalog_magnitude,
            ):
                source = None
        if source is not None:
            online_sources.append(source)
    return [*targets, *stars, *online_sources]


def _online_star_source(star: StellarObject, catalog_source: str | None) -> SkySource | None:
    """Describe a star from an online catalog as a sky source.

    Returns
    -------
    source : `SkySource` or `None`
        The star, marked global, or `None` when it has no position.
    """
    if not star.right_ascension or not star.declination:
        return None
    try:
        ra_deg = float(star.right_ascension)
        dec_deg = float(star.declination)
    except ValueError, TypeError:
        logger.warning("Online catalog star %s has an unreadable position", star.id)
        return None
    name = star.name or star.id
    return SkySource(
        id=star.id,
        ra=ra_deg,
        dec=dec_deg,
        name=name,
        common_name=name,
        spectral_type=star.spectral_type,
        magnitude=star.magnitude if isinstance(star.magnitude, int | float) else None,
        type="star",
        is_global=True,
        catalog_source=catalog_source,
    )


def online_catalog_sources(
    sky: Any,
    ra_deg: float,
    dec_deg: float,
    radius_deg: float,
    enabled_driver_names: list[str],
    magnitude_limit: float | None,
) -> list[SkySource]:
    """Ask the enabled online catalog drivers for the stars in a region.

    Parameters
    ----------
    sky : `wayfindinglib.sky.Sky`
        The sky engine, which holds the catalog drivers.
    ra_deg, dec_deg : `float`
        The region's center, in degrees.
    radius_deg : `float`
        The region's radius, in degrees.
    enabled_driver_names : `list` [`str`]
        The drivers to ask, such as ``["deep_stars"]``.
    magnitude_limit : `float` or `None`
        Faintest magnitude wanted. Drivers that can use it fetch fewer
        stars.

    Returns
    -------
    sources : `list` [`SkySource`]
        One global source per star found, with ``catalog_source`` naming
        the driver. Nothing is saved to the library.
    """
    from wayfindinglib.tasks.planning_tasks import resolution_operations

    tagged = resolution_operations.get_online_catalog_sources(
        sky, ra_deg, dec_deg, radius_deg, enabled_driver_names, magnitude_limit
    )
    sources = []
    for driver_name, star in tagged:
        source = _online_star_source(star, driver_name)
        if source is not None:
            sources.append(source)
    return sources
