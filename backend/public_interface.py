"""Purpose: Declare everything the backend lets a client reach.

Description: This module is the one list of the backend's public interface.
It has two parts:

* `RPC_METHODS`, every method the ``/api/rpc`` route answers. The app
  calls them with ``callBackend(method, params)``. The MCP backend server
  and the Electron shell call some of them too.
* `ROUTES`, the few HTTP and WebSocket routes for what RPC cannot carry:
  the event and terminal WebSockets, the image and FITS file folders, the
  Matplotlib figure pages, and the start-up routes.

The RPC router refuses to register a method that is not in `RPC_METHODS`,
and a test checks that every declared method has a handler. Another test
checks that the routes the app mounts are exactly `ROUTES`. The UI's
generated types (``ui/common/types/backendTypes.ts``) list the same method
names and route paths, so the app cannot call anything else.
"""

from dataclasses import dataclass
from typing import Literal

#: Every RPC method the backend serves, grouped by the part of the app that
#: uses it.
RPC_METHODS: tuple[str, ...] = (
    # System, settings and notifications
    "system:health",
    "system:frontend_log",
    "system:completions",
    "system:get_config",
    "system:save_config",
    "system:introspection",
    "system:cameras",
    "system:filters",
    "system:pulse",
    "system:notifications",
    "system:save",
    # Python terminal and its documentation
    "terminal:execute",
    "terminal:get_workspace",
    "terminal:completions",
    "terminal:list_recipes",
    "terminal:get_recipe",
    "terminal:list_scripts",
    "terminal:read_script",
    "terminal:save_script",
    "terminal:reset_workspace",
    "docs:list_topics",
    "docs:search_topics",
    "docs:get_topic",
    # Remote control of the desktop app's screens
    "ui:editor_get",
    "ui:editor_set",
    "ui:navigate",
    "ui:inspect_variable",
    # Hand-off of the workspace to a paired phone
    "handoff:get_state",
    "handoff:update_state",
    "handoff:beam",
    "handoff:list_devices",
    "handoff:send_alert",
    "handoff:share_file",
    # Guiding
    "guiding:status",
    "guiding:start",
    "guiding:stop",
    "guiding:capture_frame",
    # Telescope mount, focuser, filter wheel and raw INDI properties
    "telescope:connect",
    "telescope:abort_motion",
    "telescope:apply_promotion_decision",
    "telescope:focus_move",
    "telescope:get_focuser_position",
    "telescope:manual_move",
    "telescope:park",
    "telescope:set_filter",
    "telescope:set_slew_rate",
    "telescope:set_tracking",
    "telescope:slew_coordinates",
    "telescope:slew_target",
    "telescope:status",
    "telescope:sync",
    "telescope:is_syncing",
    "telescope:unpark",
    "telescope:indi_devices",
    "telescope:indi_properties",
    "telescope:set_indi_property",
    # Alignment and tracking history
    "telescope:alignment_start",
    "telescope:alignment_stop",
    "telescope:list_alignment_sessions",
    "telescope:get_session_alignment",
    "telescope:get_cumulative_tracking_data",
    "telescope:sync_logs",
    "telescope:get_pointing_model",
    "telescope:get_performance_envelope",
    "telescope:get_guiding_spectrum",
    # Equipment set-up and control mode
    "observatory:list_cameras",
    "observatory:get_equipment_configuration",
    "observatory:set_active_camera",
    "observatory:enter_monitoring_mode",
    "observatory:enter_controller_mode",
    # Ingestion of new frames
    "ingestion:start",
    "ingestion:status",
    "ingestion:scan",
    "ingestion:stats",
    "ingestion:list_files",
    "ingestion:reindex",
    # Stacking and the job list
    "processing:stack",
    "processing:siril_open",
    "processing:cancel",
    "processing:status",
    "processing:list_jobs",
    "processing:active_jobs",
    "processing:get_job",
    "processing:delete_job",
    "processing:jobs_for_target",
    "processing:job_log_tail",
    # Photometry and spectroscopy analysis
    "analysis:analyze_image",
    "analysis:get_results",
    "analysis:cancel",
    # Target library
    "target:list",
    "target:get",
    "target:get_targets",
    "target:create",
    "target:update",
    "target:delete",
    "target:add_data",
    "target:send_to_phone",
    "target:refresh",
    "target:get_files",
    "target:get_camera_index",
    "target:get_frames",
    "target:get_frames_grouped",
    "target:get_frame_header",
    # Star library
    "astronomy:list",
    "astronomy:count",
    "astronomy:target_data_availability",
    "astronomy:spectral_class_summary",
    "astronomy:stars_by_spectral_class",
    "astronomy:get",
    "astronomy:save",
    "astronomy:delete",
    "astronomy:analyze_periodicity",
    "astronomy:get_stellar_objects",
    "astronomy:get_overlay_stars",
    "astronomy:get_status",
    "astronomy:visible",
    # Planetarium
    "planetarium:get_sources",
    "planetarium:get_targets",
    "planetarium:get_visibility",
    "planetarium:get_observer_location",
    "planetarium:get_catalog_sources",
    "planetarium:list_catalog_drivers",
    "planetarium:get_deep_catalog_status",
    "planetarium:get_constellation_lines",
    # Camera capture
    "imaging:capture",
    "imaging:get_active_jobs",
    # Images and FITS files
    "images:get_target_frame",
    "images:get_light_frame_data",
    "images:convert_fits",
    "images:get_fits_header",
    "images:delete",
    "images:last",
    # Calibration frames
    "calibration:get_stats",
    # Mosaics
    "mosaic:preview",
    "mosaic:create",
    # Observing sessions
    "execution:list_sessions",
    "execution:get_session",
    "execution:abort_session",
    "execution:reconcile_session",
    "execution:record_divergence",
    # Sequencer queue
    "sequencer:get_queue",
    "sequencer:create_plan",
    "sequencer:add",
    "sequencer:remove",
    "sequencer:reorder",
    "sequencer:begin",
    "sequencer:modify",
)

#: The kinds of route: a plain HTTP route, a WebSocket, or a folder of files.
RouteKind = Literal["http", "websocket", "files"]


@dataclass(frozen=True)
class Route:
    """One HTTP route, WebSocket or file folder the backend serves.

    Attributes
    ----------
    name : `str`
        A short camelCase name. The UI's generated route constants use it.
    path : `str`
        The path as FastAPI writes it, with ``{name}`` for a path part.
    kind : `RouteKind`
        ``"http"``, ``"websocket"`` or ``"files"``.
    purpose : `str`
        What the route is for, in one sentence.
    optional : `bool`
        `True` when the app mounts the route only if its folder exists or
        differs from another folder already mounted.
    """

    name: str
    path: str
    kind: RouteKind
    purpose: str
    optional: bool = False


#: Every route the app mounts.
ROUTES: tuple[Route, ...] = (
    Route("rpc", "/api/rpc", "http", "Answers every RPC method in RPC_METHODS."),
    Route("ready", "/api/ready", "http", "Says whether start-up warm-up has finished."),
    Route("sessionToken", "/api/session-token", "http", "Gives the WebSocket token to the app."),
    Route("pairingInfo", "/api/pairing-info", "http", "Tells a companion phone how to connect."),
    Route("root", "/", "http", "Says the backend is running."),
    Route("eventsSocket", "/ws/events", "websocket", "Streams live events and telemetry to the app."),
    Route("terminalSocket", "/ws/terminal", "websocket", "Runs the plain Python terminal."),
    Route("figureSocket", "/ws/figure/{figure_id}", "websocket", "Drives one interactive Matplotlib figure."),
    Route("figureScript", "/figure/mpl.js", "http", "Serves the Matplotlib figure script."),
    Route("figurePage", "/figure/{figure_id}", "http", "Serves the page of one interactive figure."),
    Route("figureDownload", "/figure/{figure_id}/download.{fmt}", "http", "Downloads a figure as an image."),
    Route(
        "staticFrames",
        "/static/frames",
        "files",
        "Serves raw frames, then stacks, by their path in the frames folder.",
        optional=True,
    ),
    Route("staticLibrary", "/static", "files", "Serves files by their path in the library folder."),
    Route("figureStatic", "/mpl_static", "files", "Serves Matplotlib's figure styles.", optional=True),
    Route("figureImages", "/_images", "files", "Serves Matplotlib's toolbar icons.", optional=True),
    Route(
        "figurePageImages",
        "/figure/_images",
        "files",
        "Serves Matplotlib's toolbar icons to a figure page.",
        optional=True,
    ),
)


def route_path(name: str) -> str:
    """Give the path of a declared route.

    Parameters
    ----------
    name : `str`
        The route's `Route.name`.

    Returns
    -------
    path : `str`
        The route's path.

    Raises
    ------
    KeyError
        If no route has that name.
    """
    for route in ROUTES:
        if route.name == name:
            return route.path
    raise KeyError(name)
