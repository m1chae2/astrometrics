"""Purpose: Dynamic astrometrics reflection engine for MCP tools.

Description: Introspects Python class high-level interfaces (signatures,
PEP-484 type hints, and Google/NumPy docstrings) to dynamically generate
JSON schemas and register public high-level interface methods directly
as MCP tools.
"""

import inspect
import re
import typing
from collections.abc import Callable
from typing import Any, Union


def parse_docstring_params(doc: str) -> dict[str, str]:
    """Parse Google and NumPy style docstrings for parameter descriptions.

    Parameters
    ----------
    doc : `str`
        Docstring text to parse.

    Returns
    -------
    param_descriptions : `dict` [`str`, `str`]
        Mapping of parameter name to its extracted description string.
    """
    if not doc:
        return {}

    param_descriptions = {}
    lines = doc.splitlines()
    in_params_section = False
    current_param = None

    sections = ("Returns", "Raises", "Yields", "Examples", "Notes")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped in ("Parameters", "Args:") or stripped.startswith(("Parameters", "Args:")):
            in_params_section = True
            current_param = None
            continue

        if in_params_section:
            if stripped.startswith(sections):
                in_params_section = False
                current_param = None
                continue

            if set(stripped) <= {"-", "="}:
                continue

            match_bullet = re.match(r"^\s*-\s*([a-zA-Z0-9_]+)\s*:\s*(.*)", line)
            if match_bullet:
                current_param = match_bullet.group(1)
                param_descriptions[current_param] = match_bullet.group(2).strip()
                continue

            match_numpy = re.match(r"^\s*([a-zA-Z0-9_]+)\s*:\s*(.*)", line)
            if match_numpy:
                current_param = match_numpy.group(1)
                param_descriptions[current_param] = match_numpy.group(2).strip()
                continue

            if current_param:
                existing = param_descriptions.get(current_param, "")
                param_descriptions[current_param] = (existing + " " + stripped).strip()

    return param_descriptions


def get_json_schema_type(python_type: Any) -> str:
    """Map a Python PEP-484 type hint to a JSON Schema type name.

    Parameters
    ----------
    python_type : `Any`
        Python type hint or construct (e.g. `int`, `str`, `Union`).

    Returns
    -------
    json_type_name : `str`
        The JSON Schema type string (e.g. ``"string"``, ``"integer"``).
    """
    if python_type is str:
        return "string"
    elif python_type is int:
        return "integer"
    elif python_type is float:
        return "number"
    elif python_type is bool:
        return "boolean"
    elif python_type in (list, tuple, set):
        return "array"
    elif python_type in (dict,):
        return "object"

    origin = typing.get_origin(python_type)
    if origin is Union:
        args = typing.get_args(python_type)
        non_none_args = [a for a in args if a is not type(None)]
        if non_none_args:
            return get_json_schema_type(non_none_args[0])
    elif origin in (list, tuple, set):
        return "array"
    elif origin in (dict,):
        return "object"

    return "string"


def generate_tool_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate a JSON Schema object from signature and docstrings.

    Parameters
    ----------
    func : `Callable`
        The target function to introspect.

    Returns
    -------
    schema : `dict` [`str`, `Any`]
        A JSON Schema dictionary with ``"type"``, ``"properties"``, and
        ``"required"`` keys.
    """
    signature = inspect.signature(func)
    try:
        type_hints = typing.get_type_hints(func)
    except Exception:
        type_hints = {}

    docstring = inspect.getdoc(func) or ""
    param_descs = parse_docstring_params(docstring)

    properties = {}
    required_params = []

    for param_name, param in signature.parameters.items():
        if param_name in ("self", "args", "kwargs"):
            continue

        param_type = type_hints.get(param_name, str)
        js_type = get_json_schema_type(param_type)
        desc = param_descs.get(param_name, f"Parameter {param_name}")

        properties[param_name] = {"type": js_type, "description": desc}

        if param.default == inspect.Parameter.empty:
            required_params.append(param_name)

    return {"type": "object", "properties": properties, "required": required_params}


def register_astrometrics_tools(
    registry: Any,
    astrometrics_instance: Any,
    branch_mapping: dict[str, str],
) -> int:
    """Introspect an astrometrics object and register public methods as tools.

    Parameters
    ----------
    registry : `ToolRegistry`
        The tool registry instance to register tools into.
    astrometrics_instance : `Any`
        Instantiated astrometrics object (e.g. `Astrometrics` or `Wayfinder`).
    branch_mapping : `dict` [`str`, `str`]
        Mapping of attribute name on the high-level interface
        (e.g. ``"targets"``) to its tool prefix (e.g. ``"target"``). A
        key of ``""`` maps to root astrometrics methods.

    Returns
    -------
    registered_count : `int`
        Total number of public tools dynamically registered.
    """
    count = 0

    for attr_name, prefix in branch_mapping.items():
        if attr_name == "":
            target_obj = astrometrics_instance
        else:
            target_obj = getattr(astrometrics_instance, attr_name, None)

        if not target_obj:
            continue

        for method_name in dir(target_obj):
            if method_name.startswith("_"):
                continue

            method = getattr(target_obj, method_name)
            if not callable(method):
                continue

            tool_name = f"{prefix}_{method_name}" if prefix else method_name

            # Extract human-readable summary from docstring
            doc = inspect.getdoc(method) or ""
            summary = doc.split("\n\n")[0] if "\n\n" in doc else doc
            if not summary:
                summary = f"Reflected tool {tool_name}"

            schema = generate_tool_schema(method)

            # Create closure for invocation
            def make_executor(target_callable: Callable[..., Any]):  # ruff: ignore[missing-return-type-private-function]
                async def execute_reflected(**kwargs: Any) -> Any:
                    if inspect.iscoroutinefunction(target_callable):
                        return await target_callable(**kwargs)
                    return target_callable(**kwargs)

                return execute_reflected

            registry.register(tool_name, summary, schema)(make_executor(method))
            count += 1

    return count
