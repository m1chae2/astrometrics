r"""Merge stellar catalog rows that are the same star under two catalog names.

The same star can be saved twice when two runs identified it against
different catalogs, so one run named it ``HD 151086`` and another named it
``Gaia DR3 1328045433153485824``. Since the save step
(`pipelines/shared/star_recording.py`) now matches a new star to an existing
row by position, runs no longer create these duplicates; this script is for
rows saved before that change. It finds rows whose sky positions agree to
within `SAME_STAR_POSITION_TOLERANCE_ARCSEC`, keeps one row per star, and
folds the others into it so nothing is lost:

* The survivor is the row with a Henry Draper or Durchmusterung name
  (HD, BD, CD, CPD) if there is one, otherwise the Gaia DR3 row,
  otherwise the first by name.
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

from astrometricslib import Astrometrics
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import (
    SAME_STAR_POSITION_TOLERANCE_ARCSEC,
    catalog_family,
    choose_survivor_id,
)
from astrometricslib.pipelines.shared.star_recording import merge_spectra_history
from astrometricslib.scripts.reconcile_position_only_star_catalog import (
    _backup_catalog_database,
    _merge_duplicate_into_survivor,
)

logger = logging.getLogger(__name__)

# A cluster of more than this many rows is left alone. Four names for one
# star is plausible (HD, Gaia, 2MASS, TYC); more than that is more likely
# a crowded patch where distinct stars chain together.
MAXIMUM_CLUSTER_SIZE = 4

# Per-measurement lists of a light curve. Each is either empty or has one
# entry per timestamp.
_PER_MEASUREMENT_FIELDS = (
    "fluxes",
    "fluxes_normalized",
    "fluxes_detrended",
    "airmasses",
    "magnitudes",
    "is_saturated",
)

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
    chord = 2.0 * np.sin(np.radians(SAME_STAR_POSITION_TOLERANCE_ARCSEC / 3600.0) / 2.0)
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


def merge_light_curves(first: PhotometryResult, second: PhotometryResult) -> PhotometryResult | None:
    """Join two light curves of the same star by measurement time.

    Where both have a measurement at the same time, the first is kept.
    A per-measurement list is kept only when both light curves have it.

    Parameters
    ----------
    first : `PhotometryResult`
        The survivor's light curve.
    second : `PhotometryResult`
        The other row's light curve.

    Returns
    -------
    merged : `PhotometryResult` or `None`
        The joined light curve, or `None` when a list is out of step with
        its timestamps in either curve (pairing values with times would
        then be a guess).
    """
    if not second.timestamps:
        return first
    if not first.timestamps:
        return second
    for light_curve in (first, second):
        for name in _PER_MEASUREMENT_FIELDS:
            values = getattr(light_curve, name)
            if values and len(values) != len(light_curve.timestamps):
                return None

    rows: dict = {}
    for light_curve in (second, first):  # the first is added last, so it wins ties
        for index, timestamp in enumerate(light_curve.timestamps):
            rows[timestamp] = {
                name: (getattr(light_curve, name)[index] if getattr(light_curve, name) else None)
                for name in _PER_MEASUREMENT_FIELDS
            }
    merged = PhotometryResult(timestamps=sorted(rows))
    for name in _PER_MEASUREMENT_FIELDS:
        if getattr(first, name) and getattr(second, name):
            setattr(merged, name, [rows[timestamp][name] for timestamp in merged.timestamps])
    series = merged.fluxes_detrended or merged.fluxes_normalized
    if len(series) >= 2 and float(np.mean(series)) > 0:
        merged.mean_flux = float(np.mean(series))
        merged.coefficient_of_variation = float(np.std(series) / np.mean(series))
    return merged


def merge_cluster(stars: dict[str, StellarObject], survivor_id: str) -> tuple[StellarObject, list[str]] | str:
    """Fold every other row of a cluster into the survivor.

    Parameters
    ----------
    stars : `dict` [`str`, `StellarObject`]
        The cluster's rows, by id.
    survivor_id : `str`
        The id of the row to keep.

    Returns
    -------
    result : `tuple` or `str`
        ``(survivor, removed_ids)`` when the merge is safe, otherwise a
        sentence saying why the cluster was left alone.
    """
    survivor = stars[survivor_id]
    others = [star for star_id, star in stars.items() if star_id != survivor_id]

    # A catalog gives every object its own id, so two ids from the same
    # catalog are two different objects, however close together (a close
    # binary, or neighboring sources in a cluster).
    families = [catalog_family(star_id) for star_id in stars]
    if len(set(families)) < len(families):
        return "two rows come from the same catalog, so they are different objects"

    # Keep the spectrum with the most measured samples, wherever it is held
    # (the survivor may have none at all, and the fill rule below cannot
    # move it: an empty spectrum result still counts as "set"), and join the
    # spectral histories. The survivor is listed first, so it wins a tie.
    spectra_holders = [star for star in [survivor, *others] if star.has_spectra]
    if spectra_holders:
        best_holder = max(spectra_holders, key=lambda star: len(star.spectroscopy.wavelengths_angstrom))
        if best_holder is not survivor and len(best_holder.spectroscopy.wavelengths_angstrom) > len(
            survivor.spectroscopy.wavelengths_angstrom
        ):
            survivor.spectroscopy = best_holder.spectroscopy
        for holder in spectra_holders:
            if holder is not survivor:
                survivor.spectra_history = merge_spectra_history(
                    survivor.spectra_history, holder.spectra_history
                )

    merged_curve = survivor.photometry
    for other in others:
        if merged_curve is None or other.photometry is None:
            continue
        merged_curve = merge_light_curves(merged_curve, other.photometry)
        if merged_curve is None:
            return "the light curves cannot be joined safely"

    for other in others:
        _merge_duplicate_into_survivor(survivor, other)
    if merged_curve is not None:
        survivor.photometry = merged_curve
    return survivor, [star.id for star in others]


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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    astrometrics = Astrometrics()
    clusters = find_duplicate_clusters(astrometrics.catalog_access.list_star_summaries())
    if not clusters:
        print("No duplicate catalog rows found.")
        return 0

    planned: list[tuple[StellarObject, list[str]]] = []
    skipped: list[tuple[list[str], str]] = []
    for ids in clusters:
        if len(ids) > MAXIMUM_CLUSTER_SIZE:
            skipped.append((ids, f"{len(ids)} rows in one cluster"))
            continue
        stars = {star.id: star for star in astrometrics.catalog_access.get_by_ids("stellar_catalog", ids)}
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

    backup_path = _backup_catalog_database(astrometrics)
    if backup_path is None:
        print(
            "\nCould not create a safety backup of the catalog database; aborting without writing anything."
        )
        return 1
    print(f"\nBacked up the catalog database to {backup_path}.")
    for survivor, removed in planned:
        astrometrics.catalog_access.merge_and_record(
            "stellar_catalog", [survivor], lambda _existing, updated: updated
        )
        astrometrics.catalog_access.delete_by_ids("stellar_catalog", removed)
    print(f"Merged {len(planned)} cluster(s), removing {sum(len(removed) for _, removed in planned)} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(run_merge())
