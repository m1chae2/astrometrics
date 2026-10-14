"""Purpose: Check the backend serves exactly its declared public interface.

Description: `backend.public_interface` lists every RPC method and every
other route a client may reach. These tests check that:

* the RPC router has a handler for every declared method, and no other;
* the FastAPI app mounts every declared route, and no other;
* the app and the Electron shell only use route paths that are declared.
"""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount

from backend.main_backend import app
from backend.public_interface import ROUTES, RPC_METHODS
from backend.routers.rpc_router import rpc_registry

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: The only app files allowed to call `fetch` or open a WebSocket (an ESLint
#: rule enforces that), and the Electron files that call the backend.
CLIENT_FILES = (
    "ui/common/services/backendApi.ts",
    "ui/common/utils/socketClient.ts",
    "electron/backend_manager.js",
    "electron/emergency_park.js",
    "electron/ipc_handlers.js",
    "electron/main.js",
    "electron/python_terminal_manager.js",
)

#: A backend path: one of the declared top-level folders, then more parts.
_PATH_PATTERN = re.compile(r"(/(?:api|ws|static|figure|mpl_static|_images)(?![\w.\-])(?:/[\w.\-{}]*)*)")


def _flatten(routes: list, prefix: str = "") -> set[tuple[str, str]]:
    """List routes as ``(kind, path)`` pairs, opening included routers.

    Parameters
    ----------
    routes : `list`
        Routes from ``app.routes`` or from an included router.
    prefix : `str`, optional
        The path prefix the router was included with.

    Returns
    -------
    routes : `set` [`tuple` [`str`, `str`]]
        One pair per route; the kind is ``"http"``, ``"websocket"`` or
        ``"files"``. Anything else keeps its class name as the kind, so
        the comparison fails on it.
    """
    mounted: set[tuple[str, str]] = set()
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            mounted |= _flatten(included.routes, prefix + route.include_context.prefix)
        elif isinstance(route, APIWebSocketRoute):
            mounted.add(("websocket", prefix + route.path))
        elif isinstance(route, APIRoute):
            mounted.add(("http", prefix + route.path))
        elif isinstance(route, Mount):
            mounted.add(("files", prefix + route.path))
        else:
            mounted.add((type(route).__name__, prefix + getattr(route, "path", "?")))
    return mounted


def _mounted_routes() -> set[tuple[str, str]]:
    """List the routes the app mounts, as ``(kind, path)`` pairs.

    Returns
    -------
    routes : `set` [`tuple` [`str`, `str`]]
        One pair per route.
    """
    return _flatten(list(app.routes))


def test_every_declared_method_has_a_handler_and_no_other() -> None:
    """The router's handlers are exactly `RPC_METHODS`."""
    assert len(set(RPC_METHODS)) == len(RPC_METHODS), "RPC_METHODS lists a method twice"
    assert set(rpc_registry._handlers) == set(RPC_METHODS)


def test_registering_an_undeclared_method_is_refused() -> None:
    """`register` refuses a method that `RPC_METHODS` does not list."""
    with pytest.raises(ValueError, match="public_interface"):
        rpc_registry.register("undeclared:method", lambda: None)
    assert "undeclared:method" not in rpc_registry._handlers


def test_every_mounted_route_is_declared() -> None:
    """The app mounts nothing that `ROUTES` does not list."""
    declared = {(route.kind, route.path) for route in ROUTES}
    assert _mounted_routes() <= declared


def test_every_required_route_is_mounted() -> None:
    """Every route `ROUTES` does not mark optional is mounted."""
    required = {(route.kind, route.path) for route in ROUTES if not route.optional}
    assert required <= _mounted_routes()


def test_route_names_and_paths_are_unique() -> None:
    """Each route has its own name and path."""
    assert len({route.name for route in ROUTES}) == len(ROUTES)
    assert len({route.path for route in ROUTES}) == len(ROUTES)


def _declared_prefix(path: str) -> bool:
    """Say whether a path the client uses is a declared route or under one.

    Parameters
    ----------
    path : `str`
        A path found in a client file, such as ``"/static/frames/"``.

    Returns
    -------
    declared : `bool`
        `True` if the path matches a declared route (a ``{name}`` part
        matches any one path part), lies inside a declared file folder, or
        ends in ``/`` and begins a declared route's path.
    """
    if path.endswith("/") and any(route.path.startswith(path) for route in ROUTES):
        # A prefix the client tests a URL against, such as "/figure/".
        return True
    path = path.rstrip("/") or "/"
    for route in ROUTES:
        pattern = re.escape(route.path)
        pattern = re.sub(r"\\\{\w+\\\}", r"[^/]*", pattern)
        if re.fullmatch(pattern, path):
            return True
        if route.kind == "files" and path.startswith(route.path + "/"):
            return True
    return False


def test_the_client_files_use_only_declared_paths() -> None:
    """Every backend path in the app's and the shell's files is declared."""
    undeclared = []
    for relative in CLIENT_FILES:
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        undeclared.extend(
            f"{relative}: {path}" for path in _PATH_PATTERN.findall(text) if not _declared_prefix(path)
        )
    assert not undeclared, "Paths not in backend/public_interface.py:\n" + "\n".join(undeclared)


def test_the_path_check_finds_paths() -> None:
    """The path pattern finds the RPC path in the Electron park command."""
    text = (REPOSITORY_ROOT / "electron/emergency_park.js").read_text(encoding="utf-8")
    assert "/api/rpc" in _PATH_PATTERN.findall(text)


def test_the_path_check_refuses_undeclared_paths() -> None:
    """A path that is not declared, such as a removed route, is caught."""
    assert not _declared_prefix("/api/handoff/state")
    assert _declared_prefix("/figure/12")
    assert _declared_prefix("/static/frames/lights/M31/a.fits")
