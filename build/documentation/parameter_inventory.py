"""Purpose: Find the numeric constants and config defaults of the libraries.

Description: Every tuned value is a ``TunedParameter`` declaration, which is
not a number literal. This module lists the plain numeric constants
(module-level and class-level UPPER_CASE names built only from number
literals) and the numeric config defaults (calls such as
``config.get("Section", "key", fallback="3")``) in ``astrometricslib/`` and
``wayfindinglib/``. ``astrometricslib/test/test_parameter_inventory.py``
requires each one to be on exactly one of the two lists in
``build/documentation/parameter_inventory/``: ``not_tuning.csv`` (decided not
tuning, with a reason) or ``tuning_not_registered.csv`` (tuning values that
are not declared yet, such as dict or nested-tuple values).

Run ``python build/documentation/parameter_inventory.py --report`` for counts.
"""

from __future__ import annotations

import argparse
import ast
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LIBRARY_FOLDERS = ("astrometricslib", "wayfindinglib")
LIST_FOLDER = Path(__file__).resolve().parent / "parameter_inventory"
NOT_TUNING_FILE = LIST_FOLDER / "not_tuning.csv"
BACKLOG_FILE = LIST_FOLDER / "tuning_not_registered.csv"
CONFIG_METHODS = ("get", "get_value")
CONSTANT_NAME = re.compile(r"_?[A-Z][A-Z0-9_]*")
NUMBER_TEXT = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")


@dataclass(frozen=True)
class ParameterCandidate:
    """One numeric constant or config default found in the code.

    Parameters
    ----------
    kind : `str`
        ``constant`` or ``config``.
    file : `str`
        Path of the source file, relative to the repository root.
    line : `int`
        Line number of the definition.
    name : `str`
        ``NAME``, ``Class.NAME`` or ``[Section] key``.
    value : `str`
        The value as written in the source.
    comment : `str`
        The comment above or the docstring below the definition; may be empty.
    """

    kind: str
    file: str
    line: int
    name: str
    value: str
    comment: str

    @property
    def bare_name(self) -> str:
        """The constant name or the config key, without its class or section.

        Returns
        -------
        bare : `str`
            The bare name.
        """
        return bare_name(self.name)


def bare_name(name: str) -> str:
    """Return the part of a qualified name after its class or section.

    Parameters
    ----------
    name : `str`
        ``Class.NAME``, ``NAME`` or ``[Section] key``.

    Returns
    -------
    bare : `str`
        The config key, or the text after the last dot.
    """
    if name.startswith("["):
        return name.split("] ", 1)[1]
    return name.rsplit(".", 1)[-1]


def _is_numeric(node: ast.AST) -> bool:
    """Say whether an expression is built only from number literals.

    Parameters
    ----------
    node : `ast.AST`
        The expression.

    Returns
    -------
    numeric : `bool`
        True when only number literals are used.
    """
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return _is_numeric(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_numeric(node.left) and _is_numeric(node.right)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return bool(node.elts) and all(_is_numeric(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.values) and all(_is_numeric(value) for value in node.values)
    return False


def _is_numeric_fallback(node: ast.AST) -> bool:
    """Say whether a fallback is a number or a string holding one.

    Parameters
    ----------
    node : `ast.AST`
        The fallback expression.

    Returns
    -------
    numeric : `bool`
        True for a number or a numeric string.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(NUMBER_TEXT.fullmatch(node.value.strip()))
    return _is_numeric(node)


def _string_value(node: ast.AST) -> str | None:
    """Read a string literal.

    Parameters
    ----------
    node : `ast.AST`
        Any expression.

    Returns
    -------
    text : `str` or `None`
        The text, or None when the node is not a string literal.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _nearby_comment(lines: list[str], start: int, end: int) -> str:
    """Read the comment above and the docstring below an assignment.

    Parameters
    ----------
    lines : `list` [`str`]
        Source lines of the file.
    start, end : `int`
        First and last line number (1-based) of the assignment.

    Returns
    -------
    comment : `str`
        The joined text, cut to 400 characters.
    """
    above: list[str] = []
    index = start - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        above.insert(0, lines[index].strip("# \n"))
        index -= 1
    below = ""
    if end < len(lines) and lines[end].strip().startswith(('"""', "'''")):
        chunk: list[str] = []
        position = end
        while position < len(lines) and len(chunk) < 6:
            chunk.append(lines[position].strip())
            if position > end and ('"""' in lines[position] or "'''" in lines[position]):
                break
            if position == end and lines[position].strip().count('"""') == 2:
                break
            position += 1
        below = " ".join(chunk).strip("\"' ")
    return " ".join([*above, below]).strip()[:400]


def _constants_in(tree: ast.Module, text: str, lines: list[str], relative: str) -> list[ParameterCandidate]:
    """Collect the numeric constants of one parsed file.

    Parameters
    ----------
    tree : `ast.Module`
        The parsed file.
    text : `str`
        Its source.
    lines : `list` [`str`]
        Source lines.
    relative : `str`
        Repository-relative path.

    Returns
    -------
    found : `list` [`ParameterCandidate`]
        Module-level and class-level constants.
    """
    found: list[ParameterCandidate] = []
    scopes: list[tuple[ast.AST, str]] = [(tree, "")]
    scopes += [(node, node.name + ".") for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    for scope, prefix in scopes:
        for node in scope.body:  # type: ignore[attr-defined]
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name, value = node.targets[0].id, node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                name, value = node.target.id, node.value
            else:
                continue
            if not CONSTANT_NAME.fullmatch(name) or not _is_numeric(value):
                continue
            found.append(
                ParameterCandidate(
                    kind="constant",
                    file=relative,
                    line=node.lineno,
                    name=prefix + name,
                    value=(ast.get_source_segment(text, value) or "")[:120],
                    comment=_nearby_comment(lines, node.lineno, node.end_lineno or node.lineno),
                )
            )
    return found


def _config_reads_in(
    tree: ast.Module, text: str, lines: list[str], relative: str
) -> list[ParameterCandidate]:
    """Collect config reads that have a numeric fallback.

    Parameters
    ----------
    tree : `ast.Module`
        The parsed file.
    text : `str`
        Its source.
    lines : `list` [`str`]
        Source lines.
    relative : `str`
        Repository-relative path.

    Returns
    -------
    found : `list` [`ParameterCandidate`]
        Reads with a literal section, a literal key and a numeric fallback.
    """
    found: list[ParameterCandidate] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in CONFIG_METHODS or len(node.args) < 2:
            continue
        section, key = _string_value(node.args[0]), _string_value(node.args[1])
        if section is None or key is None:
            continue
        fallback = node.args[2] if len(node.args) > 2 else None
        for keyword in node.keywords:
            if keyword.arg == "fallback":
                fallback = keyword.value
        if fallback is None or not _is_numeric_fallback(fallback):
            continue
        found.append(
            ParameterCandidate(
                kind="config",
                file=relative,
                line=node.lineno,
                name=f"[{section}] {key}",
                value=(ast.get_source_segment(text, fallback) or "")[:120],
                comment=_nearby_comment(lines, node.lineno, node.end_lineno or node.lineno),
            )
        )
    return found


def find_parameters(repo_root: Path) -> list[ParameterCandidate]:
    """Find the numeric constants and config defaults of the two libraries.

    Test folders, ``scripts`` folders and ``__pycache__`` are skipped.

    Parameters
    ----------
    repo_root : `pathlib.Path`
        Root of the repository (or of a tree laid out like it).

    Returns
    -------
    candidates : `list` [`ParameterCandidate`]
        Every candidate, sorted by file, then line.
    """
    found: list[ParameterCandidate] = []
    for folder in LIBRARY_FOLDERS:
        for path in sorted((repo_root / folder).rglob("*.py")):
            relative = path.relative_to(repo_root).as_posix()
            if "/test" in relative or "/scripts/" in relative or "__pycache__" in relative:
                continue
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            lines = text.splitlines()
            found += _constants_in(tree, text, lines, relative)
            found += _config_reads_in(tree, text, lines, relative)
    return sorted(found, key=lambda candidate: (candidate.file, candidate.line, candidate.name))


def read_list(path: Path) -> list[dict[str, str]]:
    """Read one of the two inventory CSV files.

    Parameters
    ----------
    path : `pathlib.Path`
        The CSV file, with a header row.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One dictionary per row; empty when the file does not exist.
    """
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def classify(repo_root: Path) -> dict[str, list]:
    """Sort each found parameter by the list that accounts for it.

    Parameters
    ----------
    repo_root : `pathlib.Path`
        Root of the repository.

    Returns
    -------
    groups : `dict` [`str`, `list`]
        Keys ``found``, ``not_tuning``, ``backlog``, ``unlisted``.
    """
    candidates = find_parameters(repo_root)
    not_tuning = {(row["file"], row["name"]) for row in read_list(NOT_TUNING_FILE)}
    backlog = {(row["file"], row["name"]) for row in read_list(BACKLOG_FILE)}
    groups: dict[str, list] = {"found": [], "not_tuning": [], "backlog": [], "unlisted": []}
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate.file, candidate.name)
        if key in seen:
            continue
        seen.add(key)
        groups["found"].append(candidate)
        if key in not_tuning:
            groups["not_tuning"].append(candidate)
        elif key in backlog:
            groups["backlog"].append(candidate)
        else:
            groups["unlisted"].append(candidate)
    return groups


def main(argv: list[str] | None = None) -> int:
    """Print the inventory report.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; defaults to ``sys.argv``.

    Returns
    -------
    exit_code : `int`
        1 when any parameter is not listed, otherwise 0.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--report", action="store_true", help="print the counts")
    args = parser.parse_args(argv)
    groups = classify(args.repo)
    unlisted = groups["unlisted"]
    if args.report:
        print(f"found: {len(groups['found'])}")
        print(f"listed not tuning: {len(groups['not_tuning'])}")
        print(f"listed tuning, not registered: {len(groups['backlog'])}")
        print(f"unlisted: {len(unlisted)}")
    for candidate in unlisted:
        print(f"  {candidate.file}:{candidate.line} {candidate.name} = {candidate.value}")
    return 1 if unlisted else 0


if __name__ == "__main__":
    sys.exit(main())
