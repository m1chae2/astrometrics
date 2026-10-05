"""JSON-RPC 2.0 protocol specifications and serialization utilities.

Defines standard request models, CORS support headers, and recursive
serialization utilities for JSON-RPC 2.0 compliance.
"""

import logging
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RPCRequest(BaseModel):
    """Standard JSON-RPC 2.0 request schema."""

    jsonrpc: str = Field("2.0", description="JSON-RPC protocol version")
    method: str = Field(..., description="Action/Method name to invoke")
    params: dict[str, Any] = Field(default_factory=dict, description="Parameters for the method")
    id: int | str | None = Field(None, description="Request identifier")


class RPCMethodNotFoundError(Exception):
    """Raised when a JSON-RPC method name matches no registered handler.

    This is a dedicated class so the router can tell "no such method" apart
    from a `KeyError` that a service raises while it runs.
    """


def get_cors_headers() -> dict[str, str]:
    """Get standardized CORS headers to avoid browser CORS issues.

    Returns
    -------
    headers : `dict` [`str`, `str`]
        The permissive CORS header set applied to RPC responses.
    """
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }


def serialize_rpc_result(obj: Any) -> Any:
    """Recursively serialize the result to a format safe for JSONResponse.

    Handles Pydantic models, Custom objects with serialize(), lists, and dicts.

    Returns
    -------
    result : `~typing.Any`
        A JSON-serializable representation of `obj` -- primitives
        pass through, NaN/Inf floats become `None`, datetimes become
        ISO 8601 strings, and Pydantic/custom/list/dict values are
        recursively converted.
    """
    if obj is None:
        return None
    if isinstance(obj, (int, str, bool)):
        return obj
    if isinstance(obj, float):
        import math

        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    from datetime import datetime

    if isinstance(obj, datetime):
        return obj.isoformat()

    # NumPy scalars and arrays are not JSON-serializable. Convert them to
    # plain Python values (CLAUDE.md: use `.item()` / `.tolist()`), and send
    # the result back through this function so NaN/Inf are still handled.
    import numpy as np

    if isinstance(obj, np.generic):
        return serialize_rpc_result(obj.item())
    if isinstance(obj, np.ndarray):
        return serialize_rpc_result(obj.tolist())
    if hasattr(obj, "serialize") and callable(obj.serialize):
        return serialize_rpc_result(obj.serialize())
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return serialize_rpc_result(obj.model_dump(by_alias=True))
    if hasattr(obj, "dict") and callable(obj.dict):
        return serialize_rpc_result(obj.dict())
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [serialize_rpc_result(item) for item in obj]
    if isinstance(obj, dict):
        return {k: serialize_rpc_result(v) for k, v in obj.items()}

    # Try to verify if it is json serializable
    try:
        import json

        json.dumps(obj)
        return obj
    except TypeError, OverflowError:
        logger.warning(
            "serialize_rpc_result: %s is not JSON-serializable; sending str(). "
            "Convert it to a plain Python value in the service that returns it.",
            type(obj).__name__,
        )
        return str(obj)


def make_rpc_success_response(result: Any, request_id: int | str | None) -> JSONResponse:
    """Construct a standard successful JSON-RPC response in a success envelope.

    Parameters
    ----------
    result : `~typing.Any`
        The raw result value to serialize into the response body.
    request_id : `int` or `str` or `None`
        The JSON-RPC request identifier to echo back to the caller.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        A 200 JSON-RPC response envelope containing the serialized
        result under ``"result"`` and the standard CORS headers.
    """
    serialized_result = serialize_rpc_result(result)
    response_body = {
        "jsonrpc": "2.0",
        "result": {"status": "success", "data": serialized_result},
        "id": request_id,
    }
    return JSONResponse(content=response_body, status_code=200, headers=get_cors_headers())


def make_rpc_error_response(
    code: int,
    message: str,
    request_id: int | str | None,
    data: dict[str, Any] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    """Construct a standard JSON-RPC error response.

    A well-formed JSON-RPC call gets HTTP status 200 whether it succeeded or
    failed. The error code inside the reply tells the client what went wrong.
    Other statuses are for problems below the RPC layer, such as a request
    that is not valid JSON-RPC.

    Parameters
    ----------
    code : `int`
        The JSON-RPC error code to report.
    message : `str`
        A human-readable description of the error.
    request_id : `int` or `str` or `None`
        The JSON-RPC request identifier to echo back to the caller.
    data : `dict` [`str`, `~typing.Any`], optional
        The `ErrorInfo` record of the error, as JSON-safe data.
    status_code : `int`, optional
        The HTTP status code for the response. Defaults to 200.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        A JSON-RPC error envelope with the standard CORS headers.
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    response_body = {"jsonrpc": "2.0", "error": error, "id": request_id}
    return JSONResponse(content=response_body, status_code=status_code, headers=get_cors_headers())
