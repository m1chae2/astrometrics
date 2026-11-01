"""Purpose: Keep private library modules out of code that uses public names.

Description: The linters (ruff's banned-api rule and import-linter) read
`import` statements. A program can skip them by naming a module in a string,
as in `importlib.import_module("astrometricslib._internal.x")`. This test
reads every string in the code that must use only public names and fails
if one names a private module:

* `astrometricslib._internal`: not for the backend, the MCP servers, or
  `wayfindinglib` (outside its tests).
* `wayfindinglib._internal`: not for the backend or the MCP servers.

Tests and test helpers are not scanned, because a test may reach into a
library to set up a case. Docstrings are not scanned either, since they only
describe code.
"""

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
"""The folder that holds `backend`, `mcp_servers` and the two libraries."""

SCANNED_FOLDERS = {
    "backend": ("astrometricslib._internal", "wayfindinglib._internal"),
    "mcp_servers": ("astrometricslib._internal", "wayfindinglib._internal"),
    "wayfindinglib": ("astrometricslib._internal",),
}
"""For each folder, the private module names no string in it may contain."""


def _is_test_file(path: Path) -> bool:
    """Say whether a file is a test, a test helper or test configuration.

    Parameters
    ----------
    path : `pathlib.Path`
        The file.

    Returns
    -------
    is_test : `bool`
        `True` for files under a `test` or `tests` folder, files named
        ``test_*.py`` and ``conftest.py``.
    """
    return (
        "test" in path.parts
        or "tests" in path.parts
        or path.name.startswith("test_")
        or path.name == "conftest.py"
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Find the string nodes that are docstrings.

    Parameters
    ----------
    tree : `ast.AST`
        A parsed module.

    Returns
    -------
    node_ids : `set` [`int`]
        The `id` of each docstring's string node.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return docstrings


def private_name_strings(source: str, forbidden: tuple[str, ...]) -> list[tuple[int, str]]:
    """List the strings in some code that contain a private module name.

    Parameters
    ----------
    source : `str`
        The Python source text.
    forbidden : `tuple` [`str`]
        The private module names to look for.

    Returns
    -------
    found : `list` [`tuple` [`int`, `str`]]
        The line number and the text of each string that contains a name.
        The pieces of an f-string are checked one by one.
    """
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and any(name in node.value for name in forbidden)
        ):
            found.append((node.lineno, node.value))
    return found


def test_the_scan_finds_a_module_named_in_a_string() -> None:
    """A string passed to `import_module` is found, and a docstring is not."""
    source = (
        '"""Mentions astrometricslib._internal only in a docstring."""\n'
        "import importlib\n"
        'importlib.import_module("astrometricslib._internal.stacking")\n'
        'importlib.import_module(f"{base}.astrometricslib._internal")\n'
    )
    found = private_name_strings(source, ("astrometricslib._internal",))
    assert [line for line, _text in found] == [3, 4]


@pytest.mark.parametrize("folder", sorted(SCANNED_FOLDERS))
def test_no_string_names_a_private_library_module(folder: str) -> None:
    """No scanned code names a private library module in a string."""
    forbidden = SCANNED_FOLDERS[folder]
    problems = []
    for path in sorted((REPOSITORY_ROOT / folder).rglob("*.py")):
        if _is_test_file(path.relative_to(REPOSITORY_ROOT)):
            continue
        for line, text in private_name_strings(path.read_text(encoding="utf-8"), forbidden):
            problems.append(f"{path.relative_to(REPOSITORY_ROOT)}:{line}: {text!r}")
    assert not problems, "Strings that name a private module:\n" + "\n".join(problems)
