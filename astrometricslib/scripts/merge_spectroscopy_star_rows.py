r"""One-time cleanup: fold ``<id>::spectroscopy`` rows into the star's row.

Older spectroscopy runs saved a star's spectrum as a second row named
``<star id>::spectroscopy``, next to the star's normal row (which holds
its photometry and catalog identity). The same star therefore showed up
twice. New runs save the spectrum straight into the star's own row (see
`spectral_star_registration._apply_matches`); this script fixes the rows
already on disk.

For each ``<id>::spectroscopy`` row this script:

1. Merges its spectrum, trail geometry and spectral history into the
   ``<id>`` row, using the same rule a new spectroscopy run uses
   (`merge_spectroscopy_stellar_object`).
2. Keeps the ``<id>`` row's ``star_data`` position. That is a position in
   the normal image, while the spectroscopy row's ``star_data`` is a
   position in the spectroscopy image, so the spectroscopy position is
   moved to ``spectroscopy.star_position_px`` instead.
3. Deletes the ``<id>::spectroscopy`` row.

If a ``<id>::spectroscopy`` row has no ``<id>`` row to merge into, it is
renamed to ``<id>`` instead, so no spectrum is lost.

Check first, then apply::

    python -m astrometricslib.scripts.merge_spectroscopy_star_rows

    python -m astrometricslib.scripts.merge_spectroscopy_star_rows --apply

``--apply`` copies the catalog database to a timestamped ``.bak`` file
in the same directory before writing anything. Running it again after a
successful run finds nothing to do.
"""

import argparse
import logging
import sys

from astrometricslib import Astrometrics
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.star_recording import merge_spectroscopy_stellar_object
from astrometricslib.scripts.reconcile_position_only_star_catalog import _backup_catalog_database

logger = logging.getLogger(__name__)

SPECTROSCOPY_ROW_ID_SUFFIX = "::spectroscopy"


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering whether to write.
    """
    parser = argparse.ArgumentParser(
        prog="merge_spectroscopy_star_rows",
        description="Fold each '<id>::spectroscopy' stellar catalog row into the star's own '<id>' row.",
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


def find_spectroscopy_row_ids(astrometrics: Astrometrics) -> list[str]:
    """List the ids of every ``<id>::spectroscopy`` row in the catalog.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Provides catalog access.

    Returns
    -------
    spectroscopy_row_ids : `list` [`str`]
        The ids ending in ``::spectroscopy``, sorted.
    """
    return sorted(
        summary.id
        for summary in astrometrics.catalog_access.list_star_summaries()
        if summary.id.endswith(SPECTROSCOPY_ROW_ID_SUFFIX)
    )


def _record_spectroscopy_position(spectral_star: StellarObject) -> None:
    """Move a spectroscopy row's pixel position into its spectroscopy result.

    Parameters
    ----------
    spectral_star : `StellarObject`
        The ``<id>::spectroscopy`` row, changed in place. Its
        ``star_data`` position is in the spectroscopy image, so it is
        copied to ``spectroscopy.star_position_px`` unless that is
        already set.
    """
    if spectral_star.spectroscopy is None or spectral_star.spectroscopy.star_position_px is not None:
        return
    star_data = spectral_star.star_data if isinstance(spectral_star.star_data, dict) else {}
    x_centroid = star_data.get("xcentroid", star_data.get("x_centroid"))
    y_centroid = star_data.get("ycentroid", star_data.get("y_centroid"))
    if x_centroid is not None and y_centroid is not None:
        spectral_star.spectroscopy.star_position_px = [float(x_centroid), float(y_centroid)]


def merge_spectroscopy_rows(astrometrics: Astrometrics, spectroscopy_row_ids: list[str]) -> tuple[int, int]:
    """Fold each spectroscopy row into its star's own row, then delete it.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Provides catalog read and write access.
    spectroscopy_row_ids : `list` [`str`]
        Ids from `find_spectroscopy_row_ids`.

    Returns
    -------
    merged_count : `int`
        Rows merged into an existing ``<id>`` row.
    renamed_count : `int`
        Rows renamed to ``<id>`` because no ``<id>`` row existed.
    """
    merged_count = 0
    renamed_count = 0
    for spectroscopy_row_id in spectroscopy_row_ids:
        base_id = spectroscopy_row_id.removesuffix(SPECTROSCOPY_ROW_ID_SUFFIX)
        hydrated_rows = {
            star.id: star
            for star in astrometrics.catalog_access.get_by_ids(
                "stellar_catalog", [spectroscopy_row_id, base_id]
            )
        }
        spectral_star = hydrated_rows.get(spectroscopy_row_id)
        if spectral_star is None:
            logger.warning("Row %s vanished before it could be merged; skipping.", spectroscopy_row_id)
            continue

        _record_spectroscopy_position(spectral_star)
        base_star = hydrated_rows.get(base_id)
        if base_star is not None:
            surviving_star = merge_spectroscopy_stellar_object(base_star, spectral_star)
            merged_count += 1
        else:
            # Nothing to merge into. The row's `star_data` position is in
            # the spectroscopy image, and the row keeps that position in
            # `star_position_px`, so clear it: `star_data` should only
            # ever hold a normal-image position.
            spectral_star.id = base_id
            spectral_star.star_data = {}
            surviving_star = spectral_star
            renamed_count += 1

        astrometrics.catalog_access.merge_and_record(
            "stellar_catalog", [surviving_star], lambda _existing, updated: updated
        )
        astrometrics.catalog_access.delete_by_ids("stellar_catalog", [spectroscopy_row_id])
    return merged_count, renamed_count


def run_merge(argv: list[str] | None = None) -> int:
    """Report or apply the spectroscopy-row cleanup.

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
    spectroscopy_row_ids = find_spectroscopy_row_ids(astrometrics)

    if not spectroscopy_row_ids:
        print("No '::spectroscopy' rows found; nothing to merge.")
        return 0

    existing_base_ids = {
        star.id
        for star in astrometrics.catalog_access.get_by_ids(
            "stellar_catalog",
            [row_id.removesuffix(SPECTROSCOPY_ROW_ID_SUFFIX) for row_id in spectroscopy_row_ids],
        )
    }
    print(f"{len(spectroscopy_row_ids)} '::spectroscopy' row(s) found:")
    for spectroscopy_row_id in spectroscopy_row_ids:
        base_id = spectroscopy_row_id.removesuffix(SPECTROSCOPY_ROW_ID_SUFFIX)
        action = "merge into" if base_id in existing_base_ids else "rename to"
        print(f"  {spectroscopy_row_id}  ->  {action} {base_id}")

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

    merged_count, renamed_count = merge_spectroscopy_rows(astrometrics, spectroscopy_row_ids)
    print(f"Merged {merged_count} row(s) and renamed {renamed_count} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(run_merge())
