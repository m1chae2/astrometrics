"""Purpose: Call the throwaway backend's RPC route from a harness script.

Description: The app's UI sends every request to ``POST /api/rpc`` as a
JSON-RPC 2.0 message, and the backend wraps each result as
``{"status": ..., "data": ...}``. The harness scripts that drive the backend
without a browser (the replay preload and the processing run) share this one
function. It talks to ``127.0.0.1`` only and refuses port 5000, the real
backend's port.

Usage::

    from backend_rpc import call_rpc
    targets = call_rpc(47123, "target:list", {})
"""

from simulator.rpc_client import call_rpc as _call_rpc

#: The real backend's port. The harness never calls it.
REAL_BACKEND_PORT = 5000


def call_rpc(port: int, method: str, params: dict, timeout: float = 60.0) -> object:
    """Call one method of the throwaway backend.

    The call itself is `simulator.rpc_client.call_rpc`; this wrapper adds
    the harness rule that the real backend's port is never called.

    Parameters
    ----------
    port : `int`
        The backend's port (on 127.0.0.1).
    method : `str`
        The RPC method name.
    params : `dict`
        Its keyword arguments.
    timeout : `float`, optional
        Seconds to wait for the reply.

    Returns
    -------
    result : `object`
        The reply's ``result``, with the backend's ``data`` wrapper removed.

    Raises
    ------
    RuntimeError
        If the backend answers with an error, or the port is the real one.
    """
    if port == REAL_BACKEND_PORT:
        raise RuntimeError("Refusing to call port 5000: that is the real backend.")
    return _call_rpc(port, method, params, timeout)
