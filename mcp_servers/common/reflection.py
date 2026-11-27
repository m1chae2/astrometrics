"""Purpose: Turn the libraries' public methods into MCP tools.

Description: Reads each public method's signature, type hints and numpydoc
docstring to build a JSON schema, and registers the method as a tool. A
client sends only JSON, so before the call the tool turns three kinds of
argument into what the method takes, chosen by the parameter's type hint:

* a subject (`Target`, `StellarObject`, an observing session or an
  observation package): the client sends its id, and `resolvers` looks it
  up through the public API;
* an input model (a pydantic model, or a list of them): the tool offers
  the model's JSON schema, and `input_models` builds the model;
* a frame choice (`FrameChoice`): the client sends a small filter, and
  `frame_choice_form` builds the choice with the public frame query.

Every other argument is passed on as it is. A method marked with
`background_job` is run as a recorded job in a background thread, so a
slow call answers with the id of its job instead of blocking the server.
A server can also narrow what a client may send to one tool with
`ClientLimits`: hide arguments a client cannot send (an array of pixels),
withhold choices of no use to it (a Matplotlib figure) and lower a count's
maximum so that the answer stays small.
"""

import asyncio
import inspect
import re
import typing
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Union

from astrometricslib.exceptions import InvalidArgumentError
from mcp_servers.common.frame_choice_form import build_frame_choice, frame_filter_schema, is_frame_choice
from mcp_servers.common.input_models import (
    ModelParameter,
    build_model_argument,
    model_parameter_of,
    model_schema,
)
from mcp_servers.common.resolvers import (
    SUBJECT_ENTRY_METHODS,
    SubjectParameter,
    resolve_subject,
    subject_kind_of,
    subject_schema,
)


@dataclass(frozen=True)
class ClientLimits:
    """What a client may not send to one reflected tool.

    Attributes
    ----------
    hidden_parameters : `tuple` [`str`, ...]
        Arguments left out of the tool's schema and refused if sent, such
        as a NumPy array that JSON cannot carry.
    withheld_choices : `Mapping` [`str`, `tuple` [`str`, ...]]
        For an argument typed as a set of choices (a `Literal`), the
        choices a client may not use. The schema lists only the others as
        an ``enum``, and a call with a withheld one is refused.
    maximums : `Mapping` [`str`, `int`]
        For a count argument, the largest value a client may send, lower
        than the library's own maximum so that the answers stay small. The
        schema gives it as ``maximum``, and a call above it is refused.
    defaults : `Mapping` [`str`, `Any`]
        For an argument the client leaves out, the value the tool sends the
        library instead of the library's own default, such as a page size
        for a query that would otherwise return every match.
    """

    hidden_parameters: tuple[str, ...] = ()
    withheld_choices: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    maximums: Mapping[str, int] = field(default_factory=dict)
    defaults: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParameterPlan:
    """Which of a method's parameters the tool turns into objects first.

    Attributes
    ----------
    subjects : `Mapping` [`str`, `SubjectParameter`]
        Parameters that take a subject, sent as an id.
    models : `Mapping` [`str`, `ModelParameter`]
        Parameters that take an input model, sent as JSON.
    frame_choices : `tuple` [`str`, ...]
        Parameters that take a `FrameChoice`, sent as a filter object.
    """

    subjects: Mapping[str, SubjectParameter] = field(default_factory=dict)
    models: Mapping[str, ModelParameter] = field(default_factory=dict)
    frame_choices: tuple[str, ...] = ()


def plan_parameters(func: Callable[..., Any], method_name: str | None = None) -> ParameterPlan:
    """Find the parameters a tool must turn into objects before the call.

    Parameters
    ----------
    func : `Callable`
        The library method.
    method_name : `str`, optional
        Its name. The subject parameter of ``get``, ``add``, ``create`` and
        ``resolve`` is left alone (`SUBJECT_ENTRY_METHODS`): an id enters
        the library there, or the object is new. Defaults to the
        function's own name.

    Returns
    -------
    plan : `ParameterPlan`
        The subject, input model and frame choice parameters. A parameter
        whose name holds ``path`` is never a subject: it takes a file path
        or an object.
    """
    try:
        type_hints = typing.get_type_hints(func)
    except NameError, TypeError, AttributeError:
        return ParameterPlan()
    name = method_name or getattr(func, "__name__", "")
    subjects: dict[str, SubjectParameter] = {}
    models: dict[str, ModelParameter] = {}
    frame_choices: list[str] = []
    for parameter_name in inspect.signature(func).parameters:
        hint = type_hints.get(parameter_name)
        if hint is None:
            continue
        if is_frame_choice(hint):
            frame_choices.append(parameter_name)
            continue
        subject = subject_kind_of(hint)
        if subject is not None:
            if name not in SUBJECT_ENTRY_METHODS and "path" not in parameter_name:
                subjects[parameter_name] = subject
            continue
        model = model_parameter_of(hint)
        if model is not None:
            models[parameter_name] = model
    return ParameterPlan(subjects, models, tuple(frame_choices))


def prepare_arguments(api_root: Any, plan: ParameterPlan, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a client's JSON arguments into what the method takes.

    Subjects are looked up first, so a frame filter can use the call's
    target; then input models are built; then frame choices.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object, through which ids are looked up.
    plan : `ParameterPlan`
        Which parameters to turn into objects.
    kwargs : `Mapping` [`str`, `Any`]
        The client's arguments.

    Returns
    -------
    arguments : `dict` [`str`, `Any`]
        The arguments to call the method with.

    Notes
    -----
    The lookups raise `NotFoundError` if an id names nothing, and
    `InvalidArgumentError` if a model or a frame filter is not valid.
    """
    arguments = dict(kwargs)
    for name, subject in plan.subjects.items():
        if name in arguments:
            arguments[name] = resolve_subject(api_root, subject, name, arguments[name])
    for name, model in plan.models.items():
        if name in arguments:
            arguments[name] = build_model_argument(model, name, arguments[name])
    for name in plan.frame_choices:
        if name in arguments:
            arguments[name] = build_frame_choice(api_root, name, arguments[name], arguments)
    return arguments


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


def generate_tool_schema(func: Callable[..., Any], plan: ParameterPlan | None = None) -> dict[str, Any]:
    """Generate a JSON Schema object from signature and docstrings.

    Parameters
    ----------
    func : `Callable`
        The target function to introspect.
    plan : `ParameterPlan`, optional
        The parameters the tool turns into objects. A subject is offered as
        its id, an input model as the model's JSON schema (its nested
        definitions under ``$defs``), and a frame choice as a filter object.

    Returns
    -------
    schema : `dict` [`str`, `Any`]
        A JSON Schema dictionary with ``"type"``, ``"properties"``, and
        ``"required"`` keys.
    """
    signature = inspect.signature(func)
    try:
        type_hints = typing.get_type_hints(func)
    except NameError, TypeError, AttributeError:
        # A hint that names a type this module cannot see cannot be
        # resolved. The schema then falls back to the plain signature.
        type_hints = {}

    docstring = inspect.getdoc(func) or ""
    param_descs = parse_docstring_params(docstring)

    properties = {}
    required_params = []
    definitions: dict[str, Any] = {}
    plan = plan or ParameterPlan()

    for param_name, param in signature.parameters.items():
        if param_name in ("self", "args", "kwargs"):
            continue

        param_type = type_hints.get(param_name, str)
        js_type = get_json_schema_type(param_type)
        desc = param_descs.get(param_name, f"Parameter {param_name}")

        if param_name in plan.subjects:
            properties[param_name] = subject_schema(plan.subjects[param_name], desc)
        elif param_name in plan.models:
            properties[param_name], nested = model_schema(plan.models[param_name], desc)
            definitions.update(nested)
        elif param_name in plan.frame_choices:
            properties[param_name] = frame_filter_schema(desc)
        else:
            properties[param_name] = {"type": js_type, "description": desc}

        if param.default == inspect.Parameter.empty:
            required_params.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties, "required": required_params}
    if definitions:
        schema["$defs"] = definitions
    return schema


# Methods whose names start with one of these are never offered as tools.
# An AI client cannot be trusted to confirm a deletion with the person first
# (a target was once deleted on an unclear request), so deleting is left to
# the app's own UI, which asks the person directly.
WITHHELD_METHOD_PREFIXES = ("delete",)

# Parameters the server decides, never the client: progress callbacks,
# which a client cannot send. They are left out of every
# tool's schema.
SERVER_ONLY_PARAMETERS = ("on_item_complete", "on_progress", "on_copied")


def _job_target(kwargs: dict[str, Any]) -> str | None:
    """Name the target a slow call works on, for its job record.

    Parameters
    ----------
    kwargs : `dict` [`str`, `Any`]
        The arguments the client sent.

    Returns
    -------
    target_id : `str` or `None`
        The ``target`` or ``target_id`` argument when it is text, else
        `None` (the job is then named after its type).
    """
    for name in ("target", "target_id"):
        value = kwargs.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _apply_client_limits(schema: dict[str, Any], method: Callable[..., Any], limits: ClientLimits) -> None:
    """Narrow a tool's schema to what a client may send.

    Parameters
    ----------
    schema : `dict` [`str`, `Any`]
        The tool's schema, changed in place.
    method : `Callable`
        The library method, whose `Literal` type hints give the choices.
    limits : `ClientLimits`
        What the client may not send.
    """
    for hidden_name in limits.hidden_parameters:
        schema["properties"].pop(hidden_name, None)
        if hidden_name in schema["required"]:
            schema["required"].remove(hidden_name)
    try:
        type_hints = typing.get_type_hints(method)
    except NameError, TypeError, AttributeError:
        type_hints = {}
    for name, withheld in limits.withheld_choices.items():
        hint = type_hints.get(name)
        if name not in schema["properties"] or typing.get_origin(hint) is not Literal:
            continue
        offered = [choice for choice in typing.get_args(hint) if choice not in withheld]
        schema["properties"][name]["enum"] = offered
        schema["properties"][name]["description"] += (
            " This tool offers only: " + ", ".join(repr(choice) for choice in offered) + "."
        )
    for name, maximum in limits.maximums.items():
        if name not in schema["properties"]:
            continue
        schema["properties"][name]["maximum"] = maximum
        schema["properties"][name]["description"] += f" This tool allows at most {maximum}."
    for name, default in limits.defaults.items():
        if name not in schema["properties"]:
            continue
        schema["properties"][name]["description"] += f" This tool uses {default!r} when it is left out."


def _check_client_limits(tool_name: str, arguments: Mapping[str, Any], limits: ClientLimits) -> None:
    """Refuse a call that sends what the tool's limits keep from a client.

    Parameters
    ----------
    tool_name : `str`
        The tool being called, for the message.
    arguments : `Mapping` [`str`, `Any`]
        The client's arguments.
    limits : `ClientLimits`
        What the client may not send.

    Raises
    ------
    InvalidArgumentError
        If a hidden argument or a withheld choice was sent.
    """
    hidden = sorted(name for name in limits.hidden_parameters if name in arguments)
    if hidden:
        raise InvalidArgumentError(
            f"{tool_name} does not take: {', '.join(hidden)}.", details={"tool": tool_name, "unused": hidden}
        )
    for name, withheld in limits.withheld_choices.items():
        if arguments.get(name) in withheld:
            raise InvalidArgumentError(
                f"{tool_name} does not offer {name}={arguments[name]!r}.",
                details={"tool": tool_name, name: arguments[name]},
            )
    for name, maximum in limits.maximums.items():
        value = arguments.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value > maximum:
            raise InvalidArgumentError(
                f"{tool_name} allows {name} up to {maximum}; got {value}.",
                details={"tool": tool_name, name: value, "maximum": maximum},
            )


def register_reflected_tools(
    registry: Any,
    api_object: Any,
    branch_mapping: dict[str, str],
    skip: Collection[str] = (),
    limits: Mapping[str, ClientLimits] | None = None,
) -> int:
    """Register the public methods of a library's API object as tools.

    A tool turns the client's ids, model JSON and frame filters into
    objects (see `plan_parameters`) and passes every other argument on as
    it is.

    Parameters
    ----------
    registry : `ToolRegistry`
        The tool registry instance to register tools into.
    api_object : `Any`
        The library's top-level object (`Astrometrics` or `Wayfinder`).
    branch_mapping : `dict` [`str`, `str`]
        Mapping of attribute name on the high-level interface
        (e.g. ``"targets"``) to its tool prefix (e.g. ``"target"``). A
        key of ``""`` maps to root astrometrics methods. A dotted name
        (e.g. ``"processing.calibration"``) walks nested attributes.
    skip : `Collection` [`str`], optional
        Tool names to leave out, such as the tools another server serves
        (`mcp_servers.common.app_job_tools.APP_JOB_TOOLS`).
    limits : `Mapping` [`str`, `ClientLimits`], optional
        By tool name, the arguments and choices a client may not send.

    Returns
    -------
    registered_count : `int`
        Total number of public tools dynamically registered.
    """
    count = 0

    for attr_name, prefix in branch_mapping.items():
        if attr_name == "":
            target_obj = api_object
        else:
            target_obj = api_object
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
            if tool_name in skip:
                continue

            # Extract human-readable summary from docstring
            doc = inspect.getdoc(method) or ""
            summary = doc.split("\n\n")[0] if "\n\n" in doc else doc
            if not summary:
                summary = f"Reflected tool {tool_name}"

            plan = plan_parameters(method, method_name)
            schema = generate_tool_schema(method, plan)
            for hidden_name in SERVER_ONLY_PARAMETERS:
                schema["properties"].pop(hidden_name, None)
                if hidden_name in schema["required"]:
                    schema["required"].remove(hidden_name)
            tool_limits = (limits or {}).get(tool_name)
            if tool_limits is not None:
                _apply_client_limits(schema, method, tool_limits)

            registry.register(tool_name, summary, schema)(
                _make_executor(method, tool_name, tool_limits, api_root=api_object, plan=plan)
            )
            count += 1

    return count


def _make_executor(
    target_callable: Callable[..., Any],
    tool_name: str = "",
    limits: ClientLimits | None = None,
    *,
    api_root: Any = None,
    plan: ParameterPlan | None = None,
) -> Callable[..., Any]:
    """Wrap a library method as an asynchronous tool function.

    Parameters
    ----------
    target_callable : `Callable`
        The bound library method.
    tool_name : `str`, optional
        The tool's name, for the message of a refused call.
    limits : `ClientLimits`, optional
        What a client may not send. A call that sends it is refused.
    api_root : `Any`, optional
        The server's API object, through which ids are looked up.
    plan : `ParameterPlan`, optional
        The parameters turned into objects before the call. None when
        omitted.

    Returns
    -------
    execute : `Callable`
        A coroutine function that runs the method off the server's event
        loop with the client's arguments.
    """
    grace_period = getattr(target_callable, "__background_job_grace_period__", None)
    job_type = getattr(target_callable, "__background_job_type__", None)

    async def execute_reflected(**kwargs: Any) -> Any:
        """Run the method with the client's arguments.

        Returns
        -------
        result : `Any`
            The method's result, or for a slow method still running, the
            job id to poll.
        """
        if limits is not None:
            _check_client_limits(tool_name, kwargs, limits)
            left_out = {name: value for name, value in limits.defaults.items() if kwargs.get(name) is None}
            kwargs = {**kwargs, **left_out}
        if plan is not None and (plan.subjects or plan.models or plan.frame_choices):
            # A lookup reads storage, so it runs off the event loop too.
            kwargs = await asyncio.to_thread(prepare_arguments, api_root, plan, kwargs)
        if grace_period is not None:
            # A method marked with `@background_job` runs slowly enough that
            # calling it directly would block this server's single
            # connection for its whole duration. It runs as a recorded job
            # in a background thread (a pipeline it calls joins that job);
            # the client gets its real result if it finishes quickly, or the
            # job id to poll.
            from astrometricslib.jobs import run_as_background_job

            return await asyncio.to_thread(
                run_as_background_job,
                lambda: target_callable(**kwargs),
                grace_period_seconds=grace_period,
                job_type=job_type,
                target_id=_job_target(kwargs),
            )

        if inspect.iscoroutinefunction(target_callable):
            return await target_callable(**kwargs)

        # A plain synchronous method still needs its own thread, not a
        # direct call on this coroutine: some (e.g. wayfindinglib's
        # `ObservatoryControl`, which bridges into async INDI drivers via
        # `hardware_operations._run_sync`'s own `asyncio.run(...)`) start a
        # second event loop internally, which Python refuses whenever the
        # calling thread already has one running -- exactly this server's
        # own loop.
        return await asyncio.to_thread(target_callable, **kwargs)

    return execute_reflected
