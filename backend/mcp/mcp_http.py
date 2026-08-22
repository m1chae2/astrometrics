"""Shared HTTP utilities for MCP tools and server."""

import os

import httpx

API_BASE = os.getenv("ASTROMETRICS_API_BASE", "http://localhost:5000")

# REQ: SEC-1.1: Restrict MCP tools to non-destructive HTTP methods.
ALLOWED_HTTP_METHODS = {"GET", "POST"}


async def post_to_backend(endpoint: str, payload: dict | None = None, timeout: float = 120.0):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Send a POST request to the high-level interface backend.

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
    result : `dict`
        Parsed JSON response on success, or a dictionary with
        ``"status"`` and ``"error"`` on failure.
    """
    if payload is None:
        payload = {}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{API_BASE}{endpoint}", json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"status": "error", "error": f"Could not connect to backend at {API_BASE}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}


async def get_from_backend(endpoint: str, params: dict | None = None, timeout: float = 120.0):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Send a GET request to the high-level interface backend.

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
    result : `dict`
        Parsed JSON response on success, or a dictionary with
        ``"status"`` and ``"error"`` on failure.
    """
    if params is None:
        params = {}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE}{endpoint}", params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"status": "error", "error": f"Could not connect to backend at {API_BASE}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}


async def request_backend(  # ruff: ignore[missing-return-type-undocumented-public-function]
    method: str, endpoint: str, params: dict | None = None, json: dict | None = None, timeout: float = 120.0
):
    """Proxy a generic HTTP request, enforcing a method whitelist.

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
    result : `dict`
        Parsed JSON response on success, or a dictionary with
        ``"status"`` and ``"error"`` on failure, including when
        `method` is not in `ALLOWED_HTTP_METHODS`.
    """
    if method.upper() not in ALLOWED_HTTP_METHODS:
        return {
            "status": "error",
            "error": (
                f"HTTP method '{method}' is not allowed from MCP tools. "
                f"Only {ALLOWED_HTTP_METHODS} are permitted."
            ),
        }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method, f"{API_BASE}{endpoint}", params=params, json=json, timeout=timeout
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"status": "error", "error": f"Could not connect to backend at {API_BASE}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
