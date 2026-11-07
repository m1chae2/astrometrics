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

import json
import urllib.request

#: The real backend's port. The harness never calls it.
REAL_BACKEND_PORT = 5000


def call_rpc(port: int, method: str, params: dict, timeout: float = 60.0) -> object:
    """Call one method of the throwaway backend.

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
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/rpc", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        reply = json.loads(response.read())
    if "error" in reply:
        raise RuntimeError(f"{method} failed: {reply['error']}")
    result = reply.get("result")
    # The backend wraps every result as {"status": ..., "data": ...}.
    if isinstance(result, dict) and "data" in result and "status" in result:
        return result["data"]
    return result
