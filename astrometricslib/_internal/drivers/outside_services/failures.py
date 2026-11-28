"""Purpose: Tell what kind of trouble an outside request ran into.

Description: The clients (astroquery, pyvo, requests) report trouble in many
ways: a refused connection, a timeout, an HTTP error with a status and maybe a
``Retry-After`` header, or only a text message. `classify_failure` reads any of
them, through the chain of errors that caused it, and returns one `Failure`.
The gateway decides from it whether to ask another host, wait and ask again, or
pause the host.
"""

import contextlib
import email.utils
import enum
import http.client
import re
import socket
import time
from dataclasses import dataclass

from pyvo.dal.exceptions import DALQueryError, DALServiceError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.offline import OfflineModeError

#: Errors of the HTTP protocol itself, such as a reply cut off. Code outside
#: the gateway uses this tuple instead of importing ``http.client``.
HTTP_PROTOCOL_ERRORS: tuple[type[BaseException], ...] = (http.client.HTTPException,)

#: Errors of a connection dropped in the middle of a reply, for code that
#: retries those.
DROPPED_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    http.client.BadStatusLine,
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
)


class ServiceTimeoutError(ExternalServiceError):
    """The gateway stopped waiting for an answer."""


class ServicePausedError(ExternalServiceError):
    """The service is paused after trouble, so nothing was sent.

    It is a status, not a fault: callers use a stored answer, or carry on
    without one, and log a warning.
    """

    report = "status"
    log_level = "WARNING"


class FailureKind(enum.Enum):
    """The kinds of trouble the gateway tells apart."""

    RATE_LIMITED = "rate limited"  # HTTP 429
    BUSY = "busy"  # HTTP 503, or a TAP "too busy" reply
    SERVER_ERROR = "server error"  # another HTTP 5xx, or a reply cut off
    # HTTP 404 or 410 from a query endpoint: the host is up but no longer
    # serves the address. It is a kind of its own, not SERVER_ERROR, because
    # it does not clear in seconds, so the gateway pauses the host at once.
    ENDPOINT_MISSING = "endpoint missing"
    TIMEOUT = "timeout"
    CONNECTION = "connection"  # refused, reset, no route, name not found
    OTHER = "other"  # the server answered and the answer was bad


@dataclass(frozen=True)
class Failure:
    """One classified failure.

    Attributes
    ----------
    kind : `FailureKind`
        What happened.
    status : `int` or `None`
        The HTTP status, when one was found.
    retry_after_seconds : `float` or `None`
        The wait the server asked for, when it gave one.
    """

    kind: FailureKind
    status: int | None = None
    retry_after_seconds: float | None = None

    @property
    def host_trouble(self) -> bool:
        """Say whether another host holding the same data may do better.

        Returns
        -------
        trouble : `bool`
            `False` only for a reply that arrived and was bad: the other host
            would answer the same.
        """
        return self.kind is not FailureKind.OTHER


_STATUS_IN_TEXT = re.compile(r"\b(?:HTTP|Error|status|code)\D{0,3}(\d{3})\b", re.IGNORECASE)


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Read a ``Retry-After`` header value.

    Parameters
    ----------
    value : `str` or `None`
        The header: whole seconds, or an HTTP date.
    now : `float`, optional
        The time to measure a date from; the present by default.

    Returns
    -------
    seconds : `float` or `None`
        The wait, or `None` when there is no usable header.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        return float(text)
    with contextlib.suppress(TypeError, ValueError):
        moment = email.utils.parsedate_to_datetime(text)
        return max(0.0, moment.timestamp() - (time.time() if now is None else now))
    return None


def _kind_for_status(status: int) -> FailureKind | None:
    """Map an HTTP status to a kind.

    Returns
    -------
    kind : `FailureKind` or `None`
        `None` for a status that is not trouble with the host. A 404 or 410
        is mapped here; `treat_missing_as_answer` turns it back into an
        ordinary reply for calls where it means "no such object".
    """
    if status == 429:
        return FailureKind.RATE_LIMITED
    if status == 503:
        return FailureKind.BUSY
    if status in (404, 410):
        return FailureKind.ENDPOINT_MISSING
    if status >= 500:
        return FailureKind.SERVER_ERROR
    return None


def treat_missing_as_answer(failure: Failure) -> Failure:
    """Read a 404 or 410 as a real answer, "there is no such thing".

    Some calls ask for one object or file (a Gaia spectrum for one star, a
    download). A 404 there is the host answering correctly, so the other host
    would answer the same and the host must not be paused.

    Parameters
    ----------
    failure : `Failure`
        A classified failure.

    Returns
    -------
    failure : `Failure`
        The same failure, or an `FailureKind.OTHER` one when it was a missing
        endpoint.
    """
    if failure.kind is FailureKind.ENDPOINT_MISSING:
        return Failure(FailureKind.OTHER, failure.status, failure.retry_after_seconds)
    return failure


def classify_failure(error: BaseException) -> Failure:
    """Work out what kind of trouble an error means.

    The error and its causes are read in turn, and the most specific finding
    wins: an HTTP status first, then a timeout, then a refused connection.

    Parameters
    ----------
    error : `BaseException`
        What a request raised.

    Returns
    -------
    failure : `Failure`
        The classification.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    status: int | None = None
    retry_after: float | None = None
    kind: FailureKind | None = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if isinstance(current, HTTPError | DALServiceError) or response is not None:
            code = getattr(response, "status_code", None) or getattr(current, "code", None)
            if isinstance(code, int) and status is None:
                status = code
                headers = getattr(response, "headers", None) or {}
                retry_after = parse_retry_after(headers.get("Retry-After"))
        if isinstance(current, DALQueryError) and "too busy" in str(current).lower():
            kind = kind or FailureKind.BUSY
        if isinstance(current, ServiceTimeoutError | Timeout | TimeoutError):
            kind = kind or FailureKind.TIMEOUT
        elif isinstance(current, OfflineModeError):
            pass
        elif isinstance(current, RequestsConnectionError | ConnectionError | socket.gaierror):
            kind = kind or FailureKind.CONNECTION
        elif (
            isinstance(current, DALServiceError)
            and not isinstance(current, DALQueryError)
            and current.code is None
        ):
            kind = kind or FailureKind.CONNECTION
        elif isinstance(current, HTTP_PROTOCOL_ERRORS):
            kind = kind or FailureKind.SERVER_ERROR
        if status is None and isinstance(current, HTTPError | RuntimeError | DALServiceError):
            match = _STATUS_IN_TEXT.search(str(current))
            if match:
                status = int(match.group(1))
        current = current.__cause__ or current.__context__
    from_status = _kind_for_status(status) if status is not None else None
    return Failure(from_status or kind or FailureKind.OTHER, status, retry_after)
