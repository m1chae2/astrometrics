r"""Fill in the SIMBAD object type of targets saved before it was recorded.

A target now keeps SIMBAD's main object type code (``simbad_object_type``,
such as "G" for a galaxy or "GlC" for a globular cluster). The planner uses it
to pick the right depth goal. Targets saved earlier have none, and read as
"unknown". This script looks them up and fills them in, without touching any
image.

Targets that never have a fixed sky position (planets, the Moon, the Sun,
comets, asteroids and calibration folders) are skipped, and so is a target
that already has a type, so the script can be stopped and run again.

SIMBAD is asked about 20 targets (about 200 identifiers) per request. The
target's id, its common name and, for an id that names two objects
("M 52 - Bubble Nebula"), its first designation are tried, in that order.
Every answer is checked against the target's stored position, and an answer
more than 15 arcminutes away is rejected, so a name that resolves to a
different object cannot give a target the wrong type. (The star backfill uses
30 arcseconds, but a stored target position is often where the object was
framed, which for a large object is minutes of arc from SIMBAD's center. 15
arcminutes is the library's own position tolerance floor,
`POSITION_TOLERANCE_FLOOR_ARCMIN`.) A target whose position is empty or marked
unverified cannot be checked; an exact name match is then accepted and
reported as ``filled_unchecked``.

Check first, then apply::

    python -m astrometricslib.scripts.backfill_target_object_types

    python -m astrometricslib.scripts.backfill_target_object_types --apply

The first command asks SIMBAD but writes nothing. ``--apply`` copies the
catalog database to a timestamped ``.bak`` file in the same directory before
writing anything. Only the ``simbad_object_type`` field of each target is
changed.
"""

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from astrometricslib import Astrometrics, configure_logging
from astrometricslib._internal.drivers.local_database import backup_catalog_database
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.simbad_object_types import (
    CONSECUTIVE_FAILURE_LIMIT,
    TARGETS_PER_REQUEST,
    index_rows_by_name,
    name_variants,
    names_to_try,
    normalize_name,
    quote_adql,
    separation_arcsec,
)
from astrometricslib.pipelines.shared.target_position import (
    POSITION_TOLERANCE_FLOOR_ARCMIN,
    has_no_fixed_position,
    parse_target_position,
)
from astrometricslib.utilities.target_object_category import target_object_category

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    """What happened to one target."""

    FILLED = "filled"
    FILLED_UNCHECKED = "filled_unchecked"
    NOT_IN_SIMBAD = "not_in_simbad"
    NO_TYPE = "simbad_has_no_type"
    POSITION_MISMATCH = "position_mismatch"
    NO_STORED_POSITION = "no_stored_position"


@dataclass
class BackfillReport:
    """Everything a run learned.

    Attributes
    ----------
    candidates : `int`
        Targets chosen to look up.
    outcomes : `collections.Counter`
        How many targets ended in each `Outcome`.
    decisions : `list` [`tuple`]
        For each target: its id, its `Outcome`, the type code (empty unless
        filled) and the category of that code.
    failed_requests : `int`
        Requests that could not be completed.
    stopped_early : `bool`
        True if the run gave up because the service seemed to be down.
    saved : `int`
        Targets written (with ``--apply``).
    """

    candidates: int = 0
    outcomes: Counter = field(default_factory=Counter)
    decisions: list[tuple[str, Outcome, str, str | None]] = field(default_factory=list)
    failed_requests: int = 0
    stopped_early: bool = False
    saved: int = 0


def build_query(names: Sequence[str]) -> str:
    """Build the ADQL query that fetches the object types of some names.

    Parameters
    ----------
    names : `Sequence` [`str`]
        The names to look up.

    Returns
    -------
    query : `str`
        A query returning ``queried_id``, ``main_id``, ``ra``, ``dec``
        (degrees) and ``otype`` (the main type code).
    """
    id_list = ", ".join(quote_adql(variant) for name in names for variant in name_variants(name))
    # ADQL for a web service, not SQL for a database of ours; every name is
    # passed through `quote_adql`.
    return (
        "SELECT ident.id AS queried_id, basic.main_id AS main_id, basic.ra AS ra, "  # ruff: ignore[hardcoded-sql-expression] -- ADQL, quoted
        "basic.dec AS dec, basic.otype AS otype "
        "FROM ident JOIN basic ON basic.oid = ident.oidref "
        f"WHERE ident.id IN ({id_list})"
    )


def decide_outcome(target: Target, rows: dict[str, list[Any]]) -> tuple[Outcome, str]:
    """Decide what SIMBAD's answer means for one target.

    The target's names are tried in order. Among the rows for the first name
    that has any, the one nearest the target's stored position is used. It is
    accepted if it lies within `POSITION_TOLERANCE_FLOOR_ARCMIN` of that
    position. A target with no usable stored position (empty, or only a frame
    center marked unverified) cannot be checked, so an exact name match is
    accepted without the check and the outcome says so.

    Parameters
    ----------
    target : `Target`
        The target being filled in.
    rows : `dict` [`str`, `list` [`astropy.table.Row`]]
        SIMBAD's rows by normalized name.

    Returns
    -------
    outcome : `Outcome`
        What happened.
    object_type : `str`
        The code to save; empty unless the outcome is ``FILLED`` or
        ``FILLED_UNCHECKED``.
    """
    candidates = next((rows[key] for key in map(normalize_name, names_to_try(target)) if key in rows), None)
    if candidates is None:
        return Outcome.NOT_IN_SIMBAD, ""
    stored = parse_target_position(target)
    checkable = stored is not None and not target.position_unverified
    chosen = candidates[0]
    if checkable:
        stored_degrees = (float(stored.ra.deg), float(stored.dec.deg))

        def separation(row: Any) -> float:
            """Measure how far a row lies from the stored position.

            Returns
            -------
            separation : `float`
                Arcseconds, or infinity for a row with no usable position.
            """
            try:
                return separation_arcsec(stored_degrees, (float(row["ra"]), float(row["dec"])))
            except TypeError, ValueError, KeyError:
                return float("inf")

        chosen = min(candidates, key=separation)
        if separation(chosen) > resolve(POSITION_TOLERANCE_FLOOR_ARCMIN).value * 60.0:
            return Outcome.POSITION_MISMATCH, ""
    value = chosen["otype"]
    if getattr(value, "mask", False) or not str(value).strip():
        return Outcome.NO_TYPE, ""
    return (Outcome.FILLED if checkable else Outcome.FILLED_UNCHECKED), str(value).strip()


def select_targets_to_look_up(targets: Sequence[Target]) -> list[Target]:
    """Choose the targets that need their object type looked up.

    Parameters
    ----------
    targets : `Sequence` [`Target`]
        Targets loaded from the catalog.

    Returns
    -------
    chosen : `list` [`Target`]
        The targets with no type yet that are fixed sky objects.
    """
    return [
        target
        for target in targets
        if not target.simbad_object_type and not has_no_fixed_position(target.id) and target.id
    ]


def copy_object_type(existing: Target | None, updated: Target) -> Target:
    """Merge rule for saving: change only the object type of the saved target.

    Parameters
    ----------
    existing : `Target` or `None`
        The target as saved now.
    updated : `Target`
        The target carrying the new object type.

    Returns
    -------
    merged : `Target`
        The saved target with only its object type replaced, so anything a
        pipeline wrote since this run loaded the target is kept.
    """
    if existing is None:
        return updated
    existing.simbad_object_type = updated.simbad_object_type
    return existing


def run_backfill(
    catalog_access: Any,
    simbad_driver: Any,
    *,
    apply: bool = False,
    make_backup: Callable[[], Any] | None = None,
    log: Callable[[str], None] = print,
) -> BackfillReport:
    """Look up, and optionally save, the object types of targets lacking them.

    Parameters
    ----------
    catalog_access : `CatalogAccess`
        Loads and saves the catalog's targets.
    simbad_driver : `SimbadDriver`
        The SIMBAD driver.
    apply : `bool`, optional
        Save the results. Without it nothing is written.
    make_backup : `Callable`, optional
        Makes the safety copy of the catalog database and returns its path, or
        a false value if it could not. Required with `apply`.
    log : `Callable`, optional
        Where progress lines go.

    Returns
    -------
    report : `BackfillReport`
        What happened. A SIMBAD failure ends the run early (``stopped_early``)
        after `CONSECUTIVE_FAILURE_LIMIT` failed requests in a row.

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
    chosen = select_targets_to_look_up(catalog_access.get("target_catalog", {}) or [])
    report.candidates = len(chosen)
    log(f"{len(chosen)} target(s) to look up.")
    consecutive_failures = 0
    for start in range(0, len(chosen), TARGETS_PER_REQUEST):
        batch = chosen[start : start + TARGETS_PER_REQUEST]
        names = [name for target in batch for name in names_to_try(target)]
        try:
            rows = index_rows_by_name(simbad_driver.query_tap(build_query(names)))
        except ExternalServiceError as error:
            report.failed_requests += 1
            consecutive_failures += 1
            log(f"  request failed ({error}); {len(batch)} target(s) left for a later run.")
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                log("SIMBAD seems to be unreachable; stopping.")
                report.stopped_early = True
                return report
            continue
        consecutive_failures = 0
        to_save = []
        for target in batch:
            outcome, object_type = decide_outcome(target, rows)
            report.outcomes[outcome] += 1
            report.decisions.append((target.id, outcome, object_type, target_object_category(object_type)))
            if outcome in (Outcome.FILLED, Outcome.FILLED_UNCHECKED):
                target.simbad_object_type = object_type
                to_save.append(target)
        if apply and to_save:
            backup_path = make_backup() if report.saved == 0 else True
            if not backup_path:
                raise RuntimeError("Could not create a safety backup of the catalog database.")
            if report.saved == 0:
                log(f"Backed up the catalog database to {backup_path}.")
            catalog_access.merge_and_record("target_catalog", to_save, copy_object_type)
            report.saved += len(to_save)
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
        The report, with one line per target.
    """
    lines = [f"\n{report.candidates} target(s) looked up."]
    for target_id, outcome, object_type, category in report.decisions:
        lines.append(f"  {target_id:36s} {outcome.value:20s} {object_type or '-':6s} {category or '-'}")
    for outcome in Outcome:
        if report.outcomes[outcome]:
            lines.append(f"  {outcome.value:22s} {report.outcomes[outcome]:7d}")
    if report.failed_requests:
        lines.append(f"{report.failed_requests} request(s) failed; run again to finish those.")
    if report.stopped_early:
        lines.append("The run stopped early because SIMBAD seemed to be unreachable.")
    if apply:
        lines.append(f"Saved {report.saved} target(s).")
    else:
        lines.append("Dry run: nothing was written. Re-run with --apply to save these results.")
    return "\n".join(lines)


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
    from astrometricslib._internal.drivers.astroquery_simbad_driver import AstroquerySimbadDriver

    parser = argparse.ArgumentParser(
        prog="backfill_target_object_types",
        description="Fill in the SIMBAD object type of targets saved before it was recorded.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the results. Without this only a report is printed."
    )
    arguments = parser.parse_args(argv)
    configure_logging("backfill_target_object_types", level=logging.INFO, log_dir="")
    astrometrics = Astrometrics()
    try:
        report = run_backfill(
            astrometrics._catalog_access,
            AstroquerySimbadDriver(),
            apply=arguments.apply,
            make_backup=lambda: backup_catalog_database(astrometrics.config),
        )
    except RuntimeError as error:
        print(f"\n{error} Nothing was written.")
        return 1
    print(format_report(report, arguments.apply))
    return 2 if report.stopped_early else 0


if __name__ == "__main__":
    sys.exit(main())
