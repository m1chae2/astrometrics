"""Main entry point for the FastAPI backend server.

Sets up logging, builds the app, adds the CORS middleware and the error
handlers, mounts the routes declared in `backend.public_interface`, and
wires the lifespan that starts and stops the services. The routes live in
`backend/routers/`, and the start-up work in `backend/startup.py`.
"""

import asyncio
import logging
import os
import queue
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging.handlers import QueueHandler, QueueListener
from typing import Any

import uvicorn

# Suppress noisy Astropy FITS warnings
# REQ: SYS-1.4: Maintain clean logs
from astropy.utils.exceptions import AstropyDeprecationWarning, AstropyWarning
from astropy.wcs import FITSFixedWarning
from fastapi import FastAPI, Request

warnings.filterwarnings("ignore", category=FITSFixedWarning)
warnings.filterwarnings("ignore", category=AstropyDeprecationWarning)
warnings.filterwarnings("ignore", category=AstropyWarning, message=".*extra padding.*")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from astrometricslib import (
    AstrometricsError,
    DbLogHandler,
    ErrorInfo,
    StorageNotMountedError,
    close_interrupted_jobs,
    configure_logging,
    configure_offline_iers,
    get_configuration,
    new_request_id,
    require_mounted_storage,
    to_error_info,
)
from backend import startup
from backend.container import container
from backend.routers import figures, rpc_router, startup_routes, static_files, websockets
from backend.services.infrastructure.session_auth import ALLOWED_ORIGINS, LAN_ORIGIN_REGEX
from backend.services.infrastructure.socket_manager import SocketLoggingHandler

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

#: The handlers made by `attach_log_handlers`, kept so `detach_log_handlers`
#: can remove them.
_log_handlers: dict[str, Any] = {}


def attach_log_handlers() -> None:
    """Send log records to the app and to the log database.

    Needs the container's socket manager and job repository, so it runs
    after `container.init_resources()`.

    The database handler opens its own SQLite connection and commits each
    record. That is too slow to run on the thread that logged: one request
    that logs thousands of warnings would block for tens of seconds. So the
    records go through a queue, and a background thread writes them.
    """
    socket_handler = SocketLoggingHandler(container.socket_manager)
    # Only INFO and above go to the app, so it is not flooded.
    socket_handler.setLevel(logging.INFO)
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


def detach_log_handlers() -> None:
    """Remove the handlers added by `attach_log_handlers`."""
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
    attach_log_handlers()
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
    app.state.telemetry_task = asyncio.create_task(startup.periodic_telemetry_loop())
    app.state.sky_warmup_task = asyncio.create_task(asyncio.to_thread(startup.warm_start_up_caches))
    try:
        yield
    finally:
        app.state.telemetry_task.cancel()
        detach_log_handlers()
        container.shutdown_resources()


# The app serves only the routes in `backend.public_interface`, so FastAPI's
# own documentation pages are turned off.
app = FastAPI(
    title="Astrometrics API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=LAN_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


#: HTTP status for each error code on the plain HTTP routes. The RPC route
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
    """Build the JSON reply for a failed HTTP request.

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
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.exception_handler(AstrometricsError)
# ruff: ignore[unused-async] -- required async signature for FastAPI's
# exception_handler decorator, which awaits this handler.
async def expected_error_handler(request: Request, exc: AstrometricsError) -> JSONResponse:
    """Turn an expected error from an HTTP route into an error reply.

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


# Mount the routes of `backend.public_interface.ROUTES`. The file folders are
# mounted before the figure routes so ``/figure/_images`` is matched first.
app.include_router(rpc_router.router)
static_files.mount_library_files(app, app_configuration)
figures.mount_figure_assets(app)
app.include_router(startup_routes.router)
app.include_router(figures.router)
app.include_router(websockets.router)


def main() -> None:
    """Run the backend with uvicorn.

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
