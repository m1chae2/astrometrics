"""Purpose: Call the backend's RPC route from the layer.

Description: The app's UI sends every request to ``POST /api/rpc`` as a
JSON-RPC 2.0 message, and the backend wraps each result as
``{"status": ..., "data": ...}``. The launcher uses this one function for
the operator step (it records the calibrations and enters controller mode
after the backend answers) and the real-frame source (`frame_source`, which
lists a target's frames and deletes a delivered one). The TPM harness
scripts share it through ``build/tpm/backend_rpc.py``. It reaches
``127.0.0.1`` only, and it ignores the proxy variables. The route takes
no session token (only the WebSockets do), so none is sent.
"""

import json
import urllib.error
import urllib.request

__all__ = ["backend_is_ready", "call_rpc"]

#: An opener that ignores ``http_proxy`` and the other proxy variables, so a
#: request to 127.0.0.1 can never be sent to a proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call_rpc(port: int, method: str, params: dict, timeout: float = 60.0) -> object:
    """Call one method of a backend on this computer.

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
        If the backend answers with an error.
    """
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/rpc", data=body, headers={"Content-Type": "application/json"}
    )
    with _OPENER.open(request, timeout=timeout) as response:
        reply = json.loads(response.read())
    if "error" in reply:
        raise RuntimeError(f"{method} failed: {reply['error']}")
    result = reply.get("result")
    # The backend wraps every result as {"status": ..., "data": ...}.
    if isinstance(result, dict) and "data" in result and "status" in result:
        return result["data"]
    return result


def backend_is_ready(port: int, timeout: float = 2.0) -> bool:
    """Ask ``/api/ready`` whether the backend has finished starting.

    Parameters
    ----------
    port : `int`
        The backend's port (on 127.0.0.1).
    timeout : `float`, optional
        Seconds to wait for the reply.

    Returns
    -------
    ready : `bool`
        `True` on status 200. Any failure to connect or a 503 gives `False`.
    """
    try:
        with _OPENER.open(f"http://127.0.0.1:{port}/api/ready", timeout=timeout) as response:
            return response.status == 200
    except urllib.error.URLError, OSError, ValueError:
        return False
