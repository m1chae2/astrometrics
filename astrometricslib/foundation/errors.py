"""Purpose: One family of errors for the libraries, the backend, and agents.

Description: Every error that the code raises on purpose belongs to one of
the categories below. A category says what kind of problem happened and so
what the caller can do about it: fix the input, retry, check a device, or
check the disk. Adapters (the RPC router, the MCP servers) turn an error
into an `ErrorInfo`, a small record that travels as JSON to the user
interface or to an AI agent.

No category inherits from a built-in exception such as `ValueError` or
`KeyError`. Code that catches a category therefore never catches an error
that Python or a third-party library raised by accident.

Anything that is not an `AstrometricsError` is a bug. An adapter reports it
with the code ``internal``, a generic message, and a ``request_id`` that
points to the traceback in the log.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

#: JSON-RPC 2.0 error code for each error code. The codes between -32000
#: and -32099 are reserved by the standard for the application's own errors.
RPC_CODES: dict[str, int] = {
    "invalid_argument": -32602,
    "not_found": -32001,
    "conflict": -32002,
    "permission_denied": -32003,
    "configuration": -32004,
    "storage": -32005,
    "hardware": -32010,
    "external_service": -32011,
    "processing": -32012,
    "internal": -32603,
}


class AstrometricsError(Exception):
    """Base class of every error the code raises on purpose.

    Parameters
    ----------
    message : `str`
        One plain sentence that a user can read. It says what went wrong
        and, where possible, what to do about it.
    details : `dict` [`str`, `Any`], optional
        Facts about the error, such as the name of the target or the path
        of the file. Every value must be JSON-safe.
    retryable : `bool`, optional
        Whether the same call may succeed if tried again later. Defaults
        to the category's own default.

    Attributes
    ----------
    code : `str`
        The category's code. A subclass sets it.
    message : `str`
        The message.
    details : `dict` [`str`, `Any`]
        The details.
    retryable : `bool`
        Whether a retry may succeed.
    """

    code: str = "internal"
    default_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})
        self.retryable = self.default_retryable if retryable is None else retryable


class InvalidArgumentError(AstrometricsError):
    """The caller passed a bad value."""

    code = "invalid_argument"


class NotFoundError(AstrometricsError):
    """A named target, star, job, session, or file does not exist."""

    code = "not_found"


class ConflictError(AstrometricsError):
    """The request is valid, but the current state forbids it.

    Examples are a device that another program holds, a job that is already
    running, and a mount that is parked.
    """

    code = "conflict"


class PermissionDeniedError(AstrometricsError):
    """A policy forbids the action.

    Examples are the delegation policy, a read-only agent profile, and a path
    outside the allowed folders.
    """

    code = "permission_denied"


class ConfigurationError(AstrometricsError):
    """The configuration is missing or invalid."""

    code = "configuration"


class StorageError(AstrometricsError):
    """Disk or database trouble.

    Examples are storage that is not mounted, a file that cannot be read, and
    a database that is locked.
    """

    code = "storage"
    default_retryable = True


class HardwareError(AstrometricsError):
    """A device command failed or timed out."""

    code = "hardware"
    default_retryable = True


class ExternalServiceError(AstrometricsError):
    """Another program or service failed.

    Examples are Siril, the plate solver, SIMBAD (an online star database),
    and the observatory computer on the network.
    """

    code = "external_service"
    default_retryable = True


class ProcessingError(AstrometricsError):
    """The input was valid, but a pipeline could not produce a result from it.

    An example is a plate solve that found too few stars.
    """

    code = "processing"


class ErrorInfo(BaseModel):
    """The serializable form of an error.

    Every adapter sends this shape: the JSON-RPC ``error.data`` field, an MCP
    error result, a failed job record, and the failed items of a batch.

    Attributes
    ----------
    code : `str`
        The error code, one of the keys of `RPC_CODES`.
    message : `str`
        A sentence a user can read.
    details : `dict` [`str`, `Any`]
        Facts about the error.
    retryable : `bool`
        Whether the same call may succeed if tried again later.
    request_id : `str` or `None`
        The id of the call that failed. It also appears on every log line of
        that call.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    request_id: str | None = None

    @property
    def rpc_code(self) -> int:
        """The JSON-RPC error code for this error.

        Returns
        -------
        rpc_code : `int`
            The code from `RPC_CODES`. An unknown error code maps to the
            code for ``internal``.
        """
        return RPC_CODES.get(self.code, RPC_CODES["internal"])


def to_error_info(exc: BaseException, request_id: str | None = None) -> ErrorInfo:
    """Convert any exception to an `ErrorInfo`.

    An `AstrometricsError` keeps its own code, message, and details. A bare
    `ValueError` is reported as ``invalid_argument``, because code that has not
    moved to the error family yet raises it for a bad argument. Every other
    exception is a bug. It gets the code ``internal`` and a generic message,
    so no internal detail leaks to the caller.

    Parameters
    ----------
    exc : `BaseException`
        The exception to convert.
    request_id : `str`, optional
        The id of the call that failed.

    Returns
    -------
    info : `ErrorInfo`
        The serializable error.
    """
    if isinstance(exc, AstrometricsError):
        return ErrorInfo(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            retryable=exc.retryable,
            request_id=request_id,
        )
    if isinstance(exc, ValueError):
        return ErrorInfo(code="invalid_argument", message=str(exc), request_id=request_id)
    reference = f" Reference: {request_id}." if request_id else ""
    return ErrorInfo(
        code="internal",
        message=f"An internal error occurred.{reference}",
        request_id=request_id,
    )


def error_from_info(info: ErrorInfo) -> AstrometricsError:
    """Rebuild an error from an `ErrorInfo` that came over the wire.

    A client of the backend, such as an MCP server that calls the RPC
    route over HTTP, uses this to raise the same category the backend
    raised. The inverse is `to_error_info`.

    Parameters
    ----------
    info : `ErrorInfo`
        The error record.

    Returns
    -------
    error : `AstrometricsError`
        An instance of the category whose ``code`` matches, carrying the
        same message, details, and retry flag. An unknown code gives a
        plain `AstrometricsError`, whose code is ``internal``.
    """
    category = _CATEGORY_BY_CODE.get(info.code, AstrometricsError)
    return category(info.message, details=info.details, retryable=info.retryable)


_CATEGORY_BY_CODE: dict[str, type[AstrometricsError]] = {
    category.code: category
    for category in (
        InvalidArgumentError,
        NotFoundError,
        ConflictError,
        PermissionDeniedError,
        ConfigurationError,
        StorageError,
        HardwareError,
        ExternalServiceError,
        ProcessingError,
    )
}
