"""Purpose: The event and terminal WebSockets.

Description: ``/ws/events`` streams live events and telemetry to every app
window. ``/ws/terminal`` runs the plain Python terminal. Both check the
caller's origin and session token first (`authorize_websocket`), because
browsers do not apply CORS to a WebSocket handshake.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.container import container
from backend.services.infrastructure import session_auth

logger = logging.getLogger(__name__)

router = APIRouter()


async def authorize_websocket(websocket: WebSocket) -> bool:
    """Check the origin and the session token of a WebSocket handshake.

    Closes the connection before accepting it when either check fails, so
    a caller that is not allowed never reaches the route's body. See
    `backend.services.infrastructure.session_auth` for why CORS alone does
    not cover WebSocket routes.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The connection being set up.

    Returns
    -------
    is_authorized : `bool`
        `True` if the caller may go on to `websocket.accept()`.
    """
    origin = websocket.headers.get("origin")
    if not session_auth.is_origin_allowed(origin, session_auth.ALLOWED_ORIGINS):
        logger.warning("Rejected WebSocket handshake from disallowed origin: %r", origin)
        await websocket.close(code=4403)
        return False
    if not session_auth.is_token_valid(websocket.query_params.get("token")):
        logger.warning("Rejected WebSocket handshake with missing/invalid token (origin %r)", origin)
        await websocket.close(code=4401)
        return False
    return True


@router.websocket("/ws/terminal")
async def terminal_websocket(websocket: WebSocket) -> None:
    """Serve the plain Python terminal over a WebSocket.

    Every text message received is run as Python code by
    `container.scripting_service`, and any output is sent back.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The client connection.
    """
    if not await authorize_websocket(websocket):
        return

    await websocket.accept()
    await websocket.send_text("Connected to Astrometrics Terminal")
    await websocket.send_text("Type 'list_commands()' to see available objects.\n")

    try:
        while True:
            data = await websocket.receive_text()
            output = container.scripting_service.execute(data)
            if output:
                await websocket.send_text(output)
    except WebSocketDisconnect:
        logger.info("Terminal disconnected")


@router.websocket("/ws/events")
async def events_websocket(websocket: WebSocket) -> None:
    """Stream live system events and telemetry to an app window.

    Sends the current system state as soon as the window connects, so the
    window does not show zeros while it waits for the first update.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The client connection, registered with the `SocketManager`.
    """
    if not await authorize_websocket(websocket):
        return

    await container.socket_manager.connect(websocket)

    try:
        if container.initialized and container.telescope_service:
            # Read the telescope once so the state is fresh. This runs on a
            # worker thread; see `backend.startup.periodic_telemetry_loop`.
            await asyncio.to_thread(container.telescope_service.get_status)

        state = container.astrometrics_service.get_state()
        event = {"type": "UI_EVENT", "action": "system_state_update", "payload": state}
        await websocket.send_text(json.dumps(event))
    except Exception:
        logger.exception("Failed to send initial system state on websocket connection")

    try:
        while True:
            # Keep the connection open; the app sends nothing it needs.
            await websocket.receive_text()
    except WebSocketDisconnect:
        container.socket_manager.disconnect(websocket)
