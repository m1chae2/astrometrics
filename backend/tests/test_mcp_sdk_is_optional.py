"""Purpose: Check that the libraries and the backend never import the MCP SDK.

Description: The ``mcp`` package is an optional extra in ``pyproject.toml``.
Only the MCP servers in ``mcp_servers/`` need it. This test reads every
Python file in the two libraries and the backend and fails if one imports
``mcp``, so the code keeps working on a machine that installed the project
without the extra.
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

PACKAGES_WITHOUT_MCP = ("astrometricslib", "wayfindinglib", "backend")
"""Top-level packages that must run without the MCP SDK."""


def _imported_modules(path: Path) -> set[str]:
    """List the absolute module names a Python file imports.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to read.

    Returns
    -------
    modules : `set` [`str`]
        Each ``import x`` and ``from x import y`` module name. Relative
        imports are left out, because they stay inside the package.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_libraries_and_backend_never_import_the_mcp_sdk() -> None:
    """No file in the libraries or the backend imports ``mcp``."""
    offenders = []
    for package in PACKAGES_WITHOUT_MCP:
        for path in sorted((PROJECT_ROOT / package).rglob("*.py")):
            if any(name == "mcp" or name.startswith("mcp.") for name in _imported_modules(path)):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"These files import the optional MCP SDK: {offenders}"
