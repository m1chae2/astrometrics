"""Purpose: Limit AI-written code to the public astrometrics libraries.

Description: Code sent by an AI agent (the ``electron_run_python`` MCP tool)
runs inside the backend, where it could reach the app's internal services,
the database, the file system or the shell. This module checks the code
before it runs and allows only:

* the public names of ``astrometricslib`` and ``wayfindinglib`` (what they
  list in ``__all__``), reached through the ``astrometrics`` and ``wayfinder``
  objects or imported by name;
* analysis packages (NumPy, SciPy, pandas, Matplotlib, Astropy and a few
  standard modules) for working with the results.

Private names (starting with ``_``), ways to build names at run time
(``getattr``, ``eval``), file and shell access, and deletion are refused.
The checks are a strong guard against mistakes and clever shortcuts, but they
are not a security sandbox. The agent also runs in a reduced scope (see
``ScriptingService``) so the backend's internal services are not in reach by
name, and deleted records are archived as a last safety net.
"""

import ast
import importlib

from backend.services.infrastructure.destructive_guard import destructive_code_reason

AGENT_VISIBLE_NAMES = frozenset({
    "help",
    "list_commands",
    "inspect_api",
    "doc",
    "np",
    "plt",
    "pd",
    "astropy",
    "astrometrics",
    "wayfinder",
})
"""The only names from the backend's console that agent code can see."""

LIBRARY_ROOTS = frozenset({"astrometricslib", "wayfindinglib"})
"""Packages whose public (``__all__``) names may be imported."""

ANALYSIS_ROOTS = frozenset(
    {
        "numpy", "scipy", "pandas", "matplotlib", "astropy", "math", "cmath", "statistics", "itertools",
        "functools", "collections", "datetime", "json", "re", "typing", "dataclasses", "fractions", "decimal",
        "random", "operator", "heapq", "bisect", "copy", "textwrap", "string", "enum",
    }
)  # fmt: skip
"""Analysis and standard packages that may be imported freely."""

BANNED_NAMES = frozenset(
    {
        "eval", "exec", "compile", "open", "input", "breakpoint", "getattr", "setattr", "delattr", "globals",
        "locals", "vars", "importlib", "builtins", "os", "sys", "subprocess", "shutil", "pathlib", "socket",
        "ctypes", "pickle", "marshal", "container", "memoryview",
    }
)  # fmt: skip
"""Names (variables or attributes) that give access to the system."""

BANNED_ATTRIBUTES = frozenset(
    {
        "to_csv", "to_pickle", "to_parquet", "to_excel", "to_hdf", "to_feather", "to_json", "to_sql",
        "to_clipboard", "savetxt", "savez", "savez_compressed", "save", "tofile", "fromfile", "load",
        "loadtxt", "genfromtxt", "savefig", "dump", "writeto", "write", "read", "open",
    }
)  # fmt: skip
"""Attribute names that read or write files (names starting ``read_`` too)."""


def _library_public_names(module_name: str) -> set[str]:
    """List the public names a library package exports.

    Returns
    -------
    names : `set` of `str`
        The entries of the package's ``__all__``.
    """
    return set(getattr(importlib.import_module(module_name), "__all__", ()))


def _import_reason(node: ast.Import | ast.ImportFrom) -> str | None:
    """Say why an import statement is refused, or `None` if it is allowed.

    Returns
    -------
    reason : `str` or `None`
        The message for the client, or `None` when the import is fine.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in LIBRARY_ROOTS and alias.name != root:
                return f"Import {alias.name!r} reaches past the public API; import {root!r} instead."
            if root not in LIBRARY_ROOTS | ANALYSIS_ROOTS:
                return f"Importing {alias.name!r} is not allowed."
        return None
    module_name = node.module or ""
    root = module_name.split(".")[0]
    if node.level or root not in LIBRARY_ROOTS | ANALYSIS_ROOTS:
        return f"Importing from {module_name!r} is not allowed."
    if root in LIBRARY_ROOTS:
        if module_name != root:
            return f"Import from {module_name!r} reaches past the public API; use 'from {root} import ...'."
        public_names = _library_public_names(root)
        for alias in node.names:
            if alias.name not in public_names:
                return f"{alias.name!r} is not part of the public {root} API."
    return None


def check_agent_code(code: str) -> str | None:
    """Say why AI-written code is refused, or `None` if it may run.

    Parameters
    ----------
    code : `str`
        The Python source about to run in the backend.

    Returns
    -------
    reason : `str` or `None`
        A message for the client, or `None` when the code passes. Code that
        does not parse is passed on so the normal syntax error is shown.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            reason = _import_reason(node)
            if reason:
                return reason
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                return f"Private attribute {node.attr!r} is not allowed; use the public API."
            if node.attr in BANNED_ATTRIBUTES | BANNED_NAMES or node.attr.startswith("read_"):
                return f"{node.attr!r} is not allowed (file, system or dynamic access)."
        elif isinstance(node, ast.Name):
            if node.id.startswith("_") and node.id != "_":
                return f"Private name {node.id!r} is not allowed."
            if node.id in BANNED_NAMES:
                return f"{node.id!r} is not allowed (file, system or dynamic access)."
    return destructive_code_reason(code)
