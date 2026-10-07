"""Purpose: Record the decisions about what each MCP tool should become.

Description: The MCP tool inventory (``tool_inventory.py``) lists every tool
and drafts a safety class for it. This module adds three more things that a
person reviews:

* a **category** for each tool (for example ``stars`` or ``targets``),
* a **disposition**: keep the tool, merge it into a new tool, fix it first,
  keep it away from AI clients, drop it, or leave it undecided,
* the **proposed tools** that replace groups of near-identical tools. Each
  one lists the tools it replaces and the argument values an AI client may
  use in each profile.

The entries come from reading the code behind each tool, not from tool
names. Names alone hid several cases: a "read" tool that writes, tools that
cannot work through MCP, and tools that only repeat each other. Every
``replaces`` name is checked against the live tool list, so a typo or a
renamed tool shows up as a problem.
"""

import re
from dataclasses import dataclass, field
from typing import Any

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
    "keep": "Stays as it is.",
    "merge": "Replaced by a proposed tool that takes the differences as arguments.",
    "merged": "Replaced by a tool that now exists. No longer offered.",
    "fix": "Stays, but needs a change before an AI client can use it.",
    "withhold": "Never offered to an AI client.",
    "drop": "Removed from the MCP tool list.",
    "undecided": "Not assessed yet.",
}
"""What happens to a tool."""

DROPPED_CATEGORIES = {
    "observatory-control": (
        "Commanding a device (mount, cameras, focuser, filter wheel, enclosure) is out of scope. "
        "Reading from them and from the Pi is allowed."
    ),
}
"""Categories whose tools are all dropped, unless a decision says otherwise."""

NOT_OFFERED_CLASSES = frozenset({"change-data", "actuate", "safe-stop", "unrestricted"})
"""Classes withheld from the AI, which is read-only, unless a proposed tool
gives a read-only form."""

READ_ONLY_NOTE = "The AI is read-only, so a tool that writes is not offered."

INTERIM_BLOCKS = {
    "ui_pause_pipelines": (
        "Freezes Siril and the plate solver, so it is not read-only. Closed by decision: the AI may "
        "navigate and notify only."
    ),
    "ui_resume_pipelines": (
        "Thaws Siril and the plate solver, so it is not read-only. Closed by decision: the AI may "
        "navigate and notify only."
    ),
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
They keep their merge disposition."""

INVESTIGATOR_CLASSES = ("observe", "compute", "ingest", "process")
"""Classes the investigator profile may use without argument rules."""

PROFILES = ("investigator",)
"""Profiles with argument rules. The AI is read-only, so none may write."""


@dataclass(frozen=True)
class ToolDecision:
    """A decision about one existing tool.

    Attributes
    ----------
    disposition : `str` or `None`
        One of ``DISPOSITIONS``. `None` means only the class changes, and
        the disposition follows from the proposed tools.
    note : `str`
        Why, in plain words.
    tool_class : `str` or `None`
        A corrected class. `None` keeps the drafted class.
    """

    disposition: str | None
    note: str = ""
    tool_class: str | None = None


@dataclass(frozen=True)
class ProposedTool:
    """A new tool that replaces a group of near-identical tools.

    Attributes
    ----------
    name : `str`
        The proposed tool name.
    category : `str`
        One of ``CATEGORIES``.
    summary : `str`
        What the tool does.
    replaces : `tuple` [`str`, ...]
        Existing tools this one replaces. Empty for a tool that is new.
    parameters : `tuple` [`str`, ...]
        A sketch of the arguments, with the differences between the
        replaced tools expressed as arguments.
    tool_class : `str`
        The highest class the tool reaches when every argument is used.
    argument_rules : `dict`
        Profile name -> argument name -> rule. A rule is
        ``{"allowed": [...]}`` or ``{"max": number}``. A profile with
        rules may call the tool only with values the rules allow.
    notes : `str`
        Work needed first, and open questions.
    built : `bool`
        `True` once the tool exists. The tools it replaces are then
        ``merged`` and no longer offered.
    """

    name: str
    category: str
    summary: str
    replaces: tuple[str, ...]
    parameters: tuple[str, ...]
    tool_class: str
    argument_rules: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    notes: str = ""
    built: bool = False

    def investigator_access(self) -> str:
        """Say how much of the tool an investigator profile may use.

        Returns
        -------
        access : `str`
            ``"full"`` for a read-only tool, ``"limited"`` when argument
            rules restrict it to its safe values, or ``"none"``.
        """
        if self.argument_rules.get("investigator"):
            return "limited"
        return "full" if self.tool_class in INVESTIGATOR_CLASSES else "none"


PROPOSED_TOOLS = (
    # ---- Stars ----
    ProposedTool(
        "star_query",
        "stars",
        "Look up library stars by id, name, target, region or position, with a hard cap on the answer.",
        (),
        (
            "selector: at most one of ids, name, target_id, region{ra_deg, dec_deg, radius_deg}, "
            "position{ra_deg, dec_deg, tolerance_arcsec}",
            "magnitude_min, magnitude_max, has_spectra, spectral_class",
            "detail: exists | ids | summary | analysis | objects | class_counts | stats",
            "limit (ids 2000, summary 500, analysis and objects 10), offset",
        ),
        "observe",
        notes=(
            "Built 2026-10-03 as StellarCatalog.query. Since 2026-10-05 it is the only star read besides "
            "star_get: the narrow list, find, audit and class-count tools are gone. Region radius is at "
            "most 5 degrees. Answers are in id order so offset paging is stable. Library stars only: the "
            "online and deep catalogs stay on the planning_* tools. Browsing the whole library takes "
            "about 3 seconds."
        ),
        built=True,
    ),
    ProposedTool(
        "target_query",
        "targets",
        "Look up targets: one, a list, camera names, or the sky positions already imaged.",
        (
            "target_get",
            "target_list",
        ),
        (
            "target_id, text, camera_id, object_type, or region{ra_deg, dec_deg, radius_deg}",
            "detail: summary | full | cameras | nights | camera_index",
            "include_frames, sort, include_empty, limit, offset",
        ),
        "observe",
        notes=(
            "Built 2026-10-04 as TargetCatalog.query. summary leaves out the frame list and the empty "
            "placeholder targets; full groups one target's frames by night, filter, exposure and camera "
            "and condenses its quality summaries (about 13 KB for M 57, where target_get was cut at 40 KB). "
            "Looking does not mark targets as touched, so a later save never writes them. The camera "
            "names and the per-camera frame index are detail='cameras' and detail='camera_index'."
        ),
        built=True,
    ),
    ProposedTool(
        "target_index_frames",
        "targets",
        "Add frames to a target: one file, or a rescan of the target's folder.",
        ("target_reindex_frames",),
        (
            "target (none for every target)",
            "paths (only these files) or none (rescan)",
            "role, filter_type, camera_id",
            "prune_missing, refresh_headers",
            "dry_run",
        ),
        "change-data",
        {"investigator": {"dry_run": {"allowed": [True]}}},
        (
            "TargetCatalog.reindex_frames(target=None, paths=None) now covers the folder scan, adding single "
            "files and the full-library reindex, and returns a ReindexReport of counts. Still to do: "
            "dry_run=true, read-only, reporting what would change; and a check that the frames drive is "
            "mounted before prune_missing, which with the drive missing empties the frame list."
        ),
    ),
    # ---- Calibration ----
    ProposedTool(
        "calibration_query",
        "calibration",
        "Report the calibration library: counts, or how a target's frames match it.",
        ("calibration_get",),
        (
            "kind: dark | bias | flat (none for all)",
            "detail: counts | target_match | target_frames",
            "target, camera_id",
            "refresh",
        ),
        "observe",
        notes=(
            "Built 2026-10-05 as CalibrationCatalog.query, folding stats, load and the target's calibration "
            "frame statistics. refresh replaces the in-memory index, which is not a write to disk. "
            "calibration_get, which lists file paths, hides its keyword arguments from MCP, so no camera "
            "or filter can be passed; it stays merged until a paths detail exists."
        ),
        built=True,
    ),
    # ---- Image processing ----
    ProposedTool(
        "pipeline_run",
        "image-processing",
        "Run the processing stages for one target: stack, astrometry, photometry, spectroscopy, asteroids.",
        ("processing_process_target",),
        (
            "target (one, a list, or none for every target)",
            "stages: astrometry | photometry | spectroscopy | asteroids",
            "per-stage settings (astrometry, photometry, spectroscopy, asteroids)",
            "camera_id, focal_length_mm (several targets)",
            "mode: plan | run",
            "workspace",
        ),
        "change-data",
        {"investigator": {"mode": {"allowed": ["plan"]}}},
        (
            "plan mode does not exist yet: it must report the settings in effect, the frames "
            "chosen and where "
            "output would go. No library option runs a pipeline without writing to the live "
            "catalog, stacks and "
            "frame folders. The only complete way is a separate process with a scratch config, library and "
            "stacks folder (ASTROMETRICS_CONFIG_PATH). A workspace argument is the first piece "
            "of the stacking "
            "trial tool. Since 2026-10-05 ProcessingPipelines.process_target takes the stages (with the "
            "asteroid search as a stage) and, given a list of targets or none, runs the full pipeline for "
            "each, which stacks and saves every target."
        ),
    ),
    ProposedTool(
        "diagnostics_stack_quality",
        "image-processing",
        "Measure a stack, and optionally compare it with another or with the previous stack.",
        (),
        (
            "path_or_target",
            "kind: imaging | spectral (with a target)",
            "compare_to: none | previous | path",
            "include: fwhm, rejected_fraction, registration",
        ),
        "compute",
        notes=(
            "Built 2026-10-05 as QualityDiagnostics.stack_quality. The comparison always measures noise and "
            "flatness. The zero-order star of each spectral frame is read from Siril's per-frame .lst file, "
            "not from a stack, so it is not a section here."
        ),
        built=True,
    ),
    ProposedTool(
        "diagnostics_frame_quality",
        "image-processing",
        "Measure raw frames: statistics for a target, a check of a folder, or a quarantine preview.",
        (),
        (
            "target or folder_path",
            "kind: input_quality | raw_check | quarantine_preview",
            "include: fwhm, spectra, excluded",
            "remeasure, camera_id",
            "limit (1 to 300; the newest frames, or the first inside a range)",
            "filter_name, first_file, last_file, since, until",
        ),
        "compute",
        notes=(
            "Built 2026-10-03 as QualityDiagnostics.frame_quality. Nothing is saved: the target is measured "
            "on a copy, so the saved catalog never changes. About a second per new frame; frames already "
            "measured keep their stored values. Frames are chosen by filter, file range (a bare number "
            "such as 013 means frame 013) and time, with the choosing code in "
            "pipelines/shared/quality/frame_selection.py. Spectroscopy frames are left out unless asked "
            "for, because their smeared stars are flagged as trailing. raw_check also takes a target. "
            "include=['excluded'] lists the frames the stacker set aside."
        ),
        built=True,
    ),
    ProposedTool(
        "excluded_frames",
        "image-processing",
        "List the frames the stacker set aside, or move them back.",
        ("processing_restore_excluded_frames",),
        ("target", "apply"),
        "change-data",
        {"investigator": {"apply": {"allowed": [False]}}},
        (
            "apply=true moves raw files back into the live frames folder and reindexes the target. The read "
            "half is built: diagnostics_frame_quality with include=['excluded']."
        ),
    ),
    ProposedTool(
        "visualization_render_fits",
        "image-processing",
        "Draw a FITS frame or stack as a real image, optionally zoomed on one place.",
        (),
        (
            "path, or target and file_name (a name, or a number such as 013), or target, iso and exposure",
            "kind: image | data_url",
            "max_dimensions (100 to 2000, default 1200), stretch, center, width",
            "crop_center_x, crop_center_y, crop_size",
        ),
        "observe",
        notes=(
            "Built 2026-10-03 as Visualization.render_fits. The reply is an MCP image block plus a "
            "description of the brightness range and crop, not base64 text. It draws from the FITS data "
            "each time, so a restacked file never shows an old picture. A crop is cut at full resolution "
            "and enlarged so single stars can be judged. get_last_captured_image and target_get_frame stay "
            "for now: the first is blocked until it returns an image, the second returns a path."
        ),
        built=True,
    ),
    ProposedTool(
        "visualization_plot",
        "image-processing",
        "Draw a target dashboard, a single view of it, or the focus-versus-temperature trend.",
        (),
        (
            "kind: dashboard | photometry | spectroscopy | astrometry | asteroids | focus | star",
            "target, or star (and spectral_star) for kind=star",
            "selected_star, limit, figsize",
        ),
        "observe",
        notes=(
            "Built 2026-10-05 as Visualization.plot, which takes star ids as well as records. The MCP "
            "registry draws the figure to a PNG image."
        ),
        built=True,
    ),
    # ---- Observatory status ----
    ProposedTool(
        "observatory_equipment_status",
        "observatory-status",
        "Report the selected equipment, the camera profiles and the site.",
        (),
        (
            "include: telescope | camera | guide_scope | guide_camera | camera_profiles | configuration | "
            "observer_location | commissioning_runs | indi_devices | indi_properties",
            "device_name (only with indi_properties)",
        ),
        "observe",
        notes=(
            "Built 2026-10-05 as control.equipment.status, which reads the active equipment, the camera "
            "profiles, the equipment configuration and the site. The saved guider models are "
            "observatory_guiding_status, and the delegation policy, safety rules and enclosure are "
            "observatory_safety_status. The configuration section uses a fixed telescope name, not the "
            "active one. The commissioning records are long, so they are read only on request."
        ),
        built=True,
    ),
    ProposedTool(
        "observatory_history_query",
        "observatory-status",
        "Analyse or list past nights: capture, guiding, sky coverage, recurring issues, Ekos "
        "sessions, guiding "
        "runs and the pointing model.",
        (),
        (
            "kind: capture | guiding | sky_coverage | recurring_issues | ekos_sessions | guiding_runs | "
            "pointing_model",
            "session_id (a night, YYYY-MM-DD)",
            "ekos_file_id",
            "include (sections of an Ekos context)",
            "limit",
        ),
        "compute",
        notes=(
            "Built 2026-10-03; control.history.query since 2026-10-05, with the "
            "logic in wayfindinglib/tasks/control_tasks/night_history.py and night_analysis.py. Every reply "
            "is measured and shrunk to under 30,000 characters. An argument that a kind does not use is "
            "refused. Each night is judged against the nights before it, so the per-night work cannot be "
            "shared; limit bounds it instead (10 nights take about 10 seconds). pointing_model needs a "
            "session_id and an observer location, and refuses instead of guessing a 45 degree latitude."
        ),
        built=True,
    ),
    # ---- Observatory synchronization ----
    ProposedTool(
        "observatory_remote_list",
        "observatory-sync",
        "List folders or files on the telescope computer.",
        (),
        (
            "kind: folders | target_folders | calibration_folders | unassociated_folders | files",
            "folder_name (only with files)",
            "sizes (only with files)",
        ),
        "observe",
        notes=(
            "Built 2026-10-05 as control.remote.list. folder_name is checked against the listed folders "
            "before it reaches the ssh command. Today an offline host and an empty folder both return []; "
            "return an error instead. Output is cut at about 650 paths."
        ),
        built=True,
    ),
    ProposedTool(
        "observatory_remote_sync_frames",
        "observatory-sync",
        "Bring new frames from the telescope computer into the library, or preview it.",
        (),
        (
            "target (a library target; none means every folder)",
            "dry_run",
            "files, local_path, incremental (copy chosen files into the target)",
        ),
        "ingest",
        notes=(
            "Built 2026-10-03; control.remote.sync_frames since 2026-10-05, "
            "when it also took over the calibration-folder, all-folder and local-folder copies. The user "
            "allows the AI to ingest frames. It adds files, frame records and targets and never deletes: no "
            "copy prunes frame records. The MCP server resolves target against the library, so a made-up "
            "name cannot reach the shell or create a target. dry_run uses the downloader's rule (file name "
            "and size). A real run is a background job; follow it with jobs_query. The transfer sorts files "
            "into the library by their headers, using a fixed telescope name (Apertura 75Q) from older code."
        ),
        built=True,
    ),
    ProposedTool(
        "observatory_remote_sync_logs",
        "observatory-sync",
        "Bring the guide and Ekos logs from the telescope computer into the library's database, "
        "or preview it.",
        (),
        ("dry_run", "download", "destination_dir"),
        "ingest",
        notes=(
            "Built 2026-10-03; control.remote.sync_logs since 2026-10-05. A real "
            "run downloads the new guide and Ekos analyze logs, then stores the guiding samples and one "
            "session record per analyze file. It is safe to repeat and never deletes. It does not refit the "
            "stored guiding spectrum: that is observatory_guiding_refit_spectrum, which the user does not "
            "want an AI to trigger. dry_run compares remote and local files by name and size. The backend's "
            "SyncService.sync_telescope_logs calls this code."
        ),
        built=True,
    ),
    # ---- Planning and sessions ----
    ProposedTool(
        "planning_get_visibility",
        "planning-sessions",
        "Report where objects are at one moment or over a span, with the Sun, Moon and horizon.",
        (),
        (
            "objects: names or {id, ra_deg, dec_deg} (none for every library target)",
            "time, end_time (none for one moment), step_minutes",
            "include: meridian | samples",
            "minimum_altitude_deg, horizon_zones, timezone_offset_hours, clear_only",
        ),
        "compute",
        notes=(
            "Built 2026-10-03 as a night table; since 2026-10-06 it is ObservationPlanning.get_visibility, "
            "which also took over the single-moment answer, the meridian status (include=['meridian']) and "
            "the backend's visible-targets list. A span covers at most 30 objects and 150 rows. Names not in "
            "the library go to SIMBAD over the network. planning_lookup_coordinates stays separate: it looks "
            "up a position, not a time."
        ),
        built=True,
    ),
    ProposedTool(
        "planning_get_advisory",
        "planning-sessions",
        "Report the quality advisory for a target, or the calibration inventory advisory for a camera.",
        (),
        ("kind: quality | calibration", "target", "camera_id, frame_type, exposure_seconds, filter"),
        "observe",
        notes=(
            "Built 2026-10-06 as ObservationPlanning.get_advisory. The calibration advisory always reports 0 "
            "today: it reads a table that only reconcile_session fills, and that has never run. The quality "
            "advisory has the variable-star count fixed at 0."
        ),
        built=True,
    ),
    ProposedTool(
        "planning_create_mosaic",
        "planning-sessions",
        "Add one library target per mosaic panel, and optionally one observation package each.",
        (),
        ("target", "panels (from planning_calculate_panels)", "exposure_requests, dither_config", "packages"),
        "change-data",
        notes=(
            "Built 2026-10-06 as ObservationPlanning.create_mosaic, the write half of mosaic planning; "
            "planning_calculate_panels is the read half. It writes panel targets to the catalog even with "
            "packages=false, so no read-only form exists. calculate_panels uses a fixed 23.5 x 15.6 mm "
            "sensor and 400 mm focal length unless equipment is given, because it reads configuration "
            "keys that do not exist."
        ),
        built=True,
    ),
    ProposedTool(
        "planning_create_plan",
        "planning-sessions",
        "Create a sequence plan, an observation package, or an observing session (empty or placed).",
        (),
        (
            "kind: sequence | package | empty_session | scheduled_session",
            "target, plan_items, exposure_requests and the package settings",
            "requests (package ids), site_profile, telescope, camera_id, night_id",
        ),
        "change-data",
        notes=(
            "Built 2026-10-06 as ObservationPlanning.create_plan. Only sequence writes nothing. The session "
            "kinds need site and telescope objects that MCP cannot send. The production database holds no "
            "packages and one hand-made empty session."
        ),
        built=True,
    ),
    ProposedTool(
        "planning_edit_queue",
        "planning-sessions",
        "Add recorded packages to a session's queue, or reorder it.",
        (),
        ("session_id", "add: [{package_id, start_time_mode, requested_start_time}]", "order: entry ids"),
        "change-data",
        notes="Built 2026-10-06 as ObservationPlanning.edit_queue. Writes the session.",
        built=True,
    ),
    ProposedTool(
        "planning_get_plan",
        "planning-sessions",
        "Read one observing session with its queue, or list the sessions.",
        (),
        ("session_id, or none to list",),
        "observe",
        notes=(
            "Built 2026-10-06 as ObservationPlanning.get_plan, which also serves the backend's session list "
            "and session view. Packages are not listed yet."
        ),
        built=True,
    ),
    ProposedTool(
        "planning_deep_catalog_status",
        "planning-sessions",
        "Report on the deep star catalog download, and optionally estimate its full size.",
        (),
        ("include: estimate", "healpix_level, magnitude_limit, sample_count (estimate only)"),
        "compute",
        notes=(
            "Built 2026-10-06 as ObservationPlanning.deep_catalog_status. The estimate sends about two dozen "
            "network queries to the Gaia archive and saves nothing. planning_build_deep_star_catalog stays "
            "separate because it writes: it downloads from Gaia for hours and writes the catalog database."
        ),
        built=True,
    ),
    # ---- App ----
    ProposedTool(
        "app_status",
        "app",
        "Report the backend health, the current view, active jobs, and connection and system status.",
        ("backend_health_check",),
        ("include: health | view | active_jobs | connections | system",),
        "observe",
        notes=(
            "Built 2026-10-03 as tool_app_status. health is the old probe; connections and system come "
            "from the system:health RPC; active_jobs comes from the job history (Jobs.query). view is "
            "not tracked by the backend, so the reply says so instead of guessing."
        ),
        built=True,
    ),
    ProposedTool(
        "app_controls",
        "app",
        "Do what the person can do in the app: change the view, show a notification, pause or resume jobs.",
        (
            "ui_navigate_mode",
            "ui_show_notification",
            "ui_pause_pipelines",
            "ui_resume_pipelines",
        ),
        (
            "action: navigate | notify (pause and resume are not offered)",
            "mode, target",
            "title, body, urgency",
        ),
        "ui-control",
        {"investigator": {"action": {"allowed": ["navigate", "notify"]}}},
        (
            "The user allows navigate and notify. pause and resume freeze or thaw Siril and the "
            "plate solver, "
            "so they are not read-only and stay closed."
        ),
        built=True,
    ),
)
"""The proposed replacement tools."""

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
    "target_read_saved": ToolDecision("keep", "Reads one target record from storage. Writes nothing."),
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
}
"""Decisions for tools that are not simply merged."""

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
    """Choose the category for a tool that no proposed tool replaces.

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


def replaced_by_lookup() -> dict[str, ProposedTool]:
    """Map each replaced tool name to the proposed tool that replaces it.

    Returns
    -------
    lookup : `dict` [`str`, `ProposedTool`]
        Existing tool name -> its replacement. A name listed twice keeps
        the later entry, and `find_problems` reports it.
    """
    return {name: proposed for proposed in PROPOSED_TOOLS for name in proposed.replaces}


def find_problems(served_names: set[str], valid_classes: set[str]) -> list[str]:
    """Check the proposed tools and decisions against the live tool list.

    Parameters
    ----------
    served_names : `set` [`str`]
        Names of the tools the servers serve now.
    valid_classes : `set` [`str`]
        The class names the plan allows.

    Returns
    -------
    problems : `list` [`str`]
        One message per problem: an unknown name, a tool replaced twice,
        a bad category, disposition or profile, or a class not in the plan.
    """
    problems = []
    seen: dict[str, str] = {}
    for proposed in PROPOSED_TOOLS:
        if proposed.category not in CATEGORIES:
            problems.append(f"{proposed.name}: unknown category {proposed.category!r}")
        for profile in proposed.argument_rules:
            if profile not in PROFILES:
                problems.append(f"{proposed.name}: unknown profile {profile!r}")
        for replaced in proposed.replaces:
            if replaced not in served_names:
                problems.append(f"{proposed.name} replaces {replaced!r}, which no server serves")
            if replaced in seen:
                problems.append(f"{replaced!r} is replaced by both {seen[replaced]} and {proposed.name}")
            seen[replaced] = proposed.name
        if proposed.name in served_names and not proposed.built:
            problems.append(f"{proposed.name} is already the name of a served tool; mark it built")
        if proposed.built and proposed.name not in served_names:
            problems.append(f"{proposed.name} is marked built, but no server serves it")
    problems += [
        f"interim block for {name!r}, which no server serves"
        for name in INTERIM_BLOCKS
        if name not in served_names
    ]
    for name, decision in DECISIONS.items():
        if name not in served_names:
            problems.append(f"decision for {name!r}, which no server serves")
        if decision.disposition is not None and decision.disposition not in DISPOSITIONS:
            problems.append(f"{name}: unknown disposition {decision.disposition!r}")
        if decision.disposition == "merge":
            problems.append(f"{name}: merge comes from the proposed tools, not from a decision")
        if decision.tool_class is not None and decision.tool_class not in valid_classes:
            problems.append(f"{name}: unknown class {decision.tool_class!r}")
    problems += [
        f"{proposed.name}: unknown class {proposed.tool_class!r}"
        for proposed in PROPOSED_TOOLS
        if proposed.tool_class not in valid_classes
    ]
    return problems
