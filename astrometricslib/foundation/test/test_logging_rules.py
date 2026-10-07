"""Tests that keep the repository's logging rules from eroding.

A library or service module writes log messages and never decides where they
go. Only a program's entry point, or the shared logging code itself, may
configure logging. This test reads every source file and fails when another
module calls a configuring function, so a new violation cannot slip in.
"""

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

#: Calls that change where log messages go, or how loud they are.
CONFIGURING_CALLS = frozenset({"basicConfig", "addHandler", "removeHandler", "setLevel", "dictConfig"})

#: Files that may configure logging, with the reason for each. A program's
#: entry point decides where its messages go. The shared logging code is the
#: one place that implements that decision. A file that has an unavoidable
#: need goes on this list with its reason, and not anywhere else.
ALLOWED_FILES: dict[str, str] = {
    "astrometricslib/foundation/logging.py": "implements configure_logging and the job log router",
    "backend/main_backend.py": "program entry point",
    "backend/mcp/__main__.py": "program entry point",
    "backend/mcp/gaps/__main__.py": "program entry point that imports only the standard library",
    "mcp_servers/astrometrics_core/__main__.py": "program entry point",
    "mcp_servers/wayfinding_core/__main__.py": "program entry point",
}

#: Source folders to scan. Tests and notebooks may configure logging freely.
SCANNED_FOLDERS = ("astrometricslib", "wayfindinglib", "backend", "mcp_servers")


def _is_exempt(path: Path) -> bool:
    """Say whether a file may configure logging without being listed.

    Parameters
    ----------
    path : `pathlib.Path`
        A source file.

    Returns
    -------
    exempt : `bool`
        `True` for tests, scripts (each a program of its own), and
        ``conftest.py`` files.
    """
    parts = path.relative_to(REPOSITORY_ROOT).parts
    return "test" in parts or "tests" in parts or "scripts" in parts or path.name == "conftest.py"


def _is_null_handler_call(call: ast.Call) -> bool:
    """Say whether a call adds a `logging.NullHandler`.

    A library may add a null handler to its own logger. That stops Python from
    printing a "no handlers" warning and does not decide where messages go.

    Parameters
    ----------
    call : `ast.Call`
        A call to `addHandler` or another configuring function.

    Returns
    -------
    is_null_handler : `bool`
        `True` if the call is ``addHandler(logging.NullHandler())``.
    """
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "addHandler" and len(call.args) == 1):
        return False
    argument = call.args[0]
    return (
        isinstance(argument, ast.Call)
        and isinstance(argument.func, ast.Attribute)
        and argument.func.attr == "NullHandler"
    )


def _configuring_calls(path: Path) -> list[tuple[int, str]]:
    """Find the calls in a file that configure logging.

    Parameters
    ----------
    path : `pathlib.Path`
        A Python source file.

    Returns
    -------
    calls : `list` [`tuple` [`int`, `str`]]
        The line number and name of each configuring call.
    """
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in CONFIGURING_CALLS and not _is_null_handler_call(node):
                found.append((node.lineno, name))
    return found


def _source_files() -> list[Path]:
    """List the source files this test scans.

    Returns
    -------
    files : `list` [`pathlib.Path`]
        Every non-exempt Python file under the scanned folders.
    """
    files = []
    for folder in SCANNED_FOLDERS:
        files.extend(path for path in (REPOSITORY_ROOT / folder).rglob("*.py") if not _is_exempt(path))
    return sorted(files)


# These modules call `setLevel` or `addHandler` today. Each one is a separate
# piece of work in ARCHITECTURE_PLAN.md and leaves this list when it is done.
KNOWN_VIOLATIONS: frozenset[str] = frozenset({
    "astrometricslib/drivers/job_logging.py",
    "astrometricslib/drivers/siril_interface.py",
    "astrometricslib/utilities/parallel_batch.py",
    "backend/services/infrastructure/base_service.py",
})


@pytest.mark.parametrize("path", _source_files(), ids=lambda path: str(path.relative_to(REPOSITORY_ROOT)))
def test_only_entry_points_configure_logging(path: Path) -> None:
    """Fail when a module outside the allowed files configures logging."""
    relative = str(path.relative_to(REPOSITORY_ROOT))
    calls = _configuring_calls(path)
    if relative in ALLOWED_FILES or relative in KNOWN_VIOLATIONS:
        return
    assert not calls, (
        f"{relative} configures logging at lines {calls}. Use `configure_logging` in an entry point."
    )


def test_the_known_violations_still_exist() -> None:
    """Fail when a listed file has been fixed, so the list shrinks."""
    stale = [name for name in sorted(KNOWN_VIOLATIONS) if not _configuring_calls(REPOSITORY_ROOT / name)]
    assert not stale, f"These files no longer configure logging. Remove them from KNOWN_VIOLATIONS: {stale}"
