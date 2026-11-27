"""Purpose: One family of errors for the libraries, the backend, and agents.

Description: Every error that the code raises on purpose belongs to one of
the categories below. A category says what kind of problem happened and so
what the caller can do about it: fix the input, retry, check a device, or
check the disk. A program that serves the libraries to other programs
turns an error into an `ErrorInfo`, a small record that travels as JSON
to whoever made the call.

No category inherits from a built-in exception such as `ValueError` or
`KeyError`. Code that catches a category therefore never catches an error
that Python or a third-party library raised by accident.

Each category also says how an adapter shows the error (``report``) and how
loud its log line is (``log_level``):

* ``"status"``: something is offline or not reachable. The app shows it in a
  status chip, not as an error message, and logs it at ``INFO``.
* ``"error"``: an action the user asked for failed. The app shows an error
  message.
* ``"notify"``: the user must act on it. The app also sends a desktop
  notification.

One error can override both through its constructor, for example
``ExternalServiceError("The observatory computer is not reachable.",
report="status", log_level="INFO")``.

Errors raised by Python or by another package (`OSError`, a `ValueError`
from astropy) are wrapped into a category with `wrap_outside_error` before
they leave a library. Anything else that is not an `AstrometricsError` is a
bug. An adapter reports it with the code ``internal``, a generic message, and
a ``request_id`` that points to the traceback in the log.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
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

ErrorReport = Literal["status", "error", "notify"]
"""How an adapter shows an error: a status chip, an error message, or a
desktop notification as well."""

#: The allowed values of ``report``.
REPORTS: tuple[str, ...] = ("status", "error", "notify")

#: The allowed values of ``log_level``: the standard `logging` level names.
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _checked(value: str, allowed: tuple[str, ...], name: str) -> str:
    """Return a ``report`` or ``log_level`` value after checking it.

    Parameters
    ----------
    value : `str`
        The value given.
    allowed : `tuple` [`str`, ...]
        The allowed values.
    name : `str`
        The argument's name, for the message.

    Returns
    -------
    value : `str`
        The same value.

    Raises
    ------
    TypeError
        If the value is not allowed. This is a mistake in the code that
        raises the error, so it is a built-in error and not a category.
    """
    if value not in allowed:
        raise TypeError(f"{name} must be one of {', '.join(allowed)}; got {value!r}.")
    return value


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
    report : `str`, optional
        How an adapter shows this error: ``"status"``, ``"error"`` or
        ``"notify"``. Defaults to the category's own ``report``.
    log_level : `str`, optional
        The `logging` level name an adapter logs this error at, such as
        ``"INFO"``. Defaults to the category's own ``log_level``.

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
    report : `str`
        How an adapter shows the error. A subclass sets the default.
    log_level : `str`
        The level an adapter logs the error at. A subclass sets the default.
    """

    code: str = "internal"
    default_retryable: bool = False
    report: str = "error"
    log_level: str = "ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
        report: ErrorReport | None = None,
        log_level: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})
        self.retryable = self.default_retryable if retryable is None else retryable
        self.report = _checked(type(self).report if report is None else report, REPORTS, "report")
        self.log_level = _checked(
            type(self).log_level if log_level is None else log_level.upper(), LOG_LEVELS, "log_level"
        )

    @property
    def log_level_number(self) -> int:
        """The `logging` level number of ``log_level``.

        Returns
        -------
        level : `int`
            For example ``logging.INFO``.
        """
        return logging.getLevelNamesMapping()[self.log_level]


class InvalidArgumentError(AstrometricsError):
    """The caller passed a bad value."""

    code = "invalid_argument"
    log_level = "WARNING"


class NotFoundError(AstrometricsError):
    """A named target, star, job, session, or file does not exist.

    Asking for something that is not there is an answer, not a problem, so
    it is logged at ``INFO``. The caller decides what to show.
    """

    code = "not_found"
    log_level = "INFO"


class ConflictError(AstrometricsError):
    """The request is valid, but the current state forbids it.

    Examples are a device that another program holds, a job that is already
    running, and a mount that is parked.
    """

    code = "conflict"
    log_level = "WARNING"


class PermissionDeniedError(AstrometricsError):
    """A policy forbids the action.

    Examples are the delegation policy, a read-only agent profile, and a path
    outside the allowed folders.
    """

    code = "permission_denied"
    log_level = "WARNING"


class ConfigurationError(AstrometricsError):
    """The configuration is missing or invalid."""

    code = "configuration"
    log_level = "WARNING"


class ParameterUnavailableError(ConfigurationError):
    """A parameter that must not use its default has no value.

    Raised for a parameter that is set to refuse when its value is missing,
    such as a hardware limit.
    """


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
    report : `str`
        How to show the error: ``"status"``, ``"error"`` or ``"notify"``.
    log_level : `str`
        The `logging` level name to log the error at.
    request_id : `str` or `None`
        The id of the call that failed. It also appears on every log line of
        that call.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    report: str = "error"
    log_level: str = "ERROR"
    request_id: str | None = None

    @property
    def log_level_number(self) -> int:
        """The `logging` level number of ``log_level``.

        Returns
        -------
        level : `int`
            For example ``logging.WARNING``. An unknown name gives
            ``logging.ERROR``.
        """
        return logging.getLevelNamesMapping().get(self.log_level.upper(), logging.ERROR)

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

    An `AstrometricsError` keeps its own code, message, details, report and
    log level. A pydantic `ValidationError` means the caller sent a value
    that does not fit a model, so it is reported as ``invalid_argument`` with
    the validation problems in the details. An `OSError` is disk, file or
    network trouble: `wrap_outside_error` gives it its category, with a
    generic message, because its text can hold private paths. Every other
    exception, a bare `ValueError` included, is a bug. It gets the code
    ``internal`` and a generic message, so no internal detail leaks to the
    caller.

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
            report=exc.report,
            log_level=exc.log_level,
            request_id=request_id,
        )
    reference = f" Reference: {request_id}." if request_id else ""
    if isinstance(exc, OSError):
        kind = type(exc).__name__
        return to_error_info(
            wrap_outside_error(exc, message=f"A file, disk or network operation failed ({kind}).{reference}"),
            request_id,
        )
    if isinstance(exc, ValidationError):
        problems = [
            {"location": ".".join(str(part) for part in item["loc"]), "problem": item["msg"]}
            for item in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        return ErrorInfo(
            code="invalid_argument",
            message="A value does not fit the expected format.",
            details={"problems": problems},
            log_level=InvalidArgumentError.log_level,
            request_id=request_id,
        )
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
        same message, details, retry flag, report and log level. An unknown
        code gives a plain `AstrometricsError`, whose code is ``internal``.
    """
    category = _CATEGORY_BY_CODE.get(info.code, AstrometricsError)
    log_level = info.log_level.upper() if info.log_level.upper() in LOG_LEVELS else None
    report = info.report if info.report in REPORTS else None
    return category(
        info.message, details=info.details, retryable=info.retryable, report=report, log_level=log_level
    )


class MovedError(NotFoundError, AttributeError):
    """A public name was renamed or moved; the message says where to.

    For one release after a rename, reading an old method name on a sub-API
    object raises this error with "moved to ..." and the new call. It is an
    `AttributeError` as well, so `hasattr` and `getattr` with a default
    treat the old name as absent.

    Parameters
    ----------
    old_name : `str`
        The old qualified name, such as
        ``"astrometricslib.TargetCatalog.select_frames"``.
    new_call : `str`
        The call to use instead, such as
        ``"astrometrics.targets.frames.query(target)"``.
    """

    log_level = "WARNING"

    def __init__(self, old_name: str, new_call: str) -> None:
        super().__init__(
            f"{old_name} moved to {new_call}.", details={"old_name": old_name, "new_call": new_call}
        )
        self.old_name = old_name
        self.new_call = new_call

    @classmethod
    def lookup(cls, renames: dict[str, str], owner: type, name: str) -> MovedError | None:
        """Find the "moved to" error for a name read on an object.

        The table is keyed by ``"<library>.<class name>.<name>"`` for the
        object's class and each class it inherits from.

        Parameters
        ----------
        renames : `dict` [`str`, `str`]
            The rename table: old qualified name to new call text.
        owner : `type`
            The class of the object the name was read on.
        name : `str`
            The name that was read.

        Returns
        -------
        error : `MovedError` or `None`
            The error to raise, or `None` when the name was not renamed.
        """
        if name.startswith("__") or not renames:
            return None
        for klass in owner.__mro__:
            key = f"{klass.__module__.split('.')[0]}.{klass.__name__}.{name}"
            if key in renames:
                return cls(key, renames[key])
        return None


def wrap_outside_error(
    error: BaseException,
    *,
    category: type[AstrometricsError] | None = None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> AstrometricsError:
    """Wrap an error from Python or another package into a category.

    A library calls this where an outside error (an `OSError`, a
    `ValueError` from astropy) would leave it, and raises the result
    ``from`` the original, so the caller sees one family of errors.

    Parameters
    ----------
    error : `BaseException`
        The outside error. An `AstrometricsError` is returned unchanged.
    category : `type` [`AstrometricsError`], optional
        The category to use. Left out, it follows the error's type:
        `FileNotFoundError` is `NotFoundError`, `PermissionError` is
        `PermissionDeniedError`, `TimeoutError` and `ConnectionError` are
        `ExternalServiceError`, any other `OSError` is `StorageError`, a
        pydantic `ValidationError` is `InvalidArgumentError`, and anything
        else (a `ValueError` from a calculation, for example) is
        `ProcessingError`.
    message : `str`, optional
        The sentence to show. Defaults to the outside error's own text.
    details : `dict` [`str`, `Any`], optional
        Facts about the error. JSON-safe values only.

    Returns
    -------
    wrapped : `AstrometricsError`
        The category error, with the outside error as its ``__cause__``.
    """
    if isinstance(error, AstrometricsError):
        return error
    if category is None:
        category = _category_for_outside_error(error)
    text = message or str(error) or type(error).__name__
    wrapped = category(text, details=details)
    wrapped.__cause__ = error
    return wrapped


def _category_for_outside_error(error: BaseException) -> type[AstrometricsError]:
    """Choose the category for an outside error from its type.

    Parameters
    ----------
    error : `BaseException`
        The outside error.

    Returns
    -------
    category : `type` [`AstrometricsError`]
        The category, as `wrap_outside_error` describes.
    """
    if isinstance(error, FileNotFoundError):
        return NotFoundError
    if isinstance(error, PermissionError):
        return PermissionDeniedError
    if isinstance(error, TimeoutError | ConnectionError):
        return ExternalServiceError
    if isinstance(error, OSError):
        return StorageError
    if isinstance(error, ValidationError):
        return InvalidArgumentError
    return ProcessingError


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
