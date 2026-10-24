"""Giving a target the sky position of the object, not of one image of it.

A target record keeps one RA/Dec for the object. A solved image's center is
where the camera pointed, which is not always where the object is: a
spectroscopy frame is offset so the spectrum lands on the sensor, a frame may
be badly centered, and one panel of a mosaic points at a corner. Taking the
first solved frame's center as the object's position once put M 57 about 38
arcminutes from the Ring Nebula.

This module resolves the target's name in SIMBAD (through the SIMBAD driver)
and writes that position. A frame center is used only when the name cannot be
resolved, and then the target is marked `position_unverified` so the
position is not mistaken for a catalog value. The target record itself is the
cache: the lookup runs only for a target whose position is empty or
unverified.
"""

import logging
from typing import Any

from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.errors import ExternalServiceError, InvalidArgumentError
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

# What `fill_target_position` did, for the caller's log and for tests.
FILL_CATALOG = "catalog"
FILL_FRAME_CENTER = "frame_center"
FILL_NONE = "none"

_EMPTY_RA_TEXTS = ("", "0", "0.0", "0h 0m 0s")
_EMPTY_DEC_TEXTS = ("", "0", "0.0", "0° 0′ 0′′")


def format_position(coordinate: SkyCoord) -> tuple[str, str]:
    """Write a sky position as the RA and Dec text a target record keeps.

    Parameters
    ----------
    coordinate : `astropy.coordinates.SkyCoord`
        The position to write.

    Returns
    -------
    ra, dec : `str`
        RA in hours and Dec in degrees, space separated, to 0.01 second
        and 0.01 arcsecond.
    """
    ra_text = coordinate.ra.to_string(unit=u.hour, sep=" ", precision=2)
    dec_text = coordinate.dec.to_string(unit=u.deg, sep=" ", precision=2, alwayssign=True)
    return str(ra_text), str(dec_text)


def parse_target_position(target: Any) -> SkyCoord | None:
    """Read a target's stored RA/Dec as a sky position.

    Parameters
    ----------
    target : `Target`
        The target whose ``ra`` and ``dec`` text to read.

    Returns
    -------
    coordinate : `astropy.coordinates.SkyCoord` or `None`
        See `parse_position_text`.
    """
    return parse_position_text(target.ra, target.dec)


def parse_position_text(ra: Any, dec: Any) -> SkyCoord | None:
    """Read RA/Dec text as a sky position.

    Parameters
    ----------
    ra, dec : `str`
        The right ascension and declination text a target record keeps.

    Returns
    -------
    coordinate : `astropy.coordinates.SkyCoord` or `None`
        The position, or `None` when the text is empty, malformed, or the
        all-zero placeholder a new target starts with.
    """
    ra_text = str(ra or "").strip()
    dec_text = str(dec or "").strip()
    if ra_text in _EMPTY_RA_TEXTS or dec_text in _EMPTY_DEC_TEXTS:
        return None
    try:
        ra_degrees = parse_coordinate_string(ra_text, is_ra=True)
        dec_degrees = parse_coordinate_string(dec_text, is_ra=False)
    except InvalidArgumentError:
        return None
    # ruff: ignore[float-equality-comparison] -- exact check for the
    # all-zero placeholder, not a measured value.
    if ra_degrees == 0.0 and dec_degrees == 0.0:
        return None
    return SkyCoord(ra=ra_degrees * u.deg, dec=dec_degrees * u.deg, frame="icrs")


def resolve_catalog_position(object_name: str, simbad: SimbadDriver) -> SkyCoord | None:
    """Look an object's name up in SIMBAD and return its position.

    Parameters
    ----------
    object_name : `str`
        The name to resolve, such as ``"M 57"``.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.

    Returns
    -------
    coordinate : `astropy.coordinates.SkyCoord` or `None`
        The catalog position, or `None` when SIMBAD does not know the name
        or could not be reached (a warning is logged for the second case).
    """
    name = object_name.replace("_", " ").strip()
    if not name:
        return None
    try:
        result = simbad.query_object(name, votable_fields=("ra", "dec"))
        if result is None or len(result) == 0:
            return None
        return SkyCoord(ra=float(result["ra"][0]) * u.deg, dec=float(result["dec"][0]) * u.deg, frame="icrs")
    except (ExternalServiceError, *DATA_ERRORS) as lookup_error:
        logger.warning("Could not resolve '%s' in SIMBAD: %s", name, lookup_error)
        return None


def resolve_target_catalog_position(target: Any, simbad: SimbadDriver) -> SkyCoord | None:
    """Resolve a target by its id, then by its common name.

    Parameters
    ----------
    target : `Target`
        The target to resolve.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.

    Returns
    -------
    coordinate : `astropy.coordinates.SkyCoord` or `None`
        The catalog position, or `None` when neither name resolves.
    """
    for name in (target.id, target.common_name):
        if name:
            coordinate = resolve_catalog_position(str(name), simbad)
            if coordinate is not None:
                return coordinate
    return None


def fill_target_position(target: Any, simbad: SimbadDriver, frame_center: SkyCoord | None = None) -> str:
    """Set a target's RA/Dec from the catalog, else from a frame center.

    Only an empty position, or one marked unverified, is changed. A
    position that is set and not marked unverified is never touched. The
    catalog answer is tried first. The frame center is the fallback, and
    it marks the target `position_unverified`.

    Parameters
    ----------
    target : `Target`
        The target to update.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    frame_center : `astropy.coordinates.SkyCoord`, optional
        The center of a solved image of the target, used only when the
        name cannot be resolved.

    Returns
    -------
    action : `str`
        `FILL_CATALOG`, `FILL_FRAME_CENTER` or `FILL_NONE`.
    """
    had_position = parse_target_position(target) is not None
    if had_position and not target.position_unverified:
        return FILL_NONE

    catalog_position = resolve_target_catalog_position(target, simbad)
    if catalog_position is not None:
        old_ra, old_dec = target.ra, target.dec
        target.ra, target.dec = format_position(catalog_position)
        target.position_unverified = False
        if had_position:
            logger.warning(
                "Replaced the unverified position of target %s (%s, %s) with its catalog position (%s, %s).",
                target.id,
                old_ra,
                old_dec,
                target.ra,
                target.dec,
            )
        else:
            logger.info("Set target %s to its catalog position: %s, %s", target.id, target.ra, target.dec)
        return FILL_CATALOG

    if had_position or frame_center is None:
        return FILL_NONE
    target.ra, target.dec = format_position(frame_center)
    target.position_unverified = True
    logger.warning(
        "Target %s could not be resolved in SIMBAD. Its position (%s, %s) is the center of a solved "
        "frame, not the object, and is marked unverified.",
        target.id,
        target.ra,
        target.dec,
    )
    return FILL_FRAME_CENTER
