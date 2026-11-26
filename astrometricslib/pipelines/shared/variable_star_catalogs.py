"""Looks up stars in the AAVSO Variable Star Index (VSX).

SIMBAD's object types say whether a star is listed as variable (see
`models/known_variability.py`). VSX and Gaia are two more places a variable
may already be listed, and VSX also lists stars checked and found constant.
VSX and Gaia are asked through VizieR (CDS, Strasbourg, and its Harvard
mirror; the setting `[Catalogs] vizier_preferred_server` names the one
asked first).
VizieR is asked once for a whole field, the circle around a group of stars,
and the stars are matched to the rows it returns here, on this computer. One
request per field replaces one request per star. If neither VizieR server
answers, nothing is stored for the star and it stays "unverified" (see
`models/known_variability.py`).

The matching rules, all of them shaped by what the live services returned:

- A match must lie within `MATCH_RADIUS_ARCSEC`. At that radius a bright
  star can match a faint neighbour (Sirius matched a magnitude-8 entry 4
  arcseconds away at a wider radius), and a blended pair can match twice
  (Mira and its companion VZ Cet).
- VSX: if any entry within the radius is a variable, the star is listed
  as variable, because a variable in the aperture matters whichever star it
  is. The nearest typed variable gives the type. Entries of type ``CST`` are
  stars checked and found constant and never count as variable.
- Gaia: VizieR's copy of Gaia DR3's variability classification
  (``I/358/vclassre``) lists each classified variable once, with its class
  and no score. One request per field returns them, and each star is matched
  by its Gaia DR3 source number, not by position or magnitude, so a faint
  neighbour cannot be taken for the star. A listed star is stored as
  ``VARIABLE:<class>``; a star with a source number that is not listed is
  stored as `NO_GAIA_MATCH`, which says nothing about variability; a star
  with no source number (the brightest stars, such as Algol, Vega and Sirius,
  are not in Gaia DR3) is not asked. The old XMatch route (position match plus
  a magnitude check) was dropped.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.catalog_cache import (
    GAIA_VARIABILITY_ANSWER_LIFETIME_DAYS,
    VARIABLE_CATALOG_TILE_LEVEL,
    VSX_ANSWER_LIFETIME_DAYS,
    answer_for_cone,
)
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.known_variability import (
    GAIA_VARIABLE_FLAG,
    NO_GAIA_MATCH,
    NOT_IN_VSX,
    VSX_CONSTANT_TYPE,
    VSX_LISTED_WITHOUT_TYPE,
    VSX_UNCERTAIN_CONSTANT_TYPE,
)
from astrometricslib.models.tuned_parameter import (
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)

# The VizieR name of the VSX catalog.
VSX_CATALOG = "B/vsx/vsx"

# Gaia DR3's variability classification (one row per classified variable) and
# the VSX columns asked. The names are VizieR's; the functions that read the
# tables below use them.
GAIA_VARIABLE_CATALOG = "I/358/vclassre"
GAIA_VARIABLE_COLUMNS = ["Source", "Class", "RA_ICRS", "DE_ICRS"]
VSX_COLUMNS = ["Name", "RAJ2000", "DEJ2000", "Type", "max", "min", "f_min", "n_max", "n_min", "Period"]

# How long each VizieR server may take to answer one field, in seconds. A
# 1.1 degree VSX field answered in about 2 seconds on 2026-11-22; the rest is
# a margin for a slow server. A server that does not answer in time is
# treated as down and the other one is asked.
FIELD_QUERY_TIMEOUT_SECONDS = 120.0

# The most rows one field request may return. VizieR cuts an answer off
# silently at its row limit, so an answer that reaches this limit is refused
# (the field is treated as not asked) and never used as if it were complete.
# VSX holds about 250 entries per square degree on average; this is more than
# ten times that for a 1.5 degree field.
FIELD_ROW_LIMIT = 100_000

# How far from its center a field reaches, in degrees. Stars within this
# distance of a field's first star share one VizieR request; the request
# circle adds the match radius. Chosen so one request stays far below
# `FIELD_ROW_LIMIT` (M 52, 1.1 degrees: 2,543 VSX entries) while a typical
# imaged field fits in one request.
FIELD_RADIUS_DEGREES = 1.5

# How far a catalog entry may lie from the star, in arcseconds, for the
# cross-match with the variable-star catalogs and with the stored SIMBAD
# answers (`stored_variability`). It follows the plate scale: 3.0 arcseconds
# on the reference setup, scaled by the plate scale the run measured.
MATCH_RADIUS_ARCSEC = TunedParameter(
    name="shared.match_radius_arcsec",
    default=factor_for(3.0, RunBasis.PLATE_SCALE_ARCSEC_PER_PX),
    unit="× plate scale",
    description="Radius within which a star is matched to a variable star catalog entry.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.PLATE_SCALE_ARCSEC_PER_PX,
    reference_value=3.0,
    register_entry="AST-12",
)

# The VSX type text that means "no type given".
_UNTYPED_VSX_TYPES = ("", "*", "--")


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


def _sky_of(positions: Table) -> SkyCoord:
    """Read a position table as sky coordinates.

    Returns
    -------
    sky : `astropy.coordinates.SkyCoord`
        One coordinate per row.
    """
    return SkyCoord(
        np.asarray(positions["ra"], dtype=float), np.asarray(positions["dec"], dtype=float), unit="deg"
    )


def group_into_fields(
    positions: Table, field_radius_degrees: float = FIELD_RADIUS_DEGREES
) -> list[np.ndarray]:
    """Split positions into groups that each fit one VizieR field request.

    Parameters
    ----------
    positions : `astropy.table.Table`
        From `build_position_table`.
    field_radius_degrees : `float`, optional
        How far from its first star a group reaches.

    Returns
    -------
    groups : `list` [`numpy.ndarray`]
        Row numbers of `positions`, one array per group. Every row is in
        exactly one group.
    """
    if len(positions) == 0:
        return []
    sky = _sky_of(positions)
    remaining = np.arange(len(positions))
    groups = []
    while len(remaining):
        center = sky[remaining[0]]
        near = center.separation(sky[remaining]).deg <= field_radius_degrees
        groups.append(remaining[near])
        remaining = remaining[~near]
    return groups


def query_vizier_field(
    catalog: str,
    columns: list[str],
    center_ra_degrees: float,
    center_dec_degrees: float,
    radius_degrees: float,
    column_filters: dict[str, str] | None = None,
    allow_live: bool = True,
) -> Table:
    """Give a VizieR catalog's rows inside a circle, stored when possible.

    The sky is cut into tiles (`VARIABLE_CATALOG_TILE_LEVEL`). Each tile
    touching the circle is answered by the library's one catalog rule (see
    ``drivers/catalog_cache.py``): a fresh stored answer is used, otherwise
    VizieR is asked and the answer stored, and when VizieR cannot be reached an
    older stored answer is used with a warning. The rows returned cover every
    touching tile, a little more than the circle.

    Parameters
    ----------
    catalog : `str`
        `VSX_CATALOG` or `GAIA_VARIABLE_CATALOG`.
    columns : `list` [`str`]
        The columns to return (`VSX_COLUMNS`).
    center_ra_degrees, center_dec_degrees, radius_degrees : `float`
        The circle.
    column_filters : `dict` [`str`, `str`], optional
        VizieR column filters.
    allow_live : `bool`, optional
        When `False` only stored tiles are read and VizieR is not asked.

    Returns
    -------
    rows : `astropy.table.Table`
        The rows; empty when the tiles hold none.

    Notes
    -----
    `CatalogUnavailableError` (an `ExternalServiceError`) is raised when
    VizieR cannot be reached and a tile is not stored, or when a tile's
    answer reaches `FIELD_ROW_LIMIT`, so rows may be missing.
    """
    is_gaia = catalog == GAIA_VARIABLE_CATALOG
    lifetime = GAIA_VARIABILITY_ANSWER_LIFETIME_DAYS if is_gaia else VSX_ANSWER_LIFETIME_DAYS
    variant = f"vizier|{catalog}|{','.join(columns)}|{sorted((column_filters or {}).items())}"
    rows = answer_for_cone(
        "Gaia DR3 variability" if catalog == GAIA_VARIABLE_CATALOG else "VSX",
        variant,
        center_ra_degrees,
        center_dec_degrees,
        radius_degrees,
        healpix_level=VARIABLE_CATALOG_TILE_LEVEL,
        lifetime_days=lifetime,
        fetch_circle=lambda ra, dec, radius: _ask_vizier_circle(
            catalog, columns, ra, dec, radius, column_filters
        ),
        key_columns=tuple(columns),
        allow_live=allow_live,
    )
    return rows if rows is not None else Table(names=columns, dtype=[object] * len(columns))


def _ask_vizier_circle(
    catalog: str,
    columns: list[str],
    center_ra_degrees: float,
    center_dec_degrees: float,
    radius_degrees: float,
    column_filters: dict[str, str] | None = None,
) -> Table:
    """Ask VizieR live for the rows of a catalog inside a circle.

    Parameters
    ----------
    catalog : `str`
        The VizieR catalog name.
    columns : `list` [`str`]
        The columns to return.
    center_ra_degrees, center_dec_degrees, radius_degrees : `float`
        The circle.
    column_filters : `dict` [`str`, `str`], optional
        VizieR column filters.

    Returns
    -------
    rows : `astropy.table.Table`
        The rows; empty when the circle holds none.

    Raises
    ------
    ExternalServiceError
        If the answer reaches `FIELD_ROW_LIMIT`, so rows may be missing.

    Notes
    -----
    The question goes through the outside-service gateway: the preferred
    VizieR server first, then the other one, with a rate limit and a pause
    after repeated trouble. When this launch sends nothing off the computer,
    the gateway raises `OfflineModeError`, a `ConnectionError`.
    """
    results = outside_services.vizier_query_region(
        SkyCoord(center_ra_degrees, center_dec_degrees, unit="deg"),
        radius_degrees,
        catalog=catalog,
        columns=columns,
        column_filters=column_filters,
        row_limit=FIELD_ROW_LIMIT,
        timeout_seconds=FIELD_QUERY_TIMEOUT_SECONDS,
    )
    if len(results) == 0:
        return Table(names=columns, dtype=[object] * len(columns))
    rows = results[0]
    if len(rows) >= FIELD_ROW_LIMIT:
        raise ExternalServiceError(f"VizieR gave {len(rows)} rows of {catalog}, the most it is asked for.")
    return rows


def match_to_catalog_rows(
    positions: Table, catalog_rows: Table, right_ascension_column: str, declination_column: str
) -> Table | None:
    """Match each star to the catalog rows within the match radius.

    Parameters
    ----------
    positions : `astropy.table.Table`
        From `build_position_table`.
    catalog_rows : `astropy.table.Table`
        The rows from `query_vizier_field`.
    right_ascension_column, declination_column : `str`
        The catalog's position columns, in degrees.

    Returns
    -------
    matches : `astropy.table.Table` or `None`
        One row per (star, catalog row) pair within `MATCH_RADIUS_ARCSEC`,
        with the star's ``id``, ``angDist`` in arcseconds and every catalog
        column. `None` when nothing matches.
    """
    if len(positions) == 0 or len(catalog_rows) == 0:
        return None
    stars = _sky_of(positions)
    entries = SkyCoord(
        np.asarray(catalog_rows[right_ascension_column], dtype=float),
        np.asarray(catalog_rows[declination_column], dtype=float),
        unit="deg",
    )
    star_index, entry_index, separation, _ = entries.search_around_sky(
        stars, resolve(MATCH_RADIUS_ARCSEC).value * u.arcsec
    )
    if len(star_index) == 0:
        return None
    matches = Table(catalog_rows[entry_index], copy=True)
    matches["id"] = [str(positions["id"][index]) for index in star_index]
    matches["angDist"] = separation.arcsec
    return matches


def look_up_vsx(positions: Table) -> Table | None:
    """Match a group of stars to the VSX entries near them.

    Parameters
    ----------
    positions : `astropy.table.Table`
        From `build_position_table`; all stars within one field (see
        `group_into_fields`).

    Returns
    -------
    matches : `astropy.table.Table` or `None`
        The match table `vsx_types_from_matches` reads; `None` when no entry
        matches or there are no positions.
    """
    return _look_up_field(positions, VSX_CATALOG, VSX_COLUMNS, "RAJ2000", "DEJ2000", None)


def _look_up_field(
    positions: Table,
    catalog: str,
    columns: list[str],
    right_ascension_column: str,
    declination_column: str,
    column_filters: dict[str, str] | None,
) -> Table | None:
    """Download a catalog's rows around the stars and match them.

    Returns
    -------
    matches : `astropy.table.Table` or `None`
        From `match_to_catalog_rows`.
    """
    if len(positions) == 0:
        return None
    sky = _sky_of(positions)
    # The circle is centered on the first star and reaches the farthest star
    # plus the match radius.
    reach = sky[0].separation(sky).deg.max() + resolve(MATCH_RADIUS_ARCSEC).value / 3600.0
    rows = query_vizier_field(
        catalog, columns, float(sky[0].ra.deg), float(sky[0].dec.deg), float(reach), column_filters
    )
    return match_to_catalog_rows(positions, rows, right_ascension_column, declination_column)


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


@dataclass(frozen=True)
class VsxEntry:
    """What VSX lists about one variable star's brightness range and period.

    Parameters
    ----------
    variability_type : `str`
        The VSX type text of the entry (``"EA"``, ``"DCEP"``).
    bright_magnitude : `float` or `None`
        The magnitude at maximum light (the smaller number), or `None` when
        the entry gives none or gives its range as an amplitude.
    faint_magnitude : `float` or `None`
        The magnitude at minimum light (the larger number), or `None` when
        the entry gives none or gives its range as an amplitude.
    amplitude_mag : `float` or `None`
        The size of the range in magnitudes: the faint magnitude minus the
        bright one, or the amplitude itself when VSX lists the range that
        way (a ``(`` in its minimum flag column). `None` when neither is given.
    period_days : `float` or `None`
        The period, in days, or `None` when VSX gives none.
    band : `str`
        The photometric band VSX names for the maximum, such as ``"V"`` or
        ``"p"`` (photographic). Empty when it names none. The band is as
        VSX writes it and has not been converted to this telescope's band.
    """

    variability_type: str
    bright_magnitude: float | None
    faint_magnitude: float | None
    amplitude_mag: float | None
    period_days: float | None
    band: str


# The flag VSX writes in the column beside the minimum when the number in the
# minimum column is an amplitude and not a magnitude.
_VSX_AMPLITUDE_FLAG = "("


def _number(value: Any) -> float | None:
    """Read a table cell as a finite number, treating masked cells as missing.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` for a masked, empty or non-numeric cell.
    """
    text = _text(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    # The table stores single-precision numbers; the text carries the digits
    # VSX wrote, so reading it back as text keeps 2.09 and not
    # 2.0899999141693115.
    return number if math.isfinite(number) else None


def _cell(row: Any, column: str) -> Any:
    """Read one column of a match row, or `None` when the table lacks it.

    Returns
    -------
    value : `Any`
        The cell, or `None` when the column does not exist.
    """
    return row[column] if column in row.colnames else None


def vsx_entries_from_matches(star_ids: Sequence[str], matches: Table | None) -> dict[str, VsxEntry | None]:
    """Read the brightness range and period VSX lists for each star.

    The entry is the one `vsx_types_from_matches` takes the type from: the
    nearest entry within the match radius that has a real type (not blank and
    not ``CST``).

    Parameters
    ----------
    star_ids : `Sequence` [`str`]
        The ids that were matched.
    matches : `astropy.table.Table` or `None`
        The cross-match result, with ``id``, ``Type`` and ``angDist``, and
        the VSX columns ``max``, ``min``, ``f_min``, ``n_max``, ``n_min`` and
        ``Period``
        when VSX gave them.

    Returns
    -------
    entries : `dict` [`str`, `VsxEntry` or `None`]
        For each id, the entry, or `None` when no typed variable matched.
    """
    nearest: dict[str, tuple[float, VsxEntry]] = {}
    if matches is not None:
        for row in matches:
            text = _text(row["Type"])
            if text in _UNTYPED_VSX_TYPES or text in (VSX_CONSTANT_TYPE, VSX_UNCERTAIN_CONSTANT_TYPE):
                continue
            star_id = str(row["id"])
            distance = float(row["angDist"])
            if star_id in nearest and nearest[star_id][0] <= distance:
                continue
            bright = _number(_cell(row, "max"))
            minimum_column = _number(_cell(row, "min"))
            if _text(_cell(row, "f_min")) == _VSX_AMPLITUDE_FLAG:
                faint, amplitude = None, minimum_column
            else:
                faint = minimum_column
                if bright is not None and faint is not None and faint < bright:
                    # A "minimum" brighter than the maximum is not a magnitude.
                    # VSX sometimes writes an amplitude there without the flag
                    # (live check 2026-10-23: IC 1396 and NGC 1893 stars gave
                    # 0.028 and 0.24 beside maxima of 9.7 and 15.3), so neither
                    # is trusted.
                    faint = None
                amplitude = None if bright is None or faint is None else round(faint - bright, 4)
            # A range is the faint magnitude minus the bright one only when
            # both are in the same band. When VSX names a different band for
            # the minimum (n_min), the two numbers are not comparable, so
            # neither the faint magnitude nor the range is kept.
            minimum_band = _text(_cell(row, "n_min"))
            maximum_band = _text(_cell(row, "n_max"))
            if minimum_band and maximum_band and minimum_band != maximum_band:
                faint, amplitude = None, None
            period = _number(_cell(row, "Period"))
            nearest[star_id] = (
                distance,
                VsxEntry(
                    variability_type=text,
                    bright_magnitude=bright,
                    faint_magnitude=faint,
                    amplitude_mag=amplitude if amplitude is not None and amplitude >= 0 else None,
                    period_days=period if period is not None and period > 0 else None,
                    band=_text(_cell(row, "n_max")),
                ),
            )
    return {star_id: nearest[star_id][1] if star_id in nearest else None for star_id in star_ids}


def look_up_gaia_classes(positions: Table) -> dict[int, str]:
    """Ask VizieR which Gaia DR3 sources near a group of stars are variables.

    Parameters
    ----------
    positions : `astropy.table.Table`
        From `build_position_table`; all stars within one field (see
        `group_into_fields`).

    Returns
    -------
    classes : `dict` [`int`, `str`]
        For each Gaia DR3 source number in the field that Gaia's variability
        classification lists, its class (``"ECL"``, ``"RS"``, ``"LPV"``).
        The table holds one class per source and no score.
    """
    if len(positions) == 0:
        return {}
    sky = _sky_of(positions)
    reach = sky[0].separation(sky).deg.max() + resolve(MATCH_RADIUS_ARCSEC).value / 3600.0
    rows = query_vizier_field(
        GAIA_VARIABLE_CATALOG,
        GAIA_VARIABLE_COLUMNS,
        float(sky[0].ra.deg),
        float(sky[0].dec.deg),
        float(reach),
    )
    return {int(row["Source"]): _text(row["Class"]) for row in rows}


def gaia_flags_from_classes(
    gaia_source_ids: dict[str, int | None], classes: dict[int, str]
) -> dict[str, str]:
    """Decide what Gaia DR3 says about each star, matching by source number.

    Parameters
    ----------
    gaia_source_ids : `dict` [`str`, `int` or `None`]
        Each star's Gaia DR3 source number, or `None` for a star without one.
    classes : `dict` [`int`, `str`]
        From `look_up_gaia_classes`.

    Returns
    -------
    flags : `dict` [`str`, `str`]
        For each star with a source number: ``VARIABLE:<class>`` when the
        classification lists the source (`classify_gaia_variable_flag` reads
        this as a known variable), else `NO_GAIA_MATCH`, which says nothing
        about variability. A star with no source number is left out, so it
        stays unasked for Gaia.
    """
    flags = {}
    for star_id, source_id in gaia_source_ids.items():
        if source_id is None:
            continue
        if source_id in classes:
            flags[star_id] = f"{GAIA_VARIABLE_FLAG}:{classes[source_id] or 'UNCLASSIFIED'}"
        else:
            flags[star_id] = NO_GAIA_MATCH
    return flags
