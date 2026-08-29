r"""One-time cleanup for duplicate position-only stellar catalog rows.

`pipeline_tasks._reconcile_position_only_star_ids` stops a *new* run from
adding to this problem, but it never touches rows already on disk --
those still need a cleanup pass, which is what this script is.

Background: a star that never matched a known database gets an ID based on
its location in the sky, like ``FIELD_J{ra:.4f}{dec:+.4f}``. This location
is very precise (0.36 arcseconds). However, our image alignment isn't perfect
and can be off by a few arcseconds. So, if we measure the same star twice,
its measured location might change slightly, and we accidentally give it a
brand new ID instead of updating the existing one. This script groups these
very close "duplicate" stars together and merges them to clean up the
database.

This finds those clusters (grouped per target, the same scope
`_reconcile_position_only_star_ids` uses, since that is where this
catalog's own measured duplication concentrated) and merges each one
down to a single surviving row: every other member's non-empty fields
are copied onto the survivor wherever the survivor's own field is
still empty, so no data is discarded, then the other rows are deleted.

Check first, then apply::

    python -m astrometricslib.scripts.reconcile_position_only_star_catalog \\
        --dry-run

    python -m astrometricslib.scripts.reconcile_position_only_star_catalog \\
        --apply

``--apply`` copies the catalog database to a timestamped ``.bak`` file
in the same directory before writing anything, in addition to the
normal per-cluster non-destructive merge -- this is the one script in
this library that deletes rows outright, so it gets an extra safety
net the additive-only scripts (e.g. `backfill_focal_length`) don't
need.
"""

import argparse
import logging
import os
import shutil
import sys
import time
from typing import Any

from astrometricslib import Astrometrics
from astrometricslib.data_access.catalog_access import StarPosition
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.star_identifier import CATALOG_MATCH_RADIUS_ARCSEC

logger = logging.getLogger(__name__)

# Fields that identify the row itself or are recomputed fresh by every
# pipeline run regardless of what is already on disk -- never gap-filled
# from a duplicate, either because overwriting them from an arbitrary
# cluster member would be wrong (id/name) or because a value here says
# nothing about which duplicate is "more complete" (is_catalog_identified
# is always False for a FIELD_J row by construction).
_MERGE_EXCLUDED_FIELDS = frozenset({"id", "name", "target_ids", "is_catalog_identified"})


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering target selection and whether to write.
    """
    parser = argparse.ArgumentParser(
        prog="reconcile_position_only_star_catalog",
        description="Merge duplicate position-only (FIELD_J) stellar catalog rows created before "
        "pipeline_tasks._reconcile_position_only_star_ids existed.",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_ids",
        metavar="TARGET_ID",
        help="Restrict to this target; repeatable. Defaults to every target with position-only rows.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually merge and delete rows. Without this the script only reports what it would do.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly request a preview. This is already the default.",
    )
    return parser


def _is_empty_value(value: Any) -> bool:
    """Report whether a StellarObject field value counts as "not yet set".

    Parameters
    ----------
    value : `Any`
        A field value read off a `StellarObject`.

    Returns
    -------
    is_empty : `bool`
        `True` if this value carries no real information yet, so a
        duplicate's own value for the same field is worth copying in.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list | dict):
        return len(value) == 0
    if hasattr(value, "fluxes"):  # LightCurve
        return len(value.fluxes) == 0
    return False


def cluster_position_only_stars(stars: list[StarPosition]) -> list[list[StarPosition]]:
    """Group position-only stars into clusters within the catalog match radius.

    Uses simple nearest-neighbour connected-components: two stars in the
    same cluster if there is a chain of pairwise separations, each
    under `CATALOG_MATCH_RADIUS_ARCSEC`, linking them -- not
    necessarily a single tight group all mutually within the radius of
    each other. This matches what a chain of independent re-solves
    would actually produce: each new solve lands near the *previous*
    one, not necessarily near the very first.

    Parameters
    ----------
    stars : `list` [`StarPosition`]
        Stars already known to share this target and to have real
        numeric coordinates.

    Returns
    -------
    clusters : `list` [`list` [`StarPosition`]]
        Every input star, partitioned into clusters of size 1 (no
        duplicate found) or more.
    """
    if len(stars) < 2:
        return [[star] for star in stars]

    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    ra = np.radians(np.array([star.right_ascension for star in stars]))
    dec = np.radians(np.array([star.declination for star in stars]))
    xyz = np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])

    # Chord length on the unit sphere corresponding to the match radius,
    # for a plain Euclidean cKDTree query -- exact for this small an
    # angle, and avoids a slower per-pair great-circle computation.
    chord_radius = 2 * np.sin(np.radians(CATALOG_MATCH_RADIUS_ARCSEC / 3600) / 2)

    tree = cKDTree(xyz)
    pairs = np.array(list(tree.query_pairs(chord_radius)))
    star_count = len(stars)
    if len(pairs) == 0:
        return [[star] for star in stars]

    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(star_count, star_count))
    _, labels = connected_components(graph, directed=False)

    clusters: dict[int, list[StarPosition]] = {}
    for star, label in zip(stars, labels, strict=True):
        clusters.setdefault(int(label), []).append(star)
    return list(clusters.values())


def _merge_duplicate_into_survivor(survivor: StellarObject, duplicate: StellarObject) -> None:
    """Copy a duplicate's non-empty fields onto the survivor, in place.

    Every declared `StellarObject` field is covered generically rather
    than hand-listed, so a field added to the model later is merged
    correctly without this function needing to be updated to match --
    the alternative (an explicit per-field list) is exactly the kind
    of thing that quietly drifts out of sync with the model it mirrors.
    Only fills a gap; a survivor's own non-empty value is never
    overwritten, so merging can only add data, never lose it.

    Parameters
    ----------
    survivor : `StellarObject`
        The row that will be kept, mutated in place.
    duplicate : `StellarObject`
        The row about to be deleted; nothing it uniquely holds is lost.
    """
    for field_name in duplicate.target_ids:
        if field_name not in survivor.target_ids:
            survivor.target_ids.append(field_name)

    for field_name in type(survivor).model_fields:
        if field_name in _MERGE_EXCLUDED_FIELDS:
            continue
        if _is_empty_value(getattr(survivor, field_name)):
            duplicate_value = getattr(duplicate, field_name)
            if not _is_empty_value(duplicate_value):
                setattr(survivor, field_name, duplicate_value)


def find_position_only_clusters(
    astrometrics: Astrometrics, target_ids: list[str] | None = None
) -> dict[str, list[list[StarPosition]]]:
    """Find every target's position-only duplicate clusters.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Provides catalog access.
    target_ids : `list` [`str`], optional
        Restrict to these targets; defaults to every target with at
        least one position-only row.

    Returns
    -------
    clusters_by_target : `dict` [`str`, `list` [`list` [`StarPosition`]]]
        Every target with at least one cluster of size 2+, mapped to
        that target's clusters (clusters of size 1 are omitted --
        nothing to merge).
    """
    position_only_stars = astrometrics.catalog_access.list_position_only_stars()

    wanted_targets = {t.strip().casefold() for t in target_ids} if target_ids else None

    stars_by_target: dict[str, list[StarPosition]] = {}
    for star in position_only_stars:
        for target_id in star.target_ids:
            if wanted_targets is not None and target_id.casefold() not in wanted_targets:
                continue
            stars_by_target.setdefault(target_id, []).append(star)

    clusters_by_target: dict[str, list[list[StarPosition]]] = {}
    for target_id, target_stars in stars_by_target.items():
        clusters = [cluster for cluster in cluster_position_only_stars(target_stars) if len(cluster) > 1]
        if clusters:
            clusters_by_target[target_id] = clusters
    return clusters_by_target


def apply_clusters(
    astrometrics: Astrometrics, clusters_by_target: dict[str, list[list[StarPosition]]]
) -> int:
    """Merge and delete every cluster's duplicate rows.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Provides catalog read/write access.
    clusters_by_target : `dict` [`str`, `list` [`list` [`StarPosition`]]]
        As returned by `find_position_only_clusters`.

    Returns
    -------
    rows_removed : `int`
        Total duplicate rows deleted across every cluster.
    """
    rows_removed = 0
    for target_id, clusters in clusters_by_target.items():
        for cluster in clusters:
            ids = sorted(star.id for star in cluster)
            # Deterministic and reproducible: which id happens to
            # survive doesn't matter for the data itself, since every
            # other member's fields are merged into it below, but a
            # fixed rule keeps a re-run of this script idempotent.
            survivor_id, *duplicate_ids = ids

            hydrated = {obj.id: obj for obj in astrometrics.catalog_access.get_by_ids("stellar_catalog", ids)}
            survivor = hydrated.get(survivor_id)
            if survivor is None:
                logger.warning(
                    "[%s] Survivor row %s vanished before merge; skipping cluster.", target_id, survivor_id
                )
                continue

            for duplicate_id in duplicate_ids:
                duplicate = hydrated.get(duplicate_id)
                if duplicate is None:
                    continue
                _merge_duplicate_into_survivor(survivor, duplicate)

            astrometrics.catalog_access.merge_and_record(
                "stellar_catalog", [survivor], lambda _existing, updated: updated
            )
            astrometrics.catalog_access.delete_by_ids("stellar_catalog", duplicate_ids)
            rows_removed += len(duplicate_ids)

    return rows_removed


def _backup_catalog_database(astrometrics: Astrometrics) -> str | None:
    """Copy the catalog database aside before this script writes to it.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Provides the library path the database lives under.

    Returns
    -------
    backup_path : `str` or `None`
        Where the copy was written, or `None` if the source database
        doesn't exist yet (nothing to back up) or the copy failed.
    """
    db_path = os.path.join(str(astrometrics.config.get_library_path()), "astrometrics.db")
    if not os.path.exists(db_path):
        return None
    backup_path = f"{db_path}.{time.strftime('%Y%m%d_%H%M%S')}.bak"
    try:
        shutil.copy2(db_path, backup_path)
    except OSError as backup_error:
        logger.error("Could not back up %s before writing: %s", db_path, backup_error)
        return None
    return backup_path


def run_reconciliation(argv: list[str] | None = None) -> int:
    """Report or apply the position-only-row cleanup.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success (including "nothing to do"), ``1`` if
        `--apply` was requested but the safety backup could not be
        made.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    astrometrics = Astrometrics()
    clusters_by_target = find_position_only_clusters(astrometrics, arguments.target_ids)

    if not clusters_by_target:
        print("No duplicate position-only rows found within the match radius.")
        return 0

    total_clusters = sum(len(clusters) for clusters in clusters_by_target.values())
    total_duplicate_rows = sum(
        len(cluster) - 1 for clusters in clusters_by_target.values() for cluster in clusters
    )
    print(
        f"{total_clusters} cluster(s) of duplicate position-only rows across "
        f"{len(clusters_by_target)} target(s), {total_duplicate_rows} row(s) would be removed:"
    )
    for target_id in sorted(clusters_by_target):
        clusters = clusters_by_target[target_id]
        target_duplicate_rows = sum(len(cluster) - 1 for cluster in clusters)
        print(f"  {target_id:16s} {len(clusters):5d} cluster(s)   {target_duplicate_rows:6d} row(s) removed")

    if not arguments.apply:
        print(
            f"\nDry run: nothing was written. Re-run with --apply to merge and delete "
            f"these {total_duplicate_rows} row(s)."
        )
        return 0

    backup_path = _backup_catalog_database(astrometrics)
    if backup_path is None:
        print(
            "\nCould not create a safety backup of the catalog database; aborting without writing anything."
        )
        return 1
    print(f"\nBacked up the catalog database to {backup_path}.")

    print(f"Merging and deleting {total_duplicate_rows} row(s)...")
    rows_removed = apply_clusters(astrometrics, clusters_by_target)
    print(f"Removed {rows_removed} duplicate row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(run_reconciliation())
