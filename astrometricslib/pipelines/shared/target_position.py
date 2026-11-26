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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.errors import ExternalServiceError, InvalidArgumentError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.target import TargetObjectType
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.simbad_object_types import (
    index_rows_by_name,
    name_variants,
    normalize_name,
    quote_adql,
)
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.exceptions import DATA_ERRORS, ONLINE_QUERY_ERRORS
from astrometricslib.utilities.target_classification import classify_target_name

logger = logging.getLogger(__name__)

# What `fill_target_position` did, for the caller's log and for tests.
FILL_CATALOG = "catalog"
FILL_FRAME_CENTER = "frame_center"
FILL_TYPE_ONLY = "type_only"
FILL_NONE = "none"

# Kinds of target that never get a fixed position: they move across the sky
# (planets, the Moon, comets, asteroids) or are not sky objects at all
# (calibration folders such as flats and darks).
NO_FIXED_POSITION_TYPES = (
    TargetObjectType.SOLAR_SYSTEM,
    TargetObjectType.COMET,
    TargetObjectType.CALIBRATION,
)

# The smallest disagreement, in arcminutes, that counts as a wrong position.
# The camera in use (ZWO ASI533MM Pro, 1.92 arcsec/pixel, 3008 pixels) sees a
# field 1.60 degrees (96 arcminutes) across. 15 arcminutes is about one sixth
# of that: the object is still well inside the frame, yet the framing is
# visibly off. It is also well under the 38 arcminute error that led to this
# check. Evidence: validation register entry AST-20.
POSITION_TOLERANCE_FLOOR_ARCMIN = TunedParameter(
    name="shared.position_tolerance_floor_arcmin",
    default=15.0,
    unit="arcmin",
    description="Smallest distance from the catalog position at which a target still counts as found.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-20",
)

# An extended object may be framed anywhere over its body, so the tolerance
# grows with its size: tolerance = max(floor, K x half the major axis) with
# K = 1, that is, the object's own radius. The catalog's major axis is used
# when SIMBAD gives one.
EXTENT_TOLERANCE_FACTOR = TunedParameter(
    name="shared.extent_tolerance_factor",
    default=1.0,
    unit="× half axis",
    description="Share of an object's half axis added to the position tolerance.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-20",
)

# How far outside the solved frame, in arcminutes, a catalog position may lie
# before it is distrusted (the name probably resolved to a different object).
# It equals the tolerance floor: a mosaic panel or an offset spectral frame
# can miss the object's center, but not by more than the framing error that
# the audit already tolerates.
FOOTPRINT_MARGIN_ARCMIN = resolve(POSITION_TOLERANCE_FLOOR_ARCMIN).value

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


def position_tolerance_arcmin(
    major_axis_arcmin: float | None = None,
    floor_arcmin: float | None = None,
) -> float:
    """Return how far a stored position may be from the catalog's.

    Parameters
    ----------
    major_axis_arcmin : `float`, optional
        The object's apparent major axis in arcminutes, when SIMBAD gives
        one.
    floor_arcmin : `float`, optional
        The smallest tolerance. Defaults to `POSITION_TOLERANCE_FLOOR_ARCMIN`.

    Returns
    -------
    tolerance_arcmin : `float`
        ``max(floor_arcmin, EXTENT_TOLERANCE_FACTOR x
        half the major axis)``.
    """
    if floor_arcmin is None:
        floor_arcmin = resolve(POSITION_TOLERANCE_FLOOR_ARCMIN).value
    half_axis = 0.0 if major_axis_arcmin is None else 0.5 * float(major_axis_arcmin)
    return max(floor_arcmin, resolve(EXTENT_TOLERANCE_FACTOR).value * half_axis)


@dataclass(frozen=True)
class CatalogPosition:
    """A catalog position and, when known, the object's size.

    Attributes
    ----------
    coordinate : `astropy.coordinates.SkyCoord`
        The position (ICRS, epoch J2000).
    major_axis_arcmin : `float` or `None`
        The apparent major axis in arcminutes, or `None` when SIMBAD gave
        none.
    object_type : `str` or `None`
        SIMBAD's main object type code (such as ``"G"``), or `None` when
        SIMBAD gave none.
    """

    coordinate: SkyCoord
    major_axis_arcmin: float | None = None
    object_type: str | None = None


@dataclass(frozen=True)
class NameResolution:
    """What resolving a target's names found.

    Attributes
    ----------
    position : `CatalogPosition` or `None`
        The accepted position, or `None` when no name resolved or the names
        disagreed.
    conflict : `bool`
        True when the id and the common name both resolved but to places
        farther apart than the tolerance, so neither can be trusted.
    """

    position: CatalogPosition | None
    conflict: bool = False


def _read_major_axis(result: Any) -> float | None:
    """Read the major axis in arcminutes from a SIMBAD answer, if present.

    Parameters
    ----------
    result : `astropy.table.Table`
        A SIMBAD answer.

    Returns
    -------
    major_axis_arcmin : `float` or `None`
        The value, or `None` when the column is missing, masked or not
        positive.
    """
    if "galdim_majaxis" not in result.colnames:
        return None
    value = result["galdim_majaxis"][0]
    if getattr(value, "mask", False):
        return None
    try:
        axis = float(value)
    except DATA_ERRORS:
        return None
    return axis if axis > 0.0 else None


def read_object_type(result: Any) -> str | None:
    """Read SIMBAD's main object type code from the first row of an answer.

    Parameters
    ----------
    result : `astropy.table.Table`
        A SIMBAD answer.

    Returns
    -------
    object_type : `str` or `None`
        The code (such as ``"G"``), or `None` when the column is missing,
        masked or empty.
    """
    for column in ("otype", "OTYPE", "main_type"):
        if column not in result.colnames:
            continue
        value = result[column][0]
        if getattr(value, "mask", False):
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        text = str(value).strip()
        if text:
            return text
    return None


def resolve_catalog_position(
    object_name: str, simbad: SimbadDriver, votable_fields: tuple[str, ...] = ()
) -> CatalogPosition | None:
    """Look an object's name up in SIMBAD and return its position.

    SIMBAD gives ICRS positions at epoch J2000. A double star resolves to
    its primary; a companion needs a target id of its own. astroquery caches
    successful answers for the life of the process, but a failed lookup is
    tried again on the next call, and one request can wait up to 30 seconds
    before it times out.

    Parameters
    ----------
    object_name : `str`
        The name to resolve, such as ``"M 57"``.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    votable_fields : `tuple` [`str`], optional
        Extra SIMBAD columns. The default columns already hold the position.
        The main object type (``otype``) and the major axis
        (``galdim_majaxis``) are always requested as well.

    Returns
    -------
    position : `CatalogPosition` or `None`
        The catalog position, or `None` when SIMBAD does not know the name
        or could not be reached (a warning is logged for the second case).
    """
    name = object_name.replace("_", " ").strip()
    if not name:
        return None
    try:
        # The object type and the size are always asked for: the target
        # record keeps both.
        fields = tuple(dict.fromkeys(("otype", "galdim_majaxis", *votable_fields)))
        result = simbad.query_object(name, votable_fields=fields)
        if result is None or len(result) == 0:
            return None
        coordinate = SkyCoord(
            ra=float(result["ra"][0]) * u.deg, dec=float(result["dec"][0]) * u.deg, frame="icrs"
        )
        return CatalogPosition(coordinate, _read_major_axis(result), read_object_type(result))
    except (ExternalServiceError, *ONLINE_QUERY_ERRORS, *DATA_ERRORS) as lookup_error:
        logger.warning("Could not resolve '%s' in SIMBAD: %s", name, lookup_error)
        return None


# The SIMBAD columns a one-request lookup of several names can return: the
# position, the object type and the size, which all sit in SIMBAD's ``basic``
# table. A lookup that asks for other columns goes name by name.
_ONE_REQUEST_FIELDS = frozenset({"otype", "galdim_majaxis", "ra", "dec"})


def resolve_catalog_positions_in_one_request(
    names: Sequence[str], simbad: SimbadDriver
) -> dict[str, CatalogPosition]:
    """Look several names up in SIMBAD with one request.

    One question about all the names (each in the spellings SIMBAD may store
    it under) takes one request through the outside-service gateway, where a
    loop of `resolve_catalog_position` calls takes one request for each name.
    A name SIMBAD's identifier table does not hold is simply missing from the
    answer; the caller asks for it by name.

    Parameters
    ----------
    names : `list` [`str`]
        The names to look up.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.

    Returns
    -------
    positions : `dict` [`str`, `CatalogPosition`]
        The position of each name SIMBAD knows, under the name as given. The
        nearest-listed row is not chosen: the first row of the name is used,
        as `resolve_catalog_position` does.
    """
    id_list = ", ".join(quote_adql(variant) for name in names for variant in name_variants(name))
    # ADQL for a web service, not SQL for a database of ours; every name is
    # passed through `quote_adql`.
    query = (
        "SELECT ident.id AS queried_id, basic.ra AS ra, basic.dec AS dec, "  # ruff: ignore[hardcoded-sql-expression] -- ADQL, quoted
        "basic.otype AS otype, basic.galdim_majaxis AS galdim_majaxis "
        "FROM ident JOIN basic ON basic.oid = ident.oidref "
        f"WHERE ident.id IN ({id_list})"
    )
    answer = simbad.query_tap(query)
    rows = index_rows_by_name(answer)
    positions: dict[str, CatalogPosition] = {}
    for name in names:
        matches = rows.get(normalize_name(name))
        if not matches:
            continue
        single = answer[[matches[0].index]]
        coordinate = SkyCoord(
            ra=float(single["ra"][0]) * u.deg, dec=float(single["dec"][0]) * u.deg, frame="icrs"
        )
        positions[name] = CatalogPosition(coordinate, _read_major_axis(single), read_object_type(single))
    return positions


def resolve_names(
    names: Sequence[str], simbad: SimbadDriver, votable_fields: tuple[str, ...] = ()
) -> NameResolution:
    """Resolve a target's id and common name, and check that they agree.

    Every non-empty name is looked up. When two names resolve to places
    farther apart than the tolerance, one of them is the wrong object (for
    example an id such as "M 52" with the common name "Bubble Nebula"), so
    nothing is accepted and `NameResolution.conflict` is set.

    Parameters
    ----------
    names : `list` [`str`]
        The names to try, best first (the id, then the common name).
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    votable_fields : `tuple` [`str`], optional
        Extra SIMBAD columns.

    Returns
    -------
    resolution : `NameResolution`
        The first name's answer when the names agree or only one resolves.
    """
    answers: list[tuple[str, CatalogPosition]] = []
    unique_names = list(dict.fromkeys(str(n).strip() for n in names if n and str(n).strip()))
    one_request: dict[str, CatalogPosition] = {}
    if (
        len(unique_names) > 1
        and set(votable_fields) <= _ONE_REQUEST_FIELDS
        and callable(getattr(simbad, "query_tap", None))
    ):
        try:
            one_request = resolve_catalog_positions_in_one_request(unique_names, simbad)
        except (ExternalServiceError, NotImplementedError, *ONLINE_QUERY_ERRORS, *DATA_ERRORS) as batch_error:
            # Each name is then asked for by itself, below, and its own
            # failure is logged there.
            logger.debug("The one-request SIMBAD lookup of %s failed: %s", unique_names, batch_error)
    for name in unique_names:
        position = one_request.get(name) or resolve_catalog_position(name, simbad, votable_fields)
        if position is not None:
            answers.append((name, position))
    if not answers:
        return NameResolution(None)
    first_name, first = answers[0]
    for other_name, other in answers[1:]:
        separation = float(first.coordinate.separation(other.coordinate).to("arcmin").value)
        largest_axis = max(
            a for a in (first.major_axis_arcmin, other.major_axis_arcmin, 0.0) if a is not None
        )
        if separation > position_tolerance_arcmin(largest_axis):
            logger.warning(
                "The names '%s' and '%s' resolve to places %.1f arcmin apart in SIMBAD; "
                "neither is trusted as the position.",
                first_name,
                other_name,
                separation,
            )
            return NameResolution(None, conflict=True)
    return NameResolution(first)


def resolve_target_catalog_position(target: Any, simbad: SimbadDriver) -> NameResolution:
    """Resolve a target by its id and its common name.

    Parameters
    ----------
    target : `Target`
        The target to resolve.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.

    Returns
    -------
    resolution : `NameResolution`
        See `resolve_names`.
    """
    return resolve_names([target.id, target.common_name], simbad)


def has_no_fixed_position(target_id: str | None) -> bool:
    """Say whether a target of this name never gets a fixed position.

    Parameters
    ----------
    target_id : `str` or `None`
        The target's id.

    Returns
    -------
    result : `bool`
        True for planets, the Moon, the Sun, comets, asteroids and
        calibration folders.
    """
    return classify_target_name(target_id) in NO_FIXED_POSITION_TYPES


def fill_target_position(
    target: Any,
    simbad: SimbadDriver,
    frame_center: SkyCoord | None = None,
    footprint_radius_arcmin: float | None = None,
) -> str:
    """Set a target's RA/Dec from the catalog, else from a frame center.

    Only an empty position, or one marked unverified, is changed. A
    position that is set and not marked unverified is never touched. When
    such a target has no SIMBAD object type (or it is empty), the name is
    looked up once to fill the missing type, and the major axis if that is
    missing too; the position stays as it is. A target that has a type is
    not looked up again. A
    moving body or calibration target is never given a position. The
    catalog answer is tried first. The frame center is the fallback, and
    it marks the target `position_unverified`. The frame center is also used
    when the catalog position lies outside the solved frame by more than
    `FOOTPRINT_MARGIN_ARCMIN`, or when the target's id and common name
    resolve to different places: the name has then probably found the
    wrong object.

    Parameters
    ----------
    target : `Target`
        The target to update.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    frame_center : `astropy.coordinates.SkyCoord`, optional
        The center of a solved image of the target, used only when the
        name cannot be resolved or the answer is distrusted.
    footprint_radius_arcmin : `float`, optional
        Half the diagonal of the solved image in arcminutes. When given, a
        catalog position farther than this plus the margin from
        `frame_center` is distrusted.

    Returns
    -------
    action : `str`
        `FILL_CATALOG`, `FILL_FRAME_CENTER`, `FILL_TYPE_ONLY` (only the
        missing object type, and perhaps the major axis, was filled) or
        `FILL_NONE`.
    """
    if has_no_fixed_position(target.id):
        return FILL_NONE
    had_position = parse_target_position(target) is not None
    if had_position and not target.position_unverified:
        return _fill_missing_object_type(target, simbad, frame_center, footprint_radius_arcmin)

    resolution = resolve_target_catalog_position(target, simbad)
    catalog = resolution.position
    if catalog is not None and frame_center is not None and footprint_radius_arcmin is not None:
        distance = float(catalog.coordinate.separation(frame_center).to("arcmin").value)
        if distance > footprint_radius_arcmin + FOOTPRINT_MARGIN_ARCMIN:
            logger.warning(
                "The catalog position of target %s (%s) is %.1f arcmin from the center of its solved frame, "
                "outside the frame (%.1f arcmin) plus a %.0f arcmin margin; the name may have resolved to "
                "another object. Keeping the frame center (%s), marked unverified.",
                target.id,
                format_position(catalog.coordinate),
                distance,
                footprint_radius_arcmin,
                FOOTPRINT_MARGIN_ARCMIN,
                format_position(frame_center),
            )
            catalog = None
    if catalog is not None:
        old_ra, old_dec = target.ra, target.dec
        target.ra, target.dec = format_position(catalog.coordinate)
        target.position_unverified = False
        if catalog.object_type:
            target.simbad_object_type = catalog.object_type
        if catalog.major_axis_arcmin:
            target.major_axis_arcmin = catalog.major_axis_arcmin
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
        "Target %s has no trusted catalog position. Its position (%s, %s) is the center of a solved "
        "frame, not the object, and is marked unverified.",
        target.id,
        target.ra,
        target.dec,
    )
    return FILL_FRAME_CENTER


def _fill_missing_object_type(
    target: Any,
    simbad: SimbadDriver,
    frame_center: SkyCoord | None,
    footprint_radius_arcmin: float | None,
) -> str:
    """Fill only the missing SIMBAD object type of a confirmed target.

    The position is never changed. SIMBAD is not asked when the target
    already has a type. A catalog position that lies outside the solved
    frame plus the margin is distrusted, as in `fill_target_position`, and
    its type is not taken. A SIMBAD failure is logged and changes nothing.

    Parameters
    ----------
    target : `Target`
        The target to update.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    frame_center : `astropy.coordinates.SkyCoord`, optional
        The center of a solved image of the target.
    footprint_radius_arcmin : `float`, optional
        Half the diagonal of the solved image in arcminutes.

    Returns
    -------
    action : `str`
        `FILL_TYPE_ONLY` when a type was filled, else `FILL_NONE`.
    """
    if (target.simbad_object_type or "").strip():
        return FILL_NONE
    try:
        catalog = resolve_target_catalog_position(target, simbad).position
    except (ExternalServiceError, *ONLINE_QUERY_ERRORS) as lookup_error:
        logger.info("Could not look up the object type of target %s: %s", target.id, lookup_error)
        return FILL_NONE
    if catalog is None or not catalog.object_type:
        return FILL_NONE
    if frame_center is not None and footprint_radius_arcmin is not None:
        distance = float(catalog.coordinate.separation(frame_center).to("arcmin").value)
        if distance > footprint_radius_arcmin + FOOTPRINT_MARGIN_ARCMIN:
            logger.warning(
                "The catalog position of target %s (%s) is %.1f arcmin from the center of its solved frame, "
                "outside the frame (%.1f arcmin) plus a %.0f arcmin margin; the name may have resolved to "
                "another object. Not taking its object type.",
                target.id,
                format_position(catalog.coordinate),
                distance,
                footprint_radius_arcmin,
                FOOTPRINT_MARGIN_ARCMIN,
            )
            return FILL_NONE
    target.simbad_object_type = catalog.object_type
    if not target.major_axis_arcmin and catalog.major_axis_arcmin:
        target.major_axis_arcmin = catalog.major_axis_arcmin
    logger.info("Set the object type of target %s to %s from SIMBAD.", target.id, catalog.object_type)
    return FILL_TYPE_ONLY
