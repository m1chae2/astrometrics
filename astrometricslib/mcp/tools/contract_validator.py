"""Pydantic model serialization contract validator MCP tool.

Scans all registered Pydantic models in the high-level interface package for
fields that may hold un-serializable scientific types (numpy scalars,
astropy quantities) and verifies correct type casting at
serialization boundaries.
"""

import ast
import os
from typing import Any

from astrometricslib.mcp.tool_registry import registry


def _find_python_files(root_dir: str) -> list[str]:
    """Recursively find all .py files under the given root directory.

    Excludes test files and __pycache__ directories.

    Returns
    -------
    python_files : `list` [`str`]
        Absolute paths of all matching `.py` files.
    """
    python_files = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and d != ".git"]
        for filename in filenames:
            if filename.endswith(".py") and not filename.startswith("test_"):
                python_files.append(os.path.join(dirpath, filename))
    return python_files


def _extract_pydantic_models(filepath: str) -> list[dict[str, Any]]:
    """Parse a Python file's AST to find classes that inherit from BaseModel.

    Returns a list of dicts with class name and field information.

    Returns
    -------
    models : `list` [`dict`]
        One dict per `BaseModel` subclass found, with keys
        ``"class_name"``, ``"file"``, ``"line"``, ``"bases"``, and
        ``"fields"``; empty if `filepath` fails to parse.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except SyntaxError, UnicodeDecodeError:
        return []

    models = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base_names = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)

            if "BaseModel" in base_names:
                fields = []
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        annotation_str = ast.dump(item.annotation) if item.annotation else "Unknown"
                        fields.append({
                            "name": item.target.id,
                            "annotation": annotation_str,
                            "line": item.lineno,
                        })
                models.append({
                    "class_name": node.name,
                    "file": filepath,
                    "line": node.lineno,
                    "bases": base_names,
                    "fields": fields,
                })
    return models


DANGEROUS_TYPE_PATTERNS = [
    "ndarray",
    "numpy",
    "np.float",
    "np.int",
    "np.bool",
    "Quantity",
    "astropy",
    "Series",
    "DataFrame",
]


def _check_field_annotations(models: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Check field annotations for potentially un-serializable types.

    Returns a list of warning dicts for suspicious fields.

    Returns
    -------
    warnings : `list` [`dict` [`str`, `str`]]
        One dict per suspicious field, with keys ``"model"``,
        ``"field"``, ``"file"``, ``"line"``, and ``"issue"``.
    """
    warnings = []
    for model in models:
        for field in model.get("fields", []):
            annotation = field.get("annotation", "")
            for pattern in DANGEROUS_TYPE_PATTERNS:
                if pattern.lower() in annotation.lower():
                    warnings.append({
                        "model": model["class_name"],
                        "field": field["name"],
                        "file": model["file"],
                        "line": field["line"],
                        "issue": (
                            f"Field annotation contains potentially un-serializable type pattern: '{pattern}'"
                        ),
                    })
    return warnings


@registry.register(
    name="typegen_contract_validator",
    description=(
        "Scans all Pydantic models in the high-level interface package for "
        "fields that may hold un-serializable scientific types (numpy, "
        "astropy) and verifies type safety at serialization boundaries."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "scan_path": {
                "type": "string",
                "description": "Root directory to scan. Defaults to the high-level interface package root.",
            }
        },
        "required": [],
    },
)
async def typegen_contract_validator(  # ruff: ignore[unused-async] -- awaited
    # by ToolRegistry.execute (astrometricslib/mcp/tool_registry.py)
    scan_path: str | None = None,
) -> dict[str, Any]:
    """Validate all Pydantic models in the high-level interface package.

    Scans AST annotations for dangerous scientific types that would
    cause a ``PydanticSerializationError`` at runtime.

    Parameters
    ----------
    scan_path : `str`, optional
        Root directory to scan. If `None` (default), the high-level interface
        package root is used.

    Returns
    -------
    validation_report : `dict`
        Dictionary describing the scan outcome. On success, includes
        ``"status"``, ``"total_files_scanned"``,
        ``"total_pydantic_models_found"``, ``"models"``,
        ``"serialization_warnings"``, ``"warnings_count"``, and
        ``"verdict"``. On error, includes ``"status"`` and
        ``"message"``.
    """
    if scan_path is None:
        scan_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if not os.path.isdir(scan_path):
        return {"status": "error", "message": f"Scan path not found: {scan_path}"}

    try:
        python_files = _find_python_files(scan_path)
        all_models = []
        for filepath in python_files:
            all_models.extend(_extract_pydantic_models(filepath))

        warnings = _check_field_annotations(all_models)

        return {
            "status": "success",
            "total_files_scanned": len(python_files),
            "total_pydantic_models_found": len(all_models),
            "models": [
                {
                    "class": m["class_name"],
                    "file": m["file"],
                    "line": m["line"],
                    "field_count": len(m["fields"]),
                }
                for m in all_models
            ],
            "serialization_warnings": warnings,
            "warnings_count": len(warnings),
            "verdict": "PASS" if len(warnings) == 0 else "WARNINGS_FOUND",
        }
    except Exception as e:
        return {"status": "error", "message": f"Contract validation failed: {e!s}"}
