"""Shared HTTP helpers that MCP tools use to reach the backend.

Each helper returns the backend's parsed JSON answer. A request that
cannot reach the backend, or that gets a bad answer, raises
`ExternalServiceError`, so the MCP adapter reports it as an error result.
"""

import os
from typing import Any

import httpx

from astrometricslib import ExternalServiceError, PermissionDeniedError

API_BASE = os.getenv("ASTROMETRICS_API_BASE", "http://127.0.0.1:5000")

# REQ: SEC-1.1: Restrict MCP tools to non-destructive HTTP methods.
ALLOWED_HTTP_METHODS = {"GET", "POST"}


async def request_backend(
    method: str, endpoint: str, params: dict | None = None, json: dict | None = None, timeout: float = 120.0
) -> Any:
    """Send one HTTP request to the backend, allowing only safe methods.

    Parameters
    ----------
    method : `str`
        HTTP method (``"GET"`` or ``"POST"``).
    endpoint : `str`
        API endpoint path.
    params : `dict`, optional
        Query parameters. If `None` (default), no query parameters
        are sent.
    json : `dict`, optional
        JSON body. If `None` (default), no body is sent.
    timeout : `float`, optional
        Request timeout in seconds. Default is 120.0.

    Returns
    -------
    result : `Any`
        The parsed JSON answer.

    Raises
    ------
    PermissionDeniedError
        If `method` is not in `ALLOWED_HTTP_METHODS`.
    ExternalServiceError
        If the backend cannot be reached, answers with an HTTP error
        status, or sends something that is not JSON.
    """
    if method.upper() not in ALLOWED_HTTP_METHODS:
        raise PermissionDeniedError(
            f"HTTP method '{method}' is not allowed from MCP tools. Only {sorted(ALLOWED_HTTP_METHODS)} are."
        )

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method, f"{API_BASE}{endpoint}", params=params, json=json, timeout=timeout
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as exc:
            raise ExternalServiceError(
                f"Could not connect to the backend at {API_BASE}.", details={"endpoint": endpoint}
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"The backend request to {endpoint} failed: {exc}", details={"endpoint": endpoint}
            ) from exc
        except ValueError as exc:  # resp.json() found no valid JSON
            raise ExternalServiceError(
                f"The backend sent an answer to {endpoint} that is not JSON.", details={"endpoint": endpoint}
            ) from exc


async def post_to_backend(endpoint: str, payload: dict | None = None, timeout: float = 120.0) -> Any:
    """Send a POST request to the backend.

    Parameters
    ----------
    endpoint : `str`
        API endpoint path.
    payload : `dict`, optional
        JSON body. If `None` (default), an empty body is sent.
    timeout : `float`, optional
        Request timeout in seconds. Default is 120.0.

    Returns
    -------
    result : `Any`
        The parsed JSON answer. `request_backend` raises
        `ExternalServiceError` when the backend cannot answer.
    """
    return await request_backend("POST", endpoint, json=payload or {}, timeout=timeout)


async def get_from_backend(endpoint: str, params: dict | None = None, timeout: float = 120.0) -> Any:
    """Send a GET request to the backend.

    Parameters
    ----------
    endpoint : `str`
        API endpoint path.
    params : `dict`, optional
        Query parameters. If `None` (default), no query parameters
        are sent.
    timeout : `float`, optional
        Request timeout in seconds. Default is 120.0.

    Returns
    -------
    result : `Any`
        The parsed JSON answer. `request_backend` raises
        `ExternalServiceError` when the backend cannot answer.
    """
    return await request_backend("GET", endpoint, params=params or {}, timeout=timeout)
