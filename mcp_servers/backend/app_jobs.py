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

CANCELLABLE_JOB_TYPES = ["stacking", "analysis", "preview"]
"""The kinds of job the app's Cancel buttons stop."""

ACTIVE_JOB_STATUSES = ("started", "running")
"""Job statuses that mean the job has not ended."""

STACK_KINDS = ["imaging", "spectral"]
"""The two kinds of stack. Imaging and spectrum frames are never mixed."""

FOLLOW_A_JOB = (
    "Follow the job with jobs_get (job id), or list jobs with jobs_query (target_id and job_type). "
    "It shows in the app's job list, where the person can see its log."
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
    "processing_stacking_run",
    (
        "Stack a target's frames in the app, as its Stack button does. The app runs it as a stacking job: "
        "it shows in the job list with its progress and log, and the person can cancel it there. Choose "
        "kind (imaging or spectral, never mixed), and optionally a filter, a file range, a time range, a "
        "camera and a focal length, or give the exact frames. A choice that matches no frame, or frames "
        "from two cameras or optics, is refused before a job starts. It sets bad frames aside (never "
        "deletes them), keeps one previous stack, saves the target, then makes the stack's picture. Read "
        "the result with processing_stacking_get_summary. Needs the app's backend to be running."
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
async def tool_processing_stacking_run(
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
    force: bool = False,
    denoise: bool | None = None,
    denoise_strength: float | None = None,
    star_toning: bool | None = None,
) -> Any:
    """Start a stacking job in the app.

    Parameters
    ----------
    target : `str`
        The target id.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"``.
    filter_name, first_file, last_file : `str`, optional
        The filter and file range, as in ``targets.frames.query``.
    since, until, camera_id : `str`, optional
        The time range, and the camera (a full name or part of one).
    focal_length_mm : `float`, optional
        Only frames taken at this focal length.
    frames : `list` [`str`], optional
        The exact frame paths to stack.
    force : `bool`, optional
        Stack again even if nothing changed.
    denoise, star_toning : `bool`, optional
        Preview overrides for this run.
    denoise_strength : `float`, optional
        Cosmic Clarity's strength for this run.

    Returns
    -------
    reply : `Any`
        The backend's ``processing:stack`` answer: ``status`` (``started``
        or ``already_running``), ``jobId`` and ``logFile``. A started job's
        reply says how to follow it.
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
        "force": force or None,
        "denoise": denoise,
        "denoise_strength": denoise_strength,
        "star_toning": star_toning,
    })
    reply = await execute_rpc("processing:stack", parameters)
    return _with_hint(reply, FOLLOW_A_JOB + " The result is in processing_stacking_get_summary once it ends.")


@tool(
    "processing_astrometry_identify",
    (
        "Name every star the stack's star finder detected, in the app, as its Astrometry button does. The "
        "app runs it as an analysis job: it shows in the job list with its progress and log, and the "
        "person can cancel it there. The stack is plate-solved first if it has no solution. Read the "
        "names with processing_astrometry_get_identification when it ends. Needs the app's backend to be "
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
async def tool_processing_astrometry_identify(target: str, stack_path: str | None = None) -> Any:
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
    return _with_hint(reply, FOLLOW_A_JOB + " The names are in processing_astrometry_get_identification.")


def _first_text(*values: Any) -> str | None:
    """Return the first value that is a non-empty string.

    Parameters
    ----------
    *values : `Any`
        The candidates, in order.

    Returns
    -------
    text : `str` or `None`
        The first non-empty string, or `None`.
    """
    return next((value for value in values if isinstance(value, str) and value), None)


ANALYSIS_TOOLS = {
    "processing_astrometry_run": (
        "astrometry",
        "Plate solve a target's stack and record the stars it names",
    ),
    "processing_photometry_run": (
        "photometry",
        "Measure the brightness of a target's stars across its frames (reads astrometry's plate solution; "
        "skipped with the reason when there is none, so run processing_astrometry_run first)",
    ),
    "processing_spectroscopy_run": (
        "spectroscopy",
        "Extract and classify the spectra of a target's spectral stack (reads astrometry's plate solution; "
        "skipped with the reason when there is none, so run processing_astrometry_run first)",
    ),
    "processing_spectroscopy_record_variation": (
        "spectral_variation",
        "Follow each star's spectrum across a target's raw spectral frames, night by night (spectroscopy "
        "runs first when needed)",
    ),
}
"""Tool name -> (the ``analysis:analyze_image`` stage it runs, what it does).
One tool per library pipeline method: ``processing.astrometry.run``,
``processing.photometry.run``, ``processing.spectroscopy.run`` and
``processing.spectroscopy.record_variation``."""

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "description": 'The target id, such as "M 13".'},
        "camera_id": {
            "type": "string",
            "description": "Analyze the stack of this camera (or part of its name). Default: main stack.",
        },
        "focal_length_mm": {
            "type": "number",
            "description": "Analyze the stack taken at this focal length. Default: the main stack.",
        },
    },
    "required": ["target"],
}
"""The arguments of each analysis tool."""


def _analysis_description(summary: str) -> str:
    """Write an analysis tool's description around what it does.

    Parameters
    ----------
    summary : `str`
        What the pipeline does, as one sentence without its full stop.

    Returns
    -------
    description : `str`
        The tool's description.
    """
    return (
        f"{summary}, in the app, as its Analyze Target menu does. The app runs it as an analysis job: it "
        "shows in the target's job list with its progress and log, and the person can cancel it there. A "
        "target stacked with several cameras or optics has one stack for each: give camera_id and "
        "focal_length_mm to analyze that configuration's stack and frames instead of the main stack (an "
        "unknown one is refused with the target's list). Spectral stacks are kept only for the main "
        "configuration. Stack the target first: a pipeline with nothing to work on is refused. If an "
        "analysis of the target is already running, no second one starts. Read the results with "
        "star_query and processing_astrometry_get_identification. Needs the app's backend to be running."
    )


async def _start_analysis(
    stage: str, target: str, camera_id: str | None = None, focal_length_mm: float | None = None
) -> Any:
    """Start the Analyze Target job for one pipeline.

    Parameters
    ----------
    stage : `str`
        The pipeline's stage name for ``analysis:analyze_image``.
    target : `str`
        The target id.
    camera_id : `str`, optional
        Analyze the target's stack of this camera (the full name or part of
        it). The main stack by default.
    focal_length_mm : `float`, optional
        Analyze the target's stack taken at this focal length.

    Returns
    -------
    reply : `Any`
        The backend's ``analysis:analyze_image`` answer (``status``
        ``started`` or ``already_running``, ``jobId``, ``logFile``,
        ``stages`` and, for a chosen camera and optic, ``configuration``),
        with how to follow it.
    """
    reply = await execute_rpc(
        "analysis:analyze_image",
        _without_empty({
            "target_id": target,
            "stages": [stage],
            "camera_id": camera_id,
            "focal_length_mm": focal_length_mm,
        }),
    )
    return _with_hint(
        reply,
        FOLLOW_A_JOB + " Read the results with star_query and processing_astrometry_get_identification.",
    )


@tool(
    "processing_astrometry_run",
    _analysis_description(ANALYSIS_TOOLS["processing_astrometry_run"][1]),
    _ANALYSIS_SCHEMA,
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_astrometry_run(
    target: str, camera_id: str | None = None, focal_length_mm: float | None = None
) -> Any:
    """Start the astrometry pipeline as the app's analysis job.

    Parameters
    ----------
    target : `str`
        The target id.
    camera_id : `str`, optional
        Analyze the target's stack of this camera.
    focal_length_mm : `float`, optional
        Analyze the target's stack taken at this focal length.

    Returns
    -------
    reply : `Any`
        The backend's ``analysis:analyze_image`` answer.
    """
    return await _start_analysis("astrometry", target, camera_id, focal_length_mm)


@tool(
    "processing_photometry_run",
    _analysis_description(ANALYSIS_TOOLS["processing_photometry_run"][1]),
    _ANALYSIS_SCHEMA,
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_photometry_run(
    target: str, camera_id: str | None = None, focal_length_mm: float | None = None
) -> Any:
    """Start the photometry pipeline as the app's analysis job.

    Parameters
    ----------
    target : `str`
        The target id.
    camera_id : `str`, optional
        Analyze the target's stack of this camera.
    focal_length_mm : `float`, optional
        Analyze the target's stack taken at this focal length.

    Returns
    -------
    reply : `Any`
        The backend's ``analysis:analyze_image`` answer.
    """
    return await _start_analysis("photometry", target, camera_id, focal_length_mm)


@tool(
    "processing_spectroscopy_run",
    _analysis_description(ANALYSIS_TOOLS["processing_spectroscopy_run"][1]),
    _ANALYSIS_SCHEMA,
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_spectroscopy_run(
    target: str, camera_id: str | None = None, focal_length_mm: float | None = None
) -> Any:
    """Start the spectroscopy pipeline as the app's analysis job.

    Parameters
    ----------
    target : `str`
        The target id.
    camera_id : `str`, optional
        The camera of the stack; only the main configuration has spectra.
    focal_length_mm : `float`, optional
        The optic of the stack.

    Returns
    -------
    reply : `Any`
        The backend's ``analysis:analyze_image`` answer.
    """
    return await _start_analysis("spectroscopy", target, camera_id, focal_length_mm)


@tool(
    "processing_spectroscopy_record_variation",
    _analysis_description(ANALYSIS_TOOLS["processing_spectroscopy_record_variation"][1]),
    _ANALYSIS_SCHEMA,
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_spectroscopy_record_variation(
    target: str, camera_id: str | None = None, focal_length_mm: float | None = None
) -> Any:
    """Start the spectral variation pipeline as the app's analysis job.

    Parameters
    ----------
    target : `str`
        The target id.
    camera_id : `str`, optional
        The camera of the stack; only the main configuration has spectra.
    focal_length_mm : `float`, optional
        The optic of the stack.

    Returns
    -------
    reply : `Any`
        The backend's ``analysis:analyze_image`` answer.
    """
    return await _start_analysis("spectral_variation", target, camera_id, focal_length_mm)


@tool(
    "processing_cancel",
    (
        "Cancel a running or waiting stacking, analysis or picture remake (preview) job of a target, as "
        "the app's Cancel button does. Name the target. With job_type (stacking, analysis or preview) only "
        "that kind is cancelled; with "
        "job_id that one job is looked up and cancelled; with neither, the target's one running job is "
        "cancelled, and the call is refused if two kinds run (name one). Nothing is deleted; a "
        "cancelled stack leaves the earlier stack as it was, and a cancelled picture remake puts the "
        "old pictures back. The reply says what was "
        "cancelled, or why nothing was. Needs the app's backend to be running."
    ),
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": 'The target id, such as "M 13".'},
            "job_type": {
                "type": "string",
                "enum": CANCELLABLE_JOB_TYPES,
                "description": "Cancel only this kind of job. Default: the target's one running job.",
            },
            "job_id": {
                "type": "string",
                "description": "Cancel this job. It must belong to the target.",
            },
        },
        "required": ["target"],
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_cancel(target: str, job_type: str | None = None, job_id: str | None = None) -> Any:
    """Cancel a target's running stacking, analysis or picture remake job.

    Parameters
    ----------
    target : `str`
        The target id.
    job_type : `str`, optional
        ``"stacking"``, ``"analysis"`` or ``"preview"``. When omitted, the
        kind of the one job that is running.
    job_id : `str`, optional
        One job to cancel. Its type is read from the job list.

    Returns
    -------
    reply : `dict`
        ``cancelled`` (`bool`), ``job_type``, ``job_ids`` (the active jobs
        of that kind when the call was made), ``reason`` (why not, else
        `None`) and ``backend`` (the answer of the app's cancel call).

    Raises
    ------
    InvalidArgumentError
        If the job type is unknown, the job belongs to another target, or
        two kinds of job run and none was chosen.
    NotFoundError
        If ``job_id`` is not a job.
    """
    if job_type is not None and job_type not in CANCELLABLE_JOB_TYPES:
        raise InvalidArgumentError(
            f"Unknown job type {job_type!r}. Choose from: {', '.join(CANCELLABLE_JOB_TYPES)}.",
            details={"job_type": job_type},
        )
    if job_id:
        answer = await execute_rpc("jobs:query", {"job_id": job_id})
        job = answer.get("job") if isinstance(answer, dict) else None
        if not isinstance(job, dict):
            raise NotFoundError(f"No job with id {job_id!r}.", details={"job_id": job_id})
        if job.get("target_id") != target:
            raise InvalidArgumentError(
                f"Job {job_id!r} belongs to {job.get('target_id')!r}, not {target!r}.",
                details={"job_id": job_id, "target": target},
            )
        found_type = job.get("job_type")
        if job_type is not None and job_type != found_type:
            raise InvalidArgumentError(
                f"Job {job_id!r} is a {found_type!r} job, not {job_type!r}.",
                details={"job_id": job_id, "job_type": job_type},
            )
        active = [job] if job.get("status") in ACTIVE_JOB_STATUSES else []
        chosen_type = found_type
        if not active:
            return _not_cancelled(
                chosen_type, [], f"Job {job_id} is not running (status {job.get('status')!r})."
            )
    else:
        answer = await execute_rpc("jobs:query", {"target_id": target, "active_only": True, "limit": 50})
        jobs = [
            job
            for job in (answer.get("jobs") if isinstance(answer, dict) else None) or []
            if job.get("status") in ACTIVE_JOB_STATUSES
        ]
        if job_type is not None:
            jobs = [job for job in jobs if job.get("job_type") == job_type]
        kinds = sorted({str(job.get("job_type")) for job in jobs})
        if not jobs:
            return _not_cancelled(job_type, [], f"No job is running for {target!r}.")
        if len(kinds) > 1:
            raise InvalidArgumentError(
                f"More than one kind of job runs for {target!r} ({', '.join(kinds)}). Name the job_type "
                "or the job_id.",
                details={"target": target, "job_types": kinds},
            )
        active, chosen_type = jobs, kinds[0]

    job_ids = [str(job.get("id")) for job in active]
    if chosen_type in ("stacking", "preview"):
        method = "processing:cancel"
    elif chosen_type == "analysis":
        method = "analysis:cancel"
    else:
        return _not_cancelled(
            chosen_type, job_ids, f"The app has no Cancel for a {chosen_type!r} job, so none is sent."
        )
    outcome = await execute_rpc(method, {"target_id": target})
    outcome = outcome if isinstance(outcome, dict) else {}
    cancelled = outcome.get("status") == "cancelled" or outcome.get("cancelled") is True
    reason = None if cancelled else _first_text(outcome.get("message"), outcome.get("reason"))
    return {
        "cancelled": cancelled,
        "job_type": chosen_type,
        "job_ids": job_ids,
        "reason": reason if not cancelled else None,
        "backend": outcome,
    }


def _not_cancelled(job_type: str | None, job_ids: list[str], reason: str) -> dict[str, Any]:
    """Build the reply for a call that cancelled nothing.

    Parameters
    ----------
    job_type : `str`, optional
        The kind of job concerned, when known.
    job_ids : `list` [`str`]
        The active jobs of that kind.
    reason : `str`
        Why nothing was cancelled.

    Returns
    -------
    reply : `dict`
        The reply, in the shape of a cancelled one.
    """
    return {"cancelled": False, "job_type": job_type, "job_ids": job_ids, "reason": reason, "backend": None}


ENHANCE_APPLIES = (
    "The choice is saved on the target. It is used at the next stack or Post Process Target; no picture "
    "is made now."
)
"""What the reply says about when a saved Enhance choice takes effect."""

ENHANCE_MODES = {"target": "auto", "auto": "auto", "nothing": "off", "off": "off"}
"""The words for the two fixed Enhance choices -> the backend's mode."""

ENHANCE_WORDS = {"auto": "target", "off": "nothing"}
"""The backend's two fixed modes -> the words the tool uses for them."""


@tool(
    "processing_enhance",
    (
        "Read or set the Enhance choice of a target's Final Stack: what the picture's masked stretch "
        "protects. Without choice it returns the saved choice and the nebulae and galaxies in the "
        "stack's frame (or why none can be listed, such as: run Astrometry first). With choice it saves "
        "it: target (the target itself, when it is a nebula or a galaxy), nothing (never give an object "
        "its own stretch) or the id or name of an object from the list, such as NGC 7635. The choice "
        "is used at the next stack or Post Process Target; no picture is made now. Needs the app's "
        "backend to be running."
    ),
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": 'The target id, such as "M 52".'},
            "choice": {
                "type": "string",
                "description": (
                    'Leave out to read. "target", "nothing", or an object name from the list '
                    '(for example "NGC 7635").'
                ),
            },
        },
        "required": ["target"],
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_enhance(target: str, choice: str | None = None) -> Any:
    """Read or save a target's Enhance choice.

    Parameters
    ----------
    target : `str`
        The target id.
    choice : `str`, optional
        ``"target"``, ``"nothing"`` or the name of an object in the stack's
        frame. When omitted, nothing is changed.

    Returns
    -------
    reply : `dict`
        Without a choice: ``current`` (the saved choice), ``objects`` and
        ``message`` (why there are none, else `None`). With one: ``saved``
        (the backend's record) and ``applies``. The backend refuses a
        choice that is neither of the two words nor an object in the frame.
    """
    if choice is None:
        listing = await execute_rpc("processing:list_bright_objects", {"target": target})
        listing = listing if isinstance(listing, dict) else {}
        saved = listing.get("current") or {}
        mode = saved.get("mode", "auto")
        return {
            "current": ENHANCE_WORDS.get(mode, saved.get("simbadId")),
            "objects": listing.get("objects") or [],
            "message": listing.get("message"),
        }
    mode = ENHANCE_MODES.get(choice.strip().lower())
    parameters = (
        {"target": target, "mode": mode} if mode else {"target": target, "mode": "object", "name": choice}
    )
    saved = await execute_rpc("processing:set_bright_object", parameters)
    return {"saved": saved, "applies": ENHANCE_APPLIES}


@tool(
    "processing_enhancement_run",
    (
        "Make a target's preview picture again from its existing stack, in the app, without restacking. "
        "The app runs it as a preview job that shows in the job list. denoise, denoise_strength, "
        "star_toning and the StarNet choices apply to this run only and never reach the settings. The old "
        "pictures are kept in _previous_preview and put back if the run fails; the stack file is never "
        "written. A target stacked with several cameras or optics has one stack for each: give camera_id "
        "and focal_length_mm to remake that configuration's own pictures instead of the main stack's (an "
        "unknown one is refused with the target's list); the main stack and the picture the target shows "
        "stay as they were. Needs the app's backend to be running."
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
            "camera_id": {
                "type": "string",
                "description": "Remake the stack of this camera (or part of its name). Default: main stack.",
            },
            "focal_length_mm": {
                "type": "number",
                "description": "Remake the stack taken at this focal length. Default: the main stack.",
            },
        },
        "required": ["target"],
    },
    specs=JOB_TOOL_SPECS,
)
async def tool_processing_enhancement_run(
    target: str,
    kind: str | None = None,
    denoise: bool | None = None,
    denoise_strength: float | None = None,
    star_toning: bool | None = None,
    starnet: bool | None = None,
    star_strength: float | None = None,
    star_stretch: float | None = None,
    camera_id: str | None = None,
    focal_length_mm: float | None = None,
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
    camera_id : `str`, optional
        Remake the target's stack of this camera (``processing_stacking_run``'s
        meaning: the full name or part of it). The main stack by default.
    focal_length_mm : `float`, optional
        Remake the target's stack taken at this focal length.

    Returns
    -------
    reply : `Any`
        The backend's ``processing:remake_preview`` answer: ``status``
        (``completed`` with ``result``, or ``running``) and ``job_id``,
        with ``configuration`` (the chosen camera and optic, when one was
        asked for). The backend refuses a camera and optic that name no
        stack of the target, or more than one, or are given for a spectral
        stack.
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
        "camera_id": camera_id,
        "focal_length_mm": focal_length_mm,
    })
    return _with_hint(await execute_rpc("processing:remake_preview", parameters), FOLLOW_A_JOB)


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
        "observatory_remote_frame_locations.",
    )


@tool(
    "observatory_remote_sync_logs",
    (
        "Bring the new guide and Ekos logs from the telescope computer into the library, in the app, and "
        "store their guiding samples. It adds or updates records and never deletes. Run it with dry_run "
        "true first to see how many logs are new. A real run is a remote_sync job named logs in the job "
        "list, and it waits until the logs are stored; after two minutes the call stops waiting while the "
        "job goes on, so follow it with jobs_get. Needs the app's backend to be running."
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
