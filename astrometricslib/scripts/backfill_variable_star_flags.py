r"""Look up each stored star in VSX for a variability listing.

SIMBAD's object types say whether a star is listed as variable. Two more
places may list it: the AAVSO Variable Star Index (VSX), which also lists
stars checked and found constant, and Gaia DR3, whose ``phot_variable_flag``
marks stars Gaia's own analysis found variable. This script asks VSX through
VizieR (the Harvard mirror or Strasbourg, as the setting
``[Catalogs] vizier_preferred_server`` says), one request for each field of
sky, and stores the answer on each star (``vsx_variability_type``). It asks
Gaia DR3's variability classification (``I/358/vclassre``) the same way and
matches by Gaia source number, for the stars that have one; the class is stored
as ``VARIABLE:<class>`` in ``gaia_variable_flag`` (see
`pipelines/shared/variable_star_catalogs.py`). Together with SIMBAD they let
the library say
"not listed as variable in SIMBAD, Gaia DR3 or VSX", and pick out stars a
catalog positively calls constant. When neither VizieR server answers, the
star is left unchanged and stays "unverified".

For a star VSX lists as a typed variable it also stores the brightness range
and period VSX gives
(``vsx_bright_magnitude``, ``vsx_faint_magnitude``, ``vsx_amplitude_mag``,
``vsx_period_days`` and ``vsx_magnitude_band``), which the photometry run reads
as the catalog amplitude of its target. Stars saved before these were kept
get them with ``--refresh-vsx-ranges``.

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
what it had done. A star that already has its VSX answer is skipped. Only the
catalog-flag and VSX-range fields of each star are changed.
"""

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from astropy.table import Table, vstack

from astrometricslib import Astrometrics, configure_logging
from astrometricslib._internal.drivers.local_database import backup_catalog_database
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import (
    KnownVariability,
    classify_vsx_type,
    is_confirmed_constant,
)
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import gaia_dr3_source_id_of
from astrometricslib.pipelines.shared.star_kinds import StarKind, kind_of_star_id, stored_position_degrees
from astrometricslib.pipelines.shared.variable_star_catalogs import (
    VsxEntry,
    build_position_table,
    gaia_flags_from_classes,
    group_into_fields,
    look_up_gaia_classes,
    look_up_vsx,
    vsx_entries_from_matches,
    vsx_types_from_matches,
)

# A function that takes the position table of one field and returns the VSX
# match table (`look_up_vsx`), or `None` when nothing matches.
VsxLookup = Callable[[Table], Table | None]

# A function that takes the position table of one field and returns the Gaia
# DR3 variability class of each source in it (`look_up_gaia_classes`).
GaiaLookup = Callable[[Table], dict[int, str]]

# How many stars are looked up before the results are saved. Each batch is
# split into fields, one VizieR request each.
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
        How many stars got each Gaia flag (``VARIABLE`` or `NO_GAIA_MATCH`).
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


def needs_lookup(star: StellarObject, include_position_only: bool, refresh_vsx_ranges: bool = False) -> bool:
    """Say whether a star should be looked up.

    Parameters
    ----------
    star : `StellarObject`
        The star.
    include_position_only : `bool`
        Whether stars known only by position are looked up too.
    refresh_vsx_ranges : `bool`, optional
        Whether a star that VSX lists as a typed variable, and that was saved
        before VSX ranges were kept (`vsx_range_checked` is false), is looked
        up again to get them. A star whose VSX entry has no range is asked
        once, then marked, and not asked again.

    Returns
    -------
    needed : `bool`
        True if the star has a usable position, is lookable by the options,
        and is missing its VSX answer, or its Gaia answer when it has a Gaia
        number (or, with `refresh_vsx_ranges`, its VSX range).
    """
    lacks_range = (
        refresh_vsx_ranges
        and classify_vsx_type(star.vsx_variability_type) is KnownVariability.KNOWN_VARIABLE
        and not star.vsx_range_checked
    )
    lacks_gaia = not star.gaia_variable_flag and gaia_dr3_source_id_of(star) is not None
    if star.vsx_variability_type and not lacks_range and not lacks_gaia:
        return False
    if stored_position_degrees(star) is None:
        return False
    if star.id.endswith("::spectroscopy"):
        return False
    return include_position_only or kind_of_star_id(star.id) is not StarKind.SKIPPED


def look_up_stars(
    stars: Sequence[StellarObject],
    vsx_lookup: VsxLookup = look_up_vsx,
    gaia_lookup: GaiaLookup = look_up_gaia_classes,
) -> tuple[dict[str, str], dict[str, VsxEntry | None], dict[str, str]]:
    """Ask VSX and Gaia DR3 about a batch of stars.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The stars; each must have a stored position.
    vsx_lookup : `Callable`, optional
        Takes the position table of one field and returns the VSX match
        table (`look_up_vsx`), or a stand-in.
    gaia_lookup : `Callable`, optional
        Takes the position table of one field and returns the Gaia variable
        classes by source number (`look_up_gaia_classes`), or a stand-in.

    Returns
    -------
    vsx_types, vsx_entries, gaia_flags : `tuple` [`dict`, `dict`, `dict`]
        Each star's VSX type, VSX brightness range and period (`None` when
        VSX lists no typed variable there), and Gaia flag, by id. A star with
        no Gaia number has no Gaia flag.

    Notes
    -----
    If a field cannot be asked of either VizieR server, the error reaches the
    caller and nothing is returned for the batch, so no star gets a false
    "not in VSX".
    """
    positions = []
    for star in stars:
        position = stored_position_degrees(star)
        if position is not None:
            positions.append((star.id, position[0], position[1]))
    table = build_position_table(positions)
    ids = [row[0] for row in positions]
    matched = []
    classes: dict[int, str] = {}
    for group in group_into_fields(table):
        matches = vsx_lookup(table[group])
        if matches is not None:
            matched.append(matches)
        classes.update(gaia_lookup(table[group]))
    vsx_matches = vstack(matched) if matched else None
    gaia_flags = gaia_flags_from_classes({star.id: gaia_dr3_source_id_of(star) for star in stars}, classes)
    return vsx_types_from_matches(ids, vsx_matches), vsx_entries_from_matches(ids, vsx_matches), gaia_flags


def copy_flags(existing: StellarObject | None, updated: StellarObject) -> StellarObject:
    """Merge rule for saving: change only the flag and VSX-range fields.

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
    existing.vsx_bright_magnitude = updated.vsx_bright_magnitude
    existing.vsx_faint_magnitude = updated.vsx_faint_magnitude
    existing.vsx_amplitude_mag = updated.vsx_amplitude_mag
    existing.vsx_period_days = updated.vsx_period_days
    existing.vsx_magnitude_band = updated.vsx_magnitude_band
    existing.vsx_range_checked = updated.vsx_range_checked
    if updated.gaia_variable_flag:
        existing.gaia_variable_flag = updated.gaia_variable_flag
    return existing


def run_backfill(
    catalog_access: Any,
    vsx_lookup: VsxLookup = look_up_vsx,
    gaia_lookup: GaiaLookup = look_up_gaia_classes,
    *,
    target_id: str | None = None,
    limit: int | None = None,
    include_position_only: bool = False,
    only_with_photometry: bool = False,
    refresh_vsx_ranges: bool = False,
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
    vsx_lookup : `Callable`, optional
        Takes the position table of one field and returns the VSX match
        table (`look_up_vsx`), or a stand-in.
    gaia_lookup : `Callable`, optional
        Takes the position table of one field and returns the Gaia variable
        classes by source number (`look_up_gaia_classes`), or a stand-in.
    target_id : `str`, optional
        Only the stars of this target.
    limit : `int`, optional
        Stop after this many candidate stars.
    include_position_only : `bool`, optional
        Also look up stars known only by position.
    only_with_photometry : `bool`, optional
        Only the stars that have a light curve. Together with
        `include_position_only` this labels the stars the variability cutoff
        is checked on without looking up the far more numerous others.
    refresh_vsx_ranges : `bool`, optional
        Also look up again the stars VSX lists as variable that have no
        brightness range or period saved yet (see `needs_lookup`).
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
    ids = [summary.id for summary in summaries if summary.has_photometry or not only_with_photometry]
    log(f"{len(ids)} star(s) in the catalog" + (" with a light curve." if only_with_photometry else "."))

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
                vsx, vsx_entries, gaia = look_up_stars(batch, vsx_lookup, gaia_lookup)
            except ExternalServiceError, OSError, ValueError, RuntimeError:
                logging.getLogger(__name__).warning(
                    "VSX could not be asked of either VizieR server; these stars stay unverified.",
                    exc_info=True,
                )
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
                entry = vsx_entries.get(star.id)
                star.vsx_bright_magnitude = None if entry is None else entry.bright_magnitude
                star.vsx_faint_magnitude = None if entry is None else entry.faint_magnitude
                star.vsx_amplitude_mag = None if entry is None else entry.amplitude_mag
                star.vsx_period_days = None if entry is None else entry.period_days
                star.vsx_magnitude_band = "" if entry is None else entry.band
                star.vsx_range_checked = True
                if star.id in gaia:
                    star.gaia_variable_flag = gaia[star.id]
                    report.gaia_flags[gaia[star.id].split(":")[0]] += 1
                report.vsx[classify_vsx_type(star.vsx_variability_type)] += 1
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
            if needs_lookup(star, include_position_only, refresh_vsx_ranges):
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
            "Gaia DR3: " + ", ".join(f"{name} {count}" for name, count in report.gaia_flags.most_common())
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
        "--only-with-photometry",
        action="store_true",
        help="Only stars that have a light curve (the ones the variability cutoff check uses).",
    )
    parser.add_argument(
        "--refresh-vsx-ranges",
        action="store_true",
        help="Look up again the stars VSX lists as variable but that have no brightness range saved yet.",
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
    arguments = _build_argument_parser().parse_args(argv)
    configure_logging("backfill_variable_star_flags", level=logging.INFO, log_dir="")
    astrometrics = Astrometrics()
    try:
        report = run_backfill(
            astrometrics._catalog_access,
            target_id=arguments.target,
            limit=arguments.limit,
            include_position_only=arguments.include_position_only,
            only_with_photometry=arguments.only_with_photometry,
            refresh_vsx_ranges=arguments.refresh_vsx_ranges,
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
