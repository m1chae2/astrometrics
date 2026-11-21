r"""Merge stellar catalog rows that are the same star under two catalog names.

The same star can be saved twice when two runs identified it against
different catalogs, so one run named it ``HD 151086`` and another named it
``Gaia DR3 1328045433153485824``. Since the save step
(`pipelines/shared/star_recording.py`) now matches a new star to an existing
row by position, runs no longer create these duplicates; this script is for
rows saved before that change. It finds rows whose sky positions agree to
within `SAME_STAR_POSITION_TOLERANCE_ARCSEC`, keeps one row per star, and
folds the others into it so nothing is lost:

* The survivor is the Gaia DR3 row if there is one, otherwise the row
  with a Henry Draper or Durchmusterung name (HD, BD, CD, CPD), otherwise
  the first by name. It takes the most familiar of all the rows' names as
  its display name and keeps the others as aliases.
* Fields the survivor is missing are filled from the other rows (the
  same rule `reconcile_position_only_star_catalog` uses), and the lists
  of targets are combined.
* If both rows have a light curve, the two are joined by measurement
  time. If that cannot be done safely, the cluster is left alone and
  reported. If both rows have a spectrum, the one with more measured
  samples is kept and the spectral histories are joined.
* Two rows from the same catalog (two Gaia ids, say) are different
  objects however close they are, so such a cluster is never merged.

Check first, then apply::

    python -m astrometricslib.scripts.merge_duplicate_catalog_stars

    python -m astrometricslib.scripts.merge_duplicate_catalog_stars --apply

``--apply`` copies the catalog database to a timestamped ``.bak`` file in
the same directory before writing anything.
"""

import argparse
import logging
import sys

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from astrometricslib import Astrometrics, configure_logging
from astrometricslib._internal.drivers.local_database import backup_catalog_database
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import (
    SAME_STAR_POSITION_TOLERANCE_ARCSEC,
    catalog_family,
    choose_survivor_id,
    merge_cluster,
    merge_light_curves,
)

# `catalog_family`, `merge_cluster` and `merge_light_curves` live in
# `catalog_star_identity`, shared with the id cleanup script; they are named
# here too so this script's callers and tests can keep using them.
__all__ = [
    "catalog_family",
    "choose_survivor_id",
    "find_duplicate_clusters",
    "merge_cluster",
    "merge_light_curves",
]

logger = logging.getLogger(__name__)

# A cluster of more than this many rows is left alone. Four names for one
# star is plausible (HD, Gaia, 2MASS, TYC); more than that is more likely
# a crowded patch where distinct stars chain together.
MAXIMUM_CLUSTER_SIZE = 4

_POSITION_ONLY_PREFIX = "FIELD_J"


def find_duplicate_clusters(summaries: list) -> list[list[str]]:
    """Group catalog rows that sit on the same spot in the sky.

    Parameters
    ----------
    summaries : `list` [`StarSummary`]
        The catalog's star summaries.

    Returns
    -------
    clusters : `list` [`list` [`str`]]
        Each cluster is the ids of two or more rows within
        `SAME_STAR_POSITION_TOLERANCE_ARCSEC` of each other (chained pairwise).
        Position-only rows and rows with no position are ignored.
    """
    positioned = [
        summary
        for summary in summaries
        if not summary.id.startswith(_POSITION_ONLY_PREFIX)
        and summary.right_ascension is not None
        and summary.declination is not None
    ]
    if len(positioned) < 2:
        return []
    right_ascension = np.radians([summary.right_ascension for summary in positioned])
    declination = np.radians([summary.declination for summary in positioned])
    unit_vectors = np.column_stack([
        np.cos(declination) * np.cos(right_ascension),
        np.cos(declination) * np.sin(right_ascension),
        np.sin(declination),
    ])
    # The straight-line distance between two points on the unit sphere for
    # a small angle; faster than a great-circle formula for every pair.
    chord = 2.0 * np.sin(np.radians(resolve(SAME_STAR_POSITION_TOLERANCE_ARCSEC).value / 3600.0) / 2.0)
    pairs = np.array(sorted(cKDTree(unit_vectors).query_pairs(chord)))
    if pairs.size == 0:
        return []
    graph = coo_matrix(
        (np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(positioned), len(positioned))
    )
    _, labels = connected_components(graph, directed=False)
    members: dict[int, list[str]] = {}
    for summary, label in zip(positioned, labels, strict=True):
        members.setdefault(int(label), []).append(summary.id)
    return [sorted(ids) for ids in members.values() if len(ids) > 1]


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering whether to write.
    """
    parser = argparse.ArgumentParser(
        prog="merge_duplicate_catalog_stars",
        description="Merge catalog rows that are one star under two catalog names.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the merges. Without this only a report is printed."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Explicitly request a preview. This is already the default."
    )
    return parser


def run_merge(argv: list[str] | None = None) -> int:
    """Report or apply the duplicate-star merge.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if `--apply` was requested but the safety
        backup could not be made.
    """
    arguments = _build_argument_parser().parse_args(argv)
    configure_logging("merge_duplicate_catalog_stars", level=logging.INFO, log_dir="")

    astrometrics = Astrometrics()
    clusters = find_duplicate_clusters(astrometrics._catalog_access.list_star_summaries())
    if not clusters:
        print("No duplicate catalog rows found.")
        return 0

    planned: list[tuple[StellarObject, list[str]]] = []
    skipped: list[tuple[list[str], str]] = []
    for ids in clusters:
        if len(ids) > MAXIMUM_CLUSTER_SIZE:
            skipped.append((ids, f"{len(ids)} rows in one cluster"))
            continue
        stars = {star.id: star for star in astrometrics._catalog_access.get_by_ids("stellar_catalog", ids)}
        result = merge_cluster(stars, choose_survivor_id(list(stars)))
        if isinstance(result, str):
            skipped.append((ids, result))
        else:
            planned.append(result)

    print(f"{len(clusters)} duplicate cluster(s): {len(planned)} can be merged, {len(skipped)} left alone.")
    for survivor, removed in planned[:15]:
        print(f"  keep {survivor.id}  <-  {', '.join(removed)}")
    if len(planned) > 15:
        print(f"  ... and {len(planned) - 15} more")
    for ids, reason in skipped[:10]:
        print(f"  skipped ({reason}): {', '.join(ids)}")

    if not arguments.apply:
        print("\nDry run: nothing was written. Re-run with --apply to merge these rows.")
        return 0

    backup_path = backup_catalog_database(astrometrics.config)
    if backup_path is None:
        print(
            "\nCould not create a safety backup of the catalog database; aborting without writing anything."
        )
        return 1
    print(f"\nBacked up the catalog database to {backup_path}.")
    for survivor, removed in planned:
        astrometrics._catalog_access.merge_and_record(
            "stellar_catalog", [survivor], lambda _existing, updated: updated
        )
        astrometrics._catalog_access.delete_by_ids("stellar_catalog", removed)
    print(f"Merged {len(planned)} cluster(s), removing {sum(len(removed) for _, removed in planned)} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(run_merge())
