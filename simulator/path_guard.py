"""Purpose: Keep stored paths in a copy of the library inside the copy.

Description: The copy of the library database stores absolute paths (stack
images, inputs.json, subset folders, and so on) inside the JSON of each
target record. A copy made with a plain file copy keeps the real paths, so a
program run on the copy would write into the real stacks folder. This module
opens the copy databases read-only, walks every stored string, and lists each
string that is an absolute path outside the copy folder
(ASTROMETRICS_SCRATCH). The light and calibration frames folder
(``frames_path``) may stay at its real, read-only place. Two databases are
scanned: ``astrometrics.db`` (the ``targets`` table) in the library folder,
and ``wayfinding.db`` (every table) in the wayfinding folder.

Three groups of callers use it:

* ``build/agent_tools/copy_path_guard.py`` (a thin wrapper) is run by
  ``realrun.sh`` as ``check``; ``repoint_copy_paths.py`` calls `repoint`.
* ``build/tpm/real_snapshot.py`` calls `repoint` and `check`.
* The simulator's library copy (`simulator.library_copy`) calls
  `mirror_stale_paths` and `outside_paths` on the world's files, which have
  no settings file yet, so they take the files directly.
"""

import json
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

#: One slash first, no second slash, and no line breaks: an absolute path.
_ABSOLUTE_PATH = re.compile(r"^/[^/\n\r\0][^\n\r\0]*$")
#: Alternate mount points for removable drives; the library treats them alike.
_MOUNT_ALIASES = ("/run/media", "/media")
#: Statement templates. SQLite cannot bind a table or column name as a
#: parameter, so the names are put in with `_statement`, which only accepts
#: names read from the database's own table list and quotes them.
_SELECT_TEMPLATE = "SELECT rowid, <column> FROM <table>"
_UPDATE_TEMPLATE = "UPDATE <table> SET <column> = ? WHERE rowid = ?"
#: Folder inside the copy that mirrors stale outside paths.
_MIRROR_FOLDER = "outside"
#: How many offenders are printed before the list is cut.
_PRINT_LIMIT = 50


def read_config_value(config_file: str | os.PathLike, key: str) -> str:
    """Read one ``key = "value"`` line from the copy config file.

    Parameters
    ----------
    config_file : `str` or `os.PathLike`
        The copy config file.
    key : `str`
        The setting name, such as ``path``.

    Returns
    -------
    value : `str`
        The first quoted value found, or an empty string when the key is
        missing.
    """
    for line in Path(config_file).read_text().splitlines():
        match = re.match(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', line)
        if match:
            return match.group(1)
    return ""


def is_under(path: str, folder: str) -> bool:
    """Say whether a path lies inside a folder, or is the folder.

    Parameters
    ----------
    path : `str`
        An absolute path.
    folder : `str`
        An absolute folder path.

    Returns
    -------
    inside : `bool`
        `True` if ``path`` is ``folder`` or below it.
    """
    path = os.path.normpath(path)
    folder = os.path.normpath(folder)
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _mount_variants(path: str) -> list[str]:
    """Give a path and its ``/run/media`` or ``/media`` twin.

    Parameters
    ----------
    path : `str`
        An absolute path.

    Returns
    -------
    variants : `list` [`str`]
        The path, plus the same path under the other mount root when it
        starts with one.
    """
    variants = [path]
    for root in _MOUNT_ALIASES:
        if is_under(path, root):
            variants += [other + path[len(root) :] for other in _MOUNT_ALIASES if other != root]
    return variants


def walk_strings(node: Any, trail: str = "") -> Iterator[tuple[str, str]]:
    """Yield every string in a parsed JSON value with its key trail.

    Parameters
    ----------
    node : `object`
        A parsed JSON value.
    trail : `str`, optional
        The key trail so far.

    Yields
    ------
    trail : `str`
        Dotted key trail, with list positions in brackets.
    text : `str`
        The string found there.
    """
    if isinstance(node, dict):
        for key, child in node.items():
            yield from walk_strings(child, f"{trail}.{key}" if trail else str(key))
    elif isinstance(node, list):
        for position, child in enumerate(node):
            yield from walk_strings(child, f"{trail}[{position}]")
    elif isinstance(node, str):
        yield trail, node


def is_allowed(text: str, scratch: str, frames_path: str) -> bool:
    """Say whether a stored absolute path is safe for a run on the copy.

    Parameters
    ----------
    text : `str`
        The stored absolute path.
    scratch : `str`
        The folder that holds the copies.
    frames_path : `str`
        The frames folder of the copy config. It may be the real, read-only
        folder. An empty string means none.

    Returns
    -------
    allowed : `bool`
        `True` if the path is inside the copy folder or the frames folder.
    """
    if is_under(text, scratch):
        return True
    if not frames_path:
        return False
    return any(is_under(text, variant) for variant in _mount_variants(frames_path))


def _statement(template: str, table: str, column: str, known: list[str]) -> str:
    """Fill a statement template with a quoted table and column name.

    Parameters
    ----------
    template : `str`
        One of the statement templates in this module.
    table : `str`
        A table name, which must be in ``known``.
    column : `str`
        A column name from the table's own column list.
    known : `list` [`str`]
        The table names read from the database.

    Returns
    -------
    statement : `str`
        The statement with both names double-quoted.

    Raises
    ------
    ValueError
        If the table is not one the database listed.
    """
    if table not in known:
        raise ValueError(f"unknown table {table!r}")
    quote = str.maketrans({'"': '""'})
    return template.replace("<table>", '"' + table.translate(quote) + '"').replace(
        "<column>", '"' + column.translate(quote) + '"'
    )


def _table_names(connection: sqlite3.Connection) -> list[str]:
    """List the tables of a database.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection.

    Returns
    -------
    names : `list` [`str`]
        The table names.
    """
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return [row[0] for row in rows]


def _text_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    """List the text-like columns of a table.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection.
    table : `str`
        The table name.

    Returns
    -------
    columns : `list` [`str`]
        Column names whose declared type is text, JSON or empty.
    """
    rows = connection.execute("SELECT name, type FROM pragma_table_info(?)", (table,))
    wanted = ("CHAR", "TEXT", "JSON")
    return [name for name, kind in rows if not kind or any(word in kind.upper() for word in wanted)]


def _scan_cell(value: object) -> Iterator[tuple[str, str]]:
    """Yield the strings of one stored cell, parsing JSON when it parses.

    Parameters
    ----------
    value : `object`
        The cell value.

    Yields
    ------
    trail : `str`
        Key trail inside the cell (empty for a plain string).
    text : `str`
        A string found in the cell.
    """
    if not isinstance(value, str):
        return
    try:
        parsed = json.loads(value)
    except ValueError:
        yield "", value
        return
    yield from walk_strings(parsed)


def _row_labels(connection: sqlite3.Connection, table: str) -> dict[int, str]:
    """Name each row by its ``id`` column when the table has one.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection.
    table : `str`
        The table name.

    Returns
    -------
    labels : `dict` [`int`, `str`]
        Row id to label. Empty when the table has no ``id`` column.
    """
    names = [row[0] for row in connection.execute("SELECT name FROM pragma_table_info(?)", (table,))]
    if "id" not in names:
        return {}
    statement = _statement("SELECT rowid, <column> FROM <table>", table, "id", [table])
    return {rowid: str(label) for rowid, label in connection.execute(statement)}


def find_offending_paths(
    database_file: str,
    tables: tuple[str, ...] | None,
    scratch: str,
    frames_path: str,
) -> list[tuple[str, str, str, str, str]]:
    """List the stored absolute paths that point outside the copy.

    The database is opened read-only.

    Parameters
    ----------
    database_file : `str`
        The SQLite database file.
    tables : `tuple` [`str`] or `None`
        The tables to scan, or `None` for every table.
    scratch : `str`
        The folder that holds the copies.
    frames_path : `str`
        The allowed frames folder.

    Returns
    -------
    offenders : `list` [`tuple`]
        ``(database name, table, row label, column and key trail, path)``
        for each bad path.
    """
    connection = sqlite3.connect(f"file:{database_file}?mode=ro", uri=True)
    offenders = []
    try:
        existing = _table_names(connection)
        for table in existing if tables is None else [name for name in tables if name in existing]:
            labels = _row_labels(connection, table)
            for column in _text_columns(connection, table):
                statement = _statement(_SELECT_TEMPLATE, table, column, existing)
                for rowid, value in connection.execute(statement):
                    for trail, text in _scan_cell(value):
                        if _ABSOLUTE_PATH.match(text) and not is_allowed(text, scratch, frames_path):
                            label = labels.get(rowid, str(rowid))
                            offenders.append((
                                Path(database_file).name,
                                table,
                                label,
                                f"{column}:{trail}",
                                text,
                            ))
    finally:
        connection.close()
    return offenders


def database_files(config_file: str | os.PathLike) -> list[tuple[str, tuple[str, ...] | None]]:
    """Find the copy databases and the tables to scan in each.

    The library database sits in the config's ``[Image Library] path``. The
    wayfinding database sits in ``[Wayfinding Library] path`` when that is
    set, otherwise in the ``wayfinding`` folder of the data folder, as
    wayfindinglib's Butler does.

    Parameters
    ----------
    config_file : `str` or `os.PathLike`
        The copy config file.

    Returns
    -------
    databases : `list` [`tuple`]
        ``(file, tables)`` for each database that exists; ``tables`` is
        `None` to scan every table.
    """
    library = read_config_value(config_file, "path")
    wayfinding = read_config_value_in_section(config_file, "Wayfinding Library", "path")
    if not wayfinding:
        data_root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        wayfinding = os.path.join(data_root, "astrometrics", "wayfinding")
    candidates = [
        (os.path.join(library, "astrometrics.db"), ("targets",)),
        (os.path.join(wayfinding, "wayfinding.db"), None),
    ]
    return [(file, tables) for file, tables in candidates if os.path.exists(file)]


def read_config_value_in_section(config_file: str | os.PathLike, section: str, key: str) -> str:
    """Read one ``key = "value"`` line from a named section of the config.

    Parameters
    ----------
    config_file : `str` or `os.PathLike`
        The copy config file.
    section : `str`
        The section name, without brackets.
    key : `str`
        The setting name.

    Returns
    -------
    value : `str`
        The quoted value, or an empty string when missing.
    """
    in_section = False
    for line in Path(config_file).read_text().splitlines():
        if line.strip().startswith("["):
            # TOML allows a quoted name: ["Wayfinding Library"] is the
            # same section.
            in_section = line.strip()[1:-1].strip().strip('"') == section
            continue
        match = re.match(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', line)
        if in_section and match:
            return match.group(1)
    return ""


def check(config_file: str | os.PathLike, scratch: str) -> list[tuple[str, str, str, str, str]]:
    """Find every stored path in the copy databases that leaves the copy.

    Parameters
    ----------
    config_file : `str` or `os.PathLike`
        The copy config file.
    scratch : `str`
        The folder that holds the copies.

    Returns
    -------
    offenders : `list` [`tuple`]
        See `find_offending_paths`; empty when the copy is clean.
    """
    scratch = os.path.realpath(scratch)
    frames = read_config_value(config_file, "frames_path")
    offenders = []
    for database_file, tables in database_files(config_file):
        offenders += find_offending_paths(database_file, tables, scratch, frames)
    return offenders


def _repoint_text(text: str, stacks: str, library: str, real_data_folder: str) -> str | None:
    """Map one real path to its place in the copy.

    A path under the real data folder moves under the copy library folder. A
    path with a ``/lights/`` folder moves under the copy stacks folder.

    Parameters
    ----------
    text : `str`
        The stored real path.
    stacks : `str`
        The copy stacks folder.
    library : `str`
        The copy library folder.
    real_data_folder : `str`
        The real data folder.

    Returns
    -------
    new_text : `str` or `None`
        The new path, or `None` when no rule applies.
    """
    if is_under(text, real_data_folder):
        return os.path.join(library, os.path.relpath(text, real_data_folder))
    marker = "/lights/"
    if marker in text:
        return os.path.join(stacks, "lights", text.split(marker, 1)[1])
    return None


def _rewrite(node: Any, rewrite: Callable[[str], str]) -> Any:
    """Rewrite every string of a parsed JSON value with a function.

    Parameters
    ----------
    node : `object`
        A parsed JSON value.
    rewrite : `callable`
        Takes a string and returns the new string.

    Returns
    -------
    new_node : `object`
        The value with the strings replaced.
    """
    if isinstance(node, dict):
        return {key: _rewrite(child, rewrite) for key, child in node.items()}
    if isinstance(node, list):
        return [_rewrite(child, rewrite) for child in node]
    if isinstance(node, str):
        return rewrite(node)
    return node


def _repoint_database(
    database_file: str,
    tables: tuple[str, ...] | None,
    rewrite: Callable[[str], str],
) -> None:
    """Rewrite the strings of one database in place.

    Parameters
    ----------
    database_file : `str`
        The SQLite database file, already known to be inside the copy.
    tables : `tuple` [`str`] or `None`
        The tables to rewrite, or `None` for every table.
    rewrite : `callable`
        Takes a string and returns the new string.
    """
    connection = sqlite3.connect(database_file)
    try:
        existing = _table_names(connection)
        for table in existing if tables is None else [name for name in tables if name in existing]:
            for column in _text_columns(connection, table):
                select = _statement(_SELECT_TEMPLATE, table, column, existing)
                update = _statement(_UPDATE_TEMPLATE, table, column, existing)
                for rowid, value in connection.execute(select).fetchall():
                    if not isinstance(value, str):
                        continue
                    try:
                        new_value = json.dumps(_rewrite(json.loads(value), rewrite))
                    except ValueError:
                        new_value = rewrite(value)
                    if new_value != value:
                        connection.execute(update, (new_value, rowid))
        connection.commit()
    finally:
        connection.close()


def mirror_path(text: str, scratch: str) -> str:
    """Give the place inside the copy that stands in for an outside path.

    Parameters
    ----------
    text : `str`
        A stored absolute path outside the copy.
    scratch : `str`
        The folder that holds the copies.

    Returns
    -------
    mirrored : `str`
        ``<scratch>/outside/<text without its leading slash>``.
    """
    return os.path.join(scratch, _MIRROR_FOLDER, os.path.normpath(text).lstrip("/"))


def repoint(
    config_file: str | os.PathLike,
    scratch: str,
    real_data_folder: str | None = None,
    mirror_outside: bool = False,
) -> int:
    """Rewrite bad stored paths inside the COPY databases.

    Parameters
    ----------
    config_file : `str` or `os.PathLike`
        The copy config file.
    scratch : `str`
        The folder that holds the copies.
    real_data_folder : `str`, optional
        The real data folder. Defaults to ``$XDG_DATA_HOME/astrometrics`` or
        ``~/.local/share/astrometrics`` of the calling environment.
    mirror_outside : `bool`, optional
        If `True`, skip the stacks and data-folder rules and move every
        stored path that is still outside the copy (and not under the
        frames folder) to ``<scratch>/outside/<original path>``. Use it
        after the known paths were already rewritten, so that a stale path,
        such as an old stacks folder, cannot send a run outside the copy.

    Returns
    -------
    changed : `int`
        How many stored paths were rewritten.

    Raises
    ------
    ValueError
        If a database to edit is not inside ``scratch``.
    """
    scratch = os.path.realpath(scratch)
    library = read_config_value(config_file, "path")
    stacks = read_config_value(config_file, "stacks_path")
    frames = read_config_value(config_file, "frames_path")
    if real_data_folder is None:
        data_root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        real_data_folder = os.path.join(data_root, "astrometrics")
    changed = 0

    def rewrite(text: str) -> str:
        """Replace one string when it is a bad path with a known mapping.

        Parameters
        ----------
        text : `str`
            A stored string.

        Returns
        -------
        new_text : `str`
            The new path, or the text unchanged.
        """
        nonlocal changed
        if _ABSOLUTE_PATH.match(text) and not is_allowed(text, scratch, frames):
            if mirror_outside:
                new_text = mirror_path(text, scratch)
            else:
                new_text = _repoint_text(text, stacks, library, real_data_folder)
            if new_text is not None:
                changed += 1
                return new_text
        return text

    databases = database_files(config_file)
    for database_file, _ in databases:
        if not is_under(os.path.realpath(database_file), scratch):
            raise ValueError(f"refusing to edit {database_file}: it is not inside {scratch}")
    for database_file, tables in databases:
        _repoint_database(database_file, tables, rewrite)
    return changed


def _outside_rewriter(scratch: str, counter: list[int]) -> Callable[[str], str]:
    """Build a function that moves an outside absolute path into the copy.

    Parameters
    ----------
    scratch : `str`
        The folder that holds the copy.
    counter : `list` [`int`]
        One number, increased for every path moved.

    Returns
    -------
    rewrite : `callable`
        Takes a string; returns its mirror under ``<scratch>/outside`` when
        it is an absolute path outside ``scratch``, else the string.
    """

    def rewrite(text: str) -> str:
        """Move one stored string when it is an outside absolute path.

        Parameters
        ----------
        text : `str`
            A stored string.

        Returns
        -------
        new_text : `str`
            The mirrored path, or the text unchanged.
        """
        if _ABSOLUTE_PATH.match(text) and not is_under(text, scratch):
            counter[0] += 1
            return mirror_path(text, scratch)
        return text

    return rewrite


def mirror_stale_paths(
    databases: list[tuple[str, tuple[str, ...] | None]], json_files: list[str], scratch: str
) -> int:
    """Move every stored path that is outside the copy to a mirror inside it.

    Use it after the known folders were rewritten, so a stale path (for
    example an old stacks folder that is not on any list) cannot send a run
    outside the copy.

    Parameters
    ----------
    databases : `list` [`tuple`]
        ``(file, tables)`` for each database copy; ``tables`` is `None` for
        every table.
    json_files : `list` [`str`]
        JSON files of the copy that hold absolute paths.
    scratch : `str`
        The folder that holds the copy.

    Returns
    -------
    changed : `int`
        How many stored paths were moved.

    Raises
    ------
    ValueError
        If a file to edit is not inside ``scratch``.
    """
    scratch = os.path.realpath(scratch)
    for file in [*(name for name, _ in databases), *json_files]:
        if not is_under(os.path.realpath(file), scratch):
            raise ValueError(f"refusing to edit {file}: it is not inside {scratch}")
    counter = [0]
    rewrite = _outside_rewriter(scratch, counter)
    for database_file, tables in databases:
        _repoint_database(database_file, tables, rewrite)
    for file in json_files:
        path = Path(file)
        text = path.read_text(encoding="utf-8")
        try:
            new_text = json.dumps(_rewrite(json.loads(text), rewrite), indent=2)
        except ValueError:
            new_text = rewrite(text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
    return counter[0]


def outside_paths(
    databases: list[tuple[str, tuple[str, ...] | None]], json_files: list[str], scratch: str
) -> list[tuple[str, str, str, str, str]]:
    """List the stored absolute paths in the copy files that leave the copy.

    Nothing is allowed outside ``scratch``: not even the real frames folder.

    Parameters
    ----------
    databases : `list` [`tuple`]
        ``(file, tables)`` for each database copy.
    json_files : `list` [`str`]
        JSON files of the copy that hold absolute paths.
    scratch : `str`
        The folder that holds the copy.

    Returns
    -------
    offenders : `list` [`tuple`]
        ``(file name, table, row label, key trail, path)`` for each bad path.
    """
    scratch = os.path.realpath(scratch)
    offenders = []
    for database_file, tables in databases:
        offenders += find_offending_paths(database_file, tables, scratch, "")
    for file in json_files:
        text = Path(file).read_text(encoding="utf-8")
        try:
            strings = list(walk_strings(json.loads(text)))
        except ValueError:
            strings = [("", text)]
        offenders += [
            (Path(file).name, "json", "", trail, found)
            for trail, found in strings
            if _ABSOLUTE_PATH.match(found) and not is_under(found, scratch)
        ]
    return offenders


def _say(text: str) -> None:
    """Write one line to standard error.

    Parameters
    ----------
    text : `str`
        The line, without its line break.
    """
    sys.stderr.write(text + "\n")


def main(arguments: list[str]) -> int:
    """Run the command line: ``check <config>``, used by realrun.sh.

    Parameters
    ----------
    arguments : `list` [`str`]
        ``["check", config_file]``. The scratch folder comes from
        ASTROMETRICS_SCRATCH.

    Returns
    -------
    exit_code : `int`
        0 when clean, 1 when offending paths were found, 2 for a usage
        error.
    """
    if len(arguments) != 2 or arguments[0] != "check" or not os.environ.get("ASTROMETRICS_SCRATCH"):
        _say("usage: ASTROMETRICS_SCRATCH=... copy_path_guard.py check <copy_config>")
        return 2
    offenders = check(arguments[1], os.environ["ASTROMETRICS_SCRATCH"])
    if not offenders:
        return 0
    _say("the copy stores paths outside the copy (a run could write to the real library):")
    for database, table, label, trail, path in offenders[:_PRINT_LIMIT]:
        _say(f"  {database} {table} {label} {trail}: {path}")
    if len(offenders) > _PRINT_LIMIT:
        _say(f"  ... and {len(offenders) - _PRINT_LIMIT} more")
    _say("fix a fresh copy with: build/agent_tools/repoint_copy_paths.py <copy_config>")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
