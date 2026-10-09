r"""Look up each stored star in VSX and Gaia DR3 for a variability listing.

SIMBAD's object types say whether a star is listed as variable. Two more
places may list it: the AAVSO Variable Star Index (VSX), which also lists
stars checked and found constant, and Gaia DR3, whose ``phot_variable_flag``
marks stars Gaia's own analysis found variable. This script asks both, through
CDS XMatch, and stores the answers on each star (``vsx_variability_type`` and
``gaia_variable_flag``). Together with SIMBAD they let the library say
"not listed as variable in SIMBAD, Gaia DR3 or VSX", and pick out stars a
catalog positively calls constant.

A star is matched by its position, within 3 arcseconds (see
`pipelines/shared/variable_star_catalogs.py` for the rules). A star with no
stored position is skipped. By default only stars with an identity (a SIMBAD
name or a Gaia number) are looked up; ``--include-position-only`` adds the
stars known only by position, which are far more numerous.

Check first, then apply::

    python -m astrometricslib.scripts.backfill_variable_star_flags --limit 2000

    python -m astrometricslib.scripts.backfill_variable_star_flags --apply

``--apply`` copies the catalog database to a timestamped ``.bak`` file before
writing anything, then saves after every request, so an interrupted run keeps
what it had done. A star that already has both answers is skipped. Only the
two flag fields of each star are changed.
"""

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from astrometricslib import Astrometrics, configure_logging
from astrometricslib.drivers.local_database import backup_catalog_database
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import (
    KnownVariability,
    classify_vsx_type,
    is_confirmed_constant,
)
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.variable_star_catalogs import (
    GAIA_CATALOG,
    VSX_CATALOG,
    XMatchQuery,
    build_position_table,
    gaia_flags_from_matches,
    run_crossmatch,
    vsx_types_from_matches,
)
from astrometricslib.scripts.backfill_simbad_object_types import (
    StarKind,
    kind_of_star_id,
    stored_position_degrees,
)

# How many stars go in one cross-match request. XMatch takes far more; this
# keeps each request to a size that finishes in well under a minute.
BATCH_SIZE = 2000

# Stop after this many requests in a row fail.
CONSECUTIVE_FAILURE_LIMIT = 3

DEFAULT_PAUSE_SECONDS = 1.0


@dataclass
class FlagReport:
    """Everything a run learned.

    Attributes
    ----------
    candidates : `int`
        Stars chosen to look up.
    vsx : `collections.Counter`
        How many stars got each VSX answer, as a `KnownVariability`.
    gaia_flags : `collections.Counter`
        How many stars got each Gaia flag.
    vsx_types : `collections.Counter`
        The VSX types found on variables.
    confirmed_constant : `int`
        Stars a catalog positively calls constant, after all three catalogs.
    failed_requests : `int`
        Requests that could not be completed.
    stopped_early : `bool`
        True if the run gave up because a service seemed to be down.
    saved : `int`
        Stars written (with ``--apply``).
    """

    candidates: int = 0
    vsx: Counter = field(default_factory=Counter)
    gaia_flags: Counter = field(default_factory=Counter)
    vsx_types: Counter = field(default_factory=Counter)
    confirmed_constant: int = 0
    failed_requests: int = 0
    stopped_early: bool = False
    saved: int = 0


def needs_lookup(star: StellarObject, include_position_only: bool) -> bool:
    """Say whether a star should be looked up.

    Parameters
    ----------
    star : `StellarObject`
        The star.
    include_position_only : `bool`
        Whether stars known only by position are looked up too.

    Returns
    -------
    needed : `bool`
        True if the star has a usable position, is lookable by the options,
        and is missing either answer.
    """
    if star.gaia_variable_flag and star.vsx_variability_type:
        return False
    if stored_position_degrees(star) is None:
        return False
    if star.id.endswith("::spectroscopy"):
        return False
    return include_position_only or kind_of_star_id(star.id) is not StarKind.SKIPPED


def star_magnitude(star: StellarObject) -> float | None:
    """Read a star's magnitude as a number.

    Returns
    -------
    magnitude : `float` or `None`
        The magnitude, or `None` if it is not a usable number.
    """
    try:
        magnitude = float(star.magnitude)
    except TypeError, ValueError:
        return None
    return magnitude if magnitude == magnitude else None


def look_up_stars(
    xmatch_query: XMatchQuery, stars: Sequence[StellarObject]
) -> tuple[dict[str, str], dict[str, str]]:
    """Ask VSX and Gaia about a batch of stars.

    Parameters
    ----------
    xmatch_query : `Callable`
        `astroquery.xmatch.XMatch.query`, or a stand-in.
    stars : `Sequence` [`StellarObject`]
        The stars; each must have a stored position.

    Returns
    -------
    vsx_types, gaia_flags : `tuple` [`dict`, `dict`]
        Each star's VSX type and Gaia flag, by id.
    """
    positions = []
    for star in stars:
        position = stored_position_degrees(star)
        if position is not None:
            positions.append((star.id, position[0], position[1]))
    table = build_position_table(positions)
    ids = [row[0] for row in positions]
    vsx = vsx_types_from_matches(ids, run_crossmatch(xmatch_query, table, VSX_CATALOG))
    magnitudes = {star.id: star_magnitude(star) for star in stars}
    gaia = gaia_flags_from_matches(ids, run_crossmatch(xmatch_query, table, GAIA_CATALOG), magnitudes)
    return vsx, gaia


def copy_flags(existing: StellarObject | None, updated: StellarObject) -> StellarObject:
    """Merge rule for saving: change only the two catalog-flag fields.

    Parameters
    ----------
    existing : `StellarObject` or `None`
        The star as saved now.
    updated : `StellarObject`
        The star carrying the new flags.

    Returns
    -------
    merged : `StellarObject`
        The saved star with only its VSX and Gaia fields replaced.
    """
    if existing is None:
        return updated
    existing.vsx_variability_type = updated.vsx_variability_type
    existing.gaia_variable_flag = updated.gaia_variable_flag
    return existing


def run_backfill(
    catalog_access: Any,
    xmatch_query: XMatchQuery,
    *,
    target_id: str | None = None,
    limit: int | None = None,
    include_position_only: bool = False,
    apply: bool = False,
    pause_seconds: float = 0.0,
    make_backup: Callable[[], Any] | None = None,
    log: Callable[[str], None] = print,
) -> FlagReport:
    """Look up and (optionally) save VSX and Gaia answers for stored stars.

    Parameters
    ----------
    catalog_access : `CatalogAccess`
        Lists, loads and saves the catalog's stars.
    xmatch_query : `Callable`
        `astroquery.xmatch.XMatch.query`, or a stand-in.
    target_id : `str`, optional
        Only the stars of this target.
    limit : `int`, optional
        Stop after this many candidate stars.
    include_position_only : `bool`, optional
        Also look up stars known only by position.
    apply : `bool`, optional
        Save the results. Without it nothing is written.
    pause_seconds : `float`, optional
        Wait between requests.
    make_backup : `Callable`, optional
        Makes the safety copy of the catalog database and returns its path,
        or a false value if it could not. Required with `apply`.
    log : `Callable`, optional
        Where progress lines go.

    Returns
    -------
    report : `FlagReport`
        What happened.

    Raises
    ------
    ValueError
        If `apply` is set without `make_backup`.

    Notes
    -----
    A `RuntimeError` from the backup step (the copy could not be made) is
    not caught; nothing is written when it happens.
    """
    if apply and make_backup is None:
        raise ValueError("Saving needs a backup function.")

    report = FlagReport()
    summaries = catalog_access.list_star_summaries(target_id=target_id)
    ids = [summary.id for summary in summaries]
    log(f"{len(ids)} star(s) in the catalog.")

    backed_up = False
    consecutive_failures = 0
    pending: list[StellarObject] = []

    def flush_ready_batches(force: bool) -> bool:
        """Look up the pending stars in full batches, or all if forced.

        Returns
        -------
        keep_going : `bool`
            False if the run should stop (service down).

        Raises
        ------
        RuntimeError
            If the safety backup could not be made before the first write.
        """
        nonlocal backed_up, consecutive_failures
        while len(pending) >= BATCH_SIZE or (force and pending):
            batch = pending[:BATCH_SIZE]
            del pending[:BATCH_SIZE]
            report.candidates += len(batch)
            try:
                vsx, gaia = look_up_stars(xmatch_query, batch)
            except ExternalServiceError, OSError, ValueError, RuntimeError:
                logging.getLogger(__name__).exception("Cross-match request failed")
                report.failed_requests += 1
                consecutive_failures += 1
                log(f"  request failed; {len(batch)} star(s) left for a later run.")
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    log("A catalog service seems to be unreachable; stopping.")
                    report.stopped_early = True
                    return False
                continue
            consecutive_failures = 0
            for star in batch:
                star.vsx_variability_type = vsx[star.id]
                star.gaia_variable_flag = gaia[star.id]
                report.vsx[classify_vsx_type(star.vsx_variability_type)] += 1
                report.gaia_flags[star.gaia_variable_flag] += 1
                if classify_vsx_type(star.vsx_variability_type) is KnownVariability.KNOWN_VARIABLE:
                    report.vsx_types[star.vsx_variability_type] += 1
                if is_confirmed_constant(
                    star.simbad_object_types, star.gaia_variable_flag, star.vsx_variability_type
                ):
                    report.confirmed_constant += 1
            if apply:
                if not backed_up:
                    backup_path = make_backup()
                    if not backup_path:
                        raise RuntimeError("Could not create a safety backup of the catalog database.")
                    log(f"Backed up the catalog database to {backup_path}.")
                    backed_up = True
                catalog_access.merge_and_record("stellar_catalog", batch, copy_flags)
                report.saved += len(batch)
            log(f"  {report.candidates} looked up so far.")
            if pause_seconds:
                time.sleep(pause_seconds)
        return True

    for start in range(0, len(ids), 5000):
        for star in catalog_access.get_by_ids("stellar_catalog", ids[start : start + 5000]):
            if needs_lookup(star, include_position_only):
                if limit is not None and report.candidates + len(pending) >= limit:
                    break
                pending.append(star)
        if not flush_ready_batches(force=False):
            return report
        if limit is not None and report.candidates + len(pending) >= limit:
            break
    flush_ready_batches(force=True)
    return report


def format_report(report: FlagReport, apply: bool) -> str:
    """Write a run's report as text.

    Parameters
    ----------
    report : `FlagReport`
        What happened.
    apply : `bool`
        Whether results were saved.

    Returns
    -------
    text : `str`
        The report.
    """
    lines = [f"\n{report.candidates} star(s) looked up."]
    if report.vsx:
        lines.append("VSX says:")
        for status in KnownVariability:
            if report.vsx[status]:
                lines.append(f"  {status.value:24s} {report.vsx[status]:7d}")
    if report.vsx_types:
        common = ", ".join(f"{name} {count}" for name, count in report.vsx_types.most_common(10))
        lines.append(f"Most common VSX types: {common}")
    if report.gaia_flags:
        lines.append(
            "Gaia DR3 flag: "
            + ", ".join(f"{name} {count}" for name, count in report.gaia_flags.most_common())
        )
    lines.append(
        f"Stars a catalog positively calls constant (none lists them variable): {report.confirmed_constant}"
    )
    if report.failed_requests:
        lines.append(f"{report.failed_requests} request(s) failed; run again to finish those.")
    if report.stopped_early:
        lines.append("The run stopped early because a catalog service seemed to be unreachable.")
    if apply:
        lines.append(f"Saved {report.saved} star(s).")
    else:
        lines.append("Dry run: nothing was written. Re-run with --apply to save these results.")
    return "\n".join(lines)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering whether to write and how much to look up.
    """
    parser = argparse.ArgumentParser(
        prog="backfill_variable_star_flags",
        description="Look up stored stars in VSX and Gaia DR3 for a known variability listing.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the results. Without this only a report is printed."
    )
    parser.add_argument("--target", default=None, help="Only the stars of this target. Default: every star.")
    parser.add_argument("--limit", type=int, default=None, help="Look up at most this many stars.")
    parser.add_argument(
        "--include-position-only",
        action="store_true",
        help="Also look up stars known only by position (far more numerous).",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help=f"Wait between requests. Default: {DEFAULT_PAUSE_SECONDS}.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the backfill from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if `--apply` was requested but the safety
        backup could not be made, ``2`` if a service could not be reached.
    """
    from astroquery.xmatch import XMatch

    arguments = _build_argument_parser().parse_args(argv)
    configure_logging("backfill_variable_star_flags", level=logging.INFO, log_dir="")
    astrometrics = Astrometrics()
    try:
        report = run_backfill(
            astrometrics.catalog_access,
            XMatch.query,
            target_id=arguments.target,
            limit=arguments.limit,
            include_position_only=arguments.include_position_only,
            apply=arguments.apply,
            pause_seconds=arguments.pause_seconds,
            make_backup=lambda: backup_catalog_database(astrometrics.config),
        )
    except RuntimeError as error:
        print(f"\n{error} Nothing was written.")
        return 1
    print(format_report(report, arguments.apply))
    return 2 if report.stopped_early else 0


if __name__ == "__main__":
    sys.exit(main())
