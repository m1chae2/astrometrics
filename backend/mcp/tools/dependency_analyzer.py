"""Description: Static MCP tool for detecting import cycles.

Detects circular dependencies across backend python files. Leverages
AST parsing to map module dependencies and check for recursive DFS
import loops. # REQ: AGENT-3.1
"""

import ast
import logging
import os
from typing import Any

from backend.mcp.tool_registry import registry

logger = logging.getLogger(__name__)


def parse_imports_from_file(file_path: str, repo_root: str) -> list[str]:
    """Parse a python file using AST to extract all local project imports.

    Parameters
    ----------
    file_path : `str`
        Path to the python file to parse.
    repo_root : `str`
        Base repository root directory.

    Returns
    -------
    result : `list` [`str`]
        Dotted module names imported by `file_path` that belong to
        the `backend` package.
    """
    imports = []
    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    if name.name.startswith("backend"):
                        imports.append(name.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module.startswith("backend") or node.level > 0):
                    # Resolve relative imports
                    mod = node.module
                    if node.level > 0:
                        # Convert relative path to absolute module notation
                        rel_dir = os.path.dirname(file_path)
                        rel_parts = os.path.normpath(os.path.join(rel_dir, "../" * (node.level - 1))).split(
                            os.sep
                        )
                        try:
                            start_idx = rel_parts.index("backend")
                            base_mod = ".".join(rel_parts[start_idx:])
                            mod = f"{base_mod}.{mod}" if mod else base_mod
                        except ValueError:
                            pass
                    if mod:
                        imports.append(mod)
    except Exception as e:
        logger.warning(f"Failed to parse imports for {file_path}: {e}")

    return imports


def detect_import_cycles_in_graph(graph: dict[str, list[str]]) -> list[list[str]]:
    """Find all cyclic pathways in a dependency graph using DFS.

    Parameters
    ----------
    graph : `dict`
        Dependency adjacency list, mapping module name to the list
        of modules it imports.

    Returns
    -------
    result : `list` [`list` [`str`]]
        Each entry is a cyclic import path through `graph`.
    """
    cycles = []
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str):  # ruff: ignore[missing-return-type-private-function]
        if node in stack:
            # Found a cycle! Capture the cycle loop path
            idx = stack.index(node)
            cycle_path = [*stack[idx:], node]
            # Ensure unique cycles by canonical sorting/hashing if
            # necessary, or keep as is
            if cycle_path not in cycles:
                cycles.append(cycle_path)
            return

        if node in visited:
            return

        stack.append(node)
        for neighbor in graph.get(node, []):
            dfs(neighbor)
        stack.pop()
        visited.add(node)

    for node in graph:
        dfs(node)

    return cycles


@registry.register(
    name="import_cycle_analyzer",
    description=(
        "Parses python source files to build an import dependency graph and identify cyclic import loops."
    ),
    input_schema={"type": "object", "properties": {}},
)
# ruff: ignore[unused-async] -- awaited by ToolRegistry.execute, which
# calls every registered tool via `await func(**arguments)`.
async def import_cycle_analyzer() -> dict[str, Any]:
    """Map imports across the backend python sources with AST parsing.

    Returns
    -------
    result : `dict`
        On success, includes ``"status"``, ``"message"``, and
        ``"modules_scanned"``. When cycles are found, ``"status"``
        is ``"warning"`` and a ``"cycles"`` list is included instead
        of ``"modules_scanned"``. On failure, includes ``"status"``
        set to ``"error"`` and ``"message"``.
    """
    try:
        # Find repo root directory
        cwd = os.getcwd()
        repo_root = cwd
        while repo_root != "/" and not os.path.exists(os.path.join(repo_root, "backend")):
            repo_root = os.path.dirname(repo_root)

        backend_path = os.path.join(repo_root, "backend")
        if not os.path.exists(backend_path):
            return {"status": "error", "message": f"Backend source directory not found at {backend_path}."}

        dependency_graph = {}
        file_to_module = {}

        # 1. Walk directory and map files to module strings
        for root, _, files in os.walk(backend_path):
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_parts = os.path.relpath(full_path, repo_root).replace(".py", "").split(os.sep)
                    module_name = ".".join(rel_parts)
                    file_to_module[full_path] = module_name

        # 2. Parse imports from each file
        for full_path, module_name in file_to_module.items():
            raw_imports = parse_imports_from_file(full_path, repo_root)
            # Resolve to exact known modules in our project
            resolved_imports = []
            for imp in raw_imports:
                # Find matching module or sub-module prefix
                for known_mod in file_to_module.values():
                    if imp == known_mod or imp.startswith(known_mod + "."):
                        resolved_imports.append(known_mod)
                        break

            dependency_graph[module_name] = list(set(resolved_imports))

        # 3. Detect cyclic import paths
        cycles = detect_import_cycles_in_graph(dependency_graph)

        if cycles:
            formatted_cycles = []
            for cycle in cycles:
                formatted_cycles.append(" -> ".join(cycle))
            return {
                "status": "warning",
                "message": f"Found {len(cycles)} circular import cycles in the codebase!",
                "cycles": formatted_cycles,
            }

        return {
            "status": "success",
            "message": "No circular import cycles detected. The codebase architecture is completely healthy!",
            "modules_scanned": len(dependency_graph),
        }
    except Exception as e:
        return {"status": "error", "message": f"Dependency analysis failed: {e}"}
