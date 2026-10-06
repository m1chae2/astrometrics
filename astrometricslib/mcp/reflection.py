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

from astrometricslib.foundation.errors import NotFoundError


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
        synthetic `"batch:<camera>"` label for a many-target call, or
        `"unknown"` if neither is present.
    """
    target_value = kwargs.get("target")
    if target_value is not None and hasattr(target_value, "id"):
        return target_value.id
    if "camera_id" in kwargs and (target_value is None or isinstance(target_value, list)):
        return f"batch:{kwargs['camera_id']}"
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

    # The many-target form of `process_target`: a list of targets, or
    # `None` for every target, always with a camera.
    if "camera_id" not in kwargs or not (target_value is None or isinstance(target_value, list)):
        return None

    targets_api = getattr(astrometrics_instance, "targets", None)
    if targets_api is None:
        return None

    def snapshot_all_batch_targets() -> dict[str, Any]:
        """Read every affected target fresh and snapshot its quality.

        Returns
        -------
        snapshot : `dict` [`str`, `Any`]
            Each affected target's quality summaries, by target id.
        """
        # `.list()` always re-reads from disk (unlike `.get()`, which
        # prefers its in-memory cache); a fresh read matters here because
        # each target in the batch is actually processed in its own
        # `ProcessPoolExecutor` worker (see
        # `astrometricslib.pipelines.target_batch`), so this process's
        # cached copies would not reflect that work.
        fresh_targets_by_id = {t.id: t for t in targets_api.list()}
        if target_value:
            target_ids = [getattr(item, "id", item) for item in target_value]
        else:
            target_ids = list(fresh_targets_by_id)
        return {
            target_id: _snapshot_target_quality(fresh_targets_by_id[target_id])
            for target_id in target_ids
            if target_id in fresh_targets_by_id
        }

    return snapshot_all_batch_targets


def _prepare_arguments(
    kwargs: dict[str, Any],
    injected: dict[str, Callable[[], Any]],
    resolvers: dict[str, Callable[[Any], Any]] | None,
) -> dict[str, Any]:
    """Apply injected values and argument converters to one tool call.

    Parameters
    ----------
    kwargs : `dict` [`str`, `Any`]
        The arguments the client sent.
    injected : `dict` [`str`, `Callable`]
        Factories for parameters the server supplies. A factory fills its
        parameter when the client left it out or sent a plain string,
        since a client has no way to build the real object.
    resolvers : `dict` [`str`, `Callable`] or `None`
        Converters applied to any supplied parameter of the same name.

    Returns
    -------
    prepared : `dict` [`str`, `Any`]
        A new arguments dictionary ready to pass to the method.
    """
    prepared = dict(kwargs)
    for name, factory in injected.items():
        if prepared.get(name) is None or isinstance(prepared[name], str):
            prepared[name] = factory()
    for name, convert in (resolvers or {}).items():
        if name in prepared and prepared[name] is not None:
            prepared[name] = convert(prepared[name])
    return prepared


# Methods whose names start with one of these are never offered as tools.
# An AI client cannot be trusted to confirm a deletion with the person first
# (a target was once deleted on an unclear request), so deleting is left to
# the app's own UI, which asks the person directly.
WITHHELD_METHOD_PREFIXES = ("delete",)

# Parameters the server decides, never the client: whether a call shows up
# in the job list (the server already runs each slow call as a job), and
# progress callbacks, which a client cannot send. They are left out of every
# tool's schema.
SERVER_ONLY_PARAMETERS = ("register_job", "on_item_complete", "on_progress")


def register_astrometrics_tools(
    registry: Any,
    astrometrics_instance: Any,
    branch_mapping: dict[str, str],
    argument_resolvers: dict[str, Callable[[Any], Any]] | None = None,
    injected_arguments: dict[str, Callable[[], Any]] | None = None,
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
    argument_resolvers : `dict` [`str`, `Callable`], optional
        Converters from what an MCP client can send (a name, an id, an
        ISO time string) to the object a method needs. Keyed by
        parameter name; a converter runs on any tool call that supplies
        that parameter. A converter raises `ValueError` with a plain
        message when it cannot convert, so the client sees the reason
        rather than a later `AttributeError`.
    injected_arguments : `dict` [`str`, `Callable`], optional
        Factories for parameters the server supplies itself (for example
        the `Astrometrics` handle). Keyed by parameter name. The
        parameter is hidden from the tool's schema, and the factory
        fills it whenever the client leaves it out or sends a string.

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
            if method_name.startswith("_") or method_name.startswith(WITHHELD_METHOD_PREFIXES):
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
            method_parameter_names = set(inspect.signature(method).parameters)
            method_injected = {
                name: factory
                for name, factory in (injected_arguments or {}).items()
                if name in method_parameter_names
            }
            for hidden_name in (*method_injected, *SERVER_ONLY_PARAMETERS):
                schema["properties"].pop(hidden_name, None)
                if hidden_name in schema["required"]:
                    schema["required"].remove(hidden_name)

            try:
                type_hints = typing.get_type_hints(method)
            except Exception:
                type_hints = {}

            # Create closure for invocation with domain model identifier
            # resolution
            def make_executor(  # ruff: ignore[missing-return-type-private-function]
                target_callable: Callable[..., Any],
                hints: dict[str, Any],
                injected: dict[str, Callable[[], Any]],
            ):
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
                                    # Another program (a frame sync, the
                                    # app) may have changed the catalog
                                    # since this server loaded it. A fresh
                                    # read takes about 0.1 s and keeps a
                                    # tool from reporting a stale frame list.
                                    if hasattr(targets_api, "list"):
                                        await asyncio.to_thread(targets_api.list)
                                    resolved = targets_api.get(param_v)
                                    if not resolved:
                                        raise NotFoundError(f"No target with id {param_v!r} in the library.")
                                    kwargs[param_k] = resolved

                    # Fill the server-supplied parameters and convert
                    # client-friendly values (names, ISO strings) in a
                    # worker thread: a converter may read the library or
                    # query SIMBAD, which must not block this server's
                    # single event loop.
                    if injected or argument_resolvers:
                        kwargs = await asyncio.to_thread(
                            _prepare_arguments, kwargs, injected, argument_resolvers
                        )

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

                    # A plain synchronous method still needs its own
                    # thread, not a direct call on this coroutine: some
                    # (e.g. wayfindinglib's `ObservatoryControl`, which
                    # bridges into async INDI drivers via
                    # `hardware_operations._run_sync`'s own
                    # `asyncio.run(...)`) start a *second* event loop
                    # internally, which Python refuses whenever the
                    # calling thread already has one running -- exactly
                    # this server's own loop. `backend/main_backend.py`'s
                    # periodic telemetry loop hit this same conflict
                    # calling the same hardware layer and fixed it the
                    # same way: run it via `asyncio.to_thread` so it gets
                    # a plain worker thread with no event loop of its own.
                    return await asyncio.to_thread(target_callable, **kwargs)

                return execute_reflected

            registry.register(tool_name, summary, schema)(make_executor(method, type_hints, method_injected))
            count += 1

    return count
