"""Purpose: List the nebulae and galaxies in a stack's field.

Description: The masked stretch (see `masked_stretch`) protects one small
bright smooth object in the preview. By default it looks for the target
itself, and only when the target is a nebula or a galaxy. A field can hold
another object worth protecting: M 52 is an open cluster, and the Bubble
Nebula is in the same picture. This module lists such objects so the user can
choose one.

1. The stack's plate solution (its sky coordinate header) gives the sky area
   the stack covers. A stack without one cannot be listed.
2. SIMBAD is asked for the objects inside a circle around that area whose main
   type is a nebula or a galaxy (see `target_object_category`).
3. The objects that fall inside the stack's pixels are kept.
4. The list is cleaned (`clean_candidates`). SIMBAD lists the HII regions,
   clusters and knots inside a large galaxy as objects of their own, and
   they would bury the galaxy. An object is dropped when it lies inside the
   radius (half the major axis) of a larger listed object (one that fits in
   the frame), unless its own
   major axis is at least `KEEP_INSIDE_OTHER_ARCMIN`. An object with no
   common name (M, NGC, IC, Sh2, LBN, vdB, Abell, UGC or PGC, read from its
   SIMBAD identifiers) is dropped when it has no size or is smaller than
   `MINIMUM_UNNAMED_ARCMIN`. Each kept object is shown by its best common
   name, for example "NGC 7635" rather than a survey component label.
   Candidates ("?" types) and objects under `MINIMUM_LISTED_ARCMIN` are
   dropped. So are objects of a type that is not seen as an optical nebula
   (only the types in `OPTICAL_TYPES` stay: emission, reflection, planetary
   and supernova-remnant nebulae and single galaxies; dark nebulae, clouds,
   shells, X-ray and radio sources and groups of galaxies go), unnamed
   objects from infrared or X-ray surveys (`INFRARED_NAME_PATTERN`) and
   objects whose major axis is more than `MAXIMUM_FRAME_FRACTION` of the
   frame's short side. The rest are ranked by
   how much they could matter in the picture: objects with a common name
   first, then objects with a SIMBAD magnitude, brightest first (V, else B,
   else G), then by common-name catalog, then by size. Only the top
   `MAXIMUM_LISTED_OBJECTS` are listed.
5. SIMBAD's answers are kept in the library's stored catalog answers (see
   ``drivers/catalog_cache.py``), not beside the stack. The question is asked
   about a rounded circle (`footprint_circle`), so a new stack of the same
   field reuses the stored answer. The next call reads it without asking
   SIMBAD until it is older than the SIMBAD lifetime.

SIMBAD being unreachable is a status, not an error: stored answers are used
when there are any; otherwise the answer has no objects and a message that
says so.
"""

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.catalog_cache import catalog_use_scope
from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.processing_results import BrightObjectChoice, BrightObjectChoices
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.shared.simbad_object_types import quote_adql, without_name_prefix
from astrometricslib.pipelines.stacking.post_processing.stack_image import pixel_scale_arcsec
from astrometricslib.utilities.table_cells import optional_float
from astrometricslib.utilities.target_object_category import (
    GALAXY_CODES,
    NEBULA_CODES,
    target_object_category,
)

logger = logging.getLogger(__name__)

MESSAGE_NO_STACK = "The target has no stack yet."
MESSAGE_NO_SOLUTION = "Run Astrometry first to list objects in the frame"
MESSAGE_SIMBAD_UNREACHABLE = "SIMBAD could not be reached, so the objects in the frame are not listed."
MESSAGE_NONE_FOUND = "SIMBAD lists no nebula or galaxy in this frame."

# An object inside a larger listed object stays in the list only when its own
# major axis is at least this many arcminutes (a real neighbor seen in front
# of the big one, not an HII region of it). Design choice: HII regions and
# knots inside a galaxy are well under this; a separate nebula is far larger.
KEEP_INSIDE_OTHER_ARCMIN = 3.0

# An object with no common name stays in the list only when its major axis is
# at least this many arcminutes. Design choice: a smaller unnamed object is a
# survey component too small to protect in a preview.
MINIMUM_UNNAMED_ARCMIN = 1.0

# The only SIMBAD types listed: nebulae seen as bright optical objects (HII
# regions, reflection, emission-line, planetary and general bright nebulae,
# supernova remnants) and single galaxies. Dark nebulae, molecular clouds,
# cores, globules, shells, bubbles, neutral hydrogen, X-ray and radio sources
# and groups or clusters of galaxies are left out. Design choice from the
# SIMBAD type definitions.
OPTICAL_TYPES = frozenset({"HII", "RNe", "PN", "SNR", "EmO", "GNe", "BNe"}) | (
    GALAXY_CODES - {"GrG", "CGG", "ClG", "SCG", "SCl", "Gr?", "ClG?"}
)

# An unnamed object whose SIMBAD main name comes from an infrared survey
# (WISE and its HII-region catalog [ABB2014]) is dropped: it is not a separate
# visible object, and the visible nebula has its own common-name entry.
INFRARED_NAME_PATTERN = re.compile(r"WISE|\[ABB2014\]|^RX J|^MCXC|^1RXS|^XMMU|^GAL ")

# An object whose major axis is more than this fraction of the frame's short
# side is dropped, and is not used as a container for other objects. Such an
# object fills the frame, so there is no sky left around it to protect it
# from, and it is not a small bright object to enhance. Design choice: 1.0
# keeps a target that just fills the frame (a galaxy such as M 31) while
# dropping structures several frames wide.
MAXIMUM_FRAME_FRACTION = 1.0

# The most candidates whose identifiers are asked for (nearest the center
# first), to keep the second SIMBAD question short. Design choice.
MAXIMUM_IDENTIFIER_LOOKUPS = 300

# Catalog names that count as common, best first. Pattern: the name with its
# spaces tidied, as the identifier table writes it (for example "NGC  7635").
_COMMON_NAME_PATTERNS = (
    re.compile(r"^M \d+$"),
    re.compile(r"^NGC \d+[A-Z]?$"),
    re.compile(r"^IC \d+[A-Z]?$"),
    re.compile(r"^Sh 2-\d+$"),
    re.compile(r"^LBN \d+$"),
    re.compile(r"^vdB \d+$"),
    re.compile(r"^Abell \d+$"),
    re.compile(r"^UGC \d+$"),
    re.compile(r"^PGC \d+$"),
    re.compile(r"^Ced \d+[A-Z]?$"),
    re.compile(r"^RCW \d+$"),
)

# An object with a known major axis under this many arcminutes is dropped: it
# is too small to protect in a preview. Design choice.
MINIMUM_LISTED_ARCMIN = 1.0

# The most objects the list holds. A big galaxy lists dozens of nebulae and
# remnants; a short menu of the objects that matter is easier to choose from.
# Design choice (the user asked for the top 5).
MAXIMUM_LISTED_OBJECTS = 5


def _positive(value: object) -> float | None:
    """Read one table cell as a positive number.

    Parameters
    ----------
    value : `object`
        A table cell, possibly masked.

    Returns
    -------
    number : `float` or `None`
        The value, or `None` when it is masked, not a number or not positive
        (see `table_cells.optional_float`).
    """
    number = optional_float(value)
    return number if number is not None and number > 0.0 else None


def _text(value: object) -> str:
    """Read one table cell as text.

    Parameters
    ----------
    value : `object`
        A table cell, possibly bytes.

    Returns
    -------
    text : `str`
        The text with the outer spaces removed; empty when masked.
    """
    if getattr(value, "mask", False):
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip()


def _magnitude(row: object, column_names: list[str]) -> float | None:
    """Read an object's brightness from a table row.

    Parameters
    ----------
    row : `astropy.table.Row`
        One row of SIMBAD's answer.
    column_names : `list` [`str`]
        The table's column names.

    Returns
    -------
    magnitude : `float` or `None`
        The V magnitude, else B, else G; `None` when the row has none.
    """
    for name in ("mag_v", "mag_b", "mag_g"):
        if name in column_names:
            magnitude = optional_float(row[name])  # type: ignore[index]
            if magnitude is not None:
                return magnitude
    return None


@dataclass
class _Candidate:
    """One object SIMBAD listed inside the stack's pixels, before cleaning."""

    # Distance from the frame's center, in pixels.
    distance: float
    # SIMBAD's object number, or None when the answer had none.
    object_id: int | None
    # The object as listed, named by SIMBAD's main identifier.
    choice: BrightObjectChoice
    # SIMBAD's V magnitude, else B, else G; None when it lists none.
    magnitude: float | None = None


def _integer(value: object) -> int | None:
    """Read one table cell as a whole number.

    Parameters
    ----------
    value : `object`
        A table cell, possibly masked.

    Returns
    -------
    number : `int` or `None`
        The value, or `None` when it is masked or not a whole number.
    """
    if getattr(value, "mask", False):
        return None
    try:
        return int(value)  # type: ignore[call-overload]
    except TypeError, ValueError:
        return None


def common_name_rank(identifier: str) -> int | None:
    """Rank an identifier as a common catalog name.

    Parameters
    ----------
    identifier : `str`
        One of an object's SIMBAD identifiers, for example ``"NGC  7635"``.

    Returns
    -------
    rank : `int` or `None`
        0 for a Messier name, then NGC, IC, Sh2, LBN, vdB, Abell, UGC and PGC;
        `None` when the identifier is none of these.
    """
    tidy = without_name_prefix(identifier)
    for rank, pattern in enumerate(_COMMON_NAME_PATTERNS):
        if pattern.match(tidy):
            return rank
    return None


def _best_common_names(simbad: SimbadDriver, candidates: list[_Candidate]) -> dict[int, str]:
    """Pick a common name for each of the nearest candidates.

    A failed question is logged and gives no names: the list is then shown by
    SIMBAD's main names.

    Parameters
    ----------
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    candidates : `list` [`_Candidate`]
        The candidates, nearest the center first.

    Returns
    -------
    names : `dict` [`int`, `str`]
        The best common name of each object, by SIMBAD's object number. An
        object with no common name is absent.
    """
    numbers = [c.object_id for c in candidates[:MAXIMUM_IDENTIFIER_LOOKUPS] if c.object_id is not None]
    if not numbers:
        return {}
    query = f"SELECT oidref, id FROM ident WHERE oidref IN ({', '.join(str(n) for n in numbers)})"  # ruff: ignore[hardcoded-sql-expression] -- ADQL, integers only
    try:
        table = simbad.query_tap(query)
    except ExternalServiceError as error:
        logger.info("Common names of the objects in the frame not read: %s", error)
        return {}
    best: dict[int, tuple[int, str]] = {}
    for row in table if table is not None else []:
        number, identifier = _integer(row["oidref"]), _text(row["id"])
        rank = common_name_rank(identifier) if number is not None else None
        if number is None or rank is None:
            continue
        if number not in best or rank < best[number][0]:
            best[number] = (rank, without_name_prefix(identifier))
    return {number: name for number, (_, name) in best.items()}


def clean_candidates(
    candidates: list[_Candidate],
    common_names: dict[int, str],
    frame_short_side_arcmin: float | None = None,
) -> list[BrightObjectChoice]:
    """Drop sub-components and unnamed small objects, and name the rest.

    An object is dropped when it lies inside a larger listed object (its
    separation from that object's position is at most half that object's
    major axis) and its own major axis is under `KEEP_INSIDE_OTHER_ARCMIN`.
    An object with no common name is dropped when it has no size or is under
    `MINIMUM_UNNAMED_ARCMIN`. Candidates ("?" types) and objects under
    `MINIMUM_LISTED_ARCMIN` are dropped. The rest are ranked (magnitude
    first, brightest first; then major axis, largest first; then common-name
    catalog) and the top `MAXIMUM_LISTED_OBJECTS` are returned. Objects of a
    type outside `OPTICAL_TYPES`, unnamed infrared-survey objects and objects
    larger than `MAXIMUM_FRAME_FRACTION` of the frame are dropped first, and
    the large ones are not containers.

    Parameters
    ----------
    candidates : `list` [`_Candidate`]
        The candidates, nearest the center first.
    common_names : `dict` [`int`, `str`]
        The best common name by SIMBAD's object number.
    frame_short_side_arcmin : `float`, optional
        The frame's short side in arcminutes; `None` applies no size limit.

    Returns
    -------
    objects : `list` [`BrightObjectChoice`]
        The kept objects, best first, shown by common name.
    """
    limit = None if frame_short_side_arcmin is None else MAXIMUM_FRAME_FRACTION * frame_short_side_arcmin
    # Objects over the limit still count as containers of unnamed objects: an
    # unnamed region inside a huge galaxy is part of it, not a separate object.
    all_candidates = [c for c in candidates if c.choice.object_type in OPTICAL_TYPES]
    candidates = [
        c
        for c in all_candidates
        if limit is None or c.choice.major_axis_arcmin is None or c.choice.major_axis_arcmin <= limit
    ]
    kept: list[tuple[tuple[bool, bool, float, int, float], BrightObjectChoice]] = []
    for candidate in candidates:
        choice = candidate.choice
        axis = choice.major_axis_arcmin
        common_name = common_names.get(candidate.object_id) if candidate.object_id is not None else None
        if common_name is None and common_name_rank(choice.simbad_id) is not None:
            common_name = re.sub(r"\s+", " ", choice.simbad_id).strip()
        if choice.object_type.endswith("?") or (axis is not None and axis < MINIMUM_LISTED_ARCMIN):
            continue
        if common_name is None and (
            axis is None or axis < MINIMUM_UNNAMED_ARCMIN or INFRARED_NAME_PATTERN.search(choice.simbad_id)
        ):
            continue
        if (axis is None or axis < KEEP_INSIDE_OTHER_ARCMIN) and any(
            other is not candidate
            and other.choice.major_axis_arcmin is not None
            and (axis is None or other.choice.major_axis_arcmin > axis)
            and _separation_arcmin(choice, other.choice) <= 0.5 * other.choice.major_axis_arcmin
            for other in candidates
        ):
            continue
        if common_name is None and any(
            other is not candidate
            and other.choice.major_axis_arcmin is not None
            and (axis is None or other.choice.major_axis_arcmin > axis)
            and _separation_arcmin(choice, other.choice) <= 0.5 * other.choice.major_axis_arcmin
            for other in all_candidates
        ):
            continue
        name_rank = common_name_rank(common_name) if common_name else None
        rank = (
            common_name is None,
            candidate.magnitude is None,
            candidate.magnitude if candidate.magnitude is not None else 0.0,
            len(_COMMON_NAME_PATTERNS) if name_rank is None else name_rank,
            -(axis or 0.0),
        )
        kept.append((rank, choice.model_copy(update={"simbad_id": common_name}) if common_name else choice))
    kept.sort(key=lambda entry: entry[0])
    return [choice for _, choice in kept[:MAXIMUM_LISTED_OBJECTS]]


def _separation_arcmin(first: BrightObjectChoice, second: BrightObjectChoice) -> float:
    """Give the angle between two objects.

    Parameters
    ----------
    first, second : `BrightObjectChoice`
        The two objects.

    Returns
    -------
    separation : `float`
        The angle between them in arcminutes (flat-sky approximation, which
        is exact enough for objects a few arcminutes apart).
    """
    mean_dec = math.radians(0.5 * (first.dec_deg + second.dec_deg))
    delta_ra = ((first.ra_deg - second.ra_deg + 180.0) % 360.0 - 180.0) * math.cos(mean_dec)
    return 60.0 * math.hypot(delta_ra, first.dec_deg - second.dec_deg)


def build_footprint_query(ra_deg: float, dec_deg: float, radius_deg: float) -> str:
    """Build the ADQL query for the nebulae and galaxies in a circle.

    Parameters
    ----------
    ra_deg : `float`
        Right ascension of the circle's center, in degrees.
    dec_deg : `float`
        Declination of the center, in degrees.
    radius_deg : `float`
        The circle's radius, in degrees.

    Returns
    -------
    query : `str`
        A query that returns ``main_id``, ``ra``, ``dec``, ``otype``,
        ``major_axis`` (``galdim_majaxis``, arcminutes), ``oid`` and the
        magnitudes ``mag_v``, ``mag_b`` and ``mag_g``.
    """
    codes = ", ".join(quote_adql(code) for code in sorted(GALAXY_CODES | NEBULA_CODES))
    # ADQL for a web service, not SQL for a database of ours; the numbers are
    # formatted here and the codes are quoted by `quote_adql`.
    return (
        "SELECT basic.main_id AS main_id, basic.ra AS ra, basic.dec AS dec, "  # ruff: ignore[hardcoded-sql-expression] -- ADQL, quoted
        "basic.otype AS otype, basic.galdim_majaxis AS major_axis, basic.oid AS oid, "
        'allfluxes."V" AS mag_v, allfluxes."B" AS mag_b, allfluxes."G" AS mag_g FROM basic '
        "LEFT JOIN allfluxes ON allfluxes.oidref = basic.oid "
        f"WHERE CONTAINS(POINT('ICRS', basic.ra, basic.dec), CIRCLE('ICRS', {ra_deg:.6f}, {dec_deg:.6f}, "
        f"{radius_deg:.6f})) = 1 AND basic.otype IN ({codes})"
    )


def list_bright_object_choices(
    target_id: str,
    stacked_path: str | None,
    simbad: SimbadDriver,
    *,
    use_cache: bool = True,
) -> BrightObjectChoices:
    """List the nebulae and galaxies SIMBAD places inside a stack.

    Parameters
    ----------
    target_id : `str`
        The target the stack belongs to.
    stacked_path : `str` or `None`
        The target's imaging stack, or `None` when it has none.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    use_cache : `bool`, optional
        Use SIMBAD's stored answers while they are fresh. `False` asks SIMBAD
        again (the stored answer still stands in if SIMBAD cannot be
        reached).

    Returns
    -------
    choices : `BrightObjectChoices`
        The objects, nearest the center first, or no objects and a message
        that says why (no stack, no plate solution, SIMBAD not reached,
        nothing in the frame).
    """
    if not stacked_path or not os.path.isfile(stacked_path):
        return BrightObjectChoices(target_id=target_id, message=MESSAGE_NO_STACK)
    try:
        header = read_header(stacked_path)
        columns, rows = int(header["NAXIS1"]), int(header["NAXIS2"])
    except OSError, KeyError, ValueError, TypeError:
        return BrightObjectChoices(target_id=target_id, message=MESSAGE_NO_SOLUTION)
    wcs = celestial_wcs(header)
    if wcs is None:
        return BrightObjectChoices(target_id=target_id, message=MESSAGE_NO_SOLUTION)
    center_ra, center_dec = (
        float(value) for value in wcs.pixel_to_world_values((columns - 1) / 2, (rows - 1) / 2)
    )
    # The circle reaches the farthest corner, with a 10 percent margin.
    radius_deg = 1.1 * 0.5 * math.hypot(columns, rows) * pixel_scale_arcsec(wcs) / 3600.0
    center_ra, center_dec, radius_deg = footprint_circle(center_ra, center_dec, radius_deg)
    with catalog_use_scope(refresh=not use_cache) as use_log:
        try:
            table = simbad.query_tap(build_footprint_query(center_ra, center_dec, radius_deg))
        except ExternalServiceError as error:
            logger.warning("Objects in the frame of '%s' not listed: %s", stacked_path, error)
            return BrightObjectChoices(target_id=target_id, message=MESSAGE_SIMBAD_UNREACHABLE)
        objects = _choices_from_table(table, wcs, columns, rows, simbad)
    return BrightObjectChoices(
        target_id=target_id,
        objects=objects,
        message=None if objects else MESSAGE_NONE_FOUND,
        from_cache=bool(use_log.uses) and use_log.live_calls == 0,
    )


def footprint_circle(ra_deg: float, dec_deg: float, radius_deg: float) -> tuple[float, float, float]:
    """Round a field's circle so one field asks one question.

    The center goes to the nearest 0.05 degree (3 arcminutes) and the
    radius up to the next 0.05 degree, so the rounded circle still holds
    the whole field. The objects inside the stack's own pixels are picked
    afterwards. Design choice, not a measured value.

    Parameters
    ----------
    ra_deg, dec_deg, radius_deg : `float`
        The circle that holds the stack.

    Returns
    -------
    circle : `tuple` [`float`, `float`, `float`]
        The rounded center and radius, in degrees.
    """
    step = 0.05
    rounded_ra = round(ra_deg / step) * step
    rounded_dec = round(dec_deg / step) * step
    shift = math.hypot((ra_deg - rounded_ra) * math.cos(math.radians(dec_deg)), dec_deg - rounded_dec)
    return rounded_ra % 360.0, rounded_dec, math.ceil((radius_deg + shift) / step) * step


def _choices_from_table(
    table: object, wcs: object, columns: int, rows: int, simbad: SimbadDriver
) -> list[BrightObjectChoice]:
    """Pick, clean and name the SIMBAD objects that lie in the stack.

    Parameters
    ----------
    table : `astropy.table.Table` or `None`
        SIMBAD's answer for the footprint circle.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    columns, rows : `int`
        The stack's size in pixels.
    simbad : `SimbadDriver`
        The SIMBAD driver, asked for common names.

    Returns
    -------
    objects : `list` [`BrightObjectChoice`]
        The kept objects, nearest the center first.
    """
    candidates: list[_Candidate] = []
    for row in table if table is not None else []:
        ra_deg, dec_deg = optional_float(row["ra"]), optional_float(row["dec"])
        code, name = _text(row["otype"]), _text(row["main_id"])
        category = target_object_category(code)
        if ra_deg is None or dec_deg is None or not name or category not in ("nebula", "galaxy"):
            continue
        column, line = (float(value) for value in wcs.world_to_pixel_values(ra_deg, dec_deg))
        if not (np.isfinite(column) and np.isfinite(line) and 0 <= column < columns and 0 <= line < rows):
            continue
        candidates.append(
            _Candidate(
                distance=math.hypot(column - (columns - 1) / 2, line - (rows - 1) / 2),
                object_id=_integer(row["oid"]) if "oid" in table.colnames else None,
                magnitude=_magnitude(row, table.colnames),
                choice=BrightObjectChoice(
                    simbad_id=name,
                    object_type=code,
                    category=category,
                    ra_deg=ra_deg,
                    dec_deg=dec_deg,
                    major_axis_arcmin=_positive(row["major_axis"]),
                ),
            )
        )
    candidates.sort(key=lambda candidate: (candidate.distance, candidate.choice.simbad_id))
    short_side_arcmin = min(columns, rows) * pixel_scale_arcsec(wcs) / 60.0
    return clean_candidates(candidates, _best_common_names(simbad, candidates), short_side_arcmin)


def list_target_bright_objects(
    target: Any, simbad: SimbadDriver, *, force: bool = False
) -> BrightObjectChoices:
    """List the nebulae and galaxies in a target's imaging stack.

    This is the owner of `processing.enhancement.get_bright_objects`. The
    answer carries the choice saved on the target (``current``).

    Parameters
    ----------
    target : `Target`
        The target whose imaging stack to list.
    simbad : `SimbadDriver`
        The SIMBAD driver.
    force : `bool`, optional
        Ask SIMBAD again even when an answer is stored.

    Returns
    -------
    choices : `BrightObjectChoices`
        The objects, or no objects and the reason, with ``current``.
    """
    choices = list_bright_object_choices(
        target.id, target.stacking.stacked_image, simbad, use_cache=not force
    )
    return choices.model_copy(update={"current": target.preview_bright_object})
