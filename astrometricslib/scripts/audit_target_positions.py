r"""List targets whose stored position is far from their catalog position.

A target's RA/Dec should be where the object is. An older rule copied the
center of the first solved image instead, which is wrong when that image was
offset on purpose (a spectroscopy frame) or badly centered. This script
looks up every target's name in SIMBAD and compares the answer with the
stored position::

    python -m astrometricslib.scripts.audit_target_positions [DATABASE]

It is read-only. It opens the database in SQLite's read-only mode, so it
cannot change a record, and it fixes nothing. ``DATABASE`` is the path of
``astrometrics.db``; without it the configured library's database is used.

Tolerance. A target is listed when the two positions are more than
``--tolerance-arcmin`` apart. The default is 15 arcminutes. That is a quarter
of a one-degree field of view: an object that far from the stored point is
still in frame, but a plate-solve hint or a framing center that wrong is worth
a look. It is well below the 38 arcminute error that prompted this audit.
Large objects (the Andromeda Galaxy spans about 3 degrees) are framed on
purpose away from their SIMBAD center, so a listed extended object needs a
human judgment, not an automatic fix.
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.pipelines.shared.target_position import (
    format_position,
    parse_position_text,
    resolve_catalog_position,
)

# Targets whose stored position is further than this from the catalog's are
# listed. See the module docstring for the reasoning.
DEFAULT_TOLERANCE_ARCMIN = 15.0


@dataclass(frozen=True)
class StoredTarget:
    """One target's stored name and position, as read from the database."""

    target_id: str
    common_name: str
    ra: str
    dec: str
    position_unverified: bool


@dataclass(frozen=True)
class PositionFinding:
    """The comparison for one target.

    ``separation_arcmin`` is `None` when no comparison was possible;
    ``status`` then says why.
    """

    target_id: str
    stored_ra: str
    stored_dec: str
    catalog_ra: str
    catalog_dec: str
    separation_arcmin: float | None
    position_unverified: bool
    status: str


STATUS_MATCH = "match"
STATUS_OFFSET = "offset"
STATUS_UNRESOLVED = "unresolved"
STATUS_NO_STORED_POSITION = "no stored position"


def load_stored_targets(database_path: str | Path) -> list[StoredTarget]:
    """Read every target's position from the database, read-only.

    Parameters
    ----------
    database_path : `str` or `pathlib.Path`
        The ``astrometrics.db`` file.

    Returns
    -------
    targets : `list` [`StoredTarget`]
        One entry per row of the ``targets`` table.
    """
    uri = f"{Path(database_path).resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute("SELECT id, name, ra, dec, data_json FROM targets ORDER BY id").fetchall()
    finally:
        connection.close()
    stored = []
    for target_id, name, ra, dec, data_json in rows:
        try:
            unverified = bool(json.loads(data_json or "{}").get("positionUnverified", False))
        except json.JSONDecodeError:
            unverified = False
        stored.append(
            StoredTarget(str(target_id), str(name or ""), str(ra or ""), str(dec or ""), unverified)
        )
    return stored


def audit_positions(
    targets: Sequence[StoredTarget], simbad: SimbadDriver, tolerance_arcmin: float
) -> list[PositionFinding]:
    """Compare each stored position with the catalog position of its name.

    Parameters
    ----------
    targets : `list` [`StoredTarget`]
        What the database holds.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    tolerance_arcmin : `float`
        Separations above this are marked as offsets.

    Returns
    -------
    findings : `list` [`PositionFinding`]
        One per target, in the order given.
    """
    findings = []
    for stored in targets:
        stored_position = parse_position_text(stored.ra, stored.dec)
        catalog_position = None
        for name in (stored.target_id, stored.common_name):
            if name:
                catalog_position = resolve_catalog_position(name, simbad)
                if catalog_position is not None:
                    break
        catalog_ra, catalog_dec = format_position(catalog_position) if catalog_position else ("", "")
        separation = None
        if stored_position is None:
            status = STATUS_NO_STORED_POSITION
        elif catalog_position is None:
            status = STATUS_UNRESOLVED
        else:
            separation = float(stored_position.separation(catalog_position).to("arcmin").value)
            status = STATUS_OFFSET if separation > tolerance_arcmin else STATUS_MATCH
        findings.append(
            PositionFinding(
                stored.target_id,
                stored.ra,
                stored.dec,
                catalog_ra,
                catalog_dec,
                separation,
                stored.position_unverified,
                status,
            )
        )
    return findings


def format_findings(findings: Sequence[PositionFinding], tolerance_arcmin: float, show_all: bool) -> str:
    """Write the audit as plain text.

    Parameters
    ----------
    findings : `list` [`PositionFinding`]
        The result of `audit_positions`.
    tolerance_arcmin : `float`
        The tolerance used, shown in the heading.
    show_all : `bool`
        Also list the targets that match.

    Returns
    -------
    report : `str`
        The report.
    """
    lines = [f"Targets more than {tolerance_arcmin:g} arcmin from their SIMBAD position:"]
    offsets = sorted(
        (finding for finding in findings if finding.status == STATUS_OFFSET),
        key=lambda finding: -(finding.separation_arcmin or 0.0),
    )
    shown = offsets + ([f for f in findings if f.status == STATUS_MATCH] if show_all else [])
    for finding in shown:
        flag = "  [marked unverified]" if finding.position_unverified else ""
        lines.append(
            f"  {finding.target_id}: stored {finding.stored_ra} {finding.stored_dec}; "
            f"catalog {finding.catalog_ra} {finding.catalog_dec}; "
            f"separation {finding.separation_arcmin:.1f} arcmin{flag}"
        )
    if not shown:
        lines.append("  (none)")
    unresolved = [f.target_id for f in findings if f.status == STATUS_UNRESOLVED]
    unset = [f.target_id for f in findings if f.status == STATUS_NO_STORED_POSITION]
    lines.append(
        f"Not resolved in SIMBAD, so not compared ({len(unresolved)}): {', '.join(unresolved) or '(none)'}"
    )
    lines.append(f"No stored position ({len(unset)}): {', '.join(unset) or '(none)'}")
    matched = sum(1 for f in findings if f.status == STATUS_MATCH)
    lines.append(f"{len(findings)} targets: {matched} match, {len(offsets)} offset.")
    return "\n".join(lines)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser accepting an optional database path and options.
    """
    parser = argparse.ArgumentParser(
        prog="audit_target_positions",
        description="List targets whose stored position differs from their SIMBAD position. Read-only.",
    )
    parser.add_argument("database", nargs="?", help="Path to astrometrics.db. Defaults to the library's.")
    parser.add_argument(
        "--tolerance-arcmin",
        type=float,
        default=DEFAULT_TOLERANCE_ARCMIN,
        help="Largest separation not listed.",
    )
    parser.add_argument("--all", action="store_true", dest="show_all", help="Also list targets that match.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if the database cannot be found.
    """
    arguments = _build_argument_parser().parse_args(argv)
    database = arguments.database
    if database is None:
        from astrometricslib import Astrometrics

        database = str(Path(str(Astrometrics().config.get_library_path())) / "astrometrics.db")
    if not Path(database).exists():
        print(f"No catalog database at {database}.")
        return 1
    findings = audit_positions(
        load_stored_targets(database), Drivers().simbad_or_default(), arguments.tolerance_arcmin
    )
    print(format_findings(findings, arguments.tolerance_arcmin, arguments.show_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
