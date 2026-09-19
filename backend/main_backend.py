"""Main entry point for the high-level interface FastAPI backend server.

Configures global logging, suppresses noisy libraries, registers
middleware, initializes the dependency injection container, and
defines API/WebSocket routes.
"""

import logging
import os
import threading
import time
import warnings

import uvicorn

# Suppress noisy Astropy FITS warnings
# REQ: SYS-1.4: Maintain clean logs
from astropy.utils.exceptions import AstropyDeprecationWarning, AstropyWarning
from astropy.wcs import FITSFixedWarning
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from backend.routers import rpc_router

warnings.filterwarnings("ignore", category=FITSFixedWarning)
warnings.filterwarnings("ignore", category=AstropyDeprecationWarning)
warnings.filterwarnings("ignore", category=AstropyWarning, message=".*extra padding.*")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from astrometricslib import get_configuration
from backend.container import container

# Configure Logging
# REQ: SYS-1.4: Maintain clean logs
app_configuration = get_configuration()
log_directory = str(app_configuration.get_logs_path())
log_file_path = os.path.join(log_directory, "astrometrics.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(log_file_path)],
)
# Suppress noisy library logs
# REQ: SYS-1.4: Maintain clean logs
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _use_bundled_earth_orientation_data() -> None:
    """Stop astropy downloading Earth-orientation (IERS) data while running.

    Turning a star's position into altitude and azimuth needs a small table
    of how the Earth's rotation drifts (UT1 minus UTC). By default astropy
    tries to download the latest copy the first time it is needed, which
    stalled the first star click for 6 to 14 seconds, and quietly needed the
    internet. The copy that ships with astropy is used instead.

    Measured (astropy 8.0.1, Bozeman, a star at RA 315.7 deg, Dec 68.7 deg):
    the first conversion took 5.8 to 13.8 s with the download and 0.5 s
    without. Altitude and azimuth differed by at most 0.5 arcseconds across
    dates from two years ago to ten years ahead, far below what a sky map or
    a telescope slew can resolve, and nothing raised an error for any date.
    """
    from astropy.utils import iers

    iers.conf.auto_download = False
    # Without this, astropy refuses to use a table more than 30 days old.
    iers.conf.auto_max_age = None


_use_bundled_earth_orientation_data()

# Initialize Container Resources
container.init_resources()

# Register Socket Logging Handler
from backend.services.infrastructure import session_auth
from backend.services.infrastructure.socket_manager import SocketLoggingHandler

socket_handler = SocketLoggingHandler(container.socket_manager)
socket_handler.setLevel(logging.INFO)  # Only send INFO and above to UI to avoid flood
socket_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(socket_handler)

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
from logging.handlers import QueueHandler, QueueListener

from astrometricslib import DbLogHandler

db_log_handler = DbLogHandler(container.job_repository)
db_log_handler.setLevel(logging.INFO)

db_log_queue: queue.Queue = queue.Queue()
db_log_queue_handler = QueueHandler(db_log_queue)
db_log_queue_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(db_log_queue_handler)

db_log_listener = QueueListener(db_log_queue, db_log_handler)
db_log_listener.start()

# Initialize FastAPI App
app = FastAPI(title="Astrometrics API", version="2.0.0")

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
    "http://localhost:3000",
    "app://.",  # Electron
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Services
# scripting_service is now in container


@app.exception_handler(Exception)
# ruff: ignore[unused-async] -- required async signature for FastAPI's
# exception_handler decorator, which awaits this handler.
async def global_exception_handler(request: Request, exc: Exception):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Catch any unhandled exception and return a JSON 500 response.

    Parameters
    ----------
    request : `~fastapi.Request`
        The incoming request during which the exception was raised.
    exc : `Exception`
        The unhandled exception that was raised.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        A 500 response with ``{"status": "error", "error": str(exc)}``
        as its body, with CORS headers set manually for allowed
        origins.
    """
    logger.error(f"Global error: {exc}", exc_info=True)
    response = JSONResponse(
        status_code=500,
        content={"status": "error", "error": str(exc)},
    )
    # Manual CORS headers for exceptions to avoid frontend being
    # blinded by CORS on 500
    origin = request.headers.get("origin")
    if origin in origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


# Import Routers

# Mount RPC Router and Static Files serving
app.include_router(rpc_router.router)

# Mount static file servers for both the frames directory (external
# image library) and the library index directory (metadata/DB files).
# The frames path is where actual image data lives (_Astrophotography,
# lights, darks, etc.) while the library path holds index/metadata
# files. Frames is mounted first as a separate prefix so both
# locations are reachable.
_frames_path = container.config_service.get_frames_path()
_library_path = container.config_service.get_library_path()

if _frames_path.is_dir() and _frames_path != _library_path:
    app.mount("/static/frames", StaticFiles(directory=str(_frames_path)), name="static_frames")
app.mount("/static", StaticFiles(directory=str(_library_path)), name="static")


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
        logger.warning(f"Rejected WebSocket handshake from disallowed origin: {origin!r}")
        await websocket.close(code=4403)
        return False
    if not session_auth.is_token_valid(websocket.query_params.get("token")):
        logger.warning(f"Rejected WebSocket handshake with missing/invalid token (origin {origin!r})")
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
            output = container.scripting_service.run_code(data)
            if output:
                await websocket.send_text(output)
    except WebSocketDisconnect:
        logger.info("Terminal disconnected")


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
                container.telescope_service.get_status()
        except Exception as e:
            logger.error(f"Error in periodic telemetry loop: {e}")
        await asyncio.sleep(2.0)


# Set once the startup catalog warm-up below has finished, whether or not it
# succeeded. The desktop shell polls `/api/ready` and holds its splash screen
# until this is set, so the UI never opens onto a Planetarium that would sit
# empty while the catalog loads. It is set on failure too: a broken warm-up
# only means a slow first load, and must never keep the app from opening.
sky_catalog_warmup_finished = threading.Event()


def _warm_earth_orientation_data() -> None:
    """Load astropy's Earth-orientation table now, while the splash is up.

    The first altitude/azimuth conversion in a process reads that table
    (about half a second), so do one here instead of on the first star click.
    """
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time

    started_at = time.monotonic()
    SkyCoord(0.0 * u.deg, 0.0 * u.deg).transform_to(
        AltAz(obstime=Time.now(), location=EarthLocation(lat=0.0 * u.deg, lon=0.0 * u.deg))
    )
    logger.info("Earth-orientation data loaded in %.1fs", time.monotonic() - started_at)


def _warm_sky_catalog() -> None:
    """Load the Planetarium's star catalog into memory ahead of first use.

    The first `planetarium:get_sources` request otherwise pays a one-time
    cost of tens of seconds (deserializing every stored star) while the
    user waits on an empty sky. A tiny query constructs the sky engine and
    loads that catalog now, in a worker thread, so it doesn't delay startup
    or block other requests.
    """
    started_at = time.monotonic()
    try:
        container.wayfinder.planning.get_sources(0.0, 0.0, 0.01)
        logger.info("Sky catalog warmed in %.1fs", time.monotonic() - started_at)
    except Exception as warm_error:
        logger.warning("Sky catalog warm-up failed; first Planetarium load will be slow: %s", warm_error)
    try:
        _warm_earth_orientation_data()
    except Exception as warm_error:
        logger.warning("Earth-orientation warm-up failed; the first star click will be slow: %s", warm_error)
    finally:
        sky_catalog_warmup_finished.set()


@app.on_event("startup")
# ruff: ignore[unused-async] -- required async signature for FastAPI's
# on_event("startup") decorator, which awaits this handler.
async def startup_event():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Launch the telemetry loop and sky catalog warm-up on startup."""
    app.state.telemetry_task = asyncio.create_task(periodic_telemetry_loop())
    app.state.sky_warmup_task = asyncio.create_task(asyncio.to_thread(_warm_sky_catalog))


@app.on_event("shutdown")
# ruff: ignore[unused-async] -- required async signature for FastAPI's
# on_event("shutdown") decorator, which awaits this handler.
async def shutdown_event():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Stop the background DB-log listener thread on FastAPI shutdown."""
    app.state.telemetry_task.cancel()
    db_log_listener.stop()


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
            # updates astrometrics_service
            container.telescope_service.get_status()

        state = container.astrometrics_service.get_state()
        event = {"type": "UI_EVENT", "action": "system_state_update", "payload": state}
        await websocket.send_text(json.dumps(event))
    except Exception as e:
        logger.error(f"Failed to send initial system state on websocket connection: {e}")

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
    logger.info(f"Starting Astrometrics backend on {host}:{port}")
    uvicorn.run(app, host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
