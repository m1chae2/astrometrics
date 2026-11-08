"""Purpose: Refuse requests that name a host this backend does not serve.

Description: A web page can make the browser send requests to the backend
under a name the attacker controls. The attacker's name first points to the
attacker's server, then, after the page has loaded, to 127.0.0.1. This is
called DNS rebinding. The browser then treats the backend as the same site
as the page, and the CORS rules no longer protect it. The request still
carries the attacker's name in its ``Host`` header, so the backend refuses
any name it does not expect.

`HostCheckMiddleware` accepts:

* ``localhost``, ``127.0.0.1`` and ``[::1]``, with any port;
* when the backend is bound to a network address (``run_backend.sh --lan``),
  also a private-network address (such as 192.168.x.x or 10.x.x.x), a name
  ending in ``.local``, and this computer's own host name;
* ``testserver``, the host name the test client uses, only when
  ``ASTROMETRICS_TESTING`` is set.

The check covers HTTP requests and WebSocket handshakes. It reads the
environment on each request, so a change of mode needs no restart of the
check itself.
"""

import ipaddress
import os
import socket
from typing import Any

#: Host names that always reach the backend (lower case, without a port).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})

#: Bind addresses that keep the backend on this computer only.
_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})


def _strip_port(host_header: str) -> str:
    """Remove the port from a ``Host`` header value.

    Parameters
    ----------
    host_header : `str`
        The header value, such as ``localhost:5000`` or ``[::1]:5000``.

    Returns
    -------
    host : `str`
        The host name in lower case, with brackets kept for an IPv6 address.
    """
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def is_lan_mode() -> bool:
    """Report whether the backend is bound to a network address.

    Returns
    -------
    is_lan : `bool`
        `True` when ``ASTROMETRICS_BIND_HOST`` is not a loopback address,
        which is what ``run_backend.sh --lan`` sets (``0.0.0.0``).
    """
    return os.environ.get("ASTROMETRICS_BIND_HOST", "127.0.0.1").strip().lower() not in _LOOPBACK_BIND_HOSTS


def is_host_allowed(host_header: str | None) -> bool:
    """Report whether a request may name this host.

    Parameters
    ----------
    host_header : `str` or `None`
        The request's ``Host`` header value.

    Returns
    -------
    is_allowed : `bool`
        `True` if the backend serves requests under this name.
    """
    if not host_header:
        return False
    host = _strip_port(host_header)
    if host in LOOPBACK_HOSTS:
        return True
    if os.environ.get("ASTROMETRICS_TESTING") and host == "testserver":
        return True
    if not is_lan_mode():
        return False
    if host.endswith(".local") or host == socket.gethostname().lower():
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class HostCheckMiddleware:
    """Answer 400 to a request whose ``Host`` header is not allowed."""

    def __init__(self, app: Any) -> None:
        """Wrap an ASGI application.

        Parameters
        ----------
        app : `~typing.Any`
            The ASGI application to protect.
        """
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Pass an allowed request on and refuse the others.

        Parameters
        ----------
        scope : `dict`
            The ASGI connection scope.
        receive : `~typing.Any`
            The ASGI receive channel.
        send : `~typing.Any`
            The ASGI send channel.
        """
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        host_header = next(
            (value.decode("latin-1") for name, value in scope["headers"] if name == b"host"), None
        )
        if is_host_allowed(host_header):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b"Invalid host header"
        await send({
            "type": "http.response.start",
            "status": 400,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", b"19")],
        })
        await send({"type": "http.response.body", "body": body})
