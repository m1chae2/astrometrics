r"""Move the pipeline's output files to the stacks path and fix the records.

The pipeline writes stacks, group stacks, rejection maps, registration files,
previews and processed pictures beside the raw frames, in
``<frames>/lights/<target>/``. The ``stacks_path`` setting can send them to
another disk instead, in ``<stacks_path>/lights/<target>/``. This script
moves the files that already exist and updates every path that points at
them.

For each target folder it moves:

1. The stack family at the top of the folder: ``*_Stacked*.fits`` and the
   files that go with a stack (``_RejMap.fits``, ``_Registration.seq``,
   ``_preview.jpg``, ``_processed.fits``), plus any ``starless_`` or
   ``starmask_`` copies.
2. The whole ``groups`` folder, then rewrites the paths inside its manifest
   files.

Raw frames and pictures a person added are never moved. Each file is copied,
checked by size, and only then removed from the old disk, so an interrupted
run can be started again.

Then it rewrites the old paths to the new ones in the targets table, in the
provenance records and in the job records. The database is backed up first.
Stop the backend before ``--apply``: a running backend keeps the old paths in
memory and would write them back.

Check first, then apply::

    python -m astrometricslib.scripts.move_stacks_to_stacks_path \
        --to /home/me/Astrometrics/stacks --dry-run

    python -m astrometricslib.scripts.move_stacks_to_stacks_path \
        --to /home/me/Astrometrics/stacks --apply

Afterwards, set ``stacks_path`` in the ``[Image Library]`` section of the
configuration to the same folder, so new stacks go there too.
"""

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

from astrometricslib.foundation.config import get_configuration

# The files that make up a stack at the top of a target folder.
_STACK_FAMILY = re.compile(
    r"(_Stacked.*\.fits|_Stacked.*_RejMap\.fits|_Stacked.*_Registration\.seq"
    r"|_Stacked.*_preview\.jpg|_Stacked.*_processed\.fits)$"
)
_PREFIXES = ("starless_", "starmask_")
# The port the backend listens on, used to refuse a run while it is up.
_BACKEND_PORT = 5000


def is_stack_family_file(name: str) -> bool:
    """Tell whether a file name belongs to a stack or its companions.

    Parameters
    ----------
    name : `str`
        A file name, without its folder.

    Returns
    -------
    is_stack : `bool`
        `True` for a stack, rejection map, registration file, preview or
        processed picture, and for a ``starless_`` or ``starmask_`` copy.
    """
    return bool(_STACK_FAMILY.search(name)) or name.startswith(_PREFIXES)


def plan_moves(old_lights: str, new_lights: str) -> dict[str, str]:
    """List every file to move, with where it goes.

    Parameters
    ----------
    old_lights : `str`
        The ``lights`` folder under the frames path.
    new_lights : `str`
        The ``lights`` folder under the stacks path.

    Returns
    -------
    moves : `dict` [`str`, `str`]
        Old path to new path. A target folder's top-level stack files and
        everything inside its ``groups`` folder are included.
    """
    moves: dict[str, str] = {}
    for target in sorted(os.listdir(old_lights)):
        target_folder = os.path.join(old_lights, target)
        if not os.path.isdir(target_folder):
            continue
        for name in sorted(os.listdir(target_folder)):
            path = os.path.join(target_folder, name)
            if os.path.isfile(path) and is_stack_family_file(name):
                moves[path] = os.path.join(new_lights, target, name)
        groups = os.path.join(target_folder, "groups")
        if os.path.isdir(groups):
            for root, _, names in os.walk(groups):
                for name in sorted(names):
                    old = os.path.join(root, name)
                    moves[old] = os.path.join(new_lights, target, os.path.relpath(old, target_folder))
    return moves


def copy_and_verify(old: str, new: str) -> bool:
    """Copy one file and check that the copy is complete.

    Parameters
    ----------
    old : `str`
        The file to copy.
    new : `str`
        Where to put the copy. Missing folders are made. A copy that is
        already there with the same size is kept, so a rerun skips it.

    Returns
    -------
    copied : `bool`
        `True` if `new` now exists with the size of `old`.
    """
    if os.path.isfile(new) and os.path.getsize(new) == os.path.getsize(old):
        return True
    os.makedirs(os.path.dirname(new), exist_ok=True)
    shutil.copy2(old, new)
    return os.path.getsize(new) == os.path.getsize(old)


def rewrite_paths(text: str, mapping: dict[str, str]) -> str:
    """Replace every old path in a text with its new path.

    Parameters
    ----------
    text : `str`
        Text such as a record's JSON.
    mapping : `dict` [`str`, `str`]
        Old path to new path. Only whole paths in the mapping are replaced.

    Returns
    -------
    new_text : `str`
        The text with each old path replaced.
    """
    if not mapping:
        return text
    pattern = re.compile("|".join(re.escape(old) for old in sorted(mapping, key=len, reverse=True)))
    return pattern.sub(lambda match: mapping[match.group(0)], text)


def rewrite_manifests(moves: dict[str, str]) -> int:
    """Fix the paths inside the moved group manifest files.

    Parameters
    ----------
    moves : `dict` [`str`, `str`]
        Old path to new path of the files that were moved.

    Returns
    -------
    count : `int`
        How many manifest files were rewritten.
    """
    count = 0
    for new in moves.values():
        if not new.endswith("_manifest.json") or not os.path.isfile(new):
            continue
        text = Path(new).read_text()
        rewritten = rewrite_paths(text, moves)
        if rewritten != text:
            json.loads(rewritten)  # never write a manifest that is no longer valid JSON
            Path(new).write_text(rewritten)
            count += 1
    return count


def rewrite_database(db_path: str, table: str, column: str, moves: dict[str, str]) -> int:
    """Rewrite the old paths found in one column of one table.

    Parameters
    ----------
    db_path : `str`
        The SQLite file.
    table : `str`
        The table to update.
    column : `str`
        The text column that holds paths, alone or inside JSON.
    moves : `dict` [`str`, `str`]
        Old path to new path.

    Returns
    -------
    rows_changed : `int`
        How many rows were updated.

    Raises
    ------
    ValueError
        If `table` or `column` is not a plain name.
    """
    # Table and column names cannot be bound as query parameters, so they are
    # checked to be plain identifiers before they go into the SQL text.
    for identifier in (table, column):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"Not a plain table or column name: {identifier!r}")
    select = f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" LIKE ?'  # ruff: ignore[hardcoded-sql-expression]
    update = f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?'  # ruff: ignore[hardcoded-sql-expression]
    connection = sqlite3.connect(db_path)
    changed = 0
    try:
        for rowid, value in connection.execute(select, ("%/lights/%",)).fetchall():
            if not isinstance(value, str):
                continue
            rewritten = rewrite_paths(value, moves)
            if rewritten != value:
                connection.execute(update, (rewritten, rowid))
                changed += 1
        connection.commit()
    finally:
        connection.close()
    return changed


def count_remaining(db_path: str, table: str, column: str, moves: dict[str, str]) -> int:
    """Count the rows that still hold an old path after the rewrite.

    Parameters
    ----------
    db_path : `str`
        The SQLite file.
    table : `str`
        The table to check.
    column : `str`
        The text column that holds paths.
    moves : `dict` [`str`, `str`]
        Old path to new path.

    Returns
    -------
    count : `int`
        How many rows still contain at least one old path.

    Raises
    ------
    ValueError
        If `table` or `column` is not a plain name.
    """
    for identifier in (table, column):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"Not a plain table or column name: {identifier!r}")
    select = f'SELECT "{column}" FROM "{table}" WHERE "{column}" LIKE ?'  # ruff: ignore[hardcoded-sql-expression]
    pattern = (
        re.compile("|".join(re.escape(old) for old in sorted(moves, key=len, reverse=True)))
        if moves
        else None
    )
    connection = sqlite3.connect(db_path)
    try:
        values = [row[0] for row in connection.execute(select, ("%/lights/%",)).fetchall()]
    finally:
        connection.close()
    return sum(1 for value in values if pattern and isinstance(value, str) and pattern.search(value))


def backend_is_running() -> bool:
    """Tell whether something is listening where the backend listens.

    Returns
    -------
    running : `bool`
        `True` if the backend's port accepts a connection.
    """
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(("127.0.0.1", _BACKEND_PORT)) == 0


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser, with the destination and mode options.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--to", required=True, metavar="FOLDER", help="The stacks path to move the files to.")
    parser.add_argument(
        "--backup-dir", metavar="FOLDER", default=os.path.expanduser("~/astrometrics_backups_stacks_move")
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="List what would move and change nothing.")
    mode.add_argument("--apply", action="store_true", help="Move the files and update the records.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the move.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 on success, 1 if a file could not be copied, 2 if the backend is
        running during ``--apply``.
    """
    arguments = _build_argument_parser().parse_args(argv)
    configuration = get_configuration()
    old_lights = os.path.join(str(configuration.get_frames_path()), "lights")
    new_lights = os.path.join(os.path.abspath(arguments.to), "lights")
    if os.path.abspath(old_lights) == os.path.abspath(new_lights):
        print("The destination is the same as the frames path. Nothing to do.")
        return 0
    moves = plan_moves(old_lights, new_lights)
    total = sum(os.path.getsize(old) for old in moves)
    print(f"{len(moves)} files, {total / 1e9:.2f} GB, from {old_lights} to {new_lights}")
    if arguments.dry_run:
        for old, new in list(moves.items())[:20]:
            print(f"  {old}\n    -> {new}")
        if len(moves) > 20:
            print(f"  ... and {len(moves) - 20} more")
        return 0
    if backend_is_running():
        print("The backend is running. Stop it first, then run this again.")
        return 2

    db_path = str(configuration.get_library_file_path("astrometrics.db"))
    log_db_path = str(configuration.get_logs_db_path())
    os.makedirs(arguments.backup_dir, exist_ok=True)
    for source in (db_path, log_db_path):
        shutil.copy2(source, os.path.join(arguments.backup_dir, os.path.basename(source)))
    print(f"Backed up the databases to {arguments.backup_dir}")

    copied: dict[str, str] = {}
    for old, new in moves.items():
        if copy_and_verify(old, new):
            copied[old] = new
        else:
            print(f"FAILED to copy {old}")
    print(f"Copied and checked {len(copied)} of {len(moves)} files.")
    print(f"Manifests rewritten: {rewrite_manifests(copied)}")
    print(f"targets rows updated: {rewrite_database(db_path, 'targets', 'data_json', copied)}")
    print(f"provenance rows updated: {rewrite_database(log_db_path, 'prov_entity', 'location', copied)}")
    print(f"job rows updated: {rewrite_database(log_db_path, 'processing_jobs', 'output_metrics', copied)}")
    # Only remove an old file after every record points at the new one.
    for db, table, column in ((db_path, "targets", "data_json"), (log_db_path, "prov_entity", "location")):
        left = count_remaining(db, table, column, copied)
        if left:
            print(f"{left} rows in {table} still hold an old path. Old files are kept; run this again.")
            return 1
    for old in copied:
        os.remove(old)
        folder = os.path.dirname(old)
        if os.path.basename(folder) == "groups" and not os.listdir(folder):
            os.rmdir(folder)
    print(f"Removed {len(copied)} old files.")
    return 0 if len(copied) == len(moves) else 1


if __name__ == "__main__":
    sys.exit(main())
