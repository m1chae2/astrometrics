"""Looks up stars in the AAVSO Variable Star Index (VSX) and in Gaia DR3.

SIMBAD's object types say whether a star is listed as variable (see
`models/known_variability.py`). VSX and Gaia are two more places a variable
may already be listed, and VSX also lists stars checked and found constant.
Both are asked through CDS XMatch, a service that matches a whole table of
positions against a catalog in one request, so thousands of stars take a few
requests, not thousands.

The matching rules, all of them shaped by what the live services returned:

- A match must lie within `MATCH_RADIUS_ARCSEC`. At that radius a bright
  star can match a faint neighbour (Sirius matched a magnitude-8 entry 4
  arcseconds away at a wider radius), and a blended pair can match twice
  (Mira and its companion VZ Cet).
- VSX: if any entry within the radius is a variable, the star is listed
  as variable, because a variable in the aperture matters whichever star it
  is. The nearest typed variable gives the type. Entries of type ``CST`` are
  stars checked and found constant and never count as variable.
- Gaia: only the nearest source is used, and it is ignored if its G
  magnitude differs from the star's own magnitude by more than
  `MAXIMUM_MAGNITUDE_DIFFERENCE`, which catches a faint neighbour matched in
  place of a bright star that Gaia does not list. The brightest stars are
  not in Gaia DR3 at all (Algol, Vega and Sirius have no entry), so no
  entry is stored as `NO_GAIA_MATCH`, which says nothing about variability.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

import astropy.units as u
from astropy.table import Table

from astrometricslib.models.known_variability import (
    NO_GAIA_MATCH,
    NOT_IN_VSX,
    VSX_CONSTANT_TYPE,
    VSX_LISTED_WITHOUT_TYPE,
    VSX_UNCERTAIN_CONSTANT_TYPE,
)

VSX_CATALOG = "vizier:B/vsx/vsx"
GAIA_CATALOG = "vizier:I/355/gaiadr3"

# How far a catalog entry may lie from the star, in arcseconds.
MATCH_RADIUS_ARCSEC = 3.0

# The largest allowed difference between a star's own magnitude and the G
# magnitude of the Gaia source matched to it. A colour term can make G
# differ from V by one magnitude or a little more for very red stars, so a
# difference past 2 magnitudes is a different object.
MAXIMUM_MAGNITUDE_DIFFERENCE = 2.0

# The VSX type text that means "no type given".
_UNTYPED_VSX_TYPES = ("", "*", "--")

# A callable shaped like `astroquery.xmatch.XMatch.query`.
XMatchQuery = Callable[..., Table | None]


def build_position_table(items: Iterable[tuple[str, float, float]]) -> Table:
    """Build the table of positions to match.

    Parameters
    ----------
    items : `Iterable` [`tuple` [`str`, `float`, `float`]]
        Each star's id, right ascension and declination in degrees.

    Returns
    -------
    table : `astropy.table.Table`
        Columns ``id``, ``ra`` and ``dec``.
    """
    rows = list(items)
    return Table({
        "id": [row[0] for row in rows],
        "ra": [row[1] for row in rows],
        "dec": [row[2] for row in rows],
    })


def run_crossmatch(xmatch_query: XMatchQuery, positions: Table, catalog: str) -> Table | None:
    """Match a table of positions against a catalog.

    Parameters
    ----------
    xmatch_query : `Callable`
        `astroquery.xmatch.XMatch.query`, or a stand-in.
    positions : `astropy.table.Table`
        From `build_position_table`.
    catalog : `str`
        `VSX_CATALOG` or `GAIA_CATALOG`.

    Returns
    -------
    matches : `astropy.table.Table` or `None`
        One row per match, or `None` for an empty table of positions.
    """
    if len(positions) == 0:
        return None
    return xmatch_query(
        cat1=positions,
        cat2=catalog,
        max_distance=MATCH_RADIUS_ARCSEC * u.arcsec,
        colRA1="ra",
        colDec1="dec",
    )


def _text(value: Any) -> str:
    """Read a table cell as stripped text, treating masked cells as empty.

    Returns
    -------
    text : `str`
        The cell's text; empty for a masked, missing or ``--`` cell.
    """
    if value is None or (hasattr(value, "mask") and bool(value.mask)):
        return ""
    text = str(value).strip()
    return "" if text == "--" else text


def vsx_types_from_matches(star_ids: Sequence[str], matches: Table | None) -> dict[str, str]:
    """Decide what VSX says about each star.

    Parameters
    ----------
    star_ids : `Sequence` [`str`]
        The ids that were matched.
    matches : `astropy.table.Table` or `None`
        The cross-match result, with ``id``, ``Type`` and ``angDist``.

    Returns
    -------
    types : `dict` [`str`, `str`]
        For each id, in order of preference: the VSX type of the nearest
        variable entry; `VSX_LISTED_WITHOUT_TYPE` if an entry has no type;
        ``CST`` (or ``CST:``) if only constant entries match; otherwise
        `NOT_IN_VSX`.
    """
    by_star: dict[str, list[tuple[float, str]]] = {}
    if matches is not None:
        for row in matches:
            by_star.setdefault(str(row["id"]), []).append((float(row["angDist"]), _text(row["Type"])))
    result = {}
    for star_id in star_ids:
        entries = sorted(by_star.get(star_id, []))
        typed_variables = [
            text
            for _distance, text in entries
            if text not in _UNTYPED_VSX_TYPES and text not in (VSX_CONSTANT_TYPE, VSX_UNCERTAIN_CONSTANT_TYPE)
        ]
        constants = [
            text for _distance, text in entries if text in (VSX_CONSTANT_TYPE, VSX_UNCERTAIN_CONSTANT_TYPE)
        ]
        if typed_variables:
            result[star_id] = typed_variables[0]
        elif any(text in _UNTYPED_VSX_TYPES for _distance, text in entries):
            result[star_id] = VSX_LISTED_WITHOUT_TYPE
        elif constants:
            result[star_id] = (
                VSX_CONSTANT_TYPE if VSX_CONSTANT_TYPE in constants else VSX_UNCERTAIN_CONSTANT_TYPE
            )
        else:
            result[star_id] = NOT_IN_VSX
    return result


def gaia_flags_from_matches(
    star_ids: Sequence[str], matches: Table | None, star_magnitudes: dict[str, float | None] | None = None
) -> dict[str, str]:
    """Decide what Gaia DR3 says about each star.

    Parameters
    ----------
    star_ids : `Sequence` [`str`]
        The ids that were matched.
    matches : `astropy.table.Table` or `None`
        The cross-match result, with ``id``, ``VarFlag``, ``Gmag`` and
        ``angDist``.
    star_magnitudes : `dict` [`str`, `float` or `None`], optional
        Each star's own magnitude, used to reject a faint neighbour matched
        in place of a bright star. A star with no magnitude is not checked.

    Returns
    -------
    flags : `dict` [`str`, `str`]
        For each id, the nearest source's ``phot_variable_flag``, or
        `NO_GAIA_MATCH` when there is no source or the nearest one is too
        different in magnitude to be the same star.
    """
    magnitudes = star_magnitudes or {}
    nearest: dict[str, tuple[float, str, float | None]] = {}
    if matches is not None:
        for row in matches:
            star_id = str(row["id"])
            distance = float(row["angDist"])
            if star_id in nearest and nearest[star_id][0] <= distance:
                continue
            g_text = _text(row["Gmag"])
            nearest[star_id] = (distance, _text(row["VarFlag"]), float(g_text) if g_text else None)
    result = {}
    for star_id in star_ids:
        found = nearest.get(star_id)
        if found is None:
            result[star_id] = NO_GAIA_MATCH
            continue
        _distance, flag, g_magnitude = found
        star_magnitude = magnitudes.get(star_id)
        if (
            star_magnitude is not None
            and g_magnitude is not None
            and abs(g_magnitude - star_magnitude) > MAXIMUM_MAGNITUDE_DIFFERENCE
        ):
            result[star_id] = NO_GAIA_MATCH
        else:
            result[star_id] = flag or NO_GAIA_MATCH
    return result
