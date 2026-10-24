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
Opening a database that is in WAL mode normally creates ``-wal`` and ``-shm``
files next to it, so when no ``-wal`` file exists the database is opened as
immutable (no locking, no new files).

Tolerance. A target is listed when the two positions are farther apart
than ``max(floor, half the object's major axis)``. The floor is 15
arcminutes: the camera's field is 96 arcminutes (1.60 degrees) across, so 15
arcminutes is about one sixth of it. An object that far from the stored point
is still in frame, but a framing that wrong is worth a look. It is well below
the 38 arcminute error that prompted this audit. A large object (the
Andromeda Galaxy spans about 3 degrees) is framed on purpose away from its
SIMBAD center, so its tolerance grows to its own radius when SIMBAD gives the
size. A listed extended object still needs a human judgment, not an automatic
fix. Evidence: validation register entry AST-20.

Targets that move (planets, the Moon, comets, asteroids) or are calibration
folders have no fixed position and are not compared. A target whose id and
common name resolve to different places is not compared either.
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
from astrometricslib.foundation.config import get_configuration
from astrometricslib.pipelines.shared.target_position import (
    POSITION_TOLERANCE_FLOOR_ARCMIN,
    format_position,
    has_no_fixed_position,
    parse_position_text,
    position_tolerance_arcmin,
    resolve_names,
)

# The extra SIMBAD column that gives an object's size, used to widen the
# tolerance for extended objects.
SIZE_COLUMNS = ("galdim_majaxis",)


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
    tolerance_arcmin: float = POSITION_TOLERANCE_FLOOR_ARCMIN


STATUS_MATCH = "match"
STATUS_OFFSET = "offset"
STATUS_UNRESOLVED = "unresolved"
STATUS_NO_STORED_POSITION = "no stored position"
STATUS_MOVING = "moving or calibration"
STATUS_AMBIGUOUS = "names disagree"


def _open_read_only(database_path: str | Path) -> sqlite3.Connection:
    """Open the database so that it cannot be changed and no file appears.

    Parameters
    ----------
    database_path : `str` or `pathlib.Path`
        The ``astrometrics.db`` file.

    Returns
    -------
    connection : `sqlite3.Connection`
        A read-only connection. When a ``-wal`` file exists the database is
        in use and ``mode=ro`` reads it (the files already exist). When none
        exists, ``immutable=1`` stops SQLite from creating ``-wal`` and
        ``-shm`` files; that is safe because with no ``-wal`` file nothing is
        waiting to be merged.
    """
    path = Path(database_path).resolve()
    wal_present = path.with_name(path.name + "-wal").exists()
    mode = "mode=ro" if wal_present else "mode=ro&immutable=1"
    return sqlite3.connect(f"{path.as_uri()}?{mode}", uri=True)


def load_stored_targets(database_path: str | Path) -> list[StoredTarget]:
    """Read every target's position from the database, read-only.

    No file is created, even for a database in WAL mode (see
    `_open_read_only`).

    Parameters
    ----------
    database_path : `str` or `pathlib.Path`
        The ``astrometrics.db`` file.

    Returns
    -------
    targets : `list` [`StoredTarget`]
        One entry per row of the ``targets`` table.
    """
    connection = _open_read_only(database_path)
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
    targets: Sequence[StoredTarget],
    simbad: SimbadDriver,
    floor_arcmin: float = POSITION_TOLERANCE_FLOOR_ARCMIN,
) -> list[PositionFinding]:
    """Compare each stored position with the catalog position of its name.

    Parameters
    ----------
    targets : `list` [`StoredTarget`]
        What the database holds.
    simbad : `SimbadDriver`
        The SIMBAD driver to ask.
    floor_arcmin : `float`, optional
        The smallest tolerance. The tolerance of an extended object is its
        radius when that is larger (see `position_tolerance_arcmin`).

    Returns
    -------
    findings : `list` [`PositionFinding`]
        One per target, in the order given.
    """
    findings = []
    for stored in targets:
        stored_position = parse_position_text(stored.ra, stored.dec)
        catalog = None
        separation = None
        tolerance = floor_arcmin
        if has_no_fixed_position(stored.target_id):
            status = STATUS_MOVING
        else:
            resolution = resolve_names([stored.target_id, stored.common_name], simbad, SIZE_COLUMNS)
            catalog = resolution.position
            if catalog is not None:
                tolerance = position_tolerance_arcmin(catalog.major_axis_arcmin, floor_arcmin)
            if stored_position is None:
                status = STATUS_NO_STORED_POSITION
            elif resolution.conflict:
                status = STATUS_AMBIGUOUS
            elif catalog is None:
                status = STATUS_UNRESOLVED
            else:
                separation = float(stored_position.separation(catalog.coordinate).to("arcmin").value)
                status = STATUS_OFFSET if separation > tolerance else STATUS_MATCH
        catalog_ra, catalog_dec = format_position(catalog.coordinate) if catalog else ("", "")
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
                tolerance,
            )
        )
    return findings


def format_findings(findings: Sequence[PositionFinding], floor_arcmin: float, show_all: bool) -> str:
    """Write the audit as plain text.

    Parameters
    ----------
    findings : `list` [`PositionFinding`]
        The result of `audit_positions`.
    floor_arcmin : `float`
        The tolerance floor used, shown in the heading.
    show_all : `bool`
        Also list the targets that match.

    Returns
    -------
    report : `str`
        The report.
    """
    lines = [
        f"Targets farther from their SIMBAD position than max({floor_arcmin:g} arcmin, the object's radius):"
    ]
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
            f"separation {finding.separation_arcmin:.1f} arcmin "
            f"(tolerance {finding.tolerance_arcmin:.0f}){flag}"
        )
    if not shown:
        lines.append("  (none)")
    unresolved = [f.target_id for f in findings if f.status == STATUS_UNRESOLVED]
    unset = [f.target_id for f in findings if f.status == STATUS_NO_STORED_POSITION]
    lines.append(
        f"Not resolved in SIMBAD, so not compared ({len(unresolved)}): {', '.join(unresolved) or '(none)'}"
    )
    lines.append(f"No stored position ({len(unset)}): {', '.join(unset) or '(none)'}")
    moving = [f.target_id for f in findings if f.status == STATUS_MOVING]
    ambiguous = [f.target_id for f in findings if f.status == STATUS_AMBIGUOUS]
    lines.append(f"Not compared: moving or calibration ({len(moving)}): {', '.join(moving) or '(none)'}")
    lines.append(
        f"Not compared: id and common name resolve apart ({len(ambiguous)}): "
        f"{', '.join(ambiguous) or '(none)'}"
    )
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
        "--floor-arcmin",
        type=float,
        default=POSITION_TOLERANCE_FLOOR_ARCMIN,
        help="Smallest tolerance; extended objects get their own radius if larger.",
    )
    parser.add_argument("--all", action="store_true", dest="show_all", help="Also list targets that match.")
    return parser


def main(argv: Sequence[str] | None = None, simbad: SimbadDriver | None = None) -> int:
    """Run the audit from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.
    simbad : `SimbadDriver`, optional
        The SIMBAD driver to ask; the built-in one when omitted (tests pass
        a fake).

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if the database cannot be found.
    """
    arguments = _build_argument_parser().parse_args(argv)
    database = arguments.database
    if database is None:
        database = str(get_configuration().get_library_path() / "astrometrics.db")
    if not Path(database).exists():
        print(f"No catalog database at {database}.")
        return 1
    findings = audit_positions(
        load_stored_targets(database), simbad or Drivers().simbad_or_default(), arguments.floor_arcmin
    )
    print(format_findings(findings, arguments.floor_arcmin, arguments.show_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
