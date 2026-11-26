r"""One-time cleanup of star ids: Gaia numbers, and position-only rows.

The star identifier now saves a star that Gaia DR3 lists under its Gaia
number (``Gaia DR3 <source_id>``), and keeps the SIMBAD names as its display
name and aliases (see `star_recording` and the star identifier). Rows saved
before that change, and the position-only rows (``FIELD_J...``) saved when a
Gaia query failed or the field was too large to ask for, still carry the
old ids. This script brings the saved catalog in line, in five steps:

1. **Find each row's Gaia star.** A row named ``Gaia DR3 <n>``, or one that
   stores a Gaia number from SIMBAD's list of names, is that Gaia star. Any
   other named row is matched to the nearest star in the local Gaia cache
   (the downloaded tiles, read with `stars_in_tiles`) within
   `NAMED_STAR_GAIA_RADIUS_ARCSEC` (1 arcsec), when no second Gaia star is
   that close and the catalog magnitude agrees with the Gaia G magnitude
   to within `MAGNITUDE_AGREEMENT_MAG`. A position-only row is matched
   within `POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC` (3 arcsec), to the nearest
   Gaia star when it is at most half as far as the next one. Gaia positions
   are moved by their proper motion to `MATCH_EPOCH_YEAR` first, because
   the saved positions are measured on images from about then.
2. **Merge each Gaia star's rows** into one row saved as ``Gaia DR3 <n>``.
   Two named rows from the same catalog are two stars however close, so
   only the one matched by its stored number (or else the nearest) joins;
   a row's catalogs are those of both its id and its display name.
3. **Fold the remaining position-only rows** into a star within 3 arcsec
   (chained pairwise through other such rows): a named row, or a row of a
   Gaia group. A row goes to the nearest such star of its chain.
4. **Merge position-only rows that duplicate each other** within 3 arcsec
   (chained pairwise; a chain of more than `MAXIMUM_POSITION_ONLY_CLUSTER`
   rows is left alone as a crowded patch). Rows that stay alone are
   genuine sources with no Gaia star (asteroids, passing objects, image
   artifacts) and keep their ``FIELD_J`` id.
5. **Rewrite every row** in the short stored form (see
   `stellar_storage_payload`), and give back the freed space (``VACUUM``).
   A row is rewritten only when it reads back equal to what it was.

Merging keeps every measurement it safely can: two light curves are joined
by measurement time (where both have a point at the same time, the kept
row's point is used; a row whose points at the same times are more than a
factor `SAME_FRAME_FLUX_RATIO_LIMIT` brighter or fainter is a neighbor and
is not merged), the spectrum with the most samples is kept, and other
empty fields are filled from the merged rows (`merge_cluster`). A group
whose light curves cannot be joined safely is left alone and reported. The
merged row takes the most familiar of all its rows' names as its display
name and keeps the others as aliases. A field of an older stored record that
today's model no longer has (such as ``spectraHistory``) is kept when it
holds data.

Star ids are referred to only by the star table itself: the quality rows
name frames, stacks and runs, and the target records hold no star ids
(checked on a copy of the real library, 2026-11-20; see AST-22). So rewriting
a row's id updates every reference.

Running the script a second time changes nothing.

Check first, then apply::

    python -m astrometricslib.scripts.reconcile_position_only_star_catalog

    python -m astrometricslib.scripts.reconcile_position_only_star_catalog \\
        --apply

``--database PATH`` works on another copy of the catalog database instead
of the library's own. ``--apply`` first copies the database to a timestamped
``.bak`` file next to it (checked for integrity), and writes nothing if that
fails.
"""

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from astrometricslib import Astrometrics, configure_logging
from astrometricslib._internal.drivers.catalog_access import (
    STAR_RECORD_COLUMNS,
    stellar_object_from_row,
    stellar_row_values,
)
from astrometricslib._internal.drivers.catalog_store import GAIA_TILE_LEVEL, stars_in_tiles
from astrometricslib._internal.drivers.local_database import snapshot_sqlite_database
from astrometricslib._internal.drivers.sky_tiles import healpix_pixels_of_points
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.storage.data_versions import STARS_KIND, bump_data_version
from astrometricslib.models.stellar_source import StellarObject, has_catalog_magnitude
from astrometricslib.pipelines.shared.catalog_star_identity import (
    POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC,
    catalog_family,
    gaia_dr3_source_id_from_names,
    identity_rank,
    merge_cluster,
    settle_names,
)
from astrometricslib.utilities.gaia_record_ids import gaia_record_id

logger = logging.getLogger(__name__)

_POSITION_ONLY_PREFIX = "FIELD_J"

# A named row with no stored Gaia number is matched to a cached Gaia star
# only this close. Named rows hold the position measured on an image, which
# for the stars whose Gaia number is known lies a median 0.9 arcsec from the
# Gaia position (AST-22); 1 arcsec keeps a bright star from being given a
# faint neighbor's number.
NAMED_STAR_GAIA_RADIUS_ARCSEC = 1.0

# A named row's catalog (V) magnitude and the Gaia G magnitude of its match
# may differ by at most this much. V - G is about 0 for a blue star and
# about 2 for a red giant; 3 magnitudes allows the reddest normal stars.
MAGNITUDE_AGREEMENT_MAG = 3.0

# The year the saved positions were measured, about. Gaia positions are
# for 2016.0 and are moved to this year by their proper motion before
# matching, so a fast-moving star still matches.
MATCH_EPOCH_YEAR = 2026.0

# The Gaia DR3 position epoch.
_GAIA_EPOCH_YEAR = 2016.0

# A position-only match to a Gaia star is taken only when the next Gaia
# star is at least this many times farther away.
_UNIQUE_MATCH_DISTANCE_RATIO = 2.0

# Two rows with light-curve points at the same times are one star only when
# the median ratio of their fluxes there is within this factor of 1. On the
# copy of the library, the ratio's middle half ran from 0.82 to 0.99 (two
# runs measuring one star); a factor of 2 separates a neighbor (AST-22).
SAME_FRAME_FLUX_RATIO_LIMIT = 2.0

# A chain of position-only rows longer than this, each within 3 arcsec of
# the next, is a crowded patch where distinct sources link up, and is left
# alone.
MAXIMUM_POSITION_ONLY_CLUSTER = 6

# Top-level keys a star's stored record may hold that today's model does
# not read. They are kept on rewrite when they hold data.
_MODEL_KEYS = {
    *(info.alias or name for name, info in StellarObject.model_fields.items()),
    *StellarObject.model_fields,
    *(info.alias or name for name, info in StellarObject.model_computed_fields.items()),
}


@dataclass
class StarRow:
    """What the matching steps need to know about one saved row.

    Attributes
    ----------
    id : `str`
        The row id.
    name : `str`
        The row's display name.
    right_ascension, declination : `float`
        The saved position, in degrees.
    magnitude : `float` or `None`
        The saved magnitude.
    stored_gaia_source_id : `int` or `None`
        A Gaia DR3 number the row already carries (its id, or the number
        SIMBAD listed among its names).
    """

    id: str
    name: str
    right_ascension: float
    declination: float
    magnitude: float | None
    stored_gaia_source_id: int | None

    @property
    def is_position_only(self) -> bool:
        """Tell whether this row has a position-only id.

        Returns
        -------
        is_position_only : `bool`
            `True` for a ``FIELD_J`` id.
        """
        return self.id.startswith(_POSITION_ONLY_PREFIX)


@dataclass
class MergeGroup:
    """Rows that are one star, and the id they will be saved under.

    Attributes
    ----------
    final_id : `str`
        The id the merged row is saved under.
    member_ids : `list` [`str`]
        Every row of the star, including the one already under `final_id`.
    gaia_source_id : `int` or `None`
        The Gaia DR3 number, for a group matched to a Gaia star.
    gaia_magnitude : `float` or `None`
        The Gaia G magnitude, for a group matched to a Gaia star.
    neighbor_ids : `list` [`str`]
        Rows left out of the merge because their light curve shows a
        different star in the same frames (set by `merge_group`).
    """

    final_id: str
    member_ids: list[str] = field(default_factory=list)
    gaia_source_id: int | None = None
    gaia_magnitude: float | None = None
    neighbor_ids: list[str] = field(default_factory=list)


@dataclass
class CleanupReport:
    """What a run of the cleanup found or did.

    Attributes
    ----------
    rows_before, rows_after : `int`
        Rows in the star table before and after.
    ids_rewritten : `int`
        Rows whose star is saved under a new id (one per merge group whose
        id changed).
    rows_merged : `int`
        Rows folded into another row and removed.
    gaia_groups : `int`
        Merges of step 2.
    position_only_into_named : `int`
        Merges of step 3.
    position_only_duplicate_groups : `int`
        Merges of step 4.
    skipped_groups : `list` [`tuple` [`list` [`str`], `str`]]
        Groups left alone, with the reason.
    rows_compacted : `int`
        Rows rewritten in the short stored form.
    rows_not_compacted : `int`
        Rows kept as they were because the short form did not read back
        equal.
    rows_kept_as_neighbors : `int`
        Rows near a star but left as they were, because their light curve
        shows a different star in the same frames.
    gaia_numbers_corrected : `int`
        Rows named by a Gaia number whose stored Gaia number was missing,
        or was another star's (copied in by an earlier merge), set right.
    """

    rows_before: int = 0
    rows_after: int = 0
    ids_rewritten: int = 0
    rows_merged: int = 0
    gaia_groups: int = 0
    position_only_into_named: int = 0
    position_only_duplicate_groups: int = 0
    skipped_groups: list[tuple[list[str], str]] = field(default_factory=list)
    rows_compacted: int = 0
    rows_not_compacted: int = 0
    gaia_numbers_corrected: int = 0
    rows_kept_as_neighbors: int = 0


def _unit_vectors(right_ascension: np.ndarray, declination: np.ndarray) -> np.ndarray:
    """Turn sky positions into points on the unit sphere.

    Returns
    -------
    vectors : `numpy.ndarray`
        One ``(x, y, z)`` row per position.
    """
    ra = np.radians(right_ascension)
    dec = np.radians(declination)
    return np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def _chord(arcsec: float) -> float:
    """Give the straight-line distance on the unit sphere for a small angle.

    Returns
    -------
    chord : `float`
        The distance between two unit vectors `arcsec` apart.
    """
    return 2.0 * math.sin(math.radians(arcsec / 3600.0) / 2.0)


def _chord_to_arcsec(chord: np.ndarray) -> np.ndarray:
    """Turn unit-sphere distances back into angles.

    Returns
    -------
    arcsec : `numpy.ndarray`
        The angles, in arcseconds.
    """
    return np.degrees(2.0 * np.arcsin(np.clip(chord, 0.0, 2.0) / 2.0)) * 3600.0


def read_star_rows(connection: sqlite3.Connection) -> list[StarRow]:
    """Read the position and any Gaia number of every placed saved star.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.

    Returns
    -------
    rows : `list` [`StarRow`]
        One per row with both coordinates.
    """
    rows = []
    query = (
        "SELECT id, name, ra, dec, magnitude, json_extract(data_json, '$.gaiaDr3SourceId') "
        "FROM stellar_objects WHERE ra IS NOT NULL AND dec IS NOT NULL"
    )
    for star_id, name, right_ascension, declination, magnitude, stored in connection.execute(query):
        if not (right_ascension or declination):
            continue  # 0, 0 means "no position saved yet"
        # A row named by a Gaia number is that star, whatever number an
        # earlier merge may have copied into its record.
        source_id = gaia_dr3_source_id_from_names([star_id])
        if source_id is None and stored is not None:
            source_id = int(stored)
        rows.append(
            StarRow(star_id, name or "", float(right_ascension), float(declination), magnitude, source_id)
        )
    return rows


def read_cached_gaia_stars(config: Any, rows: list[StarRow]) -> dict[str, np.ndarray]:
    """Read the cached Gaia stars of every tile the saved stars touch.

    Parameters
    ----------
    config : `AppConfiguration`
        Locates the Gaia cache.
    rows : `list` [`StarRow`]
        The saved stars.

    Returns
    -------
    gaia : `dict` [`str`, `numpy.ndarray`]
        ``source_id``, ``ra``, ``dec`` (moved to `MATCH_EPOCH_YEAR`) and
        ``g``, one entry per cached star.
    """
    level = int(resolve(GAIA_TILE_LEVEL).value)
    right_ascension = np.array([row.right_ascension for row in rows])
    declination = np.array([row.declination for row in rows])
    tiles: set[int] = set()
    # The star's own tile, and those of points 4 arcsec around it, so a star
    # near a tile edge also finds the Gaia stars just across it.
    step = 4.0 / 3600.0
    for offset_ra, offset_dec in (
        (0, 0),
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
        (1, 1),
        (1, -1),
        (-1, 1),
        (-1, -1),
    ):
        shifted_dec = np.clip(declination + offset_dec * step, -90.0, 90.0)
        shifted_ra = (
            right_ascension + offset_ra * step / np.maximum(np.cos(np.radians(declination)), 1e-6)
        ) % 360
        tiles.update(
            int(tile) for tile in np.unique(healpix_pixels_of_points(level, shifted_ra, shifted_dec))
        )
    cached = stars_in_tiles(config, sorted(tiles), 99.0, include_proper_motion=True)
    if not cached:
        empty = np.array([])
        return {"source_id": empty.astype(np.int64), "ra": empty, "dec": empty, "g": empty}
    source_id = np.array([star[0] for star in cached], dtype=np.int64)
    gaia_ra = np.array([star[1] for star in cached], dtype=float)
    gaia_dec = np.array([star[2] for star in cached], dtype=float)
    g = np.array([np.nan if star[3] is None else star[3] for star in cached], dtype=float)
    # Each cached star is (source_id, ra, dec, g, designation, pmra, pmdec).
    pmra = np.nan_to_num(np.array([np.nan if star[5] is None else star[5] for star in cached], dtype=float))
    pmdec = np.nan_to_num(np.array([np.nan if star[6] is None else star[6] for star in cached], dtype=float))
    # Proper motion is in milliarcseconds per year; pmra already includes
    # the cos(dec) factor.
    years = MATCH_EPOCH_YEAR - _GAIA_EPOCH_YEAR
    moved_dec = gaia_dec + pmdec * years / 3.6e6
    moved_ra = (gaia_ra + pmra * years / 3.6e6 / np.maximum(np.cos(np.radians(gaia_dec)), 1e-6)) % 360.0
    return {"source_id": source_id, "ra": moved_ra, "dec": moved_dec, "g": g}


def match_rows_to_gaia(
    rows: list[StarRow], gaia: dict[str, np.ndarray]
) -> dict[str, tuple[int, float, bool]]:
    """Find the Gaia star of each saved row (step 1).

    Parameters
    ----------
    rows : `list` [`StarRow`]
        The saved stars.
    gaia : `dict` [`str`, `numpy.ndarray`]
        The cached Gaia stars, from `read_cached_gaia_stars`.

    Returns
    -------
    matches : `dict` [`str`, `tuple` [`int`, `float`, `bool`]]
        For each matched row id: the Gaia number, the distance in arcsec
        (0 for a stored number) and whether the number was stored.
    """
    matches: dict[str, tuple[int, float, bool]] = {}
    to_search = []
    for row in rows:
        if row.stored_gaia_source_id is not None:
            matches[row.id] = (row.stored_gaia_source_id, 0.0, True)
        else:
            to_search.append(row)
    if not to_search or gaia["source_id"].size == 0:
        return matches

    tree = cKDTree(_unit_vectors(gaia["ra"], gaia["dec"]))
    vectors = _unit_vectors(
        np.array([row.right_ascension for row in to_search]), np.array([row.declination for row in to_search])
    )
    distances, indices = tree.query(vectors, k=2)
    distances = _chord_to_arcsec(distances)
    position_only_radius = resolve(POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC).value
    for row, (nearest, second), (nearest_index, _) in zip(to_search, distances, indices, strict=True):
        if row.is_position_only:
            if nearest > position_only_radius:
                continue
            if second <= position_only_radius and second < _UNIQUE_MATCH_DISTANCE_RATIO * nearest:
                continue
        else:
            if nearest > NAMED_STAR_GAIA_RADIUS_ARCSEC or second <= NAMED_STAR_GAIA_RADIUS_ARCSEC:
                continue
            gaia_g = gaia["g"][nearest_index]
            if (
                has_catalog_magnitude(row.magnitude)
                and np.isfinite(gaia_g)
                and abs(float(row.magnitude) - float(gaia_g)) > MAGNITUDE_AGREEMENT_MAG
            ):
                continue
        matches[row.id] = (int(gaia["source_id"][nearest_index]), float(nearest), False)
    return matches


def build_gaia_groups(
    rows: list[StarRow], matches: dict[str, tuple[int, float, bool]], gaia: dict[str, np.ndarray]
) -> dict[int, MergeGroup]:
    """Gather the rows of each Gaia star (step 2).

    A row already named ``Gaia DR3 <n>`` is in its own star's group. Two
    named rows of one catalog are two stars, so of those only the one with
    the stored number, or else the nearest, joins.

    Parameters
    ----------
    rows : `list` [`StarRow`]
        The saved stars.
    matches : `dict`
        From `match_rows_to_gaia`.
    gaia : `dict` [`str`, `numpy.ndarray`]
        The cached Gaia stars, for the G magnitude.

    Returns
    -------
    groups : `dict` [`int`, `MergeGroup`]
        One group per Gaia number, holding every row that joins it.
    """
    g_by_source = dict(zip(gaia["source_id"].tolist(), gaia["g"].tolist(), strict=True))
    candidates: dict[int, list[tuple[bool, float, StarRow]]] = {}
    for row in rows:
        match = matches.get(row.id)
        if match is None:
            continue
        source_id, distance, stored = match
        candidates.setdefault(source_id, []).append((not stored, distance, row))

    groups: dict[int, MergeGroup] = {}
    for source_id, members in candidates.items():
        final_id = gaia_record_id(source_id)
        accepted: list[str] = []
        families: set[str] = set()
        for _, _, row in sorted(members, key=lambda member: (member[0], member[1], member[2].id)):
            if row.is_position_only:
                accepted.append(row.id)
                continue
            # A row's catalogs are those of its id and of its name: a row
            # saved under a Gaia number keeps its HD name, say, and a second
            # HD row is a different star.
            row_families = {
                catalog_family(text)
                for text in (row.id, row.name)
                if text and not text.startswith(("Gaia DR3 ", _POSITION_ONLY_PREFIX))
            }
            if row.id != final_id and row_families & families:
                continue
            families.update(row_families)
            accepted.append(row.id)
        gaia_g = g_by_source.get(source_id)
        groups[source_id] = MergeGroup(
            final_id=final_id,
            member_ids=accepted,
            gaia_source_id=source_id,
            gaia_magnitude=None if gaia_g is None or not np.isfinite(gaia_g) else float(gaia_g),
        )
    return groups


def build_position_only_groups(
    rows: list[StarRow], gaia_groups: dict[int, MergeGroup]
) -> tuple[list[MergeGroup], list[MergeGroup], list[tuple[list[str], str]]]:
    """Fold leftover position-only rows into named rows or each other.

    These are steps 3 and 4. A leftover is a position-only row in no Gaia
    group. The anchors are the named rows and every row of a Gaia group
    (a position-only row in one stands for that Gaia star). Leftovers and
    anchors within 3 arcsec of a leftover are joined into chains, and each
    chain is settled at once, so a second run finds nothing more to join:

    * A chain with anchors gives each leftover to the group of its nearest
      anchor in the chain (a Gaia group, or the named row's own group).
    * A chain of leftovers only is one source, saved under its first id. A
      chain of more than `MAXIMUM_POSITION_ONLY_CLUSTER` leftovers is a
      crowded patch where distinct sources link up, and is left alone.

    Parameters
    ----------
    rows : `list` [`StarRow`]
        The saved stars.
    gaia_groups : `dict` [`int`, `MergeGroup`]
        The Gaia groups, extended in place with the leftovers they take.

    Returns
    -------
    into_named : `list` [`MergeGroup`]
        Groups of a named row (not in a Gaia group) with the position-only
        rows folded into it.
    duplicates : `list` [`MergeGroup`]
        Groups of position-only rows that duplicate each other.
    skipped : `list` [`tuple` [`list` [`str`], `str`]]
        Chains left alone, with the reason.
    """
    radius = resolve(POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC).value
    group_of_row = {member_id: group for group in gaia_groups.values() for member_id in group.member_ids}
    leftovers = [row for row in rows if row.is_position_only and row.id not in group_of_row]
    anchors = [row for row in rows if row.id in group_of_row or not row.is_position_only]
    if not leftovers:
        return [], [], []
    nodes = leftovers + anchors
    leftover_count = len(leftovers)
    vectors = _unit_vectors(
        np.array([row.right_ascension for row in nodes]), np.array([row.declination for row in nodes])
    )
    tree = cKDTree(vectors)
    pairs = tree.query_ball_point(vectors[:leftover_count], _chord(radius))
    first = [index for index, near in enumerate(pairs) for other in near if other != index]
    second = [other for index, near in enumerate(pairs) for other in near if other != index]
    graph = coo_matrix((np.ones(len(first)), (first, second)), shape=(len(nodes), len(nodes)))
    _, labels = connected_components(graph, directed=False)

    chains: dict[int, list[int]] = {}
    for index in range(len(nodes)):
        chains.setdefault(int(labels[index]), []).append(index)

    into_named_by_id: dict[str, MergeGroup] = {}
    duplicates: list[MergeGroup] = []
    skipped: list[tuple[list[str], str]] = []
    for members in chains.values():
        leftover_indices = [index for index in members if index < leftover_count]
        if not leftover_indices:
            continue
        anchor_indices = [index for index in members if index >= leftover_count]
        if not anchor_indices:
            if len(leftover_indices) < 2:
                continue
            ids = sorted(nodes[index].id for index in leftover_indices)
            if len(ids) > MAXIMUM_POSITION_ONLY_CLUSTER:
                skipped.append((ids, f"{len(ids)} position-only rows chain together"))
                continue
            duplicates.append(MergeGroup(final_id=ids[0], member_ids=ids))
            continue
        anchor_vectors = vectors[anchor_indices]
        for index in leftover_indices:
            nearest = anchor_indices[int(np.argmin(np.linalg.norm(anchor_vectors - vectors[index], axis=1)))]
            anchor = nodes[nearest]
            group = group_of_row.get(anchor.id)
            if group is None:
                group = into_named_by_id.setdefault(
                    anchor.id, MergeGroup(final_id=anchor.id, member_ids=[anchor.id])
                )
            group.member_ids.append(nodes[index].id)
    return list(into_named_by_id.values()), duplicates, skipped


def _read_raw_rows(connection: sqlite3.Connection, ids: Iterable[str]) -> dict[str, tuple[dict, dict]]:
    """Read the stored records and columns of some rows.

    Returns
    -------
    rows : `dict` [`str`, `tuple` [`dict`, `dict`]]
        For each id found: the parsed record and the row's columns.
    """
    wanted = list(ids)
    found: dict[str, tuple[dict, dict]] = {}
    columns_sql = ", ".join(STAR_RECORD_COLUMNS)
    for start in range(0, len(wanted), 900):
        chunk = wanted[start : start + 900]
        marks = ",".join("?" * len(chunk))
        query = f"SELECT data_json, {columns_sql} FROM stellar_objects WHERE id IN ({marks})"  # ruff: ignore[hardcoded-sql-expression]
        for data_json, *values in connection.execute(query, chunk):
            columns = dict(zip(STAR_RECORD_COLUMNS, values, strict=True))
            found[columns["id"]] = (json.loads(data_json), columns)
    return found


def _legacy_fields(record: dict) -> dict:
    """Pick the fields of a stored record that today's model does not read.

    Returns
    -------
    legacy : `dict`
        The keys the model does not know, when they hold data.
    """
    return {
        key: value
        for key, value in record.items()
        if key not in _MODEL_KEYS and value not in (None, [], {}, "")
    }


def _star_from_record(record: dict, columns: dict) -> StellarObject:
    """Build a star from a copy of its stored record and its columns.

    Returns
    -------
    star : `StellarObject`
        The star as the catalog reads it.
    """
    return stellar_object_from_row(json.loads(json.dumps(record)), dict(columns))


def merge_group(
    group: MergeGroup, raw_rows: dict[str, tuple[dict, dict]]
) -> tuple[StellarObject, dict, list[str]] | str:
    """Merge the rows of one group into one star.

    Parameters
    ----------
    group : `MergeGroup`
        The rows and the id to save them under.
    raw_rows : `dict`
        The stored records and columns of the group's rows.

    Returns
    -------
    result : `tuple` or `str`
        ``(star, legacy_fields, removed_ids)``, or a sentence saying why the
        group was left alone.
    """
    present = [member_id for member_id in group.member_ids if member_id in raw_rows]
    if not present:
        return "nothing to merge"
    if present == [group.final_id]:
        # Only the row under its own Gaia number: fill in its stored Gaia
        # number, or correct one an earlier merge copied in from another
        # star.
        star = _star_from_record(*raw_rows[group.final_id])
        if group.gaia_source_id is None or star.gaia_dr3_source_id == group.gaia_source_id:
            return "nothing to merge"
        star.gaia_dr3_source_id = group.gaia_source_id
        return star, _legacy_fields(raw_rows[group.final_id][0]), []
    stars = {member_id: _star_from_record(*raw_rows[member_id]) for member_id in present}
    # The row already saved under the final id is the base; otherwise the
    # row with the best id, then the one with the longest light curve.
    if group.final_id in stars:
        base_id = group.final_id
    else:
        base_id = min(
            present,
            key=lambda member_id: (
                identity_rank(member_id),
                -len(stars[member_id].photometry.timestamps if stars[member_id].photometry else []),
                member_id,
            ),
        )
    # A row whose light curve has points at the same times as the base's
    # but at clearly different brightness is a different star seen in the
    # same frames (a neighbor), not the same star; it is left out.
    # Each row is checked against every row already taken, nearest first.
    taken = [base_id]
    neighbors = []
    for member_id in present:
        if member_id == base_id:
            continue
        if any(light_curves_disagree(stars[kept_id], stars[member_id]) for kept_id in taken):
            neighbors.append(member_id)
        else:
            taken.append(member_id)
    group.neighbor_ids = neighbors
    present = [member_id for member_id in present if member_id not in neighbors]
    if len(present) < 2 and present[0] == group.final_id:
        return "nothing to merge"
    stars = {member_id: stars[member_id] for member_id in present}
    others = [stars[member_id] for member_id in present if member_id != base_id]
    result = merge_cluster(stars, base_id)
    if isinstance(result, str):
        return result
    survivor, _ = result

    legacy = _legacy_fields(raw_rows[base_id][0])
    for member_id in present:
        for key, value in _legacy_fields(raw_rows[member_id][0]).items():
            legacy.setdefault(key, value)

    survivor.id = group.final_id
    if group.gaia_source_id is not None:
        survivor.gaia_dr3_source_id = group.gaia_source_id
        survivor.is_catalog_identified = True
        if not has_catalog_magnitude(survivor.magnitude) and group.gaia_magnitude is not None:
            survivor.magnitude = group.gaia_magnitude
    elif any(star.is_catalog_identified for star in stars.values()):
        survivor.is_catalog_identified = True
    settle_names(survivor, others)
    return survivor, legacy, [member_id for member_id in present if member_id != group.final_id]


def light_curves_disagree(first: StellarObject, second: StellarObject) -> bool:
    """Tell whether two rows measured different stars in the same frames.

    One star has one brightness in one frame. When two rows have points at
    the same times, the median ratio of their fluxes there should be near
    1 (two runs that measured the same star). A ratio beyond
    `SAME_FRAME_FLUX_RATIO_LIMIT` either way means two stars.

    Parameters
    ----------
    first, second : `StellarObject`
        The two rows.

    Returns
    -------
    disagree : `bool`
        `True` when they share measurement times and their fluxes there
        differ by more than the limit; `False` when they share none.
    """
    if first.photometry is None or second.photometry is None:
        return False
    first_flux = dict(zip(first.photometry.timestamps, first.photometry.fluxes, strict=False))
    ratios = [
        flux / first_flux[time]
        for time, flux in zip(second.photometry.timestamps, second.photometry.fluxes, strict=False)
        if time in first_flux and first_flux[time] and flux and flux > 0 and first_flux[time] > 0
    ]
    if not ratios:
        return False
    median_ratio = float(np.median(ratios))
    return not (1.0 / SAME_FRAME_FLUX_RATIO_LIMIT <= median_ratio <= SAME_FRAME_FLUX_RATIO_LIMIT)


def _row_values_with_legacy(star: StellarObject, legacy: dict) -> dict[str, Any]:
    """Give a star's row values, with any older fields added to its record.

    Returns
    -------
    values : `dict`
        The row's columns, as `stellar_row_values` gives them.
    """
    values = stellar_row_values(star)
    if legacy:
        record = json.loads(values["data_json"])
        record.update(legacy)
        values["data_json"] = json.dumps(record)
    return values


def _write_row(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    """Save one star row, replacing any row with the same id."""
    columns = list(values)
    marks = ",".join("?" * len(columns))
    connection.execute(
        f"INSERT OR REPLACE INTO stellar_objects ({','.join(columns)}) VALUES ({marks})",  # ruff: ignore[hardcoded-sql-expression]
        [values[column] for column in columns],
    )


def compact_rows(connection: sqlite3.Connection, apply: bool) -> tuple[int, int]:
    """Rewrite every row in the short stored form (step 5).

    A row is rewritten only when its short form reads back equal to the
    star its current record gives; otherwise it is kept as it is.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.
    apply : `bool`
        Write the rows; otherwise only count them.

    Returns
    -------
    compacted, kept : `int`
        Rows rewritten (or that would be), and rows kept as they were.
    """
    compacted = kept = 0
    columns_sql = ", ".join(STAR_RECORD_COLUMNS)
    reader = connection.cursor()
    reader.execute(f"SELECT data_json, {columns_sql} FROM stellar_objects")  # ruff: ignore[hardcoded-sql-expression]
    pending: list[dict[str, Any]] = []
    while batch := reader.fetchmany(2000):
        for data_json, *values in batch:
            record = json.loads(data_json)
            columns = dict(zip(STAR_RECORD_COLUMNS, values, strict=True))
            star = _star_from_record(record, columns)
            new_values = _row_values_with_legacy(star, _legacy_fields(record))
            if new_values["data_json"] == data_json:
                continue
            reread = stellar_object_from_row(json.loads(new_values["data_json"]), _columns_of(new_values))
            if reread.model_dump(mode="json") != star.model_dump(mode="json"):
                kept += 1
                continue
            compacted += 1
            if apply:
                pending.append(new_values)
        if apply and pending:
            for row_values in pending:
                _write_row(connection, row_values)
            pending.clear()
    return compacted, kept


def _columns_of(values: dict[str, Any]) -> dict[str, Any]:
    """Pick the columns `stellar_object_from_row` reads out of row values.

    A number that is not finite is saved by the database as empty.

    Returns
    -------
    columns : `dict`
        The values of `STAR_RECORD_COLUMNS`.
    """
    columns = {column: values.get(column) for column in STAR_RECORD_COLUMNS}
    for column in ("ra", "dec", "magnitude"):
        if isinstance(columns[column], float) and not math.isfinite(columns[column]):
            columns[column] = None
    return columns


def plan_cleanup(
    connection: sqlite3.Connection, config: Any
) -> tuple[list[tuple[str, MergeGroup]], CleanupReport]:
    """Work out every merge, without writing (steps 1 to 4).

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.
    config : `AppConfiguration`
        Locates the Gaia cache.

    Returns
    -------
    groups : `list` [`tuple` [`str`, `MergeGroup`]]
        Each group to merge, tagged with its step (``"gaia"``,
        ``"into_named"`` or ``"duplicates"``).
    report : `CleanupReport`
        The counts known before merging.
    """
    report = CleanupReport(
        rows_before=connection.execute("SELECT COUNT(*) FROM stellar_objects").fetchone()[0]
    )
    rows = read_star_rows(connection)
    gaia = read_cached_gaia_stars(config, rows)
    matches = match_rows_to_gaia(rows, gaia)
    gaia_groups = build_gaia_groups(rows, matches, gaia)
    into_named, duplicates, skipped = build_position_only_groups(rows, gaia_groups)
    report.skipped_groups.extend(skipped)
    groups = [("gaia", group) for group in gaia_groups.values()]
    groups += [("into_named", group) for group in into_named]
    groups += [("duplicates", group) for group in duplicates]
    return groups, report


def run_cleanup(
    connection: sqlite3.Connection, config: Any, apply: bool, compact: bool = True
) -> CleanupReport:
    """Plan and (with `apply`) carry out the whole cleanup.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.
    config : `AppConfiguration`
        Locates the Gaia cache.
    apply : `bool`
        Write the changes, in one transaction; otherwise only report them.
    compact : `bool`, optional
        Also rewrite every row in the short stored form (step 5).

    Returns
    -------
    report : `CleanupReport`
        What was found or done.
    """
    groups, report = plan_cleanup(connection, config)
    all_ids = {member_id for _, group in groups for member_id in group.member_ids}
    raw_rows = _read_raw_rows(connection, sorted(all_ids))
    try:
        for step, group in groups:
            result = merge_group(group, raw_rows)
            report.rows_kept_as_neighbors += len(group.neighbor_ids)
            if isinstance(result, str):
                if result != "nothing to merge":
                    report.skipped_groups.append((group.member_ids, result))
                continue
            star, legacy, removed_ids = result
            if not removed_ids and group.final_id in raw_rows:
                report.gaia_numbers_corrected += 1
                if apply:
                    _write_row(connection, _row_values_with_legacy(star, legacy))
                continue
            report.rows_merged += len(removed_ids) - (0 if group.final_id in raw_rows else 1)
            report.ids_rewritten += 0 if group.final_id in raw_rows else 1
            if step == "gaia":
                report.gaia_groups += 1
            elif step == "into_named":
                report.position_only_into_named += 1
            else:
                report.position_only_duplicate_groups += 1
            if apply:
                for removed_id in removed_ids:
                    connection.execute("DELETE FROM stellar_objects WHERE id = ?", (removed_id,))
                _write_row(connection, _row_values_with_legacy(star, legacy))
        if compact:
            report.rows_compacted, report.rows_not_compacted = compact_rows(connection, apply)
        if apply:
            bump_data_version(connection, STARS_KIND)
            connection.commit()
    except BaseException:
        connection.rollback()
        raise
    report.rows_after = connection.execute("SELECT COUNT(*) FROM stellar_objects").fetchone()[0]
    return report


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering the database, whether to write, and compaction.
    """
    parser = argparse.ArgumentParser(
        prog="reconcile_position_only_star_catalog",
        description="Save stars under their Gaia DR3 number, merge position-only (FIELD_J) rows into "
        "the stars they duplicate, and rewrite every row in the short stored form.",
    )
    parser.add_argument(
        "--database",
        help="The catalog database to clean. Defaults to the library's own astrometrics.db.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without this the script only reports what it would do.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Explicitly request a preview. This is already the default."
    )
    parser.add_argument(
        "--no-compact",
        action="store_true",
        help="Leave the stored records in their current form (skip step 5 and VACUUM).",
    )
    return parser


def _print_report(report: CleanupReport, applied: bool) -> None:
    """Print what the cleanup found or did."""
    verb = "" if applied else "would be "
    print(f"Rows before: {report.rows_before}")
    print(f"Gaia stars with rows {verb}merged or renamed: {report.gaia_groups}")
    print(f"Named rows {verb}given position-only rows: {report.position_only_into_named}")
    print(f"Groups of duplicate position-only rows {verb}merged: {report.position_only_duplicate_groups}")
    print(f"Stars {verb}saved under a new id: {report.ids_rewritten}")
    print(f"Rows {verb}merged into another row and removed: {report.rows_merged}")
    print(f"Rows kept apart as neighbors (light curves differ in one frame): {report.rows_kept_as_neighbors}")
    print(f"Stored Gaia numbers {verb}filled in or corrected: {report.gaia_numbers_corrected}")
    print(f"Rows {verb}rewritten in the short form: {report.rows_compacted}")
    print(f"Rows kept as they were (short form did not read back equal): {report.rows_not_compacted}")
    print(f"Groups left alone: {len(report.skipped_groups)}")
    for ids, reason in report.skipped_groups[:10]:
        print(f"  ({reason}): {', '.join(ids[:6])}{' ...' if len(ids) > 6 else ''}")
    if applied:
        print(f"Rows after: {report.rows_after}")


def run_reconciliation(argv: list[str] | None = None) -> int:
    """Report or apply the star id cleanup.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if the database is missing or `--apply` was
        requested but the safety backup could not be made.
    """
    arguments = _build_argument_parser().parse_args(argv)
    configure_logging("reconcile_position_only_star_catalog", level=logging.INFO, log_dir="")
    config = Astrometrics().config
    database = arguments.database or os.path.join(str(config.get_library_path()), "astrometrics.db")
    if not os.path.isfile(database):
        print(f"No catalog database at {database}.")
        return 1

    if arguments.apply:
        backup_path = f"{database}.{time.strftime('%Y%m%d_%H%M%S')}.bak"
        if not snapshot_sqlite_database(database, backup_path):
            print("Could not create a safety backup of the catalog database; nothing was written.")
            return 1
        print(f"Backed up the catalog database to {backup_path}.")

    size_before = os.path.getsize(database)
    connection = sqlite3.connect(database, timeout=60.0)
    try:
        report = run_cleanup(connection, config, apply=arguments.apply, compact=not arguments.no_compact)
        if arguments.apply and not arguments.no_compact:
            print("Giving back the freed space (VACUUM)...")
            connection.execute("VACUUM")
    finally:
        connection.close()
    _print_report(report, arguments.apply)
    print(f"Database size before: {size_before / 1e6:.1f} MB")
    if arguments.apply:
        print(f"Database size after: {os.path.getsize(database) / 1e6:.1f} MB")
    else:
        print("\nDry run: nothing was written. Re-run with --apply to make these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(run_reconciliation())
