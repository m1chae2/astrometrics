r"""Rename the stored "quarantine" names to their "set aside" names, once.

The frame check that moves cloudy and misaligned frames into `_excluded`
used to be called the quarantine. Its names were stored in the library
database and in the user's configuration. This script renames them, so the
code no longer has to read the old names.

What it changes (the old name, then the new one):

- Quality database, `quality_measurements.metric`: `frame.quarantine_roundness`
  (retired), `frame.quarantine_sky_median_adu`,
  `stacking.frames_quarantined_count` and `stacking.gate_frame_quarantine`.
- Stored stack summaries, `targets.data_json`: the key `framesQuarantined`
  (and `frames_quarantined`), the gate name `frame_quarantine`, and the three
  reason texts the step wrote ("set aside by an earlier quarantine", "skipped,
  set aside by the frame quarantine" and "already set aside by the frame
  quarantine").
- Configuration file: the key `quarantine_bad_frames_enabled` in
  `[Processing.Siril]` becomes `set_aside_bad_frames_enabled`. Only that line
  is rewritten; every other byte of the file stays.

The photometry result key `quarantine_skipped_frames` is never stored, so
there is nothing to rename for it. Log messages and the `reason` text of old
quality rows are history and are left as they are.

Run it without ``--apply`` first. That counts what would change and writes
nothing. With ``--apply`` it first copies each database (with the SQLite
backup call) and the configuration into ``--backup-folder`` under a dated
name, checks that each copy opens and has the same row counts, and only then
renames. When it is done it counts the rows that still carry an old name; the
count must be zero::

    python migrate_set_aside_names.py \
        --library-folder ~/.local/share/astrometrics \
        --config ~/.config/astrometrics/astrometrics.config.toml

    python migrate_set_aside_names.py \
        --library-folder ~/.local/share/astrometrics \
        --config ~/.config/astrometrics/astrometrics.config.toml --apply

Stop the app first, so nothing writes to the database while it runs.
"""

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DATABASE_NAME = "astrometrics.db"

METRIC_RENAMES = {
    "frame.quarantine_roundness": "frame.set_aside_roundness",
    "frame.quarantine_sky_median_adu": "frame.set_aside_sky_median_adu",
    "stacking.frames_quarantined_count": "stacking.frames_set_aside_count",
    "stacking.gate_frame_quarantine": "stacking.gate_frame_set_aside",
}
"""Old metric name to new metric name."""

SUMMARY_RENAMES = {
    '"framesQuarantined"': '"framesSetAside"',
    '"frames_quarantined"': '"frames_set_aside"',
    '"frame_quarantine"': '"frame_set_aside"',
    "set aside by an earlier quarantine": "set aside by an earlier set-aside check",
    "skipped, set aside by the frame quarantine": (
        "skipped, set aside by the cloud and target alignment check"
    ),
    "already set aside by the frame quarantine": (
        "already set aside by the cloud and target alignment check"
    ),
}
"""Old text to new text inside a stored stack summary (`targets.data_json`)."""

OLD_CONFIG_KEY = "quarantine_bad_frames_enabled"
NEW_CONFIG_KEY = "set_aside_bad_frames_enabled"


def _count_old_rows(connection: sqlite3.Connection) -> dict[str, int]:
    """Count the stored rows that still carry an old name.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the library database.

    Returns
    -------
    counts : `dict` [`str`, `int`]
        The old name (or text) mapped to the number of rows that hold it.
        Metric names count `quality_measurements` rows; the other texts count
        `targets` rows.
    """
    counts: dict[str, int] = {}
    for old in METRIC_RENAMES:
        row = connection.execute(
            "SELECT COUNT(*) FROM quality_measurements WHERE metric = ?", (old,)
        ).fetchone()
        counts[old] = int(row[0])
    for old in SUMMARY_RENAMES:
        row = connection.execute(
            "SELECT COUNT(*) FROM targets WHERE instr(data_json, ?) > 0", (old,)
        ).fetchone()
        counts[old] = int(row[0])
    return counts


def _table_counts(path: Path) -> dict[str, int]:
    """Count the rows of every table of a database.

    Parameters
    ----------
    path : `pathlib.Path`
        The database file.

    Returns
    -------
    counts : `dict` [`str`, `int`]
        Table name to row count.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        # The names come from the database's own table list, not from a user.
        return {
            name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])  # ruff: ignore[hardcoded-sql-expression]
            for name in names
        }
    finally:
        connection.close()


def _back_up(library_folder: Path, config: Path | None, backup_folder: Path) -> list[Path]:
    """Copy every database and the configuration, then check each copy.

    Parameters
    ----------
    library_folder : `pathlib.Path`
        The folder that holds the databases. Every ``*.db`` file in it and in
        its `wayfinding` folder is copied.
    config : `pathlib.Path` or `None`
        The configuration file, when it is to be migrated.
    backup_folder : `pathlib.Path`
        The folder that receives the dated copies.

    Returns
    -------
    copies : `list` [`pathlib.Path`]
        The copies made.

    Raises
    ------
    RuntimeError
        If a copy does not open or its row counts differ from the source.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_folder.mkdir(parents=True, exist_ok=True)
    copies: list[Path] = []
    databases = sorted([*library_folder.glob("*.db"), *(library_folder / "wayfinding").glob("*.db")])
    for database in databases:
        label = (
            database.stem if database.parent == library_folder else f"{database.parent.name}_{database.stem}"
        )
        target = backup_folder / f"{label}_before_set_aside_rename_{stamp}{database.suffix}"
        source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        if _table_counts(target) != _table_counts(database):
            raise RuntimeError(f"the copy {target} has different row counts from {database}")
        copies.append(target)
    if config is not None:
        copy = backup_folder / f"{config.name}.before_set_aside_rename_{stamp}"
        shutil.copy2(config, copy)
        if copy.read_bytes() != config.read_bytes():
            raise RuntimeError(f"the copy {copy} differs from {config}")
        copies.append(copy)
    return copies


def _rename_in_database(database: Path) -> dict[str, int]:
    """Rename the stored names inside the database, in one transaction.

    Parameters
    ----------
    database : `pathlib.Path`
        The library database.

    Returns
    -------
    changed : `dict` [`str`, `int`]
        How many rows changed for each old name (metric rows, then summary
        rows). A rewritten stack summary that is no longer valid JSON raises
        an error and rolls the transaction back.
    """
    connection = sqlite3.connect(database)
    changed: dict[str, int] = {}
    try:
        connection.execute("BEGIN")
        for old, new in METRIC_RENAMES.items():
            cursor = connection.execute(
                "UPDATE quality_measurements SET metric = ? WHERE metric = ?", (new, old)
            )
            changed[old] = cursor.rowcount
        for old in SUMMARY_RENAMES:
            changed[old] = 0
        rows = connection.execute("SELECT id, data_json FROM targets").fetchall()
        for target_id, text in rows:
            if not isinstance(text, str):
                continue
            new_text = text
            for old, new in SUMMARY_RENAMES.items():
                if old in new_text:
                    changed[old] += 1
                    new_text = new_text.replace(old, new)
            if new_text != text:
                json.loads(new_text)
                connection.execute("UPDATE targets SET data_json = ? WHERE id = ?", (new_text, target_id))
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return changed


def _rename_config_key(config: Path) -> int:
    """Rewrite the old setting name in place, leaving every other byte alone.

    Parameters
    ----------
    config : `pathlib.Path`
        The configuration file.

    Returns
    -------
    count : `int`
        How many lines were rewritten (0 or 1).
    """
    data = config.read_bytes()
    pattern = re.compile(rb"(?m)^(\s*)" + re.escape(OLD_CONFIG_KEY.encode()) + rb"(\s*=)")
    new_data, count = pattern.subn(rb"\g<1>" + NEW_CONFIG_KEY.encode() + rb"\g<2>", data)
    if count:
        config.write_bytes(new_data)
    return count


def main(arguments: list[str] | None = None) -> int:
    """Count, back up and rename, as the command line asks.

    Parameters
    ----------
    arguments : `list` [`str`], optional
        The command-line arguments. Default is ``sys.argv[1:]``.

    Returns
    -------
    status : `int`
        0 when the run finished (and, with ``--apply``, no old name is left),
        1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--library-folder", required=True, type=Path, help="The folder that holds astrometrics.db."
    )
    parser.add_argument("--config", type=Path, help="The configuration file to rewrite.")
    parser.add_argument(
        "--backup-folder",
        type=Path,
        default=Path.home() / ".cache" / "astrometrics" / "backup",
        help="Where the dated copies go (default: ~/.cache/astrometrics/backup).",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Back up, then rename. Without it nothing is written."
    )
    options = parser.parse_args(arguments)

    database = options.library_folder.expanduser() / DATABASE_NAME
    config = options.config.expanduser() if options.config else None
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        before = _count_old_rows(connection)
    finally:
        connection.close()
    config_before = (
        len(re.findall(rb"(?m)^\s*" + OLD_CONFIG_KEY.encode(), config.read_bytes())) if config else 0
    )
    print("Rows holding an old name now:")
    for name, count in before.items():
        print(f"  {name}: {count}")
    print(f"  config key {OLD_CONFIG_KEY}: {config_before}")
    if not options.apply:
        print("Dry run: nothing was written. Add --apply to back up and rename.")
        return 0

    for copy in _back_up(options.library_folder.expanduser(), config, options.backup_folder.expanduser()):
        print(f"Backed up and checked: {copy}")
    changed = _rename_in_database(database)
    print("Rows changed:")
    for name, count in changed.items():
        print(f"  {name}: {count}")
    if config is not None:
        print(f"  config key {OLD_CONFIG_KEY}: {_rename_config_key(config)}")

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        after = _count_old_rows(connection)
    finally:
        connection.close()
    config_after = (
        len(re.findall(rb"(?m)^\s*" + OLD_CONFIG_KEY.encode(), config.read_bytes())) if config else 0
    )
    left = sum(after.values()) + config_after
    print(f"Rows still holding an old name: {left}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
