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

When a public method is renamed, its tool is renamed too. ``TOOL_RENAMES``
maps the old tool name to the new one, so the reviewed decision recorded
under the old name holds for the new name (`decision_for`) until someone
moves the row.
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
    "observatory_imaging_status": (
        "The filters and focuser sections return [] and 0 because the MCP process has no hardware "
        "connection. Use app_status with the telescope section (focuser position, filter, pier side, "
        "tracking)."
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
        "withhold",
        "Reaches every backend method, including all the ones above and the night plan writes "
        "(plan:edit, plan:tick_checklist_item).",
    ),
    "terminal_inspect_api": ToolDecision("withhold", "Builds Python code from its argument. Console only."),
    "terminal_get_recent_runs": ToolDecision(
        "keep",
        "Built 2026-11-15. Reads what the person's recent Command Console runs printed and drew. Read-only.",
    ),
    "terminal_add_script": ToolDecision(
        "keep",
        "Built 2026-11-15. Saves a new script to the Command Console's My scripts list for the person to "
        "review and run. It cannot run it or replace a script.",
    ),
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
    "observatory_equipment_set_active": ToolDecision(
        "withhold", "Writes the active telescope and camera into the configuration file."
    ),
    "observatory_equipment_set_pointing_limits": ToolDecision(
        "withhold", "Writes the telescope's safety limits into the configuration file."
    ),
    "observatory_configuration_create": ToolDecision("withhold", "Writes the database. The AI is read-only."),
    "observatory_configuration_update": ToolDecision("withhold", "Writes the database. The AI is read-only."),
    "observatory_configuration_scan": ToolDecision(
        "withhold", "Writes the database and the frame records. The AI is read-only."
    ),
    "observatory_safety_apply_promotion_decision": ToolDecision(
        "withhold", "Applies an operator's decision. Needs the operator."
    ),
    # ---- Fix first ----
    "star_get_spectrum": ToolDecision(
        "withhold", "A script convenience. The AI reads a star's spectrum through star_get and star_query."
    ),
    "star_measure_periodicity": ToolDecision(
        "keep",
        "Since #206 the period and transit searches of star_record_periodicity without the save. Runs the "
        "searches on one star's light curve and writes nothing.",
        tool_class="compute",
    ),
    "star_record_periodicity": ToolDecision(
        "withhold",
        "Writes the period and transit results into the live star catalog. The AI is read-only, so it runs "
        "the same searches with star_measure_periodicity instead.",
        "change-data",
    ),
    "processing_plate_solve": ToolDecision(
        "keep",
        "Since 2026-11-26 (#199) returns a PlateSolution: the image center, pixel scale, rotation and the "
        "sky map's header cards. Solves one image in memory and writes nothing.",
        tool_class="compute",
    ),
    "processing_list_preview_bright_objects": ToolDecision(
        "withhold",
        "Built 2026-11-22 for the Image Processing screen's bright-object menu. It asks SIMBAD and saves "
        "the list beside the stack. The AI reads it, and sets the choice, through processing_enhance, "
        "which the astrometrics-backend server serves; this library tool stays withheld.",
        tool_class="observe",
    ),
    "processing_find_stack_configuration": ToolDecision(
        "keep",
        "Built 2026-11-26. Names the one stack of a target taken with a camera and optic, or says why "
        "none or several match and lists the target's configurations. Reads the target record only.",
        tool_class="observe",
    ),
    # ---- Drop ----
    "planning_get_constellation_lines": ToolDecision(
        "drop",
        "Draws the Planetarium: about 750 line segments (89 KB) of fixed stick figures, no use to an AI.",
    ),
    "execution_advance_session": ToolDecision(
        "drop", "Needs callables that MCP cannot send. No MCP use today."
    ),
    "execution_create_recorder": ToolDecision(
        "drop", "Builds an object. The result is only a text label over MCP."
    ),
    # ---- Not offered yet: parts of the old sync set not built ----
    "observatory_remote_sync_frames": ToolDecision(
        "keep",
        "Built 2026-10-03. The AI may ingest frames from the telescope: adds, never deletes. Since "
        "2026-11-21 the astrometrics-backend server serves it through telescope:sync, so it is the app's "
        "own sync of one target (or Bias, Dark, Flat) and a remote_sync job in the job list; dry_run "
        "reports the plan. Copying every folder, chosen files or a local folder is no longer offered.",
        "ingest",
    ),
    "observatory_remote_sync_logs": ToolDecision(
        "keep",
        "Built 2026-10-03. The AI may ingest the guide and Ekos logs: adds or updates records, "
        "never deletes. Since 2026-11-21 the astrometrics-backend server serves it through "
        "telescope:sync_logs, the app's own log sync, recorded as a remote_sync job named logs.",
        "ingest",
    ),
    "quality_measure_frames": ToolDecision(
        "keep",
        "Built 2026-10-03. Statistics on raw frames. Nothing is saved. Since 2026-10-31 kind=spectral "
        "measures raw spectrum frames (zero-order position, tilt, width, peaks, saturated patches along "
        "the spectrum, a predicted peak at another exposure, a summary by exposure and pier side); it "
        "replaces quality_check_spectral_frames. Since 2026-11-26 it takes a target only: a folder goes "
        "to quality_measure_folder, and the set-aside preview to processing_predict_stack.",
    ),
    "review_get_live_session_status": ToolDecision(
        "keep",
        "Served 2026-10-03. refresh=true copies the newest Ekos and KStars logs into a local folder, "
        "which is reading from the telescope computer and adds files only. A folder argument is now "
        "checked against the allowed roots. Reports pier side, camera temperature, exposure counts and "
        "dither settings; the guide algorithm is not in the logs and the reply says so.",
        "ingest",
    ),
    "visualization_render_fits": ToolDecision(
        "keep",
        "Built 2026-10-03. Returns a real image of a frame, with a stretch, an optional zoom and a "
        "manual brightness range; 1200 pixels on its longest side unless asked. Since #206 it is "
        "visualization_render; the overlays moved to visualization_plot_image. Saves nothing.",
    ),
    "visualization_stack_sky_picture_files": ToolDecision(
        "drop",
        "Built 2026-11-26 for the Planetarium's Images layer: places a target's picture on the sky as a "
        "grid of sky positions. Since #206 it is visualization_get_sky_picture. Of no use to an AI client.",
    ),
    "visualization_render_newest": ToolDecision(
        "keep",
        "Since #206 the newest FITS file in the frames folder, drawn as a real image (the old "
        "visualization_get_last_captured_image sent base64 text the reply limit cut). Saves nothing.",
    ),
    "visualization_plot_image": ToolDecision(
        "keep",
        "Since #206 the figure kind of visualization_render_fits: one frame or stack at full resolution "
        "with the optional detections and named-stars overlays; the registry sends it as a PNG image. "
        "Saves nothing.",
    ),
    "observatory_mount_poll_external_alignment": ToolDecision(
        "drop",
        "The backend calls it to store the syncs other programs queued. It writes records, and the AI "
        "is read-only.",
        "ingest",
    ),
    "observatory_mount_get_polar_alignment": ToolDecision(
        "drop",
        'The backend shows it. An AI reads the stored runs through review_query(kind="alignment").',
    ),
    "observatory_equipment_apply_configuration": ToolDecision(
        "drop",
        "The backend calls it after the settings page saves. It points the INDI session at a host, so "
        "it is not offered to the AI.",
        "change-data",
    ),
    "review_query_alignment_sessions": ToolDecision(
        "drop",
        "The backend calls it with plate-solve attempts it has already read. The AI reads nights through "
        'review_query(kind="alignment"), which groups them already.',
    ),
    "review_query_alignment_records": ToolDecision(
        "drop",
        "The backend reads the stored attempts for its alignment views and passes in library targets. "
        'The AI reads nights through review_query(kind="alignment").',
    ),
    "review_frame_guiding": ToolDecision(
        "keep",
        "Built 2026-10-03. Cuts the stored guide-log samples to each light frame's exposure window. "
        "Reads only.",
    ),
    "app_status": ToolDecision("keep", "Built 2026-10-03. Reads health, connections, resources and jobs."),
    "app_controls": ToolDecision(
        "keep", "Built 2026-10-03. Navigate only; pause, resume and notify are not offered."
    ),
    "observatory_remote_frame_locations": ToolDecision(
        "keep",
        "Built 2026-10-04. Counts a target's frames at the telescope, on the drive and in the library, and "
        "names the frames that are in one place but not the next. Reads only.",
    ),
    **dict.fromkeys(
        (
            "processing_astrometry_run",
            "processing_photometry_run",
            "processing_spectroscopy_run",
            "processing_spectroscopy_record_variation",
        ),
        ToolDecision(
            "keep",
            "Since #206 one tool per analysis pipeline, replacing processing_analyze (built 2026-11-22 from "
            "gap report #5) and its stages argument. Each starts the Analyze Target button's own analysis "
            "job through analysis:analyze_image with its one stage, so it shows in the job list and the "
            "app's Cancel reaches it; the library runs astrometry (or spectroscopy, before the spectral "
            "variation) first when its result is missing or out of date. Writes the analysis results; raw "
            "frames are never written. camera_id and focal_length_mm analyze the target's stack of that "
            "camera and optic instead of the main stack; an unknown or ambiguous one is refused with the "
            "target's configurations, and another configuration gets only astrometry and photometry.",
            "process",
        ),
    ),
    "processing_cancel": ToolDecision(
        "keep",
        "Built 2026-11-22. Cancels a target's running or waiting stacking, analysis or picture remake "
        "(preview) job as the app's Cancel button does, through processing:cancel or analysis:cancel. "
        "It reads the active jobs through jobs:query to pick the kind (or the job given by job_id) and "
        "refuses when two kinds run and none was named. A cancelled picture remake puts the old pictures "
        "back. Changes app jobs only; nothing is deleted.",
        "process",
    ),
    "processing_enhance": ToolDecision(
        "keep",
        "Built 2026-11-22. Reads or saves the Final Stack's Enhance choice (target, nothing, or an object "
        "in the frame) through processing:list_bright_objects and processing:set_bright_object, as the "
        "app's menu does. Without a choice it lists the saved choice and the objects in the frame, or why "
        "there are none. The choice is used at the next stack or Post Process Target; no picture is made. "
        "Writes only the target's saved choice.",
        "process",
    ),
    "processing_remake_preview": ToolDecision(
        "keep",
        "Built 2026-10-04 from gap report #1. Makes a target's preview picture again from its existing "
        "stack, without restacking. denoise, denoise_strength and star_toning apply to that run only and "
        "never reach the configuration. The old pictures are copied into _previous_preview first and put "
        "back if the run fails. The stack file is never written. Writes only the preview JPEG, the "
        "processed FITS and the target's processed-image pointer. Since 2026-11-21 the "
        "astrometrics-backend server serves it through processing:remake_preview, so the app runs the job. "
        "Since 2026-11-25 camera_id and focal_length_mm remake the target's stack of that camera and optic "
        "(stacks_by_configuration, sent as stack_path) instead of the main stack; an unknown or ambiguous "
        "one is refused with the target's configurations, and the main stack and processed-image pointer "
        "stay as they were.",
        "process",
    ),
    "processing_stack": ToolDecision(
        "keep",
        "Built 2026-10-04 at the user's request: the AI may stack a target the way the app's Stack "
        "button does, choosing imaging or spectral frames, a filter and a file or time range. "
        "A choice that matches no frame is refused before a job starts. It runs the app's own stage: "
        "sets aside bad frames into _excluded (never deletes), keeps one previous stack, and saves the "
        "target. Since 2026-10-05 it is ProcessingPipelines.stack, which also takes the exact frames to "
        "stack. Since 2026-11-21 the astrometrics-backend server serves it through processing:stack, the "
        "Stack button's own job, so it shows in the job list and the app's Cancel reaches it. Since #206 "
        "it is processing_stacking_run, and the job stacks, then makes the picture "
        "(processing.enhancement.run).",
        "process",
    ),
    "target_imaged_field_centers": ToolDecision(
        "keep",
        "Built 2026-10-04. The distinct sky positions imaged, read from FITS headers (about 35 s on this "
        "library), so it runs as a background job.",
    ),
    "target_resolve": ToolDecision(
        "keep",
        "Built 2026-11-27 (#206). Turns a name into a sky position: the library's targets, then its "
        "stars, then SIMBAD. Reads only; a name the library does not know uses the network.",
        tool_class="observe",
    ),
    "target_count_by_camera": ToolDecision(
        "keep",
        "Built 2026-11-27 (#206). The light frames each camera took, and each target's frames per "
        "configured camera. Reads only.",
        tool_class="observe",
    ),
    "target_count_by_night": ToolDecision(
        "keep",
        "Built 2026-11-27 (#206). How many targets have frames from each observing night. Reads only.",
        tool_class="observe",
    ),
    "target_frames_get_newest": ToolDecision(
        "keep", "Built 2026-11-27 (#206). A target's newest light frame. Reads only.", tool_class="observe"
    ),
    "target_query": ToolDecision(
        "keep",
        "Built 2026-10-04. Targets as short rows or one grouped record, the cameras used, or each "
        "target's frames per camera; replaces target_get and target_list.",
    ),
    "planning_query_sources": ToolDecision(
        "keep",
        "Targets and stars near a point from the library, SIMBAD or the online catalogs, brightest stars "
        "first, cut at a limit with the total reported. Replaces planning_find_sources, "
        "planning_get_sources, planning_get_library_star_summaries and planning_get_online_catalog_sources.",
    ),
    "processing_get_stack_identification": ToolDecision(
        "keep",
        "Built 2026-11-20. Reads a stack's saved identification: the named stars and the plate solution "
        "it came from. Writes nothing.",
        tool_class="observe",
    ),
    "processing_identify_stack": ToolDecision(
        "keep",
        "Built 2026-11-20. Plate-solves a target's stack if it has no solution, then names its detected "
        "stars, as a background job. Writes the solution into the stack and its viewer copy and saves the "
        "identification; raw frames are never written. Since 2026-11-21 the astrometrics-backend server "
        "serves it through analysis:identify_stack, the Astrometry button's own analysis job, so it shows "
        "in the job list and the app's Cancel stops it.",
        "process",
    ),
    "processing_stack_summary": ToolDecision(
        "keep",
        "Built 2026-10-04. One short answer for a target's stack: frames stacked and skipped, rejected "
        "fraction, star width against the inputs and each exposure group. Reads the saved summary.",
        tool_class="observe",
    ),
    "star_query": ToolDecision(
        "keep",
        "Built 2026-10-03. Library star lookup. Since #206 it returns a page of star records without their "
        "light curves; the tool's reply is cut to 50 stars and the tool asks for at most 200.",
    ),
    "star_get": ToolDecision("keep", "Reads one star's full record by id. Writes nothing."),
    "visualization_get_star_overlay": ToolDecision(
        "keep",
        "Since #206 the overlay that star_query's detail='overlay' gave. Places a target's catalog stars "
        "in pixels on its stack or a named image and writes nothing.",
        tool_class="observe",
    ),
    "star_count_by_class": ToolDecision(
        "keep",
        "Since #206 the class counts that star_query's detail='class_counts' gave. Counts the stars of each "
        "spectral class from the indexed columns and writes nothing.",
        tool_class="observe",
    ),
    "star_count_by_target": ToolDecision(
        "keep",
        "Since #206 the per-target counts that star_query's detail='target_counts' gave. Counts each "
        "target's stars from the indexed columns and writes nothing.",
        tool_class="observe",
    ),
    "star_get_statistics": ToolDecision(
        "keep",
        "Since #206 the library statistics that star_query's detail='stats' gave. Counts the star records "
        "and writes nothing.",
        tool_class="observe",
    ),
    "calibration_query": ToolDecision(
        "keep",
        "Built 2026-10-05. Calibration counts per kind and setting, narrowed by a capture setting (also "
        "for a plan with no frames yet). Writes nothing.",
    ),
    "calibration_match": ToolDecision(
        "keep",
        "Built 2026-11-27 from calibration_query's target match (#206): groups a target's light frames by "
        "setting with the darks, biases and flats the stacker would use. Writes nothing.",
        tool_class="observe",
    ),
    "calibration_measure_sensor_noise": ToolDecision(
        "keep",
        "Since 2026-11-27 (#206) it only reads: it measures a setting's gain and read noise from the "
        "library's flats and biases and writes nothing, like the other measure_ tools. "
        "calibration_record_sensor_noise is the writer and stays withheld.",
        tool_class="compute",
    ),
    "quality_measure_star_profile": ToolDecision(
        "keep",
        "Built 2026-11-16. Measures one star at a pixel position of a FITS file: profile, Moffat and "
        "Gaussian fits, a cut and the ring around it. Reads the file only; nothing is stored.",
        tool_class="compute",
    ),
    "quality_blocks_measure_sky": ToolDecision(
        "keep",
        "Built 2026-11-26 as a Command Console building block. Measures the empty sky's level and noise "
        "of a FITS file. Reads the file only; nothing is stored.",
        tool_class="compute",
    ),
    "quality_blocks_find_stars": ToolDecision(
        "keep",
        "Built 2026-11-26 as a Command Console building block. Runs the astrometry star finder (default) "
        "or the plain DAOStarFinder on a FITS file. Reads the file only; nothing is stored.",
        tool_class="compute",
    ),
    "quality_blocks_star_widths": ToolDecision(
        "keep",
        "Built 2026-11-26 as a Command Console building block. Measures the star width of each color "
        "channel of a FITS file. Reads the file only; nothing is stored.",
        tool_class="compute",
    ),
    "quality_blocks_measure_sky_gradient": ToolDecision(
        "keep",
        "Built 2026-11-26 as a Command Console building block. Measures the sky's tilt across a FITS file "
        "as the preview does. Reads the file only; nothing is stored.",
        tool_class="compute",
    ),
    "quality_blocks_sky_corners": ToolDecision(
        "keep",
        "Built 2026-11-26 as a Command Console building block. Gives a plate-solved FITS file's corners on "
        "the sky from its header, with RADESYS and EQUINOX; none for an unsolved file. Reads the header "
        "only.",
        tool_class="compute",
    ),
    "quality_blocks_measure_aperture": ToolDecision(
        "keep",
        "Built 2026-11-26 as a Command Console building block. Aperture photometry of given pixel "
        "positions of a FITS file, with flux uncertainties from the camera profile's gain and read noise. "
        "Reads the file only; nothing is stored.",
        tool_class="compute",
    ),
    "quality_map_position": ToolDecision(
        "keep",
        "Built 2026-11-26 from the backend's images:map_position. Follows a pixel of one FITS file to "
        "another through their plate solutions and the stacks' registration records. Reads headers and "
        "records only; nothing is stored.",
        tool_class="compute",
    ),
    "quality_blocks_robust_sigma": ToolDecision(
        "drop",
        "A Command Console building block: a spread of numbers the caller already has. Of no use to an AI "
        "client.",
    ),
    "quality_blocks_sigma_clip": ToolDecision(
        "drop",
        "A Command Console building block: clips numbers the caller already has. Of no use to an AI client.",
    ),
    "quality_measure_detections": ToolDecision(
        "keep",
        "Built 2026-11-16. Lists the star finder's detections in a FITS file. Reads the file only; "
        "nothing is stored.",
        tool_class="compute",
    ),
    "quality_choose_blink_pair": ToolDecision(
        "keep",
        "Built 2026-11-20. Picks a typical frame of a frame's session and one reference star in both, "
        "for the viewer's Blink button. Reads the frames and the stored measurements only; nothing is "
        "stored.",
        tool_class="compute",
    ),
    "quality_measure_stack": ToolDecision(
        "keep",
        "Built 2026-10-05. Measures a stack (star width, rejected share, registration) and compares it with "
        "the previous stack or another stack file. Saves nothing.",
    ),
    "visualization_plot": ToolDecision(
        "keep",
        "Built 2026-10-05. Draws the combined chart of a target; the registry sends it as a PNG image. "
        "Since #206 it is visualization_plot_target, and each other chart has its own tool. Saves nothing.",
    ),
    "visualization_plot_star": ToolDecision(
        "keep", "Since #206 the star chart of visualization_plot: light curve over spectrum. Saves nothing."
    ),
    "visualization_plot_astrometry": ToolDecision(
        "keep", "Since #206 the astrometry chart of visualization_plot. Saves nothing."
    ),
    "visualization_plot_photometry": ToolDecision(
        "keep", "Since #206 the photometry chart of visualization_plot. Saves nothing."
    ),
    "visualization_plot_spectroscopy": ToolDecision(
        "keep", "Since #206 the spectroscopy chart of visualization_plot. Saves nothing."
    ),
    "visualization_plot_focus": ToolDecision(
        "keep", "Since #206 the focus against temperature chart of visualization_plot. Saves nothing."
    ),
    "visualization_plot_asteroids": ToolDecision(
        "keep", "Since #206 the asteroid track chart of visualization_plot. Saves nothing."
    ),
    "planning_query_catalogs": ToolDecision("keep", "Lists the catalog drivers. Reads only."),
    "planning_get_visibility": ToolDecision(
        "keep",
        "Built 2026-10-03. Where objects are now or over a night: altitude, horizon clearance with "
        "caller-given blocked ranges, meridian crossing, Sun and Moon. Reads nothing stored.",
    ),
    "review_get_advisory": ToolDecision(
        "keep",
        "Built 2026-10-06; split 2026-10-31. The target dataset report (quality) or the trust in one star "
        "result, worked out on demand. Saves nothing.",
    ),
    "planning_get_tonight_advice": ToolDecision(
        "keep", "Tonight's ranking of targets, worked out on demand. Saves nothing."
    ),
    "planning_get_plan": ToolDecision("keep", "Built 2026-10-06. Reads the recorded session of one night."),
    "planning_query_plans": ToolDecision("keep", "Lists the recorded sessions. Reads only."),
    "planning_packages_get": ToolDecision(
        "keep", "Since #206 (step 5a). Reads one observation package by id. Reads only.", tool_class="observe"
    ),
    "planning_packages_query": ToolDecision(
        "keep",
        "Since #206 (step 5a) the package list of planning_query_plans. Reads only.",
        tool_class="observe",
    ),
    "planning_packages_query_suggestion_log": ToolDecision(
        "keep",
        "Since #206 (step 5a) the suggestion log of planning_query_plans. Reads only.",
        tool_class="observe",
    ),
    "planning_get_package_for_target": ToolDecision(
        "keep", "Reads the one package that stands for a target. Reads only."
    ),
    "planning_get_depth_goal": ToolDecision(
        "keep",
        "Built 2026-10-27 (#134). Reads a target's depth goal: the chosen level, the depth reached and the "
        "time to reach the level. Saves nothing.",
    ),
    "planning_set_depth_goal": ToolDecision(
        "withhold",
        "Writes the user's chosen depth level for a target to the database. Not a hardware command, but the "
        "user decided an AI may not change configuration.",
        "change-data",
    ),
    "planning_get_deep_catalog_status": ToolDecision(
        "keep", "Built 2026-10-06. Reads how much of the deep-star catalog file is installed."
    ),
    "planning_catalogs_predict_deep_catalog": ToolDecision(
        "keep",
        "Since #206 (step 5a) the size estimate of planning_get_deep_catalog_status: queries the Gaia "
        "archive for a sample of chunks and saves nothing.",
        tool_class="compute",
    ),
    "planning_calculate_panels": ToolDecision("keep", "Works out mosaic panel centers. Saves nothing."),
    "planning_build_deep_star_catalog": ToolDecision(
        "withhold", "Downloads from Gaia for hours and writes the catalog database."
    ),
    "execution_abort_session": ToolDecision("withhold", "Closes a session and records it."),
    "execution_reconcile_session": ToolDecision(
        "withhold", "Writes the calibration counts and the session after a night."
    ),
    # ---- Keep ----
    "review_query": ToolDecision(
        "keep",
        "Built 2026-10-03. Reads the recorded nights and calculates the analyses, in replies under 40,000 "
        "characters. The recorded-data tables are read through the butler, which creates a missing table. "
        "Kind night_quality needs session_id (the night) and returns a typed review.",
    ),
    "jobs_query": ToolDecision(
        "keep",
        "Built 2026-10-03. Lists jobs from the logs database, opened read-only. A job stuck at 'started' "
        "after the app stopped shows as stale. Since #206 the AI gets 20 jobs unless it asks, at most 50, "
        "without their stored numbers; one job, its log, its warnings and a target's lineage have their "
        "own tools.",
    ),
    "jobs_get": ToolDecision(
        "keep", "Since #206 one job's whole record, with its stored result. Reads the logs database only."
    ),
    "jobs_get_log": ToolDecision(
        "keep", "Since #206 the last lines of one job's log, at most 200. Reads the logs database only."
    ),
    "jobs_get_warnings": ToolDecision(
        "keep",
        "Since #206 the warnings one job detected: its failed quality checks, with value, limit and "
        "register entry. Reads the quality database only.",
    ),
    "jobs_get_lineage": ToolDecision(
        "keep", "Since #206 the pipeline runs that produced a target's data. Reads the logs database only."
    ),
    "quality_query": ToolDecision(
        "keep",
        "Built 2026-10-21. Reads the quality measurements that earlier runs stored (the latest for each "
        "subject and metric, or every row). Writes nothing and creates no table.",
    ),
    "quality_capture_settings": ToolDecision(
        "keep",
        "Built 2026-10-24. Reads the camera, telescope, filter, exposure, gain setting, offset and binning "
        "that stored quality measurements point at, by capture setting id. Writes nothing and creates "
        "no table.",
        tool_class="observe",
    ),
    "quality_calibration_identities": ToolDecision(
        "keep",
        "Built 2026-10-25. Reads which flat, bias and dark masters, optic, camera, Siril version and "
        "stacking algorithm version each library stacking run used, by run id or target, so two runs "
        "confounded by a calibration change can be told apart. Writes nothing and creates no table.",
        tool_class="observe",
    ),
    "quality_resolve_rows": ToolDecision(
        "keep",
        "Built 2026-10-25. Turns stored quality row ids into the frame or stack file each row is about, with "
        "a plain reason when a picture cannot be found, and lists the ids no row has. Reads only.",
        tool_class="observe",
    ),
    "quality_blocks_solved_stars": ToolDecision(
        "withhold",
        "A Command Console building block, uncapped. The AI reads a target's named stars through star_query.",
    ),
    "quality_records_get": ToolDecision(
        "keep",
        "Built 2026-11-27 (#206). Reads one stored quality row by its id, even when a newer row replaced "
        "it, so a cited row can be read back. Writes nothing and creates no table.",
        tool_class="observe",
    ),
    "quality_records_get_metric": ToolDecision(
        "keep",
        "Built 2026-11-27 (#206). Reads the whole declaration of one quality metric by name. Fixed text, "
        "nothing is read from disk.",
    ),
    "quality_stacks_record_night_contributions": ToolDecision(
        "withhold",
        "Built 2026-11-27 (#206) from processing_measure_noise kind=night_contributions. Stacks the library "
        "stack's frames without each night into the target's _subsets folder and writes the noise into the "
        "quality database. It takes minutes. The AI is read-only, so it reads the stored numbers with "
        "quality_records_query instead.",
        "change-data",
    ),
    "quality_stacks_record_odd_even_noise": ToolDecision(
        "withhold",
        "Built 2026-11-27 (#206) from processing_measure_noise kind=odd_even_difference. Stacks two halves "
        "of the library stack's frames into the target's _subsets folder and writes the noise of their "
        "difference into the quality database, about one library stack's time. The AI is read-only, so it "
        "reads the stored numbers with quality_records_query instead.",
        "change-data",
    ),
    "quality_frame_statuses": ToolDecision(
        "drop",
        "Built 2026-11-26 for the File Browser's Status column. It takes a target and works out the newest "
        "stack file's time itself; the reply is one row per frame, sized for a table on screen. "
        "quality_set_aside_frames and review_get_star_shape_review serve an AI client.",
    ),
    "quality_set_aside_frames": ToolDecision(
        "keep",
        "Built 2026-10-24. Lists the frames lying in a target's _excluded folders with the kind and "
        "reason, and says whether the reason came from a stored quality row, the folder's manifest file, "
        "or nothing but the folder. Moves nothing and writes nothing.",
        tool_class="observe",
    ),
    "quality_metrics": ToolDecision(
        "keep",
        "Built 2026-10-21. Lists the declared quality metrics (or one, given its name, or those of one "
        "checkpoint). The default detail is a short summary (unit, checkpoint, levels, purpose); "
        "detail=full adds the meaning, which way is better and how it is estimated. Fixed text, "
        "nothing is read from disk.",
    ),
    "observatory_mount_abort_motion": ToolDecision(
        "drop", "Commands the mount, so it is dropped with the other hardware tools."
    ),
    "observatory_mount_cancel_centering": ToolDecision(
        "drop",
        "Stops a centering run by aborting the mount, so it is dropped with the other hardware tools.",
        "safe-stop",
    ),
    "observatory_guiding_stop_loop": ToolDecision(
        "drop",
        "Ends the guide loop through a stop event MCP cannot send. Dropped with the other hardware tools.",
        "safe-stop",
    ),
    "review_get_performance_envelope": ToolDecision(
        "keep", "Slow: reads every session and target twice.", "observe"
    ),
    "review_measure_unguided_mount_error": ToolDecision(
        "keep",
        "Built 2026-11-07. Reads the recorded guide samples and guiding runs and fits the mount's own "
        "error (periodic error, drift, jitter). Nothing is stored or sent. Slow on a long history.",
        "observe",
    ),
    "review_measure_tuned_parameters": ToolDecision(
        "withhold",
        "Built 2026-11-16 (#175). Works out the wayfinding tuned values; with save=True it backs up and "
        "writes the configuration file. The AI does not change settings.",
    ),
    "review_measure_guiding_spectrum": ToolDecision(
        "keep",
        "Built 2026-11-07. The guiding spectrum worked out from the recorded samples, with nights "
        "optionally left out. Unlike observatory_guiding_refit_spectrum it stores nothing.",
        "observe",
    ),
    "review_measure_focus_temperature_coefficient": ToolDecision(
        "keep",
        "Built 2026-11-07. Fits the focus-versus-temperature slope on recorded autofocus runs and "
        "frame headers. Reads only; nothing is stored. Slow when it reads every frame record.",
        "observe",
    ),
    "review_get_sky_rate_prediction": ToolDecision(
        "keep",
        "Built 2026-11-07. Predicts the sky background rate from the sky model fitted to the library's "
        "own frames. Reads only; nothing is stored. The first call fits the model and is slow.",
        "observe",
    ),
    "review_get_star_shape_review": ToolDecision(
        "keep",
        "Built 2026-11-20 (#199). Says in plain words how round a target's stars are, whether the stretch "
        "points one way, and which frames stand out, from the stored star-shape measurements. Reads only; "
        "nothing is measured or stored. Corner widths are reported as not validated (AST-18).",
        "observe",
    ),
    "observatory_mount_get_centering_exposure": ToolDecision(
        "keep",
        "Built 2026-11-08. Works out the plate-solve centering exposure from the library's zero point and "
        "star width, the camera profile, the sky model and the Gaia catalog. Reads only; nothing is "
        "stored and nothing moves. The first call may fit the sky model and be slow.",
        "observe",
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
    "observatory_guiding_query_calibrations": ToolDecision(
        "keep",
        "Lists every saved guider calibration, for all telescope and camera pairings (status reads only the "
        "active one). No device needed.",
        "observe",
    ),
    "observatory_imaging_query_focus_models": ToolDecision(
        "keep",
        "Lists every saved focus model, for all telescope and camera pairings (status reads only the active "
        "one). No device needed.",
        "observe",
    ),
    "observatory_safety_status": ToolDecision(
        "keep",
        "Reads the safety rules, a weather verdict, the enclosure, the delegation policy and the "
        "agreement evidence for one capability. The delegation policy's live row was once written by a UI "
        "test; clean it before relying on it.",
        "observe",
    ),
    "observatory_equipment_get_active": ToolDecision(
        "keep",
        "Reads the active observatory configuration: the telescope, cameras and other equipment in use.",
        "observe",
    ),
    "observatory_configuration_get": ToolDecision(
        "keep", "Reads one stored observatory configuration. Reads only.", "observe"
    ),
    "observatory_configuration_query": ToolDecision(
        "keep", "Lists the stored observatory configurations, past and present. Reads only.", "observe"
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
    "calibration_assess": ToolDecision(
        "keep",
        "Judges the dark, bias or flat sets of the library from the stored numbers. "
        "Flats are measured when asked. Writes nothing.",
    ),
    "calibration_measure_frames": ToolDecision(
        "withhold",
        "Writes the measured numbers of dark and bias frames to the quality store and their "
        "stamps to the calibration index. The AI is read-only.",
        tool_class="compute",
    ),
    "calibration_record_sensor_noise": ToolDecision(
        "withhold",
        "It writes a measured gain and read noise into the camera profile in the configuration file. "
        "The AI is read-only.",
        tool_class="compute",
    ),
    "target_display_turn": ToolDecision(
        "drop",
        "Built 2026-11-26 for the image viewer: the quarter turn that shows a raw frame the same way round "
        "as its stack, for display only. Of no use to an AI client.",
    ),
    "target_read_frame_header": ToolDecision(
        "keep",
        "Reads one FITS header. Since 2026-11-26 (#199) it is the one header reader (target_get_header "
        "is gone); an AI client passes as_cards=true for key, value and comment text.",
        tool_class="observe",
    ),
    "target_select_frames": ToolDecision(
        "drop",
        "Returns whole frame records, which can overflow the reply. Scripts and wayfindinglib use it. "
        "review_frame_guiding already serves an AI client.",
    ),
    "calibration_camera_profile": ToolDecision(
        "keep",
        "Built 2026-11-01. Reads the stored facts about one camera model: aliases, limits and gains.",
        tool_class="observe",
    ),
    "target_add_frames": ToolDecision(
        "withhold", "Moves files into the library from any path. The AI is read-only; wayfindinglib calls it."
    ),
    "calibration_add_frames": ToolDecision(
        "withhold", "Moves files into the calibration library from any path. The AI is read-only."
    ),
    "target_sessions": ToolDecision(
        "keep", "Built 2026-11-01. Groups one target's frames into nights. Reads only.", tool_class="observe"
    ),
    "quality_get_aperture_record": ToolDecision(
        "drop",
        "Reads a file from any path and returns every aperture, which can overflow the reply. "
        "Scripts and wayfindinglib use it.",
    ),
    "processing_predict_stack": ToolDecision(
        "drop",
        "Reads every frame of the target (the cloud and trailing check), which takes minutes, so the app "
        "runs it as the background job processing:check_stack for its Check Frames button. A library "
        "call would hold the tool open. An AI lists the frames already set aside with "
        "quality_set_aside_frames.",
    ),
    "processing_predict_stack_weights": ToolDecision(
        "drop",
        "Takes per-frame lists that only forecast code holds. An AI client has no way to supply them.",
    ),
    "docs_get": ToolDecision("keep", "Reads documentation."),
    "ui_run_tests": ToolDecision("keep", "Developer check."),
    "ui_diagnose_code": ToolDecision("keep", "Developer check."),
    "ui_audit_accessibility": ToolDecision("keep", "Developer check."),
    "ui_build_check": ToolDecision("keep", "Developer check."),
    "typegen_contract_validator": ToolDecision("keep", "Developer check."),
    # ---- Class corrections found by reading the code ----
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
    "observatory_guiding_record_calibration": ToolDecision(
        "withhold",
        "Saves a guider calibration record to the database. It sends nothing to a device, but the delegation "
        "rules trust the record, and the AI is read-only. Read the saved one with "
        "observatory_guiding_status.",
        "change-data",
    ),
    "observatory_imaging_record_focus_model": ToolDecision(
        "withhold",
        "Saves a focus model record to the database. It sends nothing to a device, but the delegation rules "
        "trust the record, and the AI is read-only. Read the saved one with observatory_imaging_status.",
        "change-data",
    ),
    "quality_put_back_set_aside_frames": ToolDecision(
        "withhold",
        "Moves chosen set-aside frames out of a target's _excluded folder back into the target and "
        "rescans it. It moves files, and the AI is read-only, so a person puts frames back in the File "
        "Browser. The AI lists them with quality_set_aside_frames.",
        "change-data",
    ),
    # ---- Records: dropped ----
    # ---- No use, empty, or cannot be called through MCP ----
    # ---- Not wanted by the user ----
    "observatory_guiding_run_loop": ToolDecision(
        "drop",
        "Runs the guide loop until a stop event MCP cannot send is set; the backend runs it on a thread. "
        "Sends guide pulses, which belongs to the dropped control side.",
        "actuate",
    ),
    "observatory_guiding_refit_spectrum": ToolDecision(
        "drop", "Recomputes and stores a result. The user does not want an AI to trigger it."
    ),
    # ---- Writers with no read-only form ----
    **dict.fromkeys(
        (
            "calibration_reindex_frames",
            "star_create",
            "star_update",
            "target_add",
            "target_create",
            "target_update",
            "planning_create_mosaic",
            "planning_create_package",
            "planning_create_session",
            "planning_add_to_queue",
            "planning_reorder_queue",
            "planning_remove_from_queue",
            "planning_update_package",
            "planning_undismiss_package",
            "planning_refresh_suggestions",
        ),
        ToolDecision("withhold", READ_ONLY_NOTE),
    ),
    "processing_asteroids_run": ToolDecision(
        "withhold",
        "Since #206 the asteroid search of processing_process_target, which was withheld: " + READ_ONLY_NOTE,
        "change-data",
    ),
    "quality_record_frame_measurements": ToolDecision(
        "withhold",
        "Measures a target's light frames and writes the numbers into the quality database, and stamps "
        "each measured frame on the target record. It never changes a frame file. The AI is read-only, "
        "so it reads the stored numbers with quality_query instead.",
        "change-data",
    ),
    **dict.fromkeys(
        ("processing_measure_noise",),
        ToolDecision(
            "withhold",
            "Stacks some of a target's frames into the target's _subsets folder (never over the library "
            "stack, its previews or its records) and writes the noise of each result into the quality "
            "database. It costs one stack for each subset, so it takes minutes (kind=odd_even_difference "
            "stacks two halves, about one library stack's time in all). The AI is read-only, so "
            "it reads the stored numbers with quality_query instead.",
            "change-data",
        ),
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
            "observatory_imaging_capture_image",
            "observatory_imaging_focus_move",
            "observatory_imaging_set_filter",
            "observatory_imaging_stop_capture",
            "observatory_mount_manual_move",
            "observatory_mount_park",
            "observatory_mount_set_slew_rate",
            "observatory_mount_set_tracking",
            "observatory_mount_slew",
            "observatory_mount_unpark",
        ),
        ToolDecision("drop", DEVICE_COMMAND_NOTE),
    ),
    # ---- Replaced by a broader tool ----
    "target_get": ToolDecision("drop", "Replaced by target_query."),
    "backend_health_check": ToolDecision("drop", "Replaced by app_status."),
    "ui_navigate_mode": ToolDecision("drop", "Replaced by app_controls."),
}
"""Tool name -> the decision about it. Every served tool has one."""

TOOL_RENAMES: dict[str, str] = {
    # Step 4a of #206: targets and their frames.
    "target_select_frames": "target_frames_query",
    "target_read_frame_header": "target_frames_get_header",
    "target_display_turn": "target_frames_get_rotation",
    "target_sessions": "target_frames_get_nights",
    "target_imaged_field_centers": "target_measure_field_centers",
    "target_add_frames": "target_frames_add",
    "target_reindex_frames": "target_frames_scan",
    "quality_frame_statuses": "target_frames_get_statuses",
    "quality_set_aside_frames": "target_frames_get_set_aside",
    "quality_put_back_set_aside_frames": "target_frames_restore",
    # Step 4c of #206: calibration is a sub-API with a `frames` child.
    "calibration_add_frames": "calibration_frames_add",
    "calibration_reindex_frames": "calibration_frames_scan",
    "calibration_measure_frames": "calibration_frames_record",
    "calibration_camera_profile": "calibration_get_profile",
    # Step 4e of #206: quality has the objects records, frames, stacks,
    # detections and blocks.
    "quality_query": "quality_records_query",
    "quality_capture_settings": "quality_records_get_capture_settings",
    "quality_calibration_identities": "quality_records_get_calibration_identities",
    "quality_resolve_rows": "quality_records_get_paths",
    "quality_metrics": "quality_records_get_metrics",
    "quality_measure_frames": "quality_frames_measure",
    "quality_record_frame_measurements": "quality_frames_record",
    "quality_choose_blink_pair": "quality_frames_get_blink_partner",
    "quality_map_position": "quality_frames_map_positions",
    "quality_measure_stack": "quality_stacks_measure",
    "quality_get_aperture_record": "quality_stacks_get_aperture_record",
    "processing_measure_noise": "quality_stacks_record_noise_growth",
    "quality_measure_star_profile": "quality_detections_measure_profile",
    "quality_measure_detections": "quality_detections_measure_detections",
    "quality_blocks_star_widths": "quality_detections_measure_widths",
    "quality_blocks_find_stars": "quality_blocks_measure_stars",
    "quality_blocks_sky_corners": "quality_blocks_measure_sky_corners",
    "quality_blocks_robust_sigma": "quality_blocks_measure_robust_sigma",
    "quality_blocks_sigma_clip": "quality_blocks_measure_clipped_values",
    # Step 4d of #206: one object per pipeline under `processing`.
    "processing_stack": "processing_stacking_run",
    "processing_predict_stack": "processing_stacking_predict",
    "processing_predict_stack_weights": "processing_stacking_predict_weights",
    "processing_stack_summary": "processing_stacking_get_summary",
    "processing_find_stack_configuration": "processing_stacking_get_stack",
    "processing_remake_preview": "processing_enhancement_run",
    "processing_list_preview_bright_objects": "processing_enhancement_get_bright_objects",
    "processing_plate_solve": "processing_astrometry_solve",
    "processing_identify_stack": "processing_astrometry_identify",
    "processing_get_stack_identification": "processing_astrometry_get_identification",
    "quality_blocks_solved_stars": "processing_astrometry_get_named_stars",
    # Step 4f of #206: visualization.
    "visualization_render_fits": "visualization_render",
    "visualization_plot": "visualization_plot_target",
    "visualization_stack_sky_picture_files": "visualization_get_sky_picture",
    # Step 5a of #206: planning has the objects sessions, packages, sky and
    # catalogs. The queue edits merge into sessions_update and the package
    # edits into packages_update; lookup_coordinates and
    # get_calibration_advice went to the core (targets.resolve and
    # calibration.query).
    "planning_get_plan": "planning_sessions_get",
    "planning_query_plans": "planning_sessions_query",
    "planning_create_session": "planning_sessions_create",
    "planning_add_to_queue": "planning_sessions_add",
    "planning_remove_from_queue": "planning_sessions_remove",
    "planning_reorder_queue": "planning_sessions_update",
    "planning_get_package_for_target": "planning_packages_get_for_target",
    "planning_create_package": "planning_packages_create",
    "planning_update_package": "planning_packages_update",
    "planning_undismiss_package": "planning_packages_restore",
    "planning_refresh_suggestions": "planning_packages_record_suggestions",
    "planning_get_depth_goal": "planning_packages_get_depth_goal",
    "planning_set_depth_goal": "planning_packages_set_depth_goal",
    "planning_create_mosaic": "planning_packages_create_mosaic",
    "planning_get_visibility": "planning_sky_get_visibility",
    "planning_get_tonight_advice": "planning_sky_get_tonight_advice",
    "planning_get_constellation_lines": "planning_sky_get_constellation_lines",
    "planning_calculate_panels": "planning_sky_get_panels",
    "planning_query_catalogs": "planning_catalogs_query",
    "planning_query_sources": "planning_catalogs_query_sources",
    "planning_get_deep_catalog_status": "planning_catalogs_get_deep_status",
    "planning_build_deep_star_catalog": "planning_catalogs_record_deep_catalog",
}
"""Old tool name -> new tool name, for a tool whose public method was
renamed. The decision recorded under the old name in ``DECISIONS`` applies
to the new name. Empty when every row uses the current name."""


def decision_for(name: str) -> ToolDecision | None:
    """Find the decision about a tool, following ``TOOL_RENAMES``.

    Parameters
    ----------
    name : `str`
        The tool's current name.

    Returns
    -------
    decision : `ToolDecision` or `None`
        The row under the name itself, else the row under the old name a
        rename maps to it, else `None`.
    """
    if name in DECISIONS:
        return DECISIONS[name]
    old_name = next((old for old, new in TOOL_RENAMES.items() if new == name), None)
    return DECISIONS.get(old_name) if old_name is not None else None


CATEGORY_RULES = (
    (
        re.compile(
            r"^(electron_run_python|backend_call_rpc|terminal_.+|backend_health_check|docs_get|app_status|app_controls"
            r"|ui_(navigate_mode|pause_pipelines|resume_pipelines|editor_sync"
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
    (re.compile(r"^(processing_|diagnostics_|quality_|visualization_)"), "image-processing"),
    (re.compile(r"^jobs_"), "jobs-history"),
    (re.compile(r"^(planning_|execution_)"), "planning-sessions"),
    (re.compile(r"^review_save_"), "observatory-config"),
    (re.compile(r"^review_"), "observatory-status"),
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
        disposition or class, or a tool that is not offered with no note. A
        decision under an old name of a served tool (``TOOL_RENAMES``) is
        not a problem; a rename to a tool no server serves, or a rename
        whose new name has its own row, is.
    """
    problems = [f"{name!r} has no decision" for name in sorted(served_names) if decision_for(name) is None]
    for old_name, new_name in TOOL_RENAMES.items():
        if new_name not in served_names:
            problems.append(f"rename {old_name!r} -> {new_name!r}, which no server serves")
        if old_name in DECISIONS and new_name in DECISIONS:
            problems.append(f"rename {old_name!r} -> {new_name!r}, but both names have a decision")
    problems += [
        f"interim block for {name!r}, which no server serves"
        for name in INTERIM_BLOCKS
        if name not in served_names
    ]
    for name, decision in DECISIONS.items():
        if name not in served_names and TOOL_RENAMES.get(name) not in served_names:
            problems.append(f"decision for {name!r}, which no server serves")
        if decision.disposition not in DISPOSITIONS or decision.disposition == "undecided":
            problems.append(f"{name}: unknown disposition {decision.disposition!r}")
        if decision.disposition != "keep" and not decision.note:
            problems.append(f"{name}: a tool that is not offered needs a note that says why")
        if decision.tool_class is not None and decision.tool_class not in valid_classes:
            problems.append(f"{name}: unknown class {decision.tool_class!r}")
    return problems
