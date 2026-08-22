"""Description: Static MCP tools to reduce dependency on raw terminal commands.

Provides verify_syntax, run_tests, and get_mcp_reflection_contracts tools.
"""

import ast
import inspect
import os
from typing import Any

from backend.mcp.tool_registry import get_astrometrics, registry


@registry.register(
    name="backend_verify_syntax",
    description="Verifies syntax for specified Python files using internal AST compilation checks.",
    input_schema={
        "type": "object",
        "properties": {
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of absolute file paths to check.",
            }
        },
        "required": ["file_paths"],
    },
)
async def verify_syntax(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute
    file_paths: list[str],
) -> dict[str, Any]:
    """Validate Python files for syntax errors using AST parsing.

    Parameters
    ----------
    file_paths : `list` [`str`]
        Absolute paths to the Python files to check.

    Returns
    -------
    result : `dict`
        Includes ``"status"``, ``"verified_successfully"`` (paths
        that parsed cleanly), and ``"syntax_errors"`` (details for
        paths that failed to parse or could not be read).
    """
    errors = []
    successes = []

    for path in file_paths:
        if not os.path.exists(path):
            errors.append({"path": path, "error": "File does not exist"})
            continue

        try:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            ast.parse(source, filename=path)
            successes.append(path)
        except SyntaxError as e:
            errors.append({
                "path": path,
                "error": str(e),
                "lineno": e.lineno,
                "offset": e.offset,
                "text": e.text.strip() if e.text else None,
            })
        except Exception as e:
            errors.append({"path": path, "error": f"Failed to read file: {e}"})

    return {
        "status": "success" if not errors else "failed",
        "verified_successfully": successes,
        "syntax_errors": errors,
    }


@registry.register(
    name="backend_run_tests",
    description="Programmatically run project tests using the correct virtualenv pytest package.",
    input_schema={
        "type": "object",
        "properties": {
            "test_path": {"type": "string", "description": "Specific test file or directory path"},
            "filter_keyword": {"type": "string", "description": "-k argument to filter tests"},
        },
    },
)
async def run_tests(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute
    test_path: str | None = None, filter_keyword: str | None = None
) -> dict[str, Any]:
    """Find the active virtualenv and run pytest programmatically.

    Parameters
    ----------
    test_path : `str`, optional
        Specific test file or directory, relative to the repository
        root. If `None` (default), runs `backend` and
        `astrometrics/tests`.
    filter_keyword : `str`, optional
        Keyword passed to pytest's ``-k`` option to filter tests.

    Returns
    -------
    result : `dict`
        On completion, includes ``"status"``, ``"returncode"``,
        ``"stdout"``, and ``"stderr"``. On timeout, path-traversal
        rejection, or unexpected failure, includes ``"status"`` set
        to ``"error"`` and ``"message"``.
    """
    import subprocess

    astrometrics = get_astrometrics()
    repo_root = str(astrometrics.config.get_project_root()) if astrometrics else os.getcwd()

    # Try local .venv first, then fallback to poetry
    python_bin = os.path.join(repo_root, ".venv", "bin", "python")
    if not os.path.exists(python_bin):
        python_bin = "python"
        cmd = ["poetry", "run", "python", "-m", "pytest"]
    else:
        cmd = [python_bin, "-m", "pytest"]

    # Append test paths and filters
    if test_path:
        target_abs = os.path.abspath(os.path.join(repo_root, test_path))
        if not target_abs.startswith(repo_root):
            return {"status": "error", "message": "Access denied: Path is outside the repository root."}
        cmd.append(target_abs)
    else:
        cmd.extend(["backend", "astrometrics/tests"])

    if filter_keyword:
        cmd.extend(["-k", filter_keyword])

    cmd.append("-v")

    try:
        res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, timeout=90)
        return {
            "status": "success" if res.returncode == 0 else "failed",
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Test execution timed out."}
    except Exception as e:
        return {"status": "error", "message": f"Execution failed: {e!s}"}


@registry.register(
    name="backend_get_reflection_contracts",
    description=(
        "Inspects the high-level interface dynamically and reports signature contracts to verify parity."
    ),
)
async def get_mcp_reflection_contracts(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute
) -> dict[str, Any]:
    """Inspect all astrometrics class methods using dynamic introspection.

    Returns
    -------
    result : `dict`
        On success, includes ``"status"`` and ``"astrometrics_branches"``
        (mapping of branch name to a list of method signatures and
        docstring summaries). On failure, includes ``"status"`` and
        ``"message"``.
    """
    astrometrics = get_astrometrics()
    if not astrometrics:
        return {"status": "error", "message": "Astrometrics high-level interface not available."}

    branches = {}
    for branch_name in ["observatory", "observation", "astronomy", "analysis"]:
        branch = getattr(astrometrics, branch_name, None)
        if not branch:
            continue

        branches[branch_name] = {}
        # Since branch_name might be a class or object with methods
        # directly, we extract methods
        methods = []
        for method_name in dir(branch):
            if method_name.startswith("_"):
                continue
            method = getattr(branch, method_name)
            if not callable(method):
                continue

            sig = inspect.signature(method)
            doc = inspect.getdoc(method) or ""
            doc_summary = doc.split("\n\n")[0] if "\n\n" in doc else doc

            methods.append({"name": method_name, "signature": str(sig), "summary": doc_summary})
        branches[branch_name] = methods

    return {"status": "success", "astrometrics_branches": branches}


@registry.register(
    name="backend_audit_imports",
    description=(
        "Audits a target directory using AST parsing to ensure no files violate package import boundaries."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target_dir": {
                "type": "string",
                "description": "Relative path of the folder to audit (e.g. 'backend').",
            },
            "forbidden_prefixes": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of package/module prefixes that are forbidden (e.g. ['astrometrics.utilities'])."
                ),
            },
        },
        "required": ["target_dir", "forbidden_prefixes"],
    },
)
async def audit_imports(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute
    target_dir: str, forbidden_prefixes: list[str]
) -> dict[str, Any]:
    """Check a directory for forbidden imports using AST parsing.

    Parameters
    ----------
    target_dir : `str`
        Relative directory path to search and scan.
    forbidden_prefixes : `list` [`str`]
        Package prefixes that are forbidden.

    Returns
    -------
    result : `dict`
        Includes ``"status"``, ``"violations_count"``, and
        ``"violations"`` (list of violating import nodes found).
    """
    astrometrics = get_astrometrics()
    repo_root = str(astrometrics.config.get_project_root()) if astrometrics else os.getcwd()
    search_path = os.path.abspath(os.path.join(repo_root, target_dir))

    if not search_path.startswith(repo_root) and not search_path.startswith("/tmp"):
        return {"status": "error", "message": "Access denied: Path is outside repository root."}

    violations = []
    for root, _, files in os.walk(search_path):
        if any(part in root for part in [".venv", "venv", "__pycache__", ".pytest_cache"]):
            continue
        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)
            try:
                with open(file_path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=file_path)
            except Exception as e:
                violations.append({"file": file_path, "error": f"Failed to parse AST: {e}"})
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for prefix in forbidden_prefixes:
                            if alias.name == prefix or alias.name.startswith(prefix + "."):
                                violations.append({
                                    "file": os.path.relpath(file_path, repo_root),
                                    "line": node.lineno,
                                    "import": f"import {alias.name}",
                                    "reason": f"Imports from {prefix} are forbidden.",
                                })
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        for prefix in forbidden_prefixes:
                            if node.module == prefix or node.module.startswith(prefix + "."):
                                violations.append({
                                    "file": os.path.relpath(file_path, repo_root),
                                    "line": node.lineno,
                                    "import": f"from {node.module} import ...",
                                    "reason": f"Imports from {prefix} are forbidden.",
                                })

    return {
        "status": "success" if not violations else "failed",
        "violations_count": len(violations),
        "violations": violations,
    }


@registry.register(
    name="backend_rewrite_imports",
    description=(
        "Surgically refactors files within a directory, updating targeted legacy imports "
        "to a clean target package prefix."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target_dir": {"type": "string", "description": "Relative path of folder to migrate."},
            "legacy_prefix": {
                "type": "string",
                "description": "Dotted import prefix to replace (e.g. 'astrometrics.utilities').",
            },
            "new_prefix": {
                "type": "string",
                "description": "Dotted import prefix to write in its place (e.g. 'astrometrics').",
            },
        },
        "required": ["target_dir", "legacy_prefix", "new_prefix"],
    },
)
async def rewrite_imports(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute
    target_dir: str, legacy_prefix: str, new_prefix: str
) -> dict[str, Any]:
    """Rewrite legacy imports to clean API astrometrics paths in a directory.

    Parameters
    ----------
    target_dir : `str`
        Relative directory path where imports should be rewritten.
    legacy_prefix : `str`
        Deprecated or private prefix to replace.
    new_prefix : `str`
        Clean replacement prefix.

    Returns
    -------
    result : `dict`
        Includes ``"status"``, ``"modified_files_count"``, and
        ``"modified_files"`` (relative paths of modified files).
    """
    astrometrics = get_astrometrics()
    repo_root = str(astrometrics.config.get_project_root()) if astrometrics else os.getcwd()
    search_path = os.path.abspath(os.path.join(repo_root, target_dir))

    if not search_path.startswith(repo_root) and not search_path.startswith("/tmp"):
        return {"status": "error", "message": "Access denied: Path is outside repository root."}

    modified_files = []
    for root, _, files in os.walk(search_path):
        if any(part in root for part in [".venv", "venv", "__pycache__", ".pytest_cache"]):
            continue
        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # Perform a surgical line-by-line replace for simple
            # 'from X import Y' re-routing
            lines = content.splitlines(keepends=True)
            changed = False
            for i, line in enumerate(lines):
                # Search for typical from-imports
                if line.strip().startswith(f"from {legacy_prefix}"):
                    lines[i] = line.replace(f"from {legacy_prefix}", f"from {new_prefix}")
                    changed = True
                elif line.strip().startswith(f"import {legacy_prefix}"):
                    lines[i] = line.replace(f"import {legacy_prefix}", f"import {new_prefix}")
                    changed = True

            if changed:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                modified_files.append(os.path.relpath(file_path, repo_root))

    return {
        "status": "success",
        "modified_files_count": len(modified_files),
        "modified_files": modified_files,
    }
