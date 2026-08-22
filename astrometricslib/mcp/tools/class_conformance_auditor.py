"""AST-based class diagram conformance auditor MCP tool.

Parses Mermaid class diagrams from Astrometrics_Functions.md and
compares them against actual Python class implementations to detect
architectural drift, undocumented methods, and excessive method
fragmentation.
"""

import ast
import os
import re
from typing import Any

from astrometricslib.mcp.tool_registry import registry


def _parse_mermaid_class_diagrams(markdown_path: str) -> dict[str, dict[str, Any]]:
    """Extract class definitions from Mermaid classDiagram blocks.

    Returns a dict keyed by class name, containing sets of declared
    methods and attributes.

    Returns
    -------
    class_diagrams : `dict` [`str`, `dict`]
        Mapping of class name to a dict with ``"methods"`` and
        ``"attributes"`` sets, or an empty dict if `markdown_path`
        does not exist.
    """
    if not os.path.exists(markdown_path):
        return {}

    with open(markdown_path, encoding="utf-8") as f:
        content = f.read()

    class_diagrams = {}

    # Find all mermaid code blocks that contain classDiagram
    mermaid_blocks = re.findall(r"```mermaid\s*\n(.*?)```", content, re.DOTALL)

    for block in mermaid_blocks:
        if "classDiagram" not in block:
            continue

        # Parse class definitions: class ClassName { ... }
        class_pattern = re.compile(r"class\s+(\w+)\s*\{(.*?)\}", re.DOTALL)

        for match in class_pattern.finditer(block):
            class_name = match.group(1)
            body = match.group(2)

            methods = set()
            attributes = set()

            for line in body.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue

                # Methods: +/-/# method_name(args) ReturnType
                method_match = re.match(r"[+\-#~](\w+)\(", line)
                if method_match:
                    methods.add(method_match.group(1))
                    continue

                # Attributes: +/-/# type attribute_name
                attr_match = re.match(r"[+\-#~](\w+)\s+(\w+)", line)
                if attr_match:
                    attributes.add(attr_match.group(2))

            class_diagrams[class_name] = {"methods": methods, "attributes": attributes}

    return class_diagrams


def _extract_python_classes(
    root_dir: str, target_classes: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Walk the high-level interface package and extract class definitions.

    Uses AST parsing. Returns a dict keyed by class name with sets of
    actual methods and attributes.

    Returns
    -------
    classes : `dict` [`str`, `dict`]
        Mapping of class name to a dict with ``"methods"``,
        ``"attributes"``, ``"file"``, ``"line"``, and
        ``"method_count"`` keys.
    """
    classes = {}

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and d != ".git" and d != "tests"]
        for filename in filenames:
            if not filename.endswith(".py") or filename.startswith("test_"):
                continue

            filepath = os.path.join(dirpath, filename)
            try:
                with open(filepath, encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=filepath)
            except SyntaxError, UnicodeDecodeError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue

                if target_classes and node.name not in target_classes:
                    continue

                methods = set()
                attributes = set()

                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.add(item.name)
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        attributes.add(item.target.id)

                # Also check __init__ for self.attribute assignments
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        for stmt in ast.walk(item):
                            if isinstance(stmt, ast.Assign):
                                for target in stmt.targets:
                                    if isinstance(target, ast.Attribute) and isinstance(
                                        target.value, ast.Name
                                    ):
                                        if target.value.id == "self":
                                            attributes.add(target.attr)

                classes[node.name] = {
                    "methods": methods,
                    "attributes": attributes,
                    "file": filepath,
                    "line": node.lineno,
                    "method_count": len(methods),
                }

    return classes


# Threshold for method fragmentation warning: classes with more
# methods than this relative to documented methods will be flagged.
FRAGMENTATION_THRESHOLD_RATIO = 2.0


@registry.register(
    name="audit_class_conformance",
    description=(
        "Parses Mermaid class diagrams in Astrometrics_Functions.md and compares them against actual "
        "Python implementations to detect architectural drift, undocumented methods, and method "
        "fragmentation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "functions_doc_path": {
                "type": "string",
                "description": (
                    "Path to the high-level interface_Functions.md file. Defaults to the standard "
                    "documentation location."
                ),
            },
            "source_root": {
                "type": "string",
                "description": "Root directory of the high-level interface package. Defaults to auto-detect.",
            },
        },
        "required": [],
    },
)
async def audit_class_conformance(  # ruff: ignore[unused-async] -- awaited
    # by ToolRegistry.execute (astrometricslib/mcp/tool_registry.py)
    functions_doc_path: str | None = None,
    source_root: str | None = None,
) -> dict[str, Any]:
    """Audit the codebase against the class diagrams in the docs.

    Compares Astrometrics_Functions.md against the actual codebase and
    reports undocumented methods, missing implementations, and method
    fragmentation warnings.

    Parameters
    ----------
    functions_doc_path : `str`, optional
        Path to the high-level interface_Functions.md file. If `None`
        (default), the standard documentation location relative to
        the package root is used.
    source_root : `str`, optional
        Root directory of the high-level interface package to audit. If
        `None` (default), the package root is auto-detected.

    Returns
    -------
    audit_report : `dict`
        Dictionary describing the audit outcome. On success, includes
        ``"status"``, ``"documented_classes_count"``,
        ``"audited_classes_count"``, ``"passed"``,
        ``"drift_detected"``, ``"missing_implementations"``,
        ``"results"``, and ``"verdict"``. On error or warning, includes
        ``"status"`` and ``"message"``.
    """
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if functions_doc_path is None:
        functions_doc_path = os.path.join(
            os.path.dirname(package_root), "documentation", "Astrometrics_Functions.md"
        )

    if source_root is None:
        source_root = package_root

    if not os.path.exists(functions_doc_path):
        return {"status": "error", "message": f"Astrometrics_Functions.md not found at: {functions_doc_path}"}

    try:
        documented_classes = _parse_mermaid_class_diagrams(functions_doc_path)

        if not documented_classes:
            return {
                "status": "warning",
                "message": "No Mermaid classDiagram blocks found in Astrometrics_Functions.md. "
                "Class diagrams must be added to enable conformance auditing.",
                "documented_classes": 0,
            }

        target_names = set(documented_classes.keys())
        actual_classes = _extract_python_classes(source_root, target_classes=target_names)

        audit_results = []

        for class_name, documented in documented_classes.items():
            if class_name not in actual_classes:
                audit_results.append({
                    "class": class_name,
                    "status": "MISSING_IMPLEMENTATION",
                    "message": (f"Class '{class_name}' is documented in diagrams but not found in codebase."),
                })
                continue

            actual = actual_classes[class_name]
            documented_methods = documented["methods"]
            actual_methods = actual["methods"]

            # Methods in diagram but not in code
            missing_methods = documented_methods - actual_methods
            # Methods in code but not in diagram
            undocumented_methods = actual_methods - documented_methods
            # Filter out dunder methods from undocumented (they are
            # typically not diagrammed)
            undocumented_public = {m for m in undocumented_methods if not m.startswith("__")}

            # Method fragmentation check
            fragmentation_warning = None
            if len(documented_methods) > 0:
                ratio = len(actual_methods) / len(documented_methods)
                if ratio > FRAGMENTATION_THRESHOLD_RATIO:
                    fragmentation_warning = (
                        f"Class has {len(actual_methods)} actual methods vs "
                        f"{len(documented_methods)} documented. "
                        f"Ratio {ratio:.1f}x exceeds threshold of "
                        f"{FRAGMENTATION_THRESHOLD_RATIO}x. "
                        f"Review for excessive method fragmentation."
                    )

            status = "PASS"
            if missing_methods or undocumented_public or fragmentation_warning:
                status = "DRIFT_DETECTED"

            result = {
                "class": class_name,
                "file": actual["file"],
                "status": status,
                "documented_methods": sorted(documented_methods),
                "actual_method_count": len(actual_methods),
                "missing_from_code": sorted(missing_methods) if missing_methods else [],
                "undocumented_in_diagram": sorted(undocumented_public) if undocumented_public else [],
            }

            if fragmentation_warning:
                result["fragmentation_warning"] = fragmentation_warning

            audit_results.append(result)

        passed = sum(1 for r in audit_results if r["status"] == "PASS")
        drifted = sum(1 for r in audit_results if r["status"] == "DRIFT_DETECTED")
        missing = sum(1 for r in audit_results if r["status"] == "MISSING_IMPLEMENTATION")

        return {
            "status": "success",
            "documented_classes_count": len(documented_classes),
            "audited_classes_count": len(audit_results),
            "passed": passed,
            "drift_detected": drifted,
            "missing_implementations": missing,
            "results": audit_results,
            "verdict": "PASS" if drifted == 0 and missing == 0 else "DRIFT_DETECTED",
        }
    except Exception as e:
        return {"status": "error", "message": f"Class conformance audit failed: {e!s}"}
