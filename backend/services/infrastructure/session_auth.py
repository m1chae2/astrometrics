"""WebSocket authorization for the high-level interface backend.

CORS does not protect WebSocket endpoints -- browsers do not apply the
same-origin policy to the WebSocket handshake, and no preflight is sent.
A `CORSMiddleware` allowlist therefore leaves `/ws/terminal` (which
executes arbitrary Python) and `/ws/events` (which streams observatory
telemetry) reachable from any page the user happens to visit while the
backend is running. This module supplies the two checks the handshake
needs instead: an explicit `Origin` allowlist and a shared session token.

The token is the load-bearing half. A hostile page cannot read it,
because the only ways to obtain it are a CORS-protected HTTP request
(the browser refuses to expose the response body to a disallowed
origin) or the ``ASTROMETRICS_SESSION_TOKEN`` environment variable that
a local launcher such as Electron passes to its own renderer. The
origin check is defense in depth for the ordinary browser case.
"""

import os
import secrets


def _resolve_session_token() -> str:
    """Return the process-wide session token, generating one if needed.

    Honors ``ASTROMETRICS_SESSION_TOKEN`` so a launcher that starts the
    backend as a subprocess (Electron's `backend_manager.js`) can mint
    the token itself and hand it to its renderer out of band, rather
    than round-tripping through the HTTP endpoint.

    Returns
    -------
    session_token : `str`
        The token every WebSocket client must present.
    """
    configured_token = os.environ.get("ASTROMETRICS_SESSION_TOKEN")
    if configured_token:
        return configured_token
    return secrets.token_urlsafe(32)


SESSION_TOKEN = _resolve_session_token()


def is_origin_allowed(origin: str | None, allowed_origins: list[str]) -> bool:
    """Report whether a WebSocket handshake's `Origin` is acceptable.

    A missing header means a non-browser client (a script, `curl`, the
    test suite); the literal string ``"null"`` means an opaque origin,
    which is what a `file://` page or a sandboxed iframe sends. Neither
    can be resolved against the allowlist, so both are deferred to the
    token check rather than being rejected outright -- a `file://`
    Electron renderer is legitimate, and a sandboxed iframe still has
    no way to learn the token.

    Parameters
    ----------
    origin : `str` or `None`
        The handshake's ``Origin`` header, if it sent one.
    allowed_origins : `list` of `str`
        The origins the application serves its own UI from.

    Returns
    -------
    is_allowed : `bool`
        `True` if the origin check passes.
    """
    if origin is None or origin == "null":
        return True
    return origin in allowed_origins


def is_token_valid(token: str | None) -> bool:
    """Report whether a client-supplied token matches this process's.

    Parameters
    ----------
    token : `str` or `None`
        The token the client presented, typically as a ``token`` query
        parameter on the WebSocket URL.

    Returns
    -------
    is_valid : `bool`
        `True` if `token` matches the session token.
    """
    if not token:
        return False
    return secrets.compare_digest(token, SESSION_TOKEN)
