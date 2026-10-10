r"""Fill in the SIMBAD object types of stars saved before they were recorded.

The astrometry pipeline now keeps every object type SIMBAD lists for a star
(``simbad_object_types``), which is what lets the library say whether a star
is already listed as a variable. Stars saved earlier have none, and read as
"unknown". This script looks them up and fills them in, without touching any
image.

Only stars that have an identity SIMBAD can resolve are looked up:

- stars named by a SIMBAD main identifier (``* alf CMa``, ``HD 150998``);
- stars named by a Gaia DR3 source number (``Gaia DR3 2081900940499099136``).

Stars known only by position (``FIELD_J...``), Gaia stars with a made-up
position id (``Gaia DR3 J...``) and the extra ``::spectroscopy`` copies are
skipped. A star that already has object types is left alone, so the script
can be stopped and run again.

SIMBAD is asked about about 200 identifiers per request, not one star at a
time. Every answer is checked against the star's stored position, and an
answer more than 30 arcseconds away is rejected, so a name that resolves to
a different object cannot give a star the wrong types.

Check first, then apply::

    python -m astrometricslib.scripts.backfill_simbad_object_types --limit 400

    python -m astrometricslib.scripts.backfill_simbad_object_types --apply

The first command asks SIMBAD but writes nothing. ``--target`` limits the run
to the stars of one target, and ``--limit`` to the first N candidate stars.

``--apply`` copies the catalog database to a timestamped ``.bak`` file in the
same directory before writing anything, then saves after every request, so an
interrupted run keeps what it had done. Only the ``simbad_object_types`` field
of each star is changed.
"""

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib import Astrometrics, configure_logging
from astrometricslib.drivers.local_database import backup_catalog_database
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import (
    KnownVariability,
    classify_simbad_object_types,
    variable_object_types_in,
)
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.simbad_object_types import read_simbad_object_types
from astrometricslib.pipelines.shared.star_kinds import StarKind, kind_of_star_id, stored_position_degrees

logger = logging.getLogger(__name__)

# How many identifiers go in one SIMBAD request.
BATCH_SIZE = 200

# The farthest, in arcseconds, a SIMBAD answer may lie from the star's stored
# position. The identifier already says which object is meant, so this only
# catches a name that resolved to something else. 30 arcseconds is wide
# enough for the position change of a fast-moving star over several decades
# (the fastest known move about 10 arcseconds a year, a few move 1) and
# narrow enough to reject a different star.
MAXIMUM_POSITION_DIFFERENCE_ARCSEC = 30.0

# Stop after this many requests in a row fail, so a run does not keep
# asking a service that is down.
CONSECUTIVE_FAILURE_LIMIT = 3

# Pause between requests, in seconds, to stay polite to the service.
DEFAULT_PAUSE_SECONDS = 0.5


class Outcome(StrEnum):
    """What happened to one star."""

    FILLED = "filled"
    NOT_IN_SIMBAD = "not_in_simbad"
    NO_TYPES = "simbad_has_no_types"
    POSITION_MISMATCH = "position_mismatch"
    NO_STORED_POSITION = "no_stored_position"


@dataclass
class BackfillReport:
    """Everything a run learned.

    Attributes
    ----------
    candidates : `int`
        Stars chosen to look up (an identifiable id and no types yet).
    outcomes : `collections.Counter`
        How many stars ended in each `Outcome`.
    statuses : `collections.Counter`
        For the stars that were filled, how many fall in each
        `KnownVariability` answer.
    variable_codes : `collections.Counter`
        For the stars that were filled, how often each variable or candidate
        variable code appeared, so a reader can see what made them variable.
    failed_requests : `int`
        Requests that could not be completed.
    stopped_early : `bool`
        True if the run gave up because the service seemed to be down.
    saved : `int`
        Stars written (with ``--apply``).
    """

    candidates: int = 0
    outcomes: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    variable_codes: Counter = field(default_factory=Counter)
    failed_requests: int = 0
    stopped_early: bool = False
    saved: int = 0


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


def build_query(kind: StarKind, identifiers: Sequence[str]) -> str:
    """Build the ADQL query that fetches the object types of some stars.

    Parameters
    ----------
    kind : `StarKind`
        ``SIMBAD_NAME`` matches SIMBAD's main identifier; ``GAIA_SOURCE``
        matches any identifier, which is how SIMBAD stores Gaia numbers.
    identifiers : `Sequence` [`str`]
        The ids to look up.

    Returns
    -------
    query : `str`
        A query returning ``queried_id``, ``main_id``, ``ra``, ``dec``
        (degrees) and ``otypes`` (the object types joined with ``|``).

    Raises
    ------
    ValueError
        If `kind` is ``SKIPPED``.
    """
    id_list = ", ".join(quote_adql(identifier) for identifier in identifiers)
    # ADQL for a web service, not SQL for a database of ours; every identifier
    # is passed through `quote_adql`.
    if kind is StarKind.SIMBAD_NAME:
        return (
            "SELECT basic.main_id AS queried_id, basic.main_id AS main_id, basic.ra AS ra, "  # ruff: ignore[hardcoded-sql-expression] -- ADQL, quoted
            "basic.dec AS dec, alltypes.otypes AS otypes "
            "FROM basic LEFT JOIN alltypes ON alltypes.oidref = basic.oid "
            f"WHERE basic.main_id IN ({id_list})"
        )
    if kind is StarKind.GAIA_SOURCE:
        return (
            "SELECT ident.id AS queried_id, basic.main_id AS main_id, basic.ra AS ra, "  # ruff: ignore[hardcoded-sql-expression] -- ADQL, quoted
            "basic.dec AS dec, alltypes.otypes AS otypes "
            "FROM ident JOIN basic ON basic.oid = ident.oidref "
            "LEFT JOIN alltypes ON alltypes.oidref = basic.oid "
            f"WHERE ident.id IN ({id_list})"
        )
    raise ValueError(f"Cannot build a query for a {kind.value} star.")


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


def decide_outcome(star: StellarObject, row: Any | None) -> tuple[Outcome, str]:
    """Decide what a SIMBAD answer means for one star.

    Parameters
    ----------
    star : `StellarObject`
        The star being filled in.
    row : `astropy.table.Row` or `None`
        SIMBAD's row for the star, or `None` if SIMBAD had no such object.

    Returns
    -------
    outcome : `Outcome`
        What happened.
    object_types : `str`
        The types to save; empty unless the outcome is ``FILLED``.
    """
    if row is None:
        return Outcome.NOT_IN_SIMBAD, ""
    stored = stored_position_degrees(star)
    if stored is None:
        return Outcome.NO_STORED_POSITION, ""
    try:
        answer = (float(row["ra"]), float(row["dec"]))
    except TypeError, ValueError, KeyError:
        return Outcome.POSITION_MISMATCH, ""
    if separation_arcsec(stored, answer) > MAXIMUM_POSITION_DIFFERENCE_ARCSEC:
        return Outcome.POSITION_MISMATCH, ""
    object_types = read_simbad_object_types(row)
    if not object_types:
        return Outcome.NO_TYPES, ""
    return Outcome.FILLED, object_types


def select_stars_to_look_up(stars: Iterable[StellarObject]) -> dict[StarKind, list[StellarObject]]:
    """Choose the stars that need their object types looked up.

    Parameters
    ----------
    stars : `Iterable` [`StellarObject`]
        Stars loaded from the catalog.

    Returns
    -------
    chosen : `dict` [`StarKind`, `list` [`StellarObject`]]
        The stars with no types yet and a lookable id, by kind.
    """
    chosen: dict[StarKind, list[StellarObject]] = {StarKind.SIMBAD_NAME: [], StarKind.GAIA_SOURCE: []}
    for star in stars:
        if star.simbad_object_types:
            continue
        kind = kind_of_star_id(star.id)
        if kind is not StarKind.SKIPPED:
            chosen[kind].append(star)
    return chosen


def index_rows_by_queried_id(table: Any) -> dict[str, Any]:
    """Index a SIMBAD answer by the identifier that was asked about.

    Parameters
    ----------
    table : `astropy.table.Table` or `None`
        SIMBAD's answer.

    Returns
    -------
    rows : `dict` [`str`, `astropy.table.Row`]
        Each row under its ``queried_id``. Empty for no answer.
    """
    if table is None:
        return {}
    return {str(row["queried_id"]).strip(): row for row in table}


def look_up_batch(
    simbad_driver: Any, kind: StarKind, batch: Sequence[StellarObject]
) -> list[tuple[StellarObject, Outcome, str]]:
    """Ask SIMBAD about one batch of stars and decide each outcome.

    Parameters
    ----------
    simbad_driver : `SimbadDriver`
        The SIMBAD driver.
    kind : `StarKind`
        How the batch's ids are matched.
    batch : `Sequence` [`StellarObject`]
        The stars to look up.

    Returns
    -------
    decisions : `list` [`tuple`]
        For each star: the star, its `Outcome`, and the types to save.

    Notes
    -----
    An `ExternalServiceError` from the driver is not caught here; the caller
    decides what an unreachable service means for the run.
    """
    table = simbad_driver.query_tap(build_query(kind, [star.id for star in batch]))
    rows = index_rows_by_queried_id(table)
    decisions = []
    for star in batch:
        outcome, object_types = decide_outcome(star, rows.get(star.id))
        decisions.append((star, outcome, object_types))
    return decisions


def copy_object_types(existing: StellarObject | None, updated: StellarObject) -> StellarObject:
    """Merge rule for saving: change only the object types of the saved star.

    Parameters
    ----------
    existing : `StellarObject` or `None`
        The star as saved now.
    updated : `StellarObject`
        The star carrying the new object types.

    Returns
    -------
    merged : `StellarObject`
        The saved star with only its object types replaced, so anything a
        pipeline wrote since this run loaded the star is kept.
    """
    if existing is None:
        return updated
    existing.simbad_object_types = updated.simbad_object_types
    return existing


def run_backfill(
    catalog_access: Any,
    simbad_driver: Any,
    *,
    target_id: str | None = None,
    limit: int | None = None,
    apply: bool = False,
    pause_seconds: float = 0.0,
    make_backup: Callable[[], Any] | None = None,
    log: Callable[[str], None] = print,
) -> BackfillReport:
    """Look up and (optionally) save the object types of stars that lack them.

    Parameters
    ----------
    catalog_access : `CatalogAccess`
        Lists, loads and saves the catalog's stars.
    simbad_driver : `SimbadDriver`
        The SIMBAD driver.
    target_id : `str`, optional
        Only the stars of this target.
    limit : `int`, optional
        Stop after this many candidate stars.
    apply : `bool`, optional
        Save the results. Without it nothing is written.
    pause_seconds : `float`, optional
        Wait between SIMBAD requests.
    make_backup : `Callable`, optional
        Makes the safety copy of the catalog database and returns its path,
        or a false value if it could not. Required with `apply`.
    log : `Callable`, optional
        Where progress lines go.

    Returns
    -------
    report : `BackfillReport`
        What happened.

    Raises
    ------
    ValueError
        If `apply` is set without `make_backup`.
    RuntimeError
        If the backup could not be made; nothing is written then.
    """
    if apply and make_backup is None:
        raise ValueError("Saving needs a backup function.")

    report = BackfillReport()
    summaries = catalog_access.list_star_summaries(target_id=target_id)
    ids = [summary.id for summary in summaries if kind_of_star_id(summary.id) is not StarKind.SKIPPED]
    log(f"{len(summaries)} star(s) in the catalog, {len(ids)} with an id SIMBAD can resolve.")

    backed_up = False
    consecutive_failures = 0
    looked_at = 0
    for start in range(0, len(ids), BATCH_SIZE * 5):
        if limit is not None and looked_at >= limit:
            break
        stars = catalog_access.get_by_ids("stellar_catalog", ids[start : start + BATCH_SIZE * 5])
        chosen = select_stars_to_look_up(stars)
        for kind, kind_stars in chosen.items():
            for batch_start in range(0, len(kind_stars), BATCH_SIZE):
                if limit is not None and looked_at >= limit:
                    break
                batch = kind_stars[batch_start : batch_start + BATCH_SIZE]
                if limit is not None:
                    batch = batch[: limit - looked_at]
                looked_at += len(batch)
                report.candidates += len(batch)
                try:
                    decisions = look_up_batch(simbad_driver, kind, batch)
                except ExternalServiceError as error:
                    report.failed_requests += 1
                    consecutive_failures += 1
                    log(f"  request failed ({error}); {len(batch)} star(s) left for a later run.")
                    if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                        log("SIMBAD seems to be unreachable; stopping.")
                        report.stopped_early = True
                        return report
                    continue
                consecutive_failures = 0

                to_save = []
                for star, outcome, object_types in decisions:
                    report.outcomes[outcome] += 1
                    if outcome is Outcome.FILLED:
                        star.simbad_object_types = object_types
                        report.statuses[classify_simbad_object_types(object_types)] += 1
                        report.variable_codes.update(set(variable_object_types_in(object_types)))
                        to_save.append(star)
                if apply and to_save:
                    if not backed_up:
                        backup_path = make_backup()
                        if not backup_path:
                            raise RuntimeError("Could not create a safety backup of the catalog database.")
                        log(f"Backed up the catalog database to {backup_path}.")
                        backed_up = True
                    catalog_access.merge_and_record("stellar_catalog", to_save, copy_object_types)
                    report.saved += len(to_save)
                log(f"  {looked_at} looked at, {report.outcomes[Outcome.FILLED]} filled so far.")
                if pause_seconds:
                    time.sleep(pause_seconds)
    return report


def format_report(report: BackfillReport, apply: bool) -> str:
    """Write a run's report as text.

    Parameters
    ----------
    report : `BackfillReport`
        What happened.
    apply : `bool`
        Whether results were saved.

    Returns
    -------
    text : `str`
        The report.
    """
    lines = [f"\n{report.candidates} star(s) looked up."]
    for outcome in Outcome:
        if report.outcomes[outcome]:
            lines.append(f"  {outcome.value:22s} {report.outcomes[outcome]:7d}")
    if report.statuses:
        lines.append("Of the stars that were filled in:")
        for status in KnownVariability:
            lines.append(f"  {status.value:24s} {report.statuses[status]:7d}")
    if report.variable_codes:
        common = ", ".join(f"{code} {count}" for code, count in report.variable_codes.most_common(10))
        lines.append(f"Most common variable types among them: {common}")
    if report.failed_requests:
        lines.append(f"{report.failed_requests} request(s) failed; run again to finish those.")
    if report.stopped_early:
        lines.append("The run stopped early because SIMBAD seemed to be unreachable.")
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
        prog="backfill_simbad_object_types",
        description="Fill in the SIMBAD object types of stars saved before they were recorded.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the results. Without this only a report is printed."
    )
    parser.add_argument("--target", default=None, help="Only the stars of this target. Default: every star.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Look up at most this many stars. Default: all of them."
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help=f"Wait between SIMBAD requests. Default: {DEFAULT_PAUSE_SECONDS}.",
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
        backup could not be made, ``2`` if SIMBAD could not be reached.
    """
    from astrometricslib.drivers.astroquery_simbad_driver import AstroquerySimbadDriver

    arguments = _build_argument_parser().parse_args(argv)
    configure_logging("backfill_simbad_object_types", level=logging.INFO, log_dir="")
    astrometrics = Astrometrics()
    try:
        report = run_backfill(
            astrometrics.catalog_access,
            AstroquerySimbadDriver(),
            target_id=arguments.target,
            limit=arguments.limit,
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
