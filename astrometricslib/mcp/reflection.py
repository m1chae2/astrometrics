"""Purpose: Dynamic astrometrics reflection engine for MCP tools.

Description: Introspects Python class high-level interfaces (signatures,
PEP-484 type hints, and Google/NumPy docstrings) to dynamically generate
JSON schemas and register public high-level interface methods directly
as MCP tools.
"""

import asyncio
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


def _snapshot_target_quality(target: Any) -> dict[str, Any]:
    """Collect one target's persisted, per-pipeline quality summaries.

    Meant to be called both right before and right after a background
    job runs, so the job record can show what actually changed -- not
    just that the job succeeded. Captures references to the current
    summary objects rather than copies; safe because each pipeline stage
    replaces its summary field with a new object on completion rather
    than mutating the old one's fields in place, so a reference taken
    before the run is unaffected by the run itself.

    Returns
    -------
    quality : `dict`
        The target's stack/spectral-stack/astrometry/photometry/
        spectroscopy quality summaries (any not yet computed are `None`).
    """
    return {
        "stack": target.stacking.quality_summary,
        "spectralStack": target.spectral_stacking.quality_summary,
        "astrometry": target.quality.astrometry,
        "photometry": target.quality.photometry,
        "spectroscopy": target.quality.spectroscopy,
    }


def _infer_background_job_target_id(kwargs: dict[str, Any]) -> str:
    """Choose a job-tracking target id from a background-job call's arguments.

    Returns
    -------
    target_id : `str`
        The resolved `Target`'s own id for a single-target call, a
        synthetic `"batch:<camera>"` label for an all-targets call, or
        `"unknown"` if neither is present.
    """
    target_value = kwargs.get("target")
    if target_value is not None and hasattr(target_value, "id"):
        return target_value.id
    if "camera_name" in kwargs:
        return f"batch:{kwargs['camera_name']}"
    return "unknown"


def _make_quality_snapshot_fn(
    astrometrics_instance: Any, kwargs: dict[str, Any]
) -> Callable[[], dict[str, Any]] | None:
    """Build a snapshot function for a background-job call, if one applies.

    Returns
    -------
    snapshot_fn : `Callable` or `None`
        A no-argument function returning `{target_id: quality_summaries}`
        for the target(s) this call affects, or `None` if the call's
        arguments don't identify any (so no snapshot is taken).
    """
    target_value = kwargs.get("target")
    if target_value is not None and hasattr(target_value, "id"):
        return lambda: {target_value.id: _snapshot_target_quality(target_value)}

    if "camera_name" not in kwargs:
        return None

    targets_api = getattr(astrometrics_instance, "targets", None)
    if targets_api is None:
        return None

    def snapshot_all_batch_targets() -> dict[str, Any]:
        # `.list()` always re-reads from disk (unlike `.get()`, which
        # prefers its in-memory cache); a fresh read matters here because
        # each target in the batch is actually processed in its own
        # `ProcessPoolExecutor` worker (see `astrometricslib.api.batch`),
        # so this process's cached copies would not reflect that work.
        fresh_targets_by_id = {t.id: t for t in targets_api.list()}
        explicit_ids = kwargs.get("target_ids")
        target_ids = list(explicit_ids) if explicit_ids else list(fresh_targets_by_id)
        return {
            target_id: _snapshot_target_quality(fresh_targets_by_id[target_id])
            for target_id in target_ids
            if target_id in fresh_targets_by_id
        }

    return snapshot_all_batch_targets


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
        key of ``""`` maps to root astrometrics methods. A dotted name
        (e.g. ``"processing.diagnostics"``) walks nested attributes.

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
            target_obj = astrometrics_instance
            for part in attr_name.split("."):
                target_obj = getattr(target_obj, part, None)
                if target_obj is None:
                    break

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

            try:
                type_hints = typing.get_type_hints(method)
            except Exception:
                type_hints = {}

            # Create closure for invocation with domain model identifier
            # resolution
            def make_executor(target_callable: Callable[..., Any], hints: dict[str, Any]):  # ruff: ignore[missing-return-type-private-function]
                async def execute_reflected(**kwargs: Any) -> Any:
                    # Auto-resolve target string IDs to Target domain
                    # instances if expected
                    from astrometricslib.models.target import Target

                    for param_k, param_v in list(kwargs.items()):
                        expected_type = hints.get(param_k)
                        if expected_type is not None:
                            # Handle Target or Target | None
                            type_args = typing.get_args(expected_type) or (expected_type,)
                            if Target in type_args and isinstance(param_v, str):
                                targets_api = getattr(astrometrics_instance, "targets", None)
                                if targets_api and hasattr(targets_api, "get"):
                                    resolved = targets_api.get(param_v)
                                    if resolved:
                                        kwargs[param_k] = resolved

                    # A method marked with `@background_job` (see
                    # `astrometricslib.drivers.job_logging`) runs slowly
                    # enough that calling it directly here would block this
                    # server's single connection for its whole duration.
                    # Run it in a background thread instead, and return
                    # either its real result (if it finishes quickly) or a
                    # job id to poll -- see `run_as_background_job`.
                    job_type = getattr(target_callable, "__background_job_type__", None)
                    if job_type is not None:
                        from astrometricslib.drivers.job_logging import run_as_background_job

                        grace_period = getattr(target_callable, "__background_job_grace_period__", 5.0)
                        target_id = _infer_background_job_target_id(kwargs)
                        snapshot_fn = _make_quality_snapshot_fn(astrometrics_instance, kwargs)
                        return await asyncio.to_thread(
                            run_as_background_job,
                            job_type,
                            target_id,
                            lambda job: target_callable(**kwargs),
                            grace_period_seconds=grace_period,
                            snapshot_fn=snapshot_fn,
                        )

                    if inspect.iscoroutinefunction(target_callable):
                        return await target_callable(**kwargs)
                    return target_callable(**kwargs)

                return execute_reflected

            registry.register(tool_name, summary, schema)(make_executor(method, type_hints))
            count += 1

    return count
