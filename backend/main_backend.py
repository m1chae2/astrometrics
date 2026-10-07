"""Main entry point for the high-level interface FastAPI backend server.

Configures global logging, suppresses noisy libraries, registers
middleware, initializes the dependency injection container, and
defines API/WebSocket routes.
"""

import html
import logging
import os
import socket
import threading
import time
import warnings
from collections.abc import AsyncGenerator
from typing import Any

import uvicorn

# Suppress noisy Astropy FITS warnings
# REQ: SYS-1.4: Maintain clean logs
from astropy.utils.exceptions import AstropyDeprecationWarning, AstropyWarning
from astropy.wcs import FITSFixedWarning
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from astrometricslib import configure_logging
from backend.routers import rpc_router

warnings.filterwarnings("ignore", category=FITSFixedWarning)
warnings.filterwarnings("ignore", category=AstropyDeprecationWarning)
warnings.filterwarnings("ignore", category=AstropyWarning, message=".*extra padding.*")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from astrometricslib import (
    AstrometricsError,
    ConfigurationError,
    ErrorInfo,
    StorageNotMountedError,
    close_interrupted_jobs,
    configure_offline_iers,
    get_configuration,
    new_request_id,
    require_mounted_storage,
    to_error_info,
    warm_earth_orientation_data,
)
from backend.container import container

# Configure Logging
# REQ: SYS-1.4: Maintain clean logs
app_configuration = get_configuration()
configure_logging(
    "backend", level=getattr(logging, os.environ.get("ASTROMETRICS_LOG_LEVEL", "INFO").upper(), logging.INFO)
)

logger = logging.getLogger(__name__)


# Astropy must use its bundled Earth-rotation (IERS) table and never download
# one. Importing astrometricslib already does this; the call states it here
# because the backend depends on it.
configure_offline_iers()

# Register Socket Logging Handler
# Register DB Log Handler (general/unscoped logs; job-scoped loggers attach
# their own job_id-bound instance directly and do not propagate here since
# they set propagate = False).
# REQ: SYS-1.4: Record astrometricslib/wayfindinglib/backend logs to
# astrometrics_log.db
#
# DbLogHandler.emit() opens its own SQLite connection and commits per record,
# which is too slow to run inline on whatever thread emitted the log (a single
# request that logs thousands of warnings would block for tens of seconds).
# Route it through a QueueListener so the DB write happens on a dedicated
# background thread instead of the request thread.
import queue
from contextlib import asynccontextmanager
from logging.handlers import QueueHandler, QueueListener

from astrometricslib import DbLogHandler
from backend.services.infrastructure import session_auth
from backend.services.infrastructure.fallback_static_files import FallbackStaticFiles
from backend.services.infrastructure.socket_manager import SocketLoggingHandler

# The handlers made by `_attach_log_handlers`, kept so `_detach_log_handlers`
# can remove them.
_log_handlers: dict[str, Any] = {}


def _attach_log_handlers() -> None:
    """Send log records to the UI and to the log database.

    Needs the container's socket manager and job repository, so it runs
    after `container.init_resources()`.
    """
    socket_handler = SocketLoggingHandler(container.socket_manager)
    socket_handler.setLevel(logging.INFO)  # Only send INFO and above to UI to avoid flood
    socket_handler.setFormatter(logging.Formatter("%(message)s"))

    db_log_handler = DbLogHandler(container.job_repository)
    db_log_handler.setLevel(logging.INFO)
    db_log_queue: queue.Queue = queue.Queue()
    db_log_queue_handler = QueueHandler(db_log_queue)
    db_log_queue_handler.setLevel(logging.INFO)
    db_log_listener = QueueListener(db_log_queue, db_log_handler)

    root_logger = logging.getLogger()
    root_logger.addHandler(socket_handler)
    root_logger.addHandler(db_log_queue_handler)
    db_log_listener.start()
    _log_handlers.update(
        socket_handler=socket_handler,
        db_log_queue_handler=db_log_queue_handler,
        db_log_listener=db_log_listener,
    )


def _detach_log_handlers() -> None:
    """Remove the handlers added by `_attach_log_handlers`."""
    root_logger = logging.getLogger()
    for name in ("socket_handler", "db_log_queue_handler"):
        handler = _log_handlers.pop(name, None)
        if handler is not None:
            root_logger.removeHandler(handler)
    listener = _log_handlers.pop("db_log_listener", None)
    if listener is not None:
        listener.stop()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Start the services when the server starts and stop them after.

    Importing this module builds nothing. The services, the log handlers and
    the background tasks all start here, when uvicorn starts the app. This
    runs on the event loop thread before the server accepts requests, so the
    socket manager and the astrometrics service capture the running loop
    when they are built.

    Parameters
    ----------
    app : `FastAPI`
        The application being started.

    Yields
    ------
    None
        Control, while the server runs.
    """
    container.init_resources()
    _attach_log_handlers()
    # Close the jobs an earlier run left open and put back any stack it left
    # parked in a staging folder. Another program's running jobs are left as
    # they are.
    app_configuration.watch_for_changes()
    close_interrupted_jobs(app_configuration)
    try:
        require_mounted_storage(app_configuration.get_frames_path(), app_configuration)
    except StorageNotMountedError as not_mounted:
        # Not fatal: images from the stacks folder still load, and the frames
        # folder is served as soon as the drive is mounted.
        logger.warning("%s", not_mounted)
    app.state.telemetry_task = asyncio.create_task(periodic_telemetry_loop())
    app.state.sky_warmup_task = asyncio.create_task(asyncio.to_thread(_warm_sky_catalog))
    try:
        yield
    finally:
        app.state.telemetry_task.cancel()
        _detach_log_handlers()
        container.shutdown_resources()


# Initialize FastAPI App
app = FastAPI(title="Astrometrics API", version="2.0.0", lifespan=lifespan)

# Configure CORS
origins = [
    "http://localhost:5173",  # Vite Dev Server
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
    "http://localhost:8000",  # Development Server
    "http://127.0.0.1:8000",
    "http://localhost:5000",  # Backend Server
    "http://127.0.0.1:5000",
    "http://localhost:3000",
    "app://.",  # Electron
]

lan_origin_regex = (
    r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$|"
    r"^capacitor://localhost$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=lan_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Services
# scripting_service is now in container


#: HTTP status for each error code on the plain REST routes. The RPC route
#: always answers with status 200 and puts the error code in the reply.
HTTP_STATUS_BY_CODE: dict[str, int] = {
    "invalid_argument": 400,
    "permission_denied": 403,
    "not_found": 404,
    "conflict": 409,
    "processing": 422,
    "internal": 500,
    "hardware": 502,
    "external_service": 502,
    "configuration": 503,
    "storage": 503,
}


def _error_response(request: Request, info: ErrorInfo) -> JSONResponse:
    """Build the JSON reply for a failed REST request.

    Parameters
    ----------
    request : `~fastapi.Request`
        The request that failed.
    info : `ErrorInfo`
        The error to send.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        ``{"error": ErrorInfo}`` with the HTTP status for the error's
        code, and CORS headers set by hand for allowed origins.
    """
    response = JSONResponse(
        status_code=HTTP_STATUS_BY_CODE.get(info.code, 500),
        content={"error": info.model_dump(by_alias=True)},
    )
    # Set CORS headers by hand so the frontend can still read the error.
    origin = request.headers.get("origin")
    if origin in origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.exception_handler(AstrometricsError)
# ruff: ignore[unused-async] -- required async signature for FastAPI's
# exception_handler decorator, which awaits this handler.
async def expected_error_handler(request: Request, exc: AstrometricsError) -> JSONResponse:
    """Turn an expected error from a REST route into an error reply.

    Parameters
    ----------
    request : `~fastapi.Request`
        The incoming request during which the error was raised.
    exc : `AstrometricsError`
        The error that was raised.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        The error reply built by `_error_response`.
    """
    info = to_error_info(exc, new_request_id())
    logger.warning("%s %s failed (%s): %s", request.method, request.url.path, info.code, info.message)
    return _error_response(request, info)


@app.exception_handler(Exception)
# ruff: ignore[unused-async] -- required async signature for FastAPI's
# exception_handler decorator, which awaits this handler.
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn an unexpected exception into a generic 500 error reply.

    The reply carries a request id but no internal detail. The log keeps
    the traceback under the same id.

    Parameters
    ----------
    request : `~fastapi.Request`
        The incoming request during which the exception was raised.
    exc : `Exception`
        The unhandled exception that was raised.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        The ``internal`` error reply built by `_error_response`.
    """
    info = to_error_info(exc, new_request_id())
    logger.error(
        "%s %s raised an unexpected error (request %s)",
        request.method,
        request.url.path,
        info.request_id,
        exc_info=exc,
    )
    return _error_response(request, info)


# Import Routers

# Mount RPC Router and Static Files serving
app.include_router(rpc_router.router)

# Mount static file servers for both the frames directory (external
# image library) and the library index directory (metadata/DB files).
# The frames path is where actual image data lives (_Astrophotography,
# lights, darks, etc.) while the library path holds index/metadata
# files. Frames is mounted first as a separate prefix so both
# locations are reachable.
_frames_path = app_configuration.get_frames_path()
_stacks_path = app_configuration.get_stacks_path()
_library_path = app_configuration.get_library_path()

if _frames_path != _library_path:
    # The pipeline's output can live on another disk (the stacks path), with
    # the same lights/<target>/ layout. Files not found among the frames are
    # looked up there. The mount is made even if a folder is missing now (a
    # network drive not mounted yet): the server answers 404 until the folder
    # appears, then serves it, with no restart.
    app.mount(
        "/static/frames",
        FallbackStaticFiles(
            directory=str(_frames_path),
            fallback_directory=str(_stacks_path) if _stacks_path != _frames_path else None,
        ),
        name="static_frames",
    )
app.mount("/static", StaticFiles(directory=str(_library_path)), name="static")

# Mount Matplotlib WebAgg static assets and toolbar images for
# interactive figures
try:
    import matplotlib
    import matplotlib.backends.backend_webagg_core as webagg_core

    webagg_core.FigureManagerWebAgg._toolbar2_class = webagg_core.NavigationToolbar2WebAgg

    _mpl_static_path = webagg_core.FigureManagerWebAgg.get_static_file_path()
    _mpl_img_path = os.path.join(matplotlib.get_data_path(), "images")
    if os.path.isdir(_mpl_static_path):
        app.mount("/mpl_static", StaticFiles(directory=_mpl_static_path), name="mpl_static")
    if os.path.isdir(_mpl_img_path):
        app.mount("/_images", StaticFiles(directory=_mpl_img_path), name="mpl_images")
        app.mount("/figure/_images", StaticFiles(directory=_mpl_img_path), name="mpl_figure_images")
except (ImportError, OSError, RuntimeError) as e:
    logger.warning("Failed to mount Matplotlib WebAgg static assets: %s", e)


async def authorize_websocket(websocket: WebSocket) -> bool:
    """Enforce the origin allowlist and session token on a handshake.

    Closes the connection before accepting it when either check fails,
    so an unauthorized client never reaches the endpoint body. See
    `backend.services.infrastructure.session_auth` for why CORS alone
    does not cover WebSocket endpoints.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The connection being negotiated.

    Returns
    -------
    is_authorized : `bool`
        `True` if the caller may proceed to `websocket.accept()`.
    """
    origin = websocket.headers.get("origin")
    if not session_auth.is_origin_allowed(origin, origins):
        logger.warning("Rejected WebSocket handshake from disallowed origin: %r", origin)
        await websocket.close(code=4403)
        return False
    if not session_auth.is_token_valid(websocket.query_params.get("token")):
        logger.warning("Rejected WebSocket handshake with missing/invalid token (origin %r)", origin)
        await websocket.close(code=4401)
        return False
    return True


@app.get("/api/session-token")
async def session_token():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Hand the session token to a same-origin UI client.

    Protected by the CORS allowlist above: a browser will not expose
    this response body to a page whose origin is not listed, which is
    what keeps a hostile site from reading the token and connecting to
    the WebSocket endpoints itself.

    Returns
    -------
    token : `dict`
        A ``{"token": str}`` payload for the UI to attach to its
        WebSocket URLs.
    """
    return {"token": session_auth.SESSION_TOKEN}


def _detect_lan_ip() -> str:
    """Detect the active LAN IPv4 address for remote mobile companion clients.

    Returns
    -------
    lan_ip : `str`
        The local IPv4 address, or '127.0.0.1' if none can be resolved.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@app.get("/api/pairing-info")
async def pairing_info(request: Request) -> dict[str, Any]:
    """Provide connection and authorization metadata for companion clients.

    Facilitates zero-configuration pairing with mobile companion apps
    or remote browser clients over the local network.

    Parameters
    ----------
    request : `~fastapi.Request`
        The incoming HTTP request.

    Returns
    -------
    info : `dict`
        Server metadata, host/port, endpoints, and the active session token.
    """
    host_header = request.headers.get("host", "")
    host = host_header.split(":")[0] if host_header else _detect_lan_ip()
    port = request.url.port or 5000

    return {
        "app": "Astrometrics",
        "version": "0.2.0",
        "host": host,
        "port": port,
        "lan_ip": _detect_lan_ip(),
        "session_token": session_auth.SESSION_TOKEN,
        "endpoints": {
            "rpc": f"http://{host}:{port}/api/action",
            "ws_events": f"ws://{host}:{port}/ws/events?token={session_auth.SESSION_TOKEN}",
            "ws_terminal": f"ws://{host}:{port}/ws/terminal?token={session_auth.SESSION_TOKEN}",
        },
    }


class HandoffStateUpdate(BaseModel):
    """Payload for updating the active workspace handoff state."""

    active_mode: str | None = Field(None, description="Active UI mode")
    selected_target: str | None = Field(None, description="Selected target designation")
    coordinates: dict[str, Any] | None = Field(None, description="RA/Dec coordinates and FOV")
    telemetry: dict[str, Any] | None = Field(None, description="Mount or capture telemetry")
    origin_device: str = Field("desktop", description="Device identifier")


@app.get("/api/handoff/state")
async def get_handoff_state() -> dict[str, Any]:
    """Retrieve the workspace handoff state used for cross-device continuity.

    Returns
    -------
    state : `dict`
        Active workspace mode, selected target, coordinates, and telemetry.
    """
    if container.handoff_service:
        return container.handoff_service.get_state()
    return {}


@app.post("/api/handoff/state")
async def update_handoff_state(payload: HandoffStateUpdate) -> dict[str, Any]:
    """Update the workspace handoff state and broadcast it to clients.

    Parameters
    ----------
    payload : `HandoffStateUpdate`
        State fields to update.

    Returns
    -------
    state : `dict`
        Updated active workspace state.
    """
    if container.handoff_service:
        return container.handoff_service.update_state(
            active_mode=payload.active_mode,
            selected_target=payload.selected_target,
            coordinates=payload.coordinates,
            telemetry=payload.telemetry,
            origin_device=payload.origin_device,
        )
    return {}


#: Message for a handoff call made while the handoff service is not running.
_HANDOFF_UNAVAILABLE = "The handoff service is not running, so no phone can be reached."


@app.post("/api/handoff/beam")
async def beam_to_device(target: str | None = None, mode: str | None = None) -> dict[str, Any]:
    """Beam the current target or view to a paired phone via GSConnect.

    Parameters
    ----------
    target : `str`, optional
        Target designation to open on the mobile device.
    mode : `str`, optional
        Workspace view mode to display on the mobile device.

    Returns
    -------
    result : `dict`
        Status of the beam dispatch attempt.

    Raises
    ------
    ConfigurationError
        If the handoff service is not running.
    """
    if not container.handoff_service:
        raise ConfigurationError(_HANDOFF_UNAVAILABLE)
    return container.handoff_service.beam_to_device(target=target, mode=mode)


@app.get("/api/handoff/devices")
async def list_companion_devices() -> list[dict[str, Any]]:
    """Enumerate paired companion devices via GSConnect or KDE Connect.

    Returns
    -------
    devices : `list` of `dict`
        List of paired devices with identifiers and reachable states.
    """
    if container.handoff_service:
        return container.handoff_service.list_paired_devices()
    return []


class DeviceAlertRequest(BaseModel):
    """Request schema for dispatching an alert to companion hardware."""

    title: str = Field(..., description="Short alert title or category")
    message: str = Field(..., description="Descriptive alert text")
    priority: str = Field("normal", description="Severity level")
    ring_device: bool = Field(False, description="Trigger audible ring alarm")
    device_id: str | None = Field(None, description="Optional target device ID")


@app.post("/api/handoff/alert")
async def dispatch_device_alert(payload: DeviceAlertRequest) -> dict[str, Any]:
    """Dispatch an alert to companion devices and WebSocket subscribers.

    Parameters
    ----------
    payload : `DeviceAlertRequest`
        Alert details, severity, and optional ring flag.

    Returns
    -------
    result : `dict`
        Status and delivered communication channels.

    Raises
    ------
    ConfigurationError
        If the handoff service is not running.
    """
    if not container.handoff_service:
        raise ConfigurationError(_HANDOFF_UNAVAILABLE)
    return container.handoff_service.send_device_alert(
        title=payload.title,
        message=payload.message,
        priority=payload.priority,
        ring_device=payload.ring_device,
        device_id=payload.device_id,
    )


class ShareFileRequest(BaseModel):
    """Request schema for sharing a file to a companion device."""

    file_path: str = Field(..., description="Absolute path of file to share")
    device_id: str | None = Field(None, description="Optional target device ID")


@app.post("/api/handoff/share-file")
async def share_file_to_device(payload: ShareFileRequest) -> dict[str, Any]:
    """Share an image or data file to a companion device via GSConnect.

    Parameters
    ----------
    payload : `ShareFileRequest`
        Path of file and optional target device ID.

    Returns
    -------
    result : `dict`
        Status of the file beam attempt.

    Raises
    ------
    ConfigurationError
        If the handoff service is not running.
    """
    if not container.handoff_service:
        raise ConfigurationError(_HANDOFF_UNAVAILABLE)
    return container.handoff_service.share_file_to_device(
        file_path=payload.file_path,
        device_id=payload.device_id,
    )


@app.get("/api/ready")
async def readiness():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Report whether startup warm-up has finished.

    Distinct from the liveness route below: the backend accepts requests
    as soon as it is listening, but the first Planetarium load stays slow
    until the star catalog has been loaded into memory.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        Status 200 with ``{"ready": true}`` once warm-up has finished,
        otherwise status 503 with ``{"ready": false}``.
    """
    if sky_catalog_warmup_finished.is_set():
        return JSONResponse(status_code=200, content={"ready": True})
    return JSONResponse(status_code=503, content={"ready": False})


@app.get("/")
async def root():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Return a simple liveness message for the backend root route.

    Returns
    -------
    message : `dict`
        A ``{"message": str}`` payload confirming the backend is up.
    """
    return {"message": "Astrometrics Backend Running"}


@app.websocket("/ws/terminal")
async def websocket_endpoint(websocket: WebSocket):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Serve an interactive scripting terminal over a WebSocket.

    Accepts the connection, then treats every received text message
    as Python code to execute via `container.scripting_service`,
    sending any output back to the client.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The client WebSocket connection.
    """
    if not await authorize_websocket(websocket):
        return

    await websocket.accept()
    await websocket.send_text("Connected to Astrometrics Terminal")
    await websocket.send_text("Type 'list_commands()' to see available objects.\n")

    try:
        while True:
            data = await websocket.receive_text()
            # Handle "Load Script" logic or direct commands
            # For simplicity, we assume all text is code to run
            output = container.scripting_service.execute(data)
            if output:
                await websocket.send_text(output)
    except WebSocketDisconnect:
        logger.info("Terminal disconnected")


@app.get("/figure/mpl.js")
async def get_figure_js() -> Response:
    """Serve complete Matplotlib WebAgg JavaScript bundle with toolbar items.

    Returns
    -------
    response : `~fastapi.responses.Response`
        JavaScript bundle defining figure and navigation toolbar components.
    """
    import matplotlib.backends.backend_webagg_core as webagg_core

    return Response(
        content=webagg_core.FigureManagerWebAgg.get_javascript(),
        media_type="application/javascript",
    )


@app.get("/figure/{figure_id}/download.{fmt}")
async def download_figure(figure_id: int, fmt: str) -> Response:
    """Export and download the figure image in the requested graphics format.

    Parameters
    ----------
    figure_id : `int`
        The figure identifier.
    fmt : `str`
        The output graphics format extension (e.g. 'png', 'svg', 'pdf').

    Returns
    -------
    response : `~fastapi.responses.Response`
        Rendered figure bytes with the appropriate MIME content-type.

    Raises
    ------
    HTTPException
        If the figure manager is not found or export fails.
    """
    mgr = None
    if container.scripting_service:
        mgr = container.scripting_service.get_figure_manager(figure_id)

    if not mgr:
        raise HTTPException(status_code=404, detail="Figure not found")

    import io
    import mimetypes

    buff = io.BytesIO()
    try:
        mgr.canvas.figure.savefig(buff, format=fmt)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to export figure: {exc}") from exc

    media_type, _ = mimetypes.guess_type(f"figure.{fmt}")
    return Response(
        content=buff.getvalue(),
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="figure_{figure_id}.{fmt}"'},
    )


@app.get("/figure/{figure_id}", response_class=HTMLResponse)
async def get_figure_page(figure_id: int) -> HTMLResponse:
    """Serve the interactive Matplotlib WebAgg HTML page for a figure.

    Parameters
    ----------
    figure_id : `int`
        The figure identifier.

    Returns
    -------
    html : `~fastapi.responses.HTMLResponse`
        Interactive HTML page with toolbar and HTML5 canvas.
    """
    token = session_auth.SESSION_TOKEN
    # FastAPI's `figure_id: int` annotation already rejects a request whose
    # path segment cannot be parsed as an integer, so this value cannot
    # actually hold script content. It is still escaped before being written
    # into the page, so a static scan of this function does not have to
    # trust that route-level validation elsewhere: the value is safe at the
    # point it is used no matter how it got here.
    safe_figure_id = html.escape(str(figure_id))
    html_content = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <base href="/">
    <meta charset="utf-8">
    <title>Astrometrics Plot #{safe_figure_id}</title>
    <link rel="stylesheet" href="/mpl_static/css/boilerplate.css" type="text/css">
    <link rel="stylesheet" href="/mpl_static/css/fbm.css" type="text/css">
    <link rel="stylesheet" href="/mpl_static/css/mpl.css" type="text/css">
    <script src="/figure/mpl.js"></script>
    <style>
      body {{
        margin: 0;
        padding: 12px;
        background-color: #0a0d14;
        color: #e0e0e0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: flex-start;
        min-height: 100vh;
        box-sizing: border-box;
      }}
      .mpl-toolbar {{
        background: #141923;
        border: 1px solid #2a3649;
        border-radius: 6px;
        padding: 4px 8px;
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
      }}
      .mpl-button-group {{
        display: inline-flex;
        align-items: center;
        gap: 2px;
      }}
      .mpl-widget {{
        background-color: #1e2638;
        border: 1px solid #3b4c68;
        border-radius: 4px;
        padding: 4px 8px;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        color: #e0e0e0;
      }}
      .mpl-widget:hover {{
        background-color: #2b374e;
      }}
      .mpl-widget.active {{
        background-color: #0284c7;
        border-color: #38bdf8;
      }}
      .mpl-widget img {{
        filter: invert(90%);
        width: 16px;
        height: 16px;
      }}
      select.mpl-widget {{
        background-color: #1e2638;
        color: #e0e0e0;
        border: 1px solid #3b4c68;
        border-radius: 4px;
        padding: 3px 6px;
        font-size: 12px;
      }}
      .ui-dialog-titlebar {{
        display: none !important;
      }}
      .mpl-message {{
        color: #94a3b8;
        font-size: 12px;
        margin-left: 10px;
        font-family: monospace;
      }}
      #figure {{
        background: #ffffff;
        border-radius: 6px;
        overflow: hidden;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.6);
      }}
    </style>
    <script>
      function on_download(figure, format) {{
        window.open('/figure/' + figure.id + '/download.' + format, '_blank');
      }}

      document.addEventListener("DOMContentLoaded", function () {{
        var wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsUrl = wsProtocol + '//' + window.location.host + '/ws/figure/{safe_figure_id}?token={token}';
        var websocket_type = mpl.get_websocket_type();
        var websocket = new websocket_type(wsUrl);

        var fig = new mpl.figure(
          {safe_figure_id},
          websocket,
          on_download,
          document.getElementById("figure")
        );
      }});
    </script>
  </head>
  <body>
    <div id="mpl-warnings" class="mpl-warnings" style="color: #f87171; margin-bottom: 8px;"></div>
    <div id="figure"></div>
  </body>
</html>"""
    return HTMLResponse(content=html_content)


class _FastApiWebSocketAdapter:
    """Adapter matching FigureManagerWebAgg's expected WebSocket interface."""

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self.websocket = websocket
        self.loop = loop

    def send_json(self, content: dict[str, Any]) -> None:
        """Serialize and send JSON payload to the WebSocket client."""
        asyncio.run_coroutine_threadsafe(
            self.websocket.send_text(json.dumps(content)),
            self.loop,
        )

    def send_binary(self, blob: bytes) -> None:
        """Send raw binary bytes to the WebSocket client."""
        asyncio.run_coroutine_threadsafe(
            self.websocket.send_bytes(blob),
            self.loop,
        )


@app.websocket("/ws/figure/{figure_id}")
async def figure_websocket_endpoint(websocket: WebSocket, figure_id: int):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Handle interactive Matplotlib WebAgg WebSocket communication.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The WebSocket connection.
    figure_id : `int`
        Figure number.
    """
    if not await authorize_websocket(websocket):
        return

    await websocket.accept()

    mgr = None
    if container.scripting_service:
        mgr = container.scripting_service.get_figure_manager(figure_id)

    if not mgr:
        logger.warning("No active figure manager found for figure %d", figure_id)
        await websocket.close(code=4404)
        return

    if mgr.toolbar is None:
        import matplotlib.backends.backend_webagg_core as webagg_core

        mgr.toolbar = webagg_core.NavigationToolbar2WebAgg(mgr.canvas)
        mgr.canvas.toolbar = mgr.toolbar

    loop = asyncio.get_running_loop()
    adapter = _FastApiWebSocketAdapter(websocket, loop)
    mgr.add_web_socket(adapter)

    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                msg = json.loads(raw_msg)
                msg_type = msg.get("type")
                if msg_type == "supports_binary":
                    # Client capability advertisement, safely handled
                    pass
                else:
                    mgr.handle_json(msg)
            except Exception:
                # One bad message must not close the figure's connection,
                # so every error is logged with its traceback and skipped.
                logger.exception("Error processing figure WebSocket message")
    except WebSocketDisconnect:
        logger.info("Figure %d client disconnected", figure_id)
    finally:
        try:
            mgr.remove_web_socket(adapter)
        except (KeyError, ValueError) as exc:
            logger.debug("Failed removing web socket adapter: %s", exc)


import asyncio
import json


async def periodic_telemetry_loop():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Poll telescope status on an interval to keep telemetry fresh.

    Periodically polls the telescope status from the hardware and
    updates the centralized state, which broadcasts telemetry
    updates to connected WebSocket clients in near real-time. Runs
    forever as a background task until cancelled.
    """
    while True:
        try:
            if container.initialized and container.telescope_service:
                # get_status() ultimately bridges into async driver calls via
                # asyncio.run() (hardware_operations._run_sync), which raises
                # immediately if called from a thread that already has a
                # running event loop -- exactly this loop's own thread.
                # Running it via to_thread gives it a plain worker thread
                # with no event loop of its own, and as a side benefit keeps
                # a slow hardware query from blocking this event loop (and
                # therefore every other request) for its duration.
                await asyncio.to_thread(container.telescope_service.get_status)
        except Exception:
            logger.exception("Error in periodic telemetry loop")
        await asyncio.sleep(2.0)


# Set once the startup catalog warm-up below has finished, whether or not it
# succeeded. The desktop shell polls `/api/ready` and holds its splash screen
# until this is set, so the UI never opens onto a Planetarium that would sit
# empty while the catalog loads. It is set on failure too: a broken warm-up
# only means a slow first load, and must never keep the app from opening.
sky_catalog_warmup_finished = threading.Event()


def _warm_sky_catalog() -> None:
    """Warm the caches the first screens need, all at once, ahead of first use.

    Three independent pieces of start-up work run side by side instead of one
    after another, so the splash waits for the slowest (about 2.6 s on the
    real library) rather than their sum (about 3.6 s):

    * the Planetarium's star catalog, which the first `planetarium:get_sources`
      request would otherwise load while the user waits on an empty sky;
    * astropy's Earth-orientation table, read by the first altitude/azimuth
      conversion;
    * the Astronomy Manager's stellar summary cache.

    Each step logs its own failure and never stops the others;
    `sky_catalog_warmup_finished` is set when all of them are done.
    """

    def warm_sky() -> None:
        """Construct the sky engine and load its catalog with a tiny query."""
        started_at = time.monotonic()
        try:
            container.wayfinder.planning.get_sources(0.0, 0.0, 0.01)
            logger.info("Sky catalog warmed in %.1fs", time.monotonic() - started_at)
        except Exception:
            # Warm-up runs in the background; a failure only makes the first
            # request slower, so it is logged and the server keeps going.
            logger.exception("Sky catalog warm-up failed; first Planetarium load will be slow")

    def warm_earth_orientation() -> None:
        """Load astropy's Earth-orientation table."""
        try:
            elapsed = warm_earth_orientation_data()
            logger.info("Earth-orientation data loaded in %.1fs", elapsed)
        except Exception:
            logger.exception("Earth-orientation warm-up failed; the first star click will be slow")

    def warm_stellar_summaries() -> None:
        """Fill the stellar catalog summary cache."""
        try:
            stellar_started_at = time.monotonic()
            container.stellar_service.warm_catalog_summary_cache()
            logger.info("Stellar catalog summary warmed in %.1fs", time.monotonic() - stellar_started_at)
        except Exception:
            logger.exception("Stellar summary warm-up failed; first Astronomy Manager load will be slow")

    try:
        steps = [
            threading.Thread(target=step, name=step.__name__, daemon=True)
            for step in (warm_sky, warm_earth_orientation, warm_stellar_summaries)
        ]
        for step_thread in steps:
            step_thread.start()
        for step_thread in steps:
            step_thread.join()
    finally:
        sky_catalog_warmup_finished.set()


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Serve real-time system events and telemetry updates.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The client WebSocket connection to register with the
        `SocketManager` and stream events to.
    """
    if not await authorize_websocket(websocket):
        return

    await container.socket_manager.connect(websocket)

    # Send the current system state immediately on connection so the
    # UI doesn't show default zero values
    try:
        if container.initialized and container.telescope_service:
            # Query telescope status once to get fresh data, which
            # updates astrometrics_service. Run via to_thread -- see the
            # comment in periodic_telemetry_loop -- since this also runs
            # directly on this coroutine's event-loop thread.
            await asyncio.to_thread(container.telescope_service.get_status)

        state = container.astrometrics_service.get_state()
        event = {"type": "UI_EVENT", "action": "system_state_update", "payload": state}
        await websocket.send_text(json.dumps(event))
    except Exception:
        logger.exception("Failed to send initial system state on websocket connection")

    try:
        while True:
            # Keep alive / listen for client messages (optional)
            await websocket.receive_text()
    except WebSocketDisconnect:
        container.socket_manager.disconnect(websocket)


def main():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run the high-level interface backend with uvicorn.

    Reads the bind host and port from the ``ASTROMETRICS_BIND_HOST``
    and ``ASTROMETRICS_PORT`` environment variables, defaulting to
    ``127.0.0.1:5000``.
    """
    host = os.environ.get("ASTROMETRICS_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("ASTROMETRICS_PORT", "5000"))
    logger.info("Starting Astrometrics backend on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
