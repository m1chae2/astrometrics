r"""Fill in the SIMBAD size of targets saved before it was recorded.

A target now keeps SIMBAD's major axis (``major_axis_arcmin``), which a run
records as its target-size basis (issue #175), so the values sized to the
target follow it. Targets saved earlier have none. This script looks them up
and fills them in, without touching any image.

It asks SIMBAD the same way as `backfill_target_object_types` (about 20
targets per request, the same names in the same order) and checks every
answer against the target's stored position the same way: an answer more
than `POSITION_TOLERANCE_FLOOR_ARCMIN` away is rejected. A target SIMBAD gives
no size for (a star, a planet, a comet) keeps none, so its sized values keep
their reference-setup sizes. Targets with no fixed sky position are skipped,
and so is a target that already has a size, so the script can be run again.

Check first, then apply::

    python -m astrometricslib.scripts.backfill_target_sizes
    python -m astrometricslib.scripts.backfill_target_sizes --apply

The first command asks SIMBAD but writes nothing. ``--apply`` copies the
catalog database to a timestamped ``.bak`` file first. Only the
``major_axis_arcmin`` field of each target is changed.
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

logger = logging.getLogger(__name__)


class SizeOutcome(StrEnum):
    """What happened to one target."""

    FILLED = "filled"
    FILLED_UNCHECKED = "filled_unchecked"
    NOT_IN_SIMBAD = "not_in_simbad"
    NO_SIZE = "simbad_has_no_size"
    POSITION_MISMATCH = "position_mismatch"


@dataclass
class SizeReport:
    """Everything a run learned.

    Attributes
    ----------
    candidates : `int`
        Targets chosen to look up.
    outcomes : `collections.Counter`
        How many targets ended in each `SizeOutcome`.
    decisions : `list` [`tuple`]
        For each target: its id, its outcome and the size in arcminutes (0
        unless filled).
    failed_requests : `int`
        Requests that could not be completed.
    stopped_early : `bool`
        True if the run gave up because the service seemed to be down.
    saved : `int`
        Targets written.
    """

    candidates: int = 0
    outcomes: Counter = field(default_factory=Counter)
    decisions: list[tuple[str, SizeOutcome, float]] = field(default_factory=list)
    failed_requests: int = 0
    stopped_early: bool = False
    saved: int = 0


def build_size_query(names: Sequence[str]) -> str:
    """Build the ADQL query that fetches the sizes of some names.

    Parameters
    ----------
    names : `Sequence` [`str`]
        The names to look up.

    Returns
    -------
    query : `str`
        A query returning ``queried_id``, ``ra``, ``dec`` (degrees) and
        ``major_axis`` (``galdim_majaxis``, arcminutes).
    """
    id_list = ", ".join(quote_adql(variant) for name in names for variant in name_variants(name))
    # ADQL for a web service, not SQL for a database of ours; every name is
    # passed through `quote_adql`.
    return (
        "SELECT ident.id AS queried_id, basic.ra AS ra, basic.dec AS dec, "  # ruff: ignore[hardcoded-sql-expression] -- ADQL, quoted
        "basic.galdim_majaxis AS major_axis "
        "FROM ident JOIN basic ON basic.oid = ident.oidref "
        f"WHERE ident.id IN ({id_list})"
    )


def decide_size(target: Target, rows: dict[str, list[Any]]) -> tuple[SizeOutcome, float]:
    """Decide what SIMBAD's answer gives one target as its size.

    The rows of the target's first name that SIMBAD knows are used; the one
    nearest the stored position is chosen and must lie within
    `POSITION_TOLERANCE_FLOOR_ARCMIN` of it. A target with no usable stored
    position takes an exact name match unchecked.

    Parameters
    ----------
    target : `Target`
        The target being filled in.
    rows : `dict` [`str`, `list` [`astropy.table.Row`]]
        SIMBAD's rows by normalized name.

    Returns
    -------
    outcome : `SizeOutcome`
        What happened.
    major_axis_arcmin : `float`
        The size to save; 0 unless the outcome is ``FILLED`` or
        ``FILLED_UNCHECKED``.
    """
    candidates = next((rows[key] for key in map(normalize_name, names_to_try(target)) if key in rows), None)
    if candidates is None:
        return SizeOutcome.NOT_IN_SIMBAD, 0.0
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
        if separation(chosen) > float(resolve(POSITION_TOLERANCE_FLOOR_ARCMIN).value) * 60.0:
            return SizeOutcome.POSITION_MISMATCH, 0.0
    value = chosen["major_axis"]
    try:
        size = 0.0 if getattr(value, "mask", False) else float(value)
    except TypeError, ValueError:
        size = 0.0
    if not size > 0.0:
        return SizeOutcome.NO_SIZE, 0.0
    return (SizeOutcome.FILLED if checkable else SizeOutcome.FILLED_UNCHECKED), size


def copy_size(existing: Target | None, updated: Target) -> Target:
    """Merge rule for saving: change only the size of the saved target.

    Parameters
    ----------
    existing : `Target` or `None`
        The target as saved now.
    updated : `Target`
        The target carrying the new size.

    Returns
    -------
    merged : `Target`
        The saved target with only its size replaced.
    """
    if existing is None:
        return updated
    existing.major_axis_arcmin = updated.major_axis_arcmin
    return existing


def run_size_backfill(
    catalog_access: Any,
    simbad_driver: Any,
    *,
    apply: bool = False,
    make_backup: Callable[[], Any] | None = None,
    log: Callable[[str], None] = print,
) -> SizeReport:
    """Look up, and optionally save, the sizes of targets lacking them.

    Parameters
    ----------
    catalog_access : `Any`
        The catalog access, holding ``target_catalog``.
    simbad_driver : `Any`
        The SIMBAD driver (``query_tap``).
    apply : `bool`, optional
        Save the sizes, after one backup.
    make_backup : `Callable`, optional
        Makes the backup; required with ``apply``.
    log : `Callable`, optional
        Where progress lines go.

    Returns
    -------
    report : `SizeReport`
        What happened.

    Raises
    ------
    ValueError
        If ``apply`` is set without ``make_backup``.
    RuntimeError
        If the backup could not be made; nothing is written then.
    """
    if apply and make_backup is None:
        raise ValueError("Saving needs a backup function.")
    report = SizeReport()
    chosen = [
        target
        for target in (catalog_access.get("target_catalog", {}) or [])
        if target.id and not target.major_axis_arcmin and not has_no_fixed_position(target.id)
    ]
    report.candidates = len(chosen)
    log(f"{len(chosen)} target(s) to look up.")
    consecutive_failures = 0
    for start in range(0, len(chosen), TARGETS_PER_REQUEST):
        batch = chosen[start : start + TARGETS_PER_REQUEST]
        names = [name for target in batch for name in names_to_try(target)]
        try:
            rows = index_rows_by_name(simbad_driver.query_tap(build_size_query(names)))
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
            outcome, size = decide_size(target, rows)
            report.outcomes[outcome] += 1
            report.decisions.append((target.id, outcome, size))
            if outcome in (SizeOutcome.FILLED, SizeOutcome.FILLED_UNCHECKED):
                target.major_axis_arcmin = size
                to_save.append(target)
        if apply and to_save:
            backup_path = make_backup() if report.saved == 0 else True
            if not backup_path:
                raise RuntimeError("Could not create a safety backup of the catalog database.")
            if report.saved == 0:
                log(f"Backed up the catalog database to {backup_path}.")
            catalog_access.merge_and_record("target_catalog", to_save, copy_size)
            report.saved += len(to_save)
    return report


def format_size_report(report: SizeReport, apply: bool) -> str:
    """Write the report as text.

    Parameters
    ----------
    report : `SizeReport`
        What happened.
    apply : `bool`
        Whether the run saved.

    Returns
    -------
    text : `str`
        One line per target, then the counts.
    """
    lines = [
        f"  {target_id}: {outcome}" + (f" ({size:g} arcmin)" if size else "")
        for target_id, outcome, size in report.decisions
    ]
    counts = ", ".join(f"{outcome}: {count}" for outcome, count in sorted(report.outcomes.items()))
    lines.append(f"{report.candidates} looked up; {counts or 'nothing to do'}.")
    lines.append(f"Saved {report.saved}." if apply else "Nothing written; add --apply to save.")
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
        0 on success, 1 if the safety backup failed, 2 if SIMBAD could not be
        reached.
    """
    from astrometricslib._internal.drivers.astroquery_simbad_driver import AstroquerySimbadDriver

    parser = argparse.ArgumentParser(
        prog="backfill_target_sizes",
        description="Fill in the SIMBAD size of targets saved before it was recorded.",
    )
    parser.add_argument("--apply", action="store_true", help="Write the results.")
    arguments = parser.parse_args(argv)
    configure_logging("backfill_target_sizes", level=logging.INFO, log_dir="")
    astrometrics = Astrometrics()
    try:
        report = run_size_backfill(
            astrometrics._catalog_access,
            AstroquerySimbadDriver(),
            apply=arguments.apply,
            make_backup=lambda: backup_catalog_database(astrometrics.config),
        )
    except RuntimeError as error:
        print(f"\n{error} Nothing was written.")
        return 1
    print(format_size_report(report, arguments.apply))
    return 2 if report.stopped_early else 0


if __name__ == "__main__":
    sys.exit(main())
