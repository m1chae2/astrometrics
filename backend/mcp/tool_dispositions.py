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
        (
            "star_get_object",
            "star_find_by_id_or_name",
            "star_find_all_by_id_or_name",
            "star_existing_ids",
            "star_find_by_position",
            "star_list_objects",
            "star_list_object_ids",
            "star_list_object_summaries",
            "star_list_object_summaries_in_region",
            "star_list_objects_by_ids",
            "star_list_objects_for_target",
            "star_list_objects_in_region",
            "star_list_spectrum_object_ids",
            "star_get_audit",
        ),
        (
            "selector: at most one of ids, name, target_id, region{ra, dec, radius_deg}, "
            "position{ra, dec, tolerance_arcsec}",
            "magnitude_min, magnitude_max, has_spectra",
            "detail: exists | ids | summary | full | stats",
            "limit (ids 2000, summary 500, full 10), offset",
        ),
        "observe",
        notes=(
            "Built 2026-10-03 as StellarCatalog.query. Region radius is at most 5 degrees. Answers are in "
            "id order so offset paging is stable. Library stars only: the online and deep catalogs stay "
            "on the planning_* tools. Browsing the whole library takes about 3 seconds."
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
            "target_list_camera_names",
            "target_camera_index",
        ),
        (
            "target_id, text, camera, or region{ra, dec, radius_deg}",
            "detail: summary | full | cameras",
            "include_frames, sort, include_empty, limit, offset",
        ),
        "observe",
        notes=(
            "Built 2026-10-04 as TargetCatalog.query. summary leaves out the frame list and the empty "
            "placeholder targets; full groups one target's frames by night, filter, exposure and camera "
            "and condenses its quality summaries (about 13 KB for M 57, where target_get was cut at 40 KB). "
            "Looking does not mark targets as touched, so a later save never writes them."
        ),
        built=True,
    ),
    ProposedTool(
        "target_index_frames",
        "targets",
        "Add frames to a target: one file, or a rescan of the target's folder.",
        (
            "processing_scan_target_directory",
            "target_reindex_frames",
            "target_add_frame",
            "processing_create_frame_record",
        ),
        (
            "target",
            "path (one file) or none (rescan)",
            "role, filter_type, camera",
            "prune_missing, refresh_headers, frames_root_path",
            "persist",
            "dry_run",
        ),
        "change-data",
        {"investigator": {"dry_run": {"allowed": [True]}}},
        (
            "dry_run=true is read-only and reports what would change. prune_missing needs a check that the "
            "frames drive is mounted; with the drive missing it empties the frame list. Return "
            "counts, not None."
        ),
    ),
    # ---- Calibration ----
    ProposedTool(
        "calibration_status",
        "calibration",
        "Report the calibration library: counts, file paths, or how a target's frames match it.",
        (
            "calibration_stats",
            "calibration_get",
            "target_get_calibration_frame_statistics",
            "calibration_load",
        ),
        (
            "kind: dark | bias | flat | all",
            "camera, exposure, filter, telescope",
            "target_id",
            "detail: counts | paths | target_match",
            "reload",
            "limit",
        ),
        "observe",
        notes=(
            "calibration_get hides its keyword arguments from MCP today, so no camera or filter "
            "can be passed. "
            "reload replaces the in-memory index, which is not a write to disk."
        ),
    ),
    # ---- Image processing ----
    ProposedTool(
        "pipeline_run",
        "image-processing",
        "Run the processing stages for one target: stack, astrometry, photometry, spectroscopy, asteroids.",
        (
            "processing_process_target",
            "processing_run_astrometry",
            "processing_run_photometry",
            "processing_run_spectroscopy",
            "moving_object_detect_asteroids",
        ),
        (
            "target",
            "stages",
            "per-stage settings (stack, astrometry, photometry, spectroscopy)",
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
            "trial tool."
        ),
    ),
    ProposedTool(
        "pipeline_run_batch",
        "image-processing",
        "Run the full pipeline for a named list of targets.",
        ("target_process_all_targets",),
        ("target_ids (required, never all)", "camera_name", "focal_length_mm", "mode: plan | run"),
        "change-data",
        {"investigator": {"mode": {"allowed": ["plan"]}}},
        "Kept apart from pipeline_run because of its reach: it stacks and saves each target.",
    ),
    ProposedTool(
        "diagnostics_stack_quality",
        "image-processing",
        "Measure a stack, and optionally compare it with another or with the previous stack.",
        (
            "diagnostics_compare_stacks",
            "processing_compare_with_previous_stack",
            "diagnostics_measure_stack_fwhm",
            "diagnostics_measure_stack_rejected_fraction",
            "diagnostics_parse_stack_registration_seq",
            "diagnostics_parse_stack_zero_order_star",
        ),
        (
            "stack_path or target",
            "compare_to: none | previous | path",
            "include: fwhm, noise, flatness, rejected_fraction, registration, zero_order",
        ),
        "compute",
        notes="The .seq and .lst companion files can be found from the stack path.",
    ),
    ProposedTool(
        "diagnostics_frame_quality",
        "image-processing",
        "Measure raw frames: statistics for a target, a check of a folder, or a quarantine preview.",
        (
            "diagnostics_check_raw_frames",
            "processing_preview_quarantine",
            "target_measure_frame_input_quality",
        ),
        (
            "target or folder_path",
            "mode: input_quality | raw_check | quarantine_preview",
            "include_fwhm, remeasure, camera_name",
            "limit (1 to 300; the newest frames, or the first inside a range)",
            "filter_name, first_file, last_file, since, until, include_spectra",
        ),
        "compute",
        notes=(
            "Built 2026-10-03 as QualityDiagnostics.frame_quality. Nothing is saved: the target is measured "
            "on a copy, so the saved catalog never changes. About a second per new frame; frames already "
            "measured keep their stored values. Frames are chosen by filter, file range (a bare number "
            "such as 013 means frame 013) and time, with the choosing code in "
            "pipelines/shared/quality/frame_selection.py. Spectroscopy frames are left out unless asked "
            "for, because their smeared stars are flagged as trailing. raw_check also takes a target."
        ),
        built=True,
    ),
    ProposedTool(
        "excluded_frames",
        "image-processing",
        "List the frames the stacker set aside, or move them back.",
        ("processing_list_excluded_frames", "processing_restore_excluded_frames"),
        ("target", "apply"),
        "change-data",
        {"investigator": {"apply": {"allowed": [False]}}},
        "apply=true moves raw files back into the live frames folder and changes the in-memory target.",
    ),
    ProposedTool(
        "visualization_render_fits",
        "image-processing",
        "Draw a FITS frame or stack as a real image, optionally zoomed on one place.",
        (
            "visualization_convert_fits_to_png",
            "visualization_convert_fits_to_png_with_stats",
            "visualization_get_light_frame_data",
        ),
        (
            "path, or target and file_name (a name, or a number such as 013)",
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
        (
            "visualization_plot_target_dashboard",
            "visualization_plot_photometry",
            "visualization_plot_spectroscopy",
            "visualization_plot_astrometry",
            "visualization_plot_asteroid_detection",
            "visualization_plot_focus_vs_temperature",
            "visualization_plot_star_dashboard",
        ),
        (
            "kind: dashboard | photometry | spectroscopy | astrometry | asteroids | focus | star",
            "target",
            "limit",
        ),
        "observe",
        notes=(
            "A figure comes back to an MCP client as the text 'Figure(1600x900)', so the tool must return a "
            "PNG. plot_star_dashboard always fails through MCP today because its star argument is not "
            "resolved from an id."
        ),
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
            "SyncService.sync_telescope_logs still repeats part of this; it should call this code."
        ),
        built=True,
    ),
    # ---- Planning and sessions ----
    ProposedTool(
        "planning_get_visibility_over_time",
        "planning-sessions",
        "Report where objects are over a span of times, or at one moment, with the Sun, Moon and horizon.",
        ("planning_get_visibility", "planning_get_meridian_status"),
        (
            "objects: names or {id, ra_deg, dec_deg}",
            "start, end (equal for one moment), step_minutes",
            "minimum_altitude_deg, horizon_zones",
            "timezone_offset_hours, include_samples",
        ),
        "observe",
        notes=(
            "Built 2026-10-03; the single-time tools were folded in the same day. at_start gives altitude, "
            "azimuth, hour angle, flip status and rise, set and transit at the start time. Names not in "
            "the library go to SIMBAD over the network. planning_resolve_target_coordinates stays "
            "separate: it looks up a position, not a time."
        ),
        built=True,
    ),
    ProposedTool(
        "planning_get_advisory",
        "planning-sessions",
        "Report the quality advisory for a target, or the calibration inventory advisory for a camera.",
        ("planning_get_target_quality_advisory", "planning_get_calibration_advisory"),
        ("kind: quality | calibration | both", "target_id", "camera_id, frame_type, exposure_sec, filter"),
        "observe",
        notes=(
            "The calibration advisory always reports 0 today: it reads a table that only reconcile_session "
            "fills, and that has never run. The quality advisory has the variable-star count fixed at 0."
        ),
    ),
    ProposedTool(
        "planning_plan_mosaic",
        "planning-sessions",
        "Compute mosaic panels, and optionally create their targets and packages.",
        ("planning_calculate_panels", "planning_create_mosaic_targets", "planning_generate_mosaic_packages"),
        (
            "parent_target_id or centre",
            "rows, cols, overlap_percent",
            "equipment",
            "exposure_requests",
            "dither_config",
            "commit",
        ),
        "change-data",
        {"investigator": {"commit": {"allowed": [False]}}},
        (
            "commit=true writes panel targets to the catalog and packages to the wayfinding database, not in "
            "one step. calculate_panels uses a fixed 23.5 x 15.6 mm sensor and 400 mm focal "
            "length because it "
            "reads configuration sections that do not exist. The same offset maths is written twice."
        ),
    ),
    ProposedTool(
        "planning_manage_plan",
        "planning-sessions",
        "Create observation packages and sessions, place them in a night, and edit the queue.",
        (
            "planning_create_observation_package",
            "planning_create_empty_session",
            "planning_plan_observation_session",
            "planning_add_to_queue",
            "planning_reorder_queue",
        ),
        (
            "action: create_package | create_session | auto_place | add_entry | reorder_queue",
            "package_id, session_id, site_profile_id, camera_id, night_date, entry_ids",
            "dry_run",
        ),
        "change-data",
        {"investigator": {"action": {"allowed": ["auto_place"]}, "dry_run": {"allowed": [True]}}},
        (
            "None of these five can be called through MCP today: they need package, session, site and "
            "telescope objects that MCP cannot send. The tool needs id-based arguments and resolvers. The "
            "production database holds no packages and one hand-made empty session. Consider waiting until "
            "planning matures."
        ),
    ),
    ProposedTool(
        "planning_get_plan",
        "planning-sessions",
        "Read observation packages, sessions and their queues.",
        (),
        ("session_id or package_id, or none to list",),
        "observe",
        notes="New. No tool reads a session or queue back today, so the queue tools cannot be checked.",
    ),
    ProposedTool(
        "planning_deep_catalog",
        "planning-sessions",
        "Report on the deep star catalog download, estimate its size, or start the build.",
        (
            "planning_get_deep_catalog_status",
            "planning_estimate_deep_catalog_size",
            "planning_build_deep_star_catalog",
        ),
        ("action: status | estimate | build", "healpix_level, magnitude_limit"),
        "change-data",
        {"investigator": {"action": {"allowed": ["status", "estimate"]}}},
        (
            "build downloads from Gaia for hours and writes the catalog database. Keep it away "
            "from AI clients. "
            "estimate sends about two dozen network queries."
        ),
    ),
    ProposedTool(
        "execution_session",
        "planning-sessions",
        "Read a session or list sessions, abort one, or run its after-session checks.",
        ("execution_abort_session", "execution_reconcile_session"),
        ("action: get | list | abort | reconcile", "session_id"),
        "change-data",
        {"investigator": {"action": {"allowed": ["get", "list"]}}},
        (
            "get and list are new. abort_session changes the session in memory only and nothing saves it, so "
            "an abort is lost; fix that first. reconcile writes the calibration counts and the session."
        ),
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
    "planning_create_sequence_plan": ToolDecision(
        "drop", "Old duplicate of create_observation_package. Writes nothing.", "compute"
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
    "target_remake_preview": ToolDecision(
        "keep",
        "Built 2026-10-04 from gap report #1. Makes a target's preview picture again from its existing "
        "stack, without restacking. denoise, denoise_strength and star_toning apply to that run only and "
        "never reach the configuration. The old pictures are copied into _previous_preview first and put "
        "back if the run fails. The stack file is never written. Writes only the preview JPEG, the "
        "processed FITS and the target's processed-image pointer.",
        "process",
    ),
    "target_stack": ToolDecision(
        "keep",
        "Built 2026-10-04 at the user's request: the AI may stack a target the way the app's Stack "
        "button does, choosing imaging or spectral frames, a filter and a file or time range. "
        "plan_only lists the frames without stacking. It runs the app's own stage: sets aside bad "
        "frames into _excluded (never deletes), keeps one previous stack, and saves the target.",
        "process",
    ),
    "processing_run_stacking": ToolDecision(
        "withhold",
        "Replaced by target_stack, which picks the frames, holds the stacking slot and saves the result.",
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
        "Built 2026-10-04. Targets as short rows or one grouped record; replaces target_get, target_list, "
        "target_list_camera_names and target_camera_index.",
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
    "planning_get_visibility_over_time": ToolDecision(
        "keep",
        "Built 2026-10-03. A night table for several objects: altitude, horizon clearance with "
        "caller-given blocked ranges, meridian crossing, Sun and Moon. Reads nothing stored.",
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
        "Reads the saved guider calibration, the mount's periodic error model and the guide plate scale. "
        "No device is needed.",
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
    "target_camera_index": ToolDecision(
        None, "Summarizes each target's light frames per camera for the target list. Reads only.", "observe"
    ),
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
    "processing_scan_target_directory": ToolDecision(
        None, "Changes the cached target in memory; a later save writes it.", "change-data"
    ),
    "processing_create_frame_record": ToolDecision(None, "Only reads the FITS header.", "observe"),
    "moving_object_detect_asteroids": ToolDecision(
        None, "Writes job and provenance rows and changes the cached target.", "change-data"
    ),
    "target_measure_frame_input_quality": ToolDecision(
        None, "Saves to the database by default.", "change-data"
    ),
    "planning_plan_observation_session": ToolDecision(
        None, "Writes the session to the wayfinding database.", "change-data"
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
    (re.compile(r"^(calibration_|target_get_calibration_frame_statistics$)"), "calibration"),
    (re.compile(r"^(target_measure_frame_input_quality|target_process_all_targets)$"), "image-processing"),
    (re.compile(r"^processing_(scan_target_directory|create_frame_record)$"), "targets"),
    (re.compile(r"^target_"), "targets"),
    (re.compile(r"^star_"), "stars"),
    (re.compile(r"^(processing_|diagnostics_|visualization_|moving_object_)"), "image-processing"),
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
