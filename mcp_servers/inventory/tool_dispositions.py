"""Purpose: Record the decision about every MCP tool the project serves.

Description: The MCP tool inventory (``tool_inventory.py``) lists every tool
and drafts a safety class for it from its name. This module holds what a
person decided after reading the code behind each tool:

* a **category** for each tool (for example ``stars`` or ``targets``),
* a **disposition**: offer the tool, fix it first, keep it away from AI
  clients, or drop it,
* a corrected class where the drafted one was wrong, and a short note that
  says why.

``DECISIONS`` names every tool the servers serve. The two library servers
offer every public library method as a tool, so a new public method has no
row until someone adds one. Until then the inventory marks it
``undecided``, no client gets it, and a test fails. Adding a library
method is therefore also a choice about the AI tool list.
"""

import re
from dataclasses import dataclass

CATEGORIES = {
    "image-processing": (
        "Pipelines (stacking, astrometry, photometry, spectroscopy), quality checks, rendering and plots."
    ),
    "calibration": "The dark, bias and flat library.",
    "targets": "Targets and the frame records that belong to them.",
    "stars": "The star catalog and sky lookups.",
    "observatory-control": "Commands to a device. Out of scope: every tool here is dropped.",
    "observatory-status": "Live status, equipment state and analysis of past nights.",
    "observatory-sync": "Moving frames and logs from the telescope computer to this one.",
    "observatory-config": "Tools that change equipment selection, safety rules and stored models.",
    "planning-sessions": "Observation planning, visibility, mosaics and observing sessions.",
    "jobs-history": "Running jobs, finished jobs, their logs and where each data product came from.",
    "app": "What the person sees and controls in the app, plus backend status.",
    "developer": "Checks on the project's own code. Not for observing work.",
}
"""The categories a tool can belong to."""

DISPOSITIONS = {
    "keep": "Offered to the profiles that its class allows.",
    "fix": "Not offered until a change lets an AI client use it safely.",
    "withhold": "Never offered to an AI client: it writes, runs code or reaches every backend method.",
    "drop": "Not offered: out of scope, replaced by a broader tool, or of no use to an AI client.",
    "undecided": "No decision yet. Not offered.",
}
"""What happens to a tool."""

READ_ONLY_NOTE = "The AI is read-only, so a tool that writes is not offered."
"""Note for a writer that has no read-only form."""

DEVICE_COMMAND_NOTE = (
    "Commanding a device (mount, cameras, focuser, filter wheel, enclosure) is out of scope. "
    "Reading from them and from the Pi is allowed."
)
"""Note for a tool that commands a device."""

INTERIM_BLOCKS = {
    "observatory_mount_status": (
        "Connects through connect_to_telescope, which sends CONNECTION=ON to every INDI device that is off. "
        "Blocked until it uses the connect-only path. app_status reports the mount through the backend."
    ),
    "observatory_safety_status": (
        "The assessment section connects through connect_to_telescope, which sends CONNECTION=ON to every "
        "INDI device that is off, and enclosure_state returns unknown in the MCP process. Blocked until "
        "those sections use the connect-only path."
    ),
    "planning_get_sources": (
        "Returns whole target records and every star, which overflows the 40,000 character reply. Use "
        "planning_find_sources, which caps the list, brightest first."
    ),
    "planning_get_library_star_summaries": (
        "Returns every star in the circle, which can overflow the 40,000 character reply. Use "
        "planning_find_sources, which caps the list, brightest first."
    ),
    "planning_resolve_target_coordinates": (
        "Returns the whole target record, frames and all, which overflows the reply. Use "
        "planning_lookup_coordinates, which returns the position."
    ),
    "observatory_imaging_status": (
        "The filters and focuser sections return [] and 0 because the MCP process has no hardware "
        "connection. Use app_status with the telescope section (focuser position, filter, pier side, "
        "tracking)."
    ),
    "visualization_get_last_captured_image": (
        "Returns the picture as base64 text, which the 40,000 character reply limit cuts in half. Use "
        "visualization_render_fits with a path, which returns a real image."
    ),
}
"""Read-only tools that stay hidden from the AI until a known hazard is fixed.
They keep their ``keep`` disposition."""


@dataclass(frozen=True)
class ToolDecision:
    """A decision about one tool.

    Attributes
    ----------
    disposition : `str`
        One of ``DISPOSITIONS``.
    note : `str`
        Why, in plain words.
    tool_class : `str` or `None`
        A corrected class. `None` keeps the drafted class.
    """

    disposition: str
    note: str = ""
    tool_class: str | None = None


DECISIONS = {
    # ---- Withheld: code runners and the whole-app channel ----
    "electron_run_python": ToolDecision("withhold", "Runs AI-written Python inside the backend."),
    "backend_call_rpc": ToolDecision(
        "withhold", "Reaches every backend method, including all the ones above."
    ),
    "terminal_inspect_api": ToolDecision("withhold", "Builds Python code from its argument. Console only."),
    "terminal_get_workspace": ToolDecision("withhold", "Console only. Has no use without the code runner."),
    "ui_editor_sync": ToolDecision("withhold", "Console code editor. Has no use without the code runner."),
    "ui_inspect_variable": ToolDecision(
        "withhold", "Console variable viewer. Has no use without the code runner."
    ),
    # ---- Connection tools: not device commands, but not needed ----
    "observatory_equipment_connect": ToolDecision(
        "drop",
        "Opens the INDI session. Not a device command, but the status tools already open it on demand.",
    ),
    "observatory_equipment_disconnect": ToolDecision(
        "drop",
        "Closes the INDI session. Not a device command, but it would cut off monitoring and has "
        "no monitoring use.",
    ),
    # ---- Withheld: the user decided an AI may not change configuration ----
    "observatory_equipment_set_active_camera": ToolDecision(
        "withhold", "Writes the active camera into the configuration file."
    ),
    "observatory_equipment_set_active_telescope": ToolDecision(
        "withhold", "Writes the active telescope into the configuration file."
    ),
    "observatory_safety_save_rule_set": ToolDecision(
        "withhold", "Changes the rules that decide when the observatory is safe."
    ),
    "observatory_imaging_save_focus_model": ToolDecision("withhold", "Replaces the stored focus model."),
    "observatory_guiding_save_calibration": ToolDecision(
        "withhold", "Replaces the stored guider calibration."
    ),
    "observatory_guiding_save_spectrum_analysis": ToolDecision(
        "withhold", "Replaces the standing guiding analysis."
    ),
    "observatory_safety_apply_promotion_decision": ToolDecision(
        "withhold", "Applies an operator's decision. Needs the operator."
    ),
    "star_tune_spectroscopy_calibration": ToolDecision(
        "withhold",
        "Rewrites the live configuration file through update_config. Needs an apply=false mode that only "
        "returns the fitted values.",
        "change-data",
    ),
    "star_save_all": ToolDecision("withhold", "Replaces the whole star catalog."),
    # ---- Fix first ----
    "target_save": ToolDecision("withhold", "Writes the database. The AI is read-only."),
    "star_analyze_periodicity": ToolDecision(
        "fix", "Writes the result into the live star catalog. Needs a save=false option.", "change-data"
    ),
    "star_detect_point_sources": ToolDecision(
        "fix", "Needs a NumPy array, which MCP cannot send. Take a frame path."
    ),
    "star_plate_solve": ToolDecision(
        "fix",
        "Returns an astropy WCS, which MCP cannot send. Return the solved center and pixel scale.",
    ),
    "processing_run_spectroscopy_by_session": ToolDecision(
        "fix",
        "Needs Python objects, so it cannot be called through MCP. Consider making it a stage "
        "of pipeline_run.",
    ),
    # ---- Drop ----
    "processing_acquire_analysis_slot": ToolDecision(
        "drop", "Internal concurrency lock exposed by reflection."
    ),
    "processing_acquire_stacking_slot": ToolDecision(
        "drop", "Internal concurrency lock exposed by reflection."
    ),
    "planning_get_constellation_lines": ToolDecision(
        "drop",
        "Draws the Planetarium. Returns an empty list because of a wrong file path, and is 89 KB when fixed.",
    ),
    "execution_record_divergence": ToolDecision(
        "drop", "Builds a record and writes nothing. The only arguments MCP can send are useless.", "compute"
    ),
    "execution_advance_session": ToolDecision(
        "drop", "Needs callables that MCP cannot send. No MCP use today."
    ),
    "execution_execute_meridian_flip": ToolDecision("drop", "Needs callables that MCP cannot send."),
    "execution_recover_fault": ToolDecision("drop", "Needs callables that MCP cannot send."),
    "execution_recover_guide_star_loss": ToolDecision("drop", "Needs callables that MCP cannot send."),
    "execution_create_recorder": ToolDecision(
        "drop", "Builds an object. The result is only a text label over MCP."
    ),
    "observatory_safety_assess": ToolDecision(
        "drop", "Works on readings MCP cannot express. Keeps state between calls."
    ),
    "observatory_equipment_summarize_device": ToolDecision(
        "drop", "Classifies values the caller passes in. Reads nothing."
    ),
    # ---- Not offered yet: parts of the old sync set not built ----
    "observatory_remote_sync_frames": ToolDecision(
        "keep",
        "Built 2026-10-03; since 2026-10-05 it also copies calibration folders, every folder, chosen files "
        "and a local folder. The AI may ingest frames from the telescope: adds, never deletes.",
        "ingest",
    ),
    "observatory_remote_sync_logs": ToolDecision(
        "keep",
        "Built 2026-10-03. The AI may ingest the guide and Ekos logs: adds or updates records, "
        "never deletes.",
        "ingest",
    ),
    "diagnostics_frame_quality": ToolDecision(
        "keep", "Built 2026-10-03. Statistics on raw frames. Nothing is saved."
    ),
    "observatory_history_get_live_session_status": ToolDecision(
        "keep",
        "Served 2026-10-03. refresh=true copies the newest Ekos and KStars logs into a local folder, "
        "which is reading from the telescope computer and adds files only. A folder argument is now "
        "checked against the allowed roots. Reports pier side, camera temperature, exposure counts and "
        "dither settings; the guide algorithm is not in the logs and the reply says so.",
        "ingest",
    ),
    "visualization_render_fits": ToolDecision(
        "keep",
        "Built 2026-10-03. Returns a real image of a frame, with a stretch and an optional zoom. "
        "Saves nothing.",
    ),
    "visualization_get_last_captured_image": ToolDecision(
        "fix",
        "Returns base64 text that the reply limit cuts in half. Make it return an image, or fold it "
        "into visualization_render_fits with a latest option.",
    ),
    "target_get_frame": ToolDecision(
        "keep", "Returns the path of one frame, found by ISO, exposure and index."
    ),
    "observatory_history_frame_guiding": ToolDecision(
        "keep",
        "Built 2026-10-03. Cuts the stored guide-log samples to each light frame's exposure window. "
        "Reads only.",
    ),
    "app_status": ToolDecision("keep", "Built 2026-10-03. Reads health, connections, resources and jobs."),
    "app_controls": ToolDecision(
        "keep", "Built 2026-10-03. Navigate and notify only; pause and resume are not offered."
    ),
    "observatory_remote_frame_status": ToolDecision(
        "keep",
        "Built 2026-10-04. Counts a target's frames at the telescope, on the drive and in the library, and "
        "names the frames that are in one place but not the next. Reads only.",
    ),
    "processing_remake_preview": ToolDecision(
        "keep",
        "Built 2026-10-04 from gap report #1. Makes a target's preview picture again from its existing "
        "stack, without restacking. denoise, denoise_strength and star_toning apply to that run only and "
        "never reach the configuration. The old pictures are copied into _previous_preview first and put "
        "back if the run fails. The stack file is never written. Writes only the preview JPEG, the "
        "processed FITS and the target's processed-image pointer.",
        "process",
    ),
    "processing_stack": ToolDecision(
        "keep",
        "Built 2026-10-04 at the user's request: the AI may stack a target the way the app's Stack "
        "button does, choosing imaging or spectral frames, a filter and a file or time range. "
        "plan_only lists the frames without stacking. It runs the app's own stage: sets aside bad "
        "frames into _excluded (never deletes), keeps one previous stack, and saves the target. Since "
        "2026-10-05 it is ProcessingPipelines.stack, which also takes the exact frames to stack.",
        "process",
    ),
    "diagnostics_spectral_frame_check": ToolDecision(
        "keep",
        "Built 2026-10-04. Measures raw spectrum frames: zero-order position, tilt, width, peaks, saturated "
        "patches along the spectrum, a predicted peak at another exposure, and a summary by exposure and by "
        "pier side. Nothing is saved.",
    ),
    "planning_get_imaged_field_centers": ToolDecision(
        "withhold", "Replaced by target_imaged_field_centers, which runs as a background job."
    ),
    "target_imaged_field_centers": ToolDecision(
        "keep",
        "Built 2026-10-04. The distinct sky positions imaged, read from FITS headers (about 35 s on this "
        "library), so it runs as a background job.",
    ),
    "target_query": ToolDecision(
        "keep",
        "Built 2026-10-04. Targets as short rows or one grouped record, the cameras used, or each "
        "target's frames per camera; replaces target_get and target_list.",
    ),
    "planning_find_sources": ToolDecision(
        "keep",
        "Built 2026-10-04. Library targets and stars near a point, brightest stars first, cut at a limit "
        "with the total reported.",
    ),
    "planning_lookup_coordinates": ToolDecision(
        "keep", "Built 2026-10-04. The position of a named object, from the library or SIMBAD. Reads only."
    ),
    "processing_stack_summary": ToolDecision(
        "keep",
        "Built 2026-10-04. One short answer for a target's stack: frames stacked and skipped, rejected "
        "fraction, star width against the inputs, flags and each exposure group. Reads the saved summary.",
    ),
    "star_query": ToolDecision(
        "keep", "Built 2026-10-03. Library star lookup with hard caps on rows, region size and full records."
    ),
    "star_get": ToolDecision("keep", "Reads one star's full record by id. Writes nothing."),
    "calibration_query": ToolDecision(
        "keep",
        "Built 2026-10-05. Calibration counts, or how a target's light frames match the darks. refresh "
        "reloads the index into memory and writes nothing.",
    ),
    "diagnostics_stack_quality": ToolDecision(
        "keep",
        "Built 2026-10-05. Measures a stack (star width, rejected share, registration) and compares it with "
        "the previous stack or another stack file. Saves nothing.",
    ),
    "visualization_plot": ToolDecision(
        "keep",
        "Built 2026-10-05. Draws one chart of a target or a star; the registry sends it as a PNG image. "
        "Saves nothing.",
    ),
    "planning_get_library_star_summaries": ToolDecision("keep", "Reads star summaries for planning."),
    "planning_get_sources": ToolDecision(
        "keep", "Lists sources near a position. It also returns targets; target_query will cover those."
    ),
    "planning_get_online_catalog_sources": ToolDecision(
        "keep", "Queries online catalogs over the network. Reads only."
    ),
    "planning_list_catalog_driver_metadata": ToolDecision("keep", "Lists the catalog drivers. Reads only."),
    "planning_resolve_target_coordinates": ToolDecision(
        "keep", "Looks up an object's position by name. Unknown names go to SIMBAD over the network."
    ),
    "planning_get_visibility": ToolDecision(
        "keep",
        "Built 2026-10-03. Where objects are now or over a night: altitude, horizon clearance with "
        "caller-given blocked ranges, meridian crossing, Sun and Moon. Reads nothing stored.",
    ),
    "planning_get_advisory": ToolDecision(
        "keep", "Built 2026-10-06. Quality or calibration advice, worked out on demand. Saves nothing."
    ),
    "planning_get_plan": ToolDecision(
        "keep", "Built 2026-10-06. Reads a recorded session or the session list."
    ),
    "planning_deep_catalog_status": ToolDecision(
        "keep",
        "Built 2026-10-06. Reads the catalog file; the optional estimate queries the Gaia archive and "
        "saves nothing.",
    ),
    "planning_calculate_panels": ToolDecision("keep", "Works out mosaic panel centers. Saves nothing."),
    "planning_build_deep_star_catalog": ToolDecision(
        "withhold", "Downloads from Gaia for hours and writes the catalog database."
    ),
    "execution_abort_session": ToolDecision(
        "withhold", "Closes a session and records it. Stays separate from the read tools (section 4.2)."
    ),
    "execution_reconcile_session": ToolDecision(
        "withhold", "Writes the calibration counts and the session after a night."
    ),
    # ---- Keep ----
    "observatory_history_query": ToolDecision(
        "keep",
        "Built 2026-10-03. Reads the recorded nights and calculates the analyses, in replies under 30,000 "
        "characters. The recorded-data tables are read through the butler, which creates a missing table.",
    ),
    "jobs_query": ToolDecision(
        "keep",
        "Built 2026-10-03. Reads jobs, log tails, stored results and data lineage from the logs database, "
        "opened read-only. A job stuck at 'started' after the app stopped still shows as active.",
    ),
    "observatory_mount_abort_motion": ToolDecision(
        "drop", "Commands the mount, so it is dropped with the other hardware tools."
    ),
    "observatory_safety_execute_safe_state": ToolDecision(
        "drop", "Commands the mount and enclosure, so it is dropped with the other hardware tools."
    ),
    "observatory_history_get_performance_envelope": ToolDecision(
        "keep", "Slow: reads every session and target twice.", "observe"
    ),
    "observatory_remote_check_connection": ToolDecision("keep", "A small network probe.", "observe"),
    # ---- Status reads of the control children ----
    "observatory_mount_status": ToolDecision(
        "keep",
        "Reads the mount, filter, focuser and camera temperature. Blocked for now; see the block.",
        "observe",
    ),
    "observatory_imaging_status": ToolDecision(
        "keep", "Reads the filter names, the focuser position and the saved focus model.", "observe"
    ),
    "observatory_guiding_status": ToolDecision(
        "keep",
        "Reads the saved guider calibration, the mount's periodic error model and the guide plate scale "
        "(no device needed), and with include=['live'] the guiding going on now and its RMS error.",
        "observe",
    ),
    "observatory_safety_status": ToolDecision(
        "keep",
        "Reads the safety rules, a weather verdict, the enclosure, the delegation policy and the "
        "agreement evidence for one capability. The delegation policy's live row was once written by a UI "
        "test; clean it before relying on it.",
        "observe",
    ),
    "observatory_equipment_status": ToolDecision(
        "keep",
        "Reads the active equipment, camera profiles, site, commissioning records and the INDI device "
        "list. The INDI sections use the connect-only path.",
        "observe",
    ),
    "observatory_remote_list": ToolDecision(
        "keep", "Lists folders or files on the telescope computer. Reads only.", "observe"
    ),
    "observatory_equipment_set_device_property": ToolDecision(
        "drop",
        "Sets a raw INDI property, which commands a device. Belongs to the dropped control side.",
        "actuate",
    ),
    "calibration_save": ToolDecision("withhold", "Writes the calibration index. The AI is read-only."),
    "calibration_assess_flats": ToolDecision("keep", "Checks flats. Writes nothing."),
    "target_get_header": ToolDecision("keep", "Reads one FITS header."),
    "target_read_saved": ToolDecision(
        "drop",
        "Returns the whole target record, frames and all, which overflows the reply. Use target_query.",
    ),
    "docs_get": ToolDecision("keep", "Reads documentation."),
    "ui_run_tests": ToolDecision("keep", "Developer check."),
    "ui_diagnose_code": ToolDecision("keep", "Developer check."),
    "ui_audit_accessibility": ToolDecision("keep", "Developer check."),
    "ui_build_check": ToolDecision("keep", "Developer check."),
    "typegen_contract_validator": ToolDecision("keep", "Developer check."),
    # ---- Class corrections found by reading the code ----
    "observatory_mount_run_polar_alignment_assist": ToolDecision(
        "drop",
        "Only fits a pointing model from plate-solve records passed in. The user does not want it.",
        "compute",
    ),
    "observatory_safety_enter_controller_mode": ToolDecision(
        "withhold",
        "Writes the delegation state (the Safe Mode toggle) to the database. Not a hardware command, but "
        "the user decided an AI may not change configuration.",
        "change-data",
    ),
    "observatory_safety_enter_monitoring_mode": ToolDecision(
        "withhold",
        "Writes the delegation state (the Safe Mode toggle) to the database. Not a hardware command, but "
        "the user decided an AI may not change configuration.",
        "change-data",
    ),
    # ---- Records: dropped ----
    "observatory_equipment_save_commissioning_run": ToolDecision(
        "drop", "Takes a whole run object that MCP cannot send. The ingest tools create the same records."
    ),
    "observatory_history_save_ekos_session_context": ToolDecision(
        "drop", "Takes a whole context object that MCP cannot send. The ingest tools create the same records."
    ),
    "observatory_guiding_save_run": ToolDecision(
        "drop", "Takes a whole run object that MCP cannot send. The ingest tools create the same records."
    ),
    # ---- No use, empty, or cannot be called through MCP ----
    "observatory_guiding_get_image": ToolDecision(
        "drop",
        "Returns the text form of raw bytes, and None in practice because the app never enables BLOBs.",
        "observe",
    ),
    "observatory_equipment_cooling_ramp_rate": ToolDecision(
        "drop", "Needs a cooling-policy object that MCP cannot send. Arithmetic on the caller's input."
    ),
    # ---- Not wanted by the user ----
    "observatory_guiding_run_loop": ToolDecision(
        "drop",
        "Runs the guide loop until a stop event MCP cannot send is set; the backend runs it on a thread. "
        "Sends guide pulses, which belongs to the dropped control side.",
        "actuate",
    ),
    "observatory_guiding_drain_external_pulses": ToolDecision(
        "drop",
        "Clears guide pulses when read. Belongs to the dropped control side. The user does not want it.",
    ),
    "observatory_guiding_refit_spectrum": ToolDecision(
        "drop", "Recomputes and stores a result. The user does not want an AI to trigger it."
    ),
    "observatory_imaging_compute_focus_correction": ToolDecision(
        "drop", "Advice for a device command the app will not send. The user does not want it."
    ),
    "observatory_guiding_compute_correction": ToolDecision(
        "drop", "Advice for a device command the app will not send. The user does not want it."
    ),
    "observatory_mount_compute_pointing_correction": ToolDecision(
        "drop", "Advice for a device command the app will not send. The user does not want it."
    ),
    "diagnostics_flag_value_outliers": ToolDecision(
        "drop", "Arithmetic on numbers the caller passes in. The user does not want it."
    ),
    # ---- Writers with no read-only form ----
    **dict.fromkeys(
        (
            "calibration_add",
            "calibration_refresh",
            "processing_discard_previous_stack",
            "processing_process_target",
            "processing_restore_excluded_frames",
            "processing_swap_with_previous_stack",
            "star_create",
            "star_find_or_create_by_position",
            "star_update",
            "target_add",
            "target_create",
            "planning_create_mosaic",
            "planning_create_plan",
            "planning_edit_queue",
        ),
        ToolDecision("withhold", READ_ONLY_NOTE),
    ),
    "target_reindex_frames": ToolDecision(
        "withhold",
        "Writes the frame records. A dry_run mode that only reports what would change would let an AI "
        "use it.",
    ),
    # ---- Device commands ----
    **dict.fromkeys(
        (
            "observatory_guiding_expose",
            "observatory_guiding_pulse",
            "observatory_guiding_run_backlash_calibration",
            "observatory_guiding_run_calibration",
            "observatory_guiding_run_exposure_test",
            "observatory_imaging_capture_image",
            "observatory_imaging_focus_move",
            "observatory_imaging_set_filter",
            "observatory_mount_manual_move",
            "observatory_mount_park",
            "observatory_mount_set_slew_rate",
            "observatory_mount_set_tracking",
            "observatory_mount_slew",
            "observatory_mount_sync",
            "observatory_mount_unpark",
            "observatory_safety_close_enclosure",
            "observatory_safety_open_enclosure",
        ),
        ToolDecision("drop", DEVICE_COMMAND_NOTE),
    ),
    # ---- Replaced by a broader tool ----
    "calibration_get": ToolDecision("drop", "Replaced by calibration_query."),
    "target_get": ToolDecision("drop", "Replaced by target_query."),
    "target_list": ToolDecision("drop", "Replaced by target_query."),
    "backend_health_check": ToolDecision("drop", "Replaced by app_status."),
    "ui_navigate_mode": ToolDecision("drop", "Replaced by app_controls."),
    "ui_show_notification": ToolDecision("drop", "Replaced by app_controls."),
}
"""Tool name -> the decision about it. Every served tool has one."""

CATEGORY_RULES = (
    (
        re.compile(
            r"^(electron_run_python|backend_call_rpc|terminal_.+|backend_health_check|docs_get|app_status|app_controls"
            r"|ui_(show_notification|navigate_mode|pause_pipelines|resume_pipelines|editor_sync"
            r"|inspect_variable))$"
        ),
        "app",
    ),
    (
        re.compile(
            r"^(ui_(run_tests|diagnose_code|audit_accessibility|build_check)|typegen_contract_validator)$"
        ),
        "developer",
    ),
    (re.compile(r"^calibration_"), "calibration"),
    (re.compile(r"^target_"), "targets"),
    (re.compile(r"^star_"), "stars"),
    (re.compile(r"^(processing_|diagnostics_|visualization_)"), "image-processing"),
    (re.compile(r"^jobs_"), "jobs-history"),
    (re.compile(r"^(planning_|execution_)"), "planning-sessions"),
    (
        re.compile(
            r"^observatory_[a-z]+_(save_|set_active_|apply_promotion_decision"
            r"|enter_(controller|monitoring)_mode)"
        ),
        "observatory-config",
    ),
)
"""Name patterns tried in order. Observatory tools fall through to
`categorize_tool`, which also looks at the class."""

SYNC_NAME_PATTERN = re.compile(r"remote|sync|download|ingest")
"""Observatory tools with these words in the name move frames or logs."""


def categorize_tool(name: str, tool_class: str) -> str:
    """Choose the category for a tool from its name and class.

    Parameters
    ----------
    name : `str`
        The tool's name.
    tool_class : `str`
        The tool's class. Hardware tools are told apart by it.

    Returns
    -------
    category : `str`
        One of ``CATEGORIES``, or ``"uncategorized"`` if nothing matched.
    """
    for pattern, category in CATEGORY_RULES:
        if pattern.search(name):
            return category
    if name.startswith("observatory_"):
        if tool_class in ("actuate", "safe-stop"):
            return "observatory-control"
        if SYNC_NAME_PATTERN.search(name):
            return "observatory-sync"
        return "observatory-status"
    return "uncategorized"


def find_problems(served_names: set[str], valid_classes: set[str]) -> list[str]:
    """Check the decisions against the live tool list.

    Parameters
    ----------
    served_names : `set` [`str`]
        Names of the tools the servers serve now.
    valid_classes : `set` [`str`]
        The class names the inventory allows.

    Returns
    -------
    problems : `list` [`str`]
        One message per problem: a served tool with no decision, a decision
        or interim block for a tool no server serves, an unknown
        disposition or class, or a tool that is not offered with no note.
    """
    problems = [f"{name!r} has no decision" for name in sorted(served_names - DECISIONS.keys())]
    problems += [
        f"interim block for {name!r}, which no server serves"
        for name in INTERIM_BLOCKS
        if name not in served_names
    ]
    for name, decision in DECISIONS.items():
        if name not in served_names:
            problems.append(f"decision for {name!r}, which no server serves")
        if decision.disposition not in DISPOSITIONS or decision.disposition == "undecided":
            problems.append(f"{name}: unknown disposition {decision.disposition!r}")
        if decision.disposition != "keep" and not decision.note:
            problems.append(f"{name}: a tool that is not offered needs a note that says why")
        if decision.tool_class is not None and decision.tool_class not in valid_classes:
            problems.append(f"{name}: unknown class {decision.tool_class!r}")
    return problems
