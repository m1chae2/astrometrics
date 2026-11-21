"""Purpose: The AI tools that start a job in the app, as the app's buttons do.

Description: Stacking a target, naming the stars of a stack, making a
preview picture again, and copying frames or logs from the telescope
computer are slow and write to the library. When the person starts them in
the app, the backend runs each one as a job: it shows in the job list with
its progress and log, the screens show it running, and the app's Cancel
button reaches it where the app has one. Each tool here calls the same
backend RPC method as that button (see
`mcp_servers.common.app_job_tools.APP_JOB_TOOLS`), so a job the AI starts is
the same job, in the same row, as one the person starts.

The tools need the running backend. When it is not running, a call fails
with "Could not connect to the backend"; nothing runs in this process
instead, because a job here would not be the app's job.
"""

from typing import Any

from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from mcp_servers.backend.definition import ToolFunction, execute_rpc, tool

JOB_TOOL_SPECS: list[tuple[str, str, dict[str, Any], ToolFunction]] = []
"""The job tools: name, description, argument schema and function.
`mcp_servers.backend.definition.build_registry` registers them."""

STACK_KINDS = ["imaging", "spectral"]
"""The two kinds of stack. Imaging and spectrum frames are never mixed."""

FOLLOW_A_JOB = (
    "Follow the job with jobs_query (job_id, or target_id and job_type). It shows in the app's job list, "
    "where the person can see its log."
)
"""The hint every job reply carries, so the AI knows how to follow it."""


def _without_empty(arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop the arguments the client left out, so the backend's defaults apply.

    Parameters
    ----------
    arguments : `dict` [`str`, `Any`]
        The RPC parameters, some of them `None`.

    Returns
    -------
    parameters : `dict` [`str`, `Any`]
        The same, without the `None` values.
    """
    return {name: value for name, value in arguments.items() if value is not None}


def _with_hint(reply: Any, hint: str) -> Any:
    """Add a sentence that says how to follow the work to a backend reply.

    Parameters
    ----------
    reply : `Any`
        The backend's answer.
    hint : `str`
        The sentence to add.

    Returns
    -------
    reply : `Any`
        A dictionary answer with ``follow`` set to the hint; anything else
        as it was.
    """
    if isinstance(reply, dict):
        return {**reply, "follow": hint}
    return reply


@tool(
    "processing_stack",
    (
        "Stack a target's frames in the app, as its Stack button does. The app runs it as a stacking job: "
        "it shows in the job list with its progress and log, and the person can cancel it there. Choose "
        "kind (imaging or spectral, never mixed), and optionally a filter, a file range, a time range, a "
        "camera and a focal length, or give the exact frames. Run it with plan_only true first to see "
        "which frames it would use. It sets bad frames aside (never deletes them), keeps one previous "
        "stack, and saves the target. Read the result with processing_stack_summary. Needs the app's "
        "backend to be running."
    ),
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": 'The target id, such as "M 13".'},
            "kind": {"type": "string", "enum": STACK_KINDS, "description": "Default imaging."},
            "filter_name": {"type": "string", "description": "Only frames of this filter, such as L."},
            "first_file": {
                "type": "string",
                "description": "Only frames from this file on; 013 is frame 013.",
            },
            "last_file": {"type": "string", "description": "Only frames up to this file or number."},
            "since": {"type": "string", "description": "Only frames taken at or after this ISO time."},
            "until": {"type": "string", "description": "Only frames taken at or before this ISO time."},
            "camera_id": {"type": "string", "description": "Only frames from this camera."},
            "focal_length_mm": {"type": "number", "description": "Only frames taken at this focal length."},
            "frames": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The exact frame paths to stack, instead of the choices above.",
            },
            "plan_only": {"type": "boolean", "description": "Only list the chosen frames. Default false."},
            "force": {"type": "boolean", "description": "Stack again even if nothing changed."},
            "denoise": {"type": "boolean", "description": "Run or skip Cosmic Clarity for this preview."},
            "denoise_strength": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Cosmic Clarity's strength for this run.",
            },
            "star_toning": {"type": "boolean", "description": "Star toning on or off for this preview."},
        },
        "required": ["target"],
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_stack(
    target: str,
    kind: str | None = None,
    filter_name: str | None = None,
    first_file: str | None = None,
    last_file: str | None = None,
    since: str | None = None,
    until: str | None = None,
    camera_id: str | None = None,
    focal_length_mm: float | None = None,
    frames: list[str] | None = None,
    plan_only: bool = False,
    force: bool = False,
    denoise: bool | None = None,
    denoise_strength: float | None = None,
    star_toning: bool | None = None,
) -> Any:
    """Start a stacking job in the app, or list the frames it would use.

    Parameters
    ----------
    target : `str`
        The target id.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"``.
    filter_name, first_file, last_file : `str`, optional
        The filter and file range, as in the library's ``processing.stack``.
    since, until, camera_id : `str`, optional
        The time range and camera, as in the library's ``processing.stack``.
    focal_length_mm : `float`, optional
        Only frames taken at this focal length.
    frames : `list` [`str`], optional
        The exact frame paths to stack.
    plan_only : `bool`, optional
        Only list the chosen frames.
    force : `bool`, optional
        Stack again even if nothing changed.
    denoise, star_toning : `bool`, optional
        Preview overrides for this run.
    denoise_strength : `float`, optional
        Cosmic Clarity's strength for this run.

    Returns
    -------
    reply : `Any`
        The backend's ``processing:stack`` answer: ``status`` (``started``,
        ``already_running`` or ``plan``), ``jobId`` and ``logFile``, or the
        ``plan``. A started job's reply says how to follow it.
    """
    parameters = _without_empty({
        "target_id": target,
        "image_files": frames,
        "kind": kind,
        "filter_name": filter_name,
        "first_file": first_file,
        "last_file": last_file,
        "since": since,
        "until": until,
        "camera_id": camera_id,
        "focal_length_mm": focal_length_mm,
        "plan_only": plan_only or None,
        "force": force or None,
        "denoise": denoise,
        "denoise_strength": denoise_strength,
        "star_toning": star_toning,
    })
    reply = await execute_rpc("processing:stack", parameters)
    if plan_only:
        return reply
    return _with_hint(reply, FOLLOW_A_JOB + " The result is in processing_stack_summary once it ends.")


@tool(
    "processing_identify_stack",
    (
        "Name every star the stack's star finder detected, in the app, as its Astrometry button does. The "
        "app runs it as an analysis job: it shows in the job list with its progress and log, and the "
        "person can cancel it there. The stack is plate-solved first if it has no solution. Read the "
        "names with processing_get_stack_identification when it ends. Needs the app's backend to be "
        "running."
    ),
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": 'The target id, such as "M 13".'},
            "stack_path": {
                "type": "string",
                "description": "The stack to name. Defaults to the target's current imaging stack.",
            },
        },
        "required": ["target"],
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_identify_stack(target: str, stack_path: str | None = None) -> Any:
    """Start the job that names a stack's stars.

    Parameters
    ----------
    target : `str`
        The target id.
    stack_path : `str`, optional
        The stack. The target's current imaging stack when omitted.

    Returns
    -------
    reply : `Any`
        The backend's ``analysis:identify_stack`` answer: ``status``
        (``started`` or ``already_running``) and ``jobId``, with how to
        follow it.

    Raises
    ------
    NotFoundError
        If no stack is given and the target has no imaging stack.
    """
    if not stack_path:
        record = await execute_rpc("target:get", {"target_id": target, "detail": "summary"})
        stacking = record.get("stacking") if isinstance(record, dict) else None
        stack_path = (stacking or {}).get("stackedImage") or (stacking or {}).get("stacked_image")
        if not stack_path:
            raise NotFoundError(
                f"Target {target!r} has no imaging stack to name the stars of. Stack it first, or pass "
                "stack_path.",
                details={"target": target},
            )
    reply = await execute_rpc("analysis:identify_stack", {"target_id": target, "stack_path": stack_path})
    return _with_hint(reply, FOLLOW_A_JOB + " The names are in processing_get_stack_identification.")


@tool(
    "processing_remake_preview",
    (
        "Make a target's preview picture again from its existing stack, in the app, without restacking. "
        "The app runs it as a preview job that shows in the job list. denoise, denoise_strength, "
        "star_toning and the StarNet choices apply to this run only and never reach the settings. The old "
        "pictures are kept in _previous_preview and put back if the run fails; the stack file is never "
        "written. Needs the app's backend to be running."
    ),
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": 'The target id, such as "M 13".'},
            "kind": {"type": "string", "enum": STACK_KINDS, "description": "Default imaging."},
            "denoise": {"type": "boolean", "description": "Run or skip Cosmic Clarity for this run."},
            "denoise_strength": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Cosmic Clarity's strength for this run.",
            },
            "star_toning": {"type": "boolean", "description": "Star toning on or off for this run."},
            "starnet": {
                "type": "boolean",
                "description": "Stretch the stars apart from the rest with StarNet (adds 35 to 55 s).",
            },
            "star_strength": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "With starnet: how much of the stars layer is blended. Default 0.8.",
            },
            "star_stretch": {
                "type": "number",
                "minimum": 1,
                "maximum": 100000,
                "description": "With starnet: the stars' own stretch strength. Default 1000.",
            },
        },
        "required": ["target"],
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_remake_preview(
    target: str,
    kind: str | None = None,
    denoise: bool | None = None,
    denoise_strength: float | None = None,
    star_toning: bool | None = None,
    starnet: bool | None = None,
    star_strength: float | None = None,
    star_stretch: float | None = None,
) -> Any:
    """Start the job that makes a target's preview picture again.

    Parameters
    ----------
    target : `str`
        The target id.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"``.
    denoise, star_toning, starnet : `bool`, optional
        Overrides for this run.
    denoise_strength, star_strength, star_stretch : `float`, optional
        Strengths for this run.

    Returns
    -------
    reply : `Any`
        The backend's ``processing:remake_preview`` answer: ``status``
        (``completed`` with ``result``, or ``running``) and ``job_id``.
    """
    parameters = _without_empty({
        "target_id": target,
        "kind": kind,
        "denoise": denoise,
        "denoise_strength": denoise_strength,
        "star_toning": star_toning,
        "starnet": starnet,
        "star_strength": star_strength,
        "star_stretch": star_stretch,
    })
    reply = await execute_rpc("processing:remake_preview", parameters)
    return _with_hint(reply, FOLLOW_A_JOB)


@tool(
    "observatory_remote_sync_frames",
    (
        "Bring a target's new frames (or the Bias, Dark or Flat folder) from the telescope computer into "
        "the library, in the app, as its sync button does. It adds files and records and never deletes. "
        "Run it with dry_run true first to see what would be copied. A real run starts a remote_sync job "
        "for the target that shows in the job list with an 'N of M frames transferred' progress, and the "
        "app shows the new frames as soon as they arrive. Needs the app's backend to be running."
    ),
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "The target id, or Bias, Dark or Flat."},
            "dry_run": {"type": "boolean", "description": "Only report what would be copied. Default false."},
        },
        "required": ["target"],
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_observatory_remote_sync_frames(target: str, dry_run: bool = False) -> Any:
    """Start the app's frame sync for one target, or report its plan.

    Parameters
    ----------
    target : `str`
        The target id, or ``Bias``, ``Dark`` or ``Flat``.
    dry_run : `bool`, optional
        Only report what would be copied.

    Returns
    -------
    reply : `Any`
        The backend's ``telescope:sync`` answer: ``started`` and
        ``target_id`` (with ``reason`` when a sync of the target already
        runs), or for a dry run ``plan``.

    Raises
    ------
    InvalidArgumentError
        If no target is named.
    """
    if not target or not target.strip():
        raise InvalidArgumentError("Name the target (or Bias, Dark or Flat) to copy the frames of.")
    reply = await execute_rpc("telescope:sync", {"object_id": target, "dry_run": dry_run})
    if dry_run:
        return reply
    return _with_hint(
        reply,
        f"Follow it with jobs_query (target_id {target!r}, job_type remote_sync), and count the frames with "
        "observatory_remote_frame_status.",
    )


@tool(
    "observatory_remote_sync_logs",
    (
        "Bring the new guide and Ekos logs from the telescope computer into the library, in the app, and "
        "store their guiding samples. It adds or updates records and never deletes. Run it with dry_run "
        "true first to see how many logs are new. A real run is a remote_sync job named logs in the job "
        "list, and it waits until the logs are stored; after two minutes the call stops waiting while the "
        "job goes on, so follow it with jobs_query. Needs the app's backend to be running."
    ),
    {
        "type": "object",
        "properties": {
            "dry_run": {
                "type": "boolean",
                "description": "Only report how many logs are new. Default false.",
            },
        },
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_observatory_remote_sync_logs(dry_run: bool = False) -> Any:
    """Run the app's guide and Ekos log sync, or report its plan.

    Parameters
    ----------
    dry_run : `bool`, optional
        Only report how many logs are new.

    Returns
    -------
    reply : `Any`
        The backend's ``telescope:sync_logs`` answer: the counts the app
        shows (``guideLogsDownloaded``, ``guidingSamplesIngested``), or for
        a dry run ``plan``.
    """
    return await execute_rpc("telescope:sync_logs", {"dry_run": dry_run})
