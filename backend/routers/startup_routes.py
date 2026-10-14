"""Purpose: The small HTTP routes a client uses before it can call RPC.

Description: ``/api/ready`` says whether start-up warm-up has finished, so
the desktop shell knows when to drop its splash screen.
``/api/session-token`` gives the WebSocket token to the app.
``/api/pairing-info`` tells a companion phone how to connect. ``/`` says
the backend is running.
"""

import socket
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend import startup
from backend.services.infrastructure import session_auth

router = APIRouter()


@router.get("/api/session-token")
async def session_token() -> dict[str, str]:
    """Give the session token to the app.

    The CORS allow-list protects this route: a browser does not show the
    reply to a page from an origin that is not listed. That keeps a hostile
    web site from reading the token and opening the WebSockets itself.

    Returns
    -------
    token : `dict`
        ``{"token": str}``, for the app to add to its WebSocket addresses.
    """
    return {"token": session_auth.SESSION_TOKEN}


def _detect_lan_ip() -> str:
    """Find this computer's address on the local network.

    Returns
    -------
    lan_ip : `str`
        The local IPv4 address, or ``"127.0.0.1"`` if none can be found.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 80))
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


@router.get("/api/pairing-info")
async def pairing_info(request: Request) -> dict[str, Any]:
    """Tell a companion phone or browser how to connect to this backend.

    Parameters
    ----------
    request : `~fastapi.Request`
        The incoming HTTP request.

    Returns
    -------
    info : `dict`
        The app name and version, the host and port, the addresses of the
        RPC route and the WebSockets, and the session token.
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
            "rpc": f"http://{host}:{port}/api/rpc",
            "ws_events": f"ws://{host}:{port}/ws/events?token={session_auth.SESSION_TOKEN}",
            "ws_terminal": f"ws://{host}:{port}/ws/terminal?token={session_auth.SESSION_TOKEN}",
        },
    }


@router.get("/api/ready")
async def readiness() -> JSONResponse:
    """Report whether start-up warm-up has finished.

    This differs from the ``/`` route: the backend answers requests as soon
    as it is listening, but the first Planetarium load stays slow until the
    star catalog is in memory.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        Status 200 with ``{"ready": true}`` once warm-up has finished,
        otherwise status 503 with ``{"ready": false}``.
    """
    if startup.sky_catalog_warmup_finished.is_set():
        return JSONResponse(status_code=200, content={"ready": True})
    return JSONResponse(status_code=503, content={"ready": False})


@router.get("/")
async def root() -> dict[str, str]:
    """Say that the backend is running.

    Returns
    -------
    message : `dict`
        ``{"message": "Astrometrics Backend Running"}``.
    """
    return {"message": "Astrometrics Backend Running"}
