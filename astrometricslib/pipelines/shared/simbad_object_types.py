"""Reads the object types and names out of a row of a SIMBAD result.

Used by the astrometry pipeline when it matches a detected star to SIMBAD,
and by the script that fills in the types of stars saved before they were
recorded, so both read a result the same way. It is also the one owner of
SIMBAD's ``NAME`` prefix, which SIMBAD puts in front of a common name
(``NAME Vega``): `without_name_prefix` removes it for display and
`normalize_name` for comparing names.
"""

import re
from typing import Any

from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib.models.known_variability import KnownVariability, classify_simbad_object_types
from astrometricslib.models.target import Target

# The column the all-types field arrives in, under the names it has had.
ALL_TYPES_COLUMNS = ("alltypes.otypes", "ALLTYPES.OTYPES", "otypes")

# The votable fields to ask SIMBAD for so a row carries what this module reads.
OBJECT_TYPE_VOTABLE_FIELDS = ("otype", "alltypes")

# The columns the star identifier asks for in its field-wide SIMBAD question.
# "ids" carries the common names used to label a star. "alltypes" lists every
# object type, not just the main one: the main type of the eclipsing binary
# Algol is "SB*". The stored answers are keyed on these columns, so every
# reader of the stored field uses this one tuple.
STAR_REGION_VOTABLE_FIELDS = (
    "flux(V)",
    "flux(B)",
    "sp_type",
    "ids",
    "ra(d)",
    "dec(d)",
    "otype",
    "alltypes",
)


def read_simbad_object_types(match: Any) -> str:
    """Read every object type SIMBAD lists for a matched star.

    The all-types column is used when the result has one. A result with only
    the main type cannot show that a star is *not* listed as variable (the
    main type of the eclipsing binary Algol is "SB*"), so then only a main
    type that is itself a variable type is kept, and anything else is
    treated as unknown.

    Parameters
    ----------
    match : `astropy.table.Row`
        One row of a SIMBAD result.

    Returns
    -------
    object_types : `str`
        The types joined with ``|``, or an empty string when the row has no
        usable type.
    """

    def read(*columns: str) -> str | None:
        """Read the first present, unmasked column as text.

        Returns
        -------
        text : `str` or `None`
            The value, or `None` when no column is present and unmasked.
        """
        for column in columns:
            if column not in match.colnames:
                continue
            value = match[column]
            if value is None or (hasattr(value, "mask") and bool(value.mask)):
                continue
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            text = str(value).strip()
            if text:
                return text
        return None

    all_types = read(*ALL_TYPES_COLUMNS)
    if all_types is not None:
        return all_types
    main_type = read("otype", "OTYPE", "main_type")
    if main_type is not None and classify_simbad_object_types(main_type) is not KnownVariability.NOT_LISTED:
        return main_type
    return ""


def quote_adql(text: str) -> str:
    """Write text as an ADQL string literal.

    Parameters
    ----------
    text : `str`
        The text; a single quote inside it is doubled.

    Returns
    -------
    literal : `str`
        The text in single quotes.
    """
    return "'" + text.replace("'", "''") + "'"


def separation_arcsec(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Measure the distance between two positions.

    Parameters
    ----------
    first, second : `tuple` [`float`, `float`]
        Right ascension and declination in degrees.

    Returns
    -------
    separation : `float`
        The angle between them, in arcseconds.
    """
    return float(
        SkyCoord(first[0] * u.deg, first[1] * u.deg)
        .separation(SkyCoord(second[0] * u.deg, second[1] * u.deg))
        .arcsec
    )


# Stop after this many requests in a row fail, so a run does not keep asking a
# service that is down (the same limit as the star backfill).
CONSECUTIVE_FAILURE_LIMIT = 3

# Targets per SIMBAD request. Each target is asked about by up to three names,
# each in up to three spellings, so 20 targets keep a request near the
# 200-identifier size the star backfill uses.
TARGETS_PER_REQUEST = 20


# SIMBAD's mark in front of a common name, as in "NAME Vega".
SIMBAD_NAME_PREFIX = "NAME "


def without_name_prefix(identifier: str) -> str:
    """Write a SIMBAD identifier without its leading ``NAME`` mark.

    Parameters
    ----------
    identifier : `str`
        One SIMBAD identifier, such as ``"NAME Vega"`` or ``"HD  172167"``.

    Returns
    -------
    name : `str`
        The identifier with runs of spaces turned into one space and a
        leading ``NAME `` removed (``"Vega"``, ``"HD 172167"``). Only a
        leading mark is removed, so a name that holds the word elsewhere is
        kept whole.
    """
    return " ".join(str(identifier).split()).removeprefix(SIMBAD_NAME_PREFIX)


def simbad_row_identifiers(row: Any) -> tuple[str, list[str]]:
    """Read a SIMBAD row's main identifier and its list of identifiers.

    Parameters
    ----------
    row : `astropy.table.Row`
        One row of a SIMBAD answer. The main identifier is read from
        ``main_id`` (or ``MAIN_ID``, ``ID``, ``id``) and the list from
        ``ids`` (or ``IDS``), whose entries SIMBAD separates with ``|``.

    Returns
    -------
    main_id : `str`
        The main identifier, or ``""`` when the row has none.
    identifiers : `list` [`str`]
        Every identifier, without empty entries. A row with no list gives
        its main identifier alone.
    """

    def text_of(value: Any) -> str:
        """Read one cell as text, decoding bytes.

        Parameters
        ----------
        value : `Any`
            The cell.

        Returns
        -------
        text : `str`
            The text, stripped; empty for a masked cell.
        """
        if value is None or (hasattr(value, "mask") and bool(value.mask)):
            return ""
        return (value.decode("utf-8") if isinstance(value, bytes) else str(value)).strip()

    names = row.colnames
    main_column = next((name for name in ("main_id", "MAIN_ID", "ID", "id") if name in names), None)
    ids_column = next((name for name in ("ids", "IDS") if name in names), None)
    main_id = text_of(row[main_column]) if main_column else ""
    identifiers = (
        [part.strip() for part in text_of(row[ids_column]).split("|") if part.strip()] if ids_column else []
    )
    if not identifiers and main_id:
        identifiers = [main_id]
    return main_id, identifiers


def normalize_name(name: str) -> str:
    """Write a name so spelling differences do not matter.

    Parameters
    ----------
    name : `str`
        A target id, common name or SIMBAD identifier.

    Returns
    -------
    key : `str`
        Upper case, with underscores and runs of spaces turned into one space
        and SIMBAD's leading "NAME " (which marks a common name) removed.
    """
    key = re.sub(r"\s+", " ", name.replace("_", " ").replace("\u00a0", " ")).strip().upper()
    return key.removeprefix(SIMBAD_NAME_PREFIX)


def names_to_try(target: Target) -> list[str]:
    """List the names to look a target up by, best first.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    names : `list` [`str`]
        The id, the common name, and the first designation of an id that names
        two objects ("M 52" for "M 52 - Bubble Nebula"). SIMBAD resolves that
        first designation.
    """
    names = [target.id, target.common_name, target.id.split(" - ")[0]]
    return list(dict.fromkeys(name.strip() for name in names if name and name.strip()))


def name_variants(name: str) -> list[str]:
    """List the spellings of a name SIMBAD might store it under.

    Parameters
    ----------
    name : `str`
        A target id or common name.

    Returns
    -------
    variants : `list` [`str`]
        The name with single spaces; the same name with two spaces after a
        catalog letter prefix such as "NGC" (SIMBAD pads some identifiers); and
        the name with SIMBAD's "NAME " prefix, which common names such as
        "Aldebaran" carry.
    """
    clean = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    variants = [clean, f"NAME {clean}"]
    padded = re.sub(r"^([A-Za-z]+) (\d)", r"\1  \2", clean)
    if padded != clean:
        variants.append(padded)
    return variants


def index_rows_by_name(table: Any) -> dict[str, list[Any]]:
    """Index a SIMBAD answer by the normalized name that was asked about.

    Parameters
    ----------
    table : `astropy.table.Table` or `None`
        SIMBAD's answer.

    Returns
    -------
    rows : `dict` [`str`, `list` [`astropy.table.Row`]]
        Each row under ``normalize_name(queried_id)``. A name that more than
        one object carries has several rows. Empty for no answer.
    """
    rows: dict[str, list[Any]] = {}
    if table is None:
        return rows
    for row in table:
        rows.setdefault(normalize_name(str(row["queried_id"])), []).append(row)
    return rows
