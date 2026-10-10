"""Purpose: The interactive Matplotlib figure pages and their WebSocket.

Description: When terminal code draws a plot, Matplotlib's WebAgg back end
keeps the figure live in the backend. These routes serve a page that shows
the figure with its toolbar (``/figure/{figure_id}``), the script that page
needs (``/figure/mpl.js``), a download of the figure as an image, and the
WebSocket the page uses to send mouse and toolbar events
(``/ws/figure/{figure_id}``). `mount_figure_assets` mounts Matplotlib's own
style sheets and toolbar icons.
"""

import asyncio
import html
import io
import json
import logging
import mimetypes
import os
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.container import container
from backend.routers.websockets import authorize_websocket
from backend.services.infrastructure import session_auth

logger = logging.getLogger(__name__)

router = APIRouter()


def mount_figure_assets(app: FastAPI) -> None:
    """Mount Matplotlib's WebAgg style sheets and toolbar icons.

    The icons are mounted twice, at ``/_images`` and ``/figure/_images``,
    because the figure script asks for them relative to the page. A folder
    that does not exist is skipped, and a Matplotlib that cannot be loaded
    is logged and skipped.

    Parameters
    ----------
    app : `~fastapi.FastAPI`
        The app to mount the folders on.
    """
    try:
        import matplotlib
        import matplotlib.backends.backend_webagg_core as webagg_core

        webagg_core.FigureManagerWebAgg._toolbar2_class = webagg_core.NavigationToolbar2WebAgg

        static_path = webagg_core.FigureManagerWebAgg.get_static_file_path()
        images_path = os.path.join(matplotlib.get_data_path(), "images")
        if os.path.isdir(static_path):
            app.mount("/mpl_static", StaticFiles(directory=static_path), name="mpl_static")
        if os.path.isdir(images_path):
            app.mount("/_images", StaticFiles(directory=images_path), name="mpl_images")
            app.mount("/figure/_images", StaticFiles(directory=images_path), name="mpl_figure_images")
    except (ImportError, OSError, RuntimeError) as e:
        logger.warning("Failed to mount Matplotlib WebAgg static assets: %s", e)


@router.get("/figure/mpl.js")
async def get_figure_script() -> Response:
    """Serve Matplotlib's WebAgg script, toolbar included.

    Returns
    -------
    response : `~fastapi.responses.Response`
        The JavaScript that draws a figure and its toolbar.
    """
    import matplotlib.backends.backend_webagg_core as webagg_core

    return Response(
        content=webagg_core.FigureManagerWebAgg.get_javascript(),
        media_type="application/javascript",
    )


@router.get("/figure/{figure_id}/download.{fmt}")
async def download_figure(figure_id: int, fmt: str) -> Response:
    """Save a figure in the format asked for and send it as a download.

    Parameters
    ----------
    figure_id : `int`
        The figure number.
    fmt : `str`
        The file type, such as ``"png"``, ``"svg"`` or ``"pdf"``.

    Returns
    -------
    response : `~fastapi.responses.Response`
        The image bytes, with the matching content type.

    Raises
    ------
    HTTPException
        404 if the figure does not exist; 400 if it cannot be saved in
        that format.
    """
    manager = None
    if container.scripting_service:
        manager = container.scripting_service.get_figure_manager(figure_id)

    if not manager:
        raise HTTPException(status_code=404, detail="Figure not found")

    buffer = io.BytesIO()
    try:
        manager.canvas.figure.savefig(buffer, format=fmt)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to export figure: {exc}") from exc

    media_type, _ = mimetypes.guess_type(f"figure.{fmt}")
    return Response(
        content=buffer.getvalue(),
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="figure_{figure_id}.{fmt}"'},
    )


#: The page that shows one figure. ``{figure_id}`` and ``{token}`` are filled
#: in by `get_figure_page`; doubled braces are literal braces.
_FIGURE_PAGE = """<!DOCTYPE html>
<html lang="en">
  <head>
    <base href="/">
    <meta charset="utf-8">
    <title>Astrometrics Plot #{figure_id}</title>
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
        var wsUrl = wsProtocol + '//' + window.location.host + '/ws/figure/{figure_id}?token={token}';
        var websocket_type = mpl.get_websocket_type();
        var websocket = new websocket_type(wsUrl);

        var fig = new mpl.figure(
          {figure_id},
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


@router.get("/figure/{figure_id}", response_class=HTMLResponse)
async def get_figure_page(figure_id: int) -> HTMLResponse:
    """Serve the page that shows one interactive figure.

    Parameters
    ----------
    figure_id : `int`
        The figure number.

    Returns
    -------
    html : `~fastapi.responses.HTMLResponse`
        A page with the figure's toolbar and drawing area.
    """
    # FastAPI already rejects a figure_id that is not a whole number, so it
    # cannot hold script code. It is still escaped here, so the page is safe
    # no matter how the value got here.
    safe_figure_id = html.escape(str(figure_id))
    return HTMLResponse(
        content=_FIGURE_PAGE.format(figure_id=safe_figure_id, token=session_auth.SESSION_TOKEN)
    )


class _FastApiWebSocketAdapter:
    """Give a FastAPI WebSocket the send methods a Matplotlib figure needs."""

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        """Keep the WebSocket and the event loop it runs on.

        Parameters
        ----------
        websocket : `~fastapi.WebSocket`
            The figure page's connection.
        loop : `asyncio.AbstractEventLoop`
            The loop the connection belongs to. Matplotlib sends from other
            threads, so each send is handed to this loop.
        """
        self.websocket = websocket
        self.loop = loop

    def send_json(self, content: dict[str, Any]) -> None:
        """Send a JSON message to the figure page.

        Parameters
        ----------
        content : `dict`
            The message.
        """
        asyncio.run_coroutine_threadsafe(self.websocket.send_text(json.dumps(content)), self.loop)

    def send_binary(self, blob: bytes) -> None:
        """Send raw bytes (an image of the figure) to the figure page.

        Parameters
        ----------
        blob : `bytes`
            The bytes to send.
        """
        asyncio.run_coroutine_threadsafe(self.websocket.send_bytes(blob), self.loop)


@router.websocket("/ws/figure/{figure_id}")
async def figure_websocket(websocket: WebSocket, figure_id: int) -> None:
    """Pass mouse and toolbar events between a figure page and its figure.

    Parameters
    ----------
    websocket : `~fastapi.WebSocket`
        The figure page's connection.
    figure_id : `int`
        The figure number.
    """
    if not await authorize_websocket(websocket):
        return

    await websocket.accept()

    manager = None
    if container.scripting_service:
        manager = container.scripting_service.get_figure_manager(figure_id)

    if not manager:
        logger.warning("No active figure manager found for figure %d", figure_id)
        await websocket.close(code=4404)
        return

    if manager.toolbar is None:
        import matplotlib.backends.backend_webagg_core as webagg_core

        manager.toolbar = webagg_core.NavigationToolbar2WebAgg(manager.canvas)
        manager.canvas.toolbar = manager.toolbar

    adapter = _FastApiWebSocketAdapter(websocket, asyncio.get_running_loop())
    manager.add_web_socket(adapter)

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
                # "supports_binary" only says what the page can receive.
                if message.get("type") != "supports_binary":
                    manager.handle_json(message)
            except Exception:
                # One bad message must not close the figure's connection,
                # so every error is logged with its traceback and skipped.
                logger.exception("Error processing figure WebSocket message")
    except WebSocketDisconnect:
        logger.info("Figure %d client disconnected", figure_id)
    finally:
        try:
            manager.remove_web_socket(adapter)
        except (KeyError, ValueError) as exc:
            logger.debug("Failed removing web socket adapter: %s", exc)
