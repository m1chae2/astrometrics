"""Tests for the shared error family and its JSON form.

Also covers each error's report and log level, `MovedError`, and the
wrapping of outside errors.
"""

import ast
import json
import logging
from pathlib import Path

import pytest

from astrometricslib.foundation.errors import (
    LOG_LEVELS,
    REPORTS,
    RPC_CODES,
    AstrometricsError,
    ConfigurationError,
    ConflictError,
    ErrorInfo,
    ExternalServiceError,
    HardwareError,
    InvalidArgumentError,
    MovedError,
    NotFoundError,
    PermissionDeniedError,
    ProcessingError,
    StorageError,
    error_from_info,
    to_error_info,
    wrap_outside_error,
)

CATEGORIES = [
    InvalidArgumentError,
    NotFoundError,
    ConflictError,
    PermissionDeniedError,
    StorageError,
    HardwareError,
    ExternalServiceError,
    ProcessingError,
]


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_has_a_distinct_rpc_code(category: type[AstrometricsError]) -> None:
    """Each category's code appears in the table, so adapters can map it."""
    assert category.code in RPC_CODES
    assert issubclass(category, AstrometricsError)


def test_codes_are_unique() -> None:
    """No two error codes share a JSON-RPC code."""
    assert len(set(RPC_CODES.values())) == len(RPC_CODES)


def test_no_category_is_a_builtin_error() -> None:
    """A category never inherits `ValueError`, `KeyError`, or `LookupError`."""
    for category in CATEGORIES:
        assert not issubclass(category, (ValueError, KeyError, LookupError, OSError, RuntimeError))


def test_an_error_keeps_its_message_details_and_retry_flag() -> None:
    """The constructor stores what the adapters read."""
    error = NotFoundError("No target 'M 99'.", details={"target": "M 99"})
    assert error.message == "No target 'M 99'."
    assert error.details == {"target": "M 99"}
    assert error.retryable is False
    assert str(error) == "No target 'M 99'."


def test_retryable_defaults_by_category_and_can_be_overridden() -> None:
    """Hardware errors are retryable by default; an argument error is not."""
    assert HardwareError("mount offline").retryable is True
    assert InvalidArgumentError("bad").retryable is False
    assert HardwareError("mount parked", retryable=False).retryable is False


def test_to_error_info_keeps_a_category_error() -> None:
    """An `AstrometricsError` converts without loss."""
    info = to_error_info(ConflictError("Device in use.", details={"device": "mount"}), request_id="abc123")
    assert info.code == "conflict"
    assert info.message == "Device in use."
    assert info.details == {"device": "mount"}
    assert info.request_id == "abc123"
    assert info.rpc_code == RPC_CODES["conflict"]


@pytest.mark.parametrize(
    "exc", [KeyError("secret"), RuntimeError("secret path /home/x"), ValueError("secret")]
)
def test_to_error_info_hides_the_text_of_a_bug(exc: BaseException) -> None:
    """Report any other exception as `internal`, with the request id."""
    info = to_error_info(exc, request_id="req42")
    assert info.code == "internal"
    assert "secret" not in info.message
    assert "disk" not in info.message
    assert "req42" in info.message
    assert info.rpc_code == -32603


def test_to_error_info_reports_a_validation_error_as_invalid_argument() -> None:
    """A pydantic `ValidationError` is a bad caller value, not a bug."""
    from pydantic import BaseModel, ValidationError

    class Point(BaseModel):
        """A model with one required number."""

        x: float

    with pytest.raises(ValidationError) as caught:
        Point(x="not a number")
    info = to_error_info(caught.value, request_id="req7")
    assert info.code == "invalid_argument"
    assert info.details["problems"][0]["location"] == "x"
    assert info.request_id == "req7"


@pytest.mark.parametrize("category", CATEGORIES)
def test_error_from_info_rebuilds_the_same_category(category: type[AstrometricsError]) -> None:
    """`error_from_info` undoes `to_error_info` for every category."""
    original = category("Something went wrong.", details={"target": "M31"}, retryable=True)

    rebuilt = error_from_info(to_error_info(original))

    assert type(rebuilt) is category
    assert rebuilt.message == "Something went wrong."
    assert rebuilt.details == {"target": "M31"}
    assert rebuilt.retryable is True


def test_error_from_info_gives_internal_for_an_unknown_code() -> None:
    """An unknown code rebuilds as a plain `AstrometricsError`."""
    rebuilt = error_from_info(ErrorInfo(code="made_up", message="x"))
    assert type(rebuilt) is AstrometricsError
    assert rebuilt.code == "internal"


def test_error_info_serializes_with_camel_case_keys() -> None:
    """The JSON form uses the camelCase names the UI reads."""
    info = ErrorInfo(code="hardware", message="Mount offline.", retryable=True, request_id="r1")
    data = json.loads(info.model_dump_json(by_alias=True))
    assert data == {
        "code": "hardware",
        "message": "Mount offline.",
        "details": {},
        "retryable": True,
        "report": "error",
        "logLevel": "ERROR",
        "requestId": "r1",
    }


def test_an_unknown_code_maps_to_the_internal_rpc_code() -> None:
    """An unrecognized code never produces a missing RPC code."""
    assert ErrorInfo(code="made_up", message="x").rpc_code == RPC_CODES["internal"]


def _all_subclasses(base: type[AstrometricsError]) -> set[type[AstrometricsError]]:
    """Collect every direct and indirect subclass of an error class.

    Parameters
    ----------
    base : `type` [`AstrometricsError`]
        The class to start from.

    Returns
    -------
    subclasses : `set` [`type` [`AstrometricsError`]]
        All subclasses found so far in the running program.
    """
    found: set[type[AstrometricsError]] = set()
    for subclass in base.__subclasses__():
        found.add(subclass)
        found |= _all_subclasses(subclass)
    return found


def test_every_error_class_maps_to_an_rpc_code() -> None:
    """Map each error class, including the libraries' own, to a JSON-RPC code.

    A library that adds an error class without a code of a known category
    would leave the adapters unable to report it. Importing both libraries
    first makes their classes visible.
    """
    import astrometricslib.exceptions
    import wayfindinglib.data_access.delegation_policy_reader  # ruff: ignore[unused-import]

    subclasses = _all_subclasses(AstrometricsError)
    assert astrometricslib.exceptions.PlateSolveFailedError in subclasses
    for subclass in subclasses:
        assert subclass.code in RPC_CODES, f"{subclass.__name__} has an unknown code {subclass.code!r}"


@pytest.mark.parametrize("category", [*CATEGORIES, ConfigurationError, AstrometricsError])
def test_every_category_says_how_it_is_reported_and_logged(category: type[AstrometricsError]) -> None:
    """Each category has a known ``report`` and ``log_level``."""
    assert category.report in REPORTS
    assert category.log_level in LOG_LEVELS


@pytest.mark.parametrize(
    ("category", "log_level"),
    [
        (InvalidArgumentError, "WARNING"),
        (NotFoundError, "INFO"),
        (ConflictError, "WARNING"),
        (PermissionDeniedError, "WARNING"),
        (ConfigurationError, "WARNING"),
        (StorageError, "ERROR"),
        (HardwareError, "ERROR"),
        (ExternalServiceError, "ERROR"),
        (ProcessingError, "ERROR"),
    ],
)
def test_a_failed_action_is_an_error_at_its_category_level(
    category: type[AstrometricsError], log_level: str
) -> None:
    """A category that means "the action failed" is reported as an error."""
    error = category("It failed.")
    assert error.report == "error"
    assert error.log_level == log_level


def test_one_error_can_override_its_report_and_log_level() -> None:
    """The constructor keywords override the category's defaults."""
    error = ExternalServiceError(
        "The observatory computer is not reachable.", report="status", log_level="info"
    )
    assert error.report == "status"
    assert error.log_level == "INFO"
    assert error.log_level_number == logging.INFO
    assert ExternalServiceError("x").report == "error"


def test_an_unknown_report_or_log_level_is_a_coding_mistake() -> None:
    """A value outside the allowed lists raises `TypeError` at once."""
    with pytest.raises(TypeError, match="report"):
        StorageError("x", report="loud")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="log_level"):
        StorageError("x", log_level="SHOUT")


def test_report_and_log_level_travel_through_error_info() -> None:
    """`to_error_info` and `error_from_info` keep both values."""
    original = HardwareError("Mount not connected.", report="status", log_level="INFO")
    info = to_error_info(original)
    assert (info.report, info.log_level) == ("status", "INFO")
    assert info.log_level_number == logging.INFO
    rebuilt = error_from_info(info)
    assert (rebuilt.report, rebuilt.log_level) == ("status", "INFO")


def test_an_error_info_with_unknown_values_rebuilds_with_the_defaults() -> None:
    """A record from an older or newer sender still rebuilds."""
    rebuilt = error_from_info(ErrorInfo(code="storage", message="x", report="odd", log_level="LOUD"))
    assert (rebuilt.report, rebuilt.log_level) == ("error", "ERROR")


@pytest.mark.parametrize(
    ("outside", "category"),
    [
        (FileNotFoundError("/x/y.fits"), NotFoundError),
        (PermissionError("/x"), PermissionDeniedError),
        (TimeoutError("slow"), ExternalServiceError),
        (ConnectionRefusedError("no"), ExternalServiceError),
        (OSError("disk full"), StorageError),
        (ValueError("bad WCS"), ProcessingError),
    ],
)
def test_wrap_outside_error_chooses_the_category_from_the_type(
    outside: BaseException, category: type[AstrometricsError]
) -> None:
    """Each outside error type maps to one category, keeping the cause."""
    wrapped = wrap_outside_error(outside)
    assert type(wrapped) is category
    assert wrapped.__cause__ is outside
    assert wrapped.message == str(outside)


def test_wrap_outside_error_takes_a_category_message_and_details() -> None:
    """A caller can name the category and the sentence to show."""
    cause = ValueError("could not convert")
    wrapped = wrap_outside_error(
        cause, category=InvalidArgumentError, message="ra_deg must be a number.", details={"ra_deg": "x"}
    )
    assert isinstance(wrapped, InvalidArgumentError)
    assert wrapped.message == "ra_deg must be a number."
    assert wrapped.details == {"ra_deg": "x"}


def test_wrap_outside_error_keeps_a_category_error() -> None:
    """An `AstrometricsError` is already wrapped."""
    error = ConflictError("busy")
    assert wrap_outside_error(error) is error


def test_to_error_info_wraps_an_os_error_without_its_text() -> None:
    """An `OSError` gets its category but not its text (it can hold paths)."""
    info = to_error_info(FileNotFoundError("/home/secret/frame.fits"), request_id="r9")
    assert info.code == "not_found"
    assert "secret" not in info.message
    assert "r9" in info.message
    assert to_error_info(OSError("disk")).code == "storage"


def test_moved_error_is_an_attribute_error_with_a_moved_to_message() -> None:
    """`MovedError` says where a name went and counts as a missing name."""
    error = MovedError("astrometricslib.TargetCatalog.old", "astrometrics.targets.new(target)")
    assert isinstance(error, AttributeError)
    assert isinstance(error, NotFoundError)
    assert error.report == "error"
    assert "moved to astrometrics.targets.new(target)" in error.message
    assert error.details == {
        "old_name": "astrometricslib.TargetCatalog.old",
        "new_call": "astrometrics.targets.new(target)",
    }


def test_moved_error_lookup_follows_the_class_and_its_bases() -> None:
    """A name moved on a base class is found from a subclass."""

    class Base:
        """A stand-in sub-API base class."""

    class Child(Base):
        """A stand-in sub-API class."""

    renames = {f"{Base.__module__.split('.')[0]}.Base.old": "new()"}
    found = MovedError.lookup(renames, Child, "old")
    assert found is not None
    assert found.new_call == "new()"
    assert MovedError.lookup(renames, Child, "other") is None
    assert MovedError.lookup({}, Child, "old") is None


def test_every_error_class_of_both_libraries_is_a_category() -> None:
    """Every public error class of both libraries is an `AstrometricsError`.

    Private control-flow signals (a name that starts with ``_``) never leave
    their module, so they are not checked.
    """
    offenders = []
    for root in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[3] / "wayfindinglib"):
        for path in root.rglob("*.py"):
            if "test" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                    continue
                bases = {
                    base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                    for base in node.bases
                }
                # A category may sit beside a built-in base (an
                # `OfflineModeError` is also a `ConnectionError`), but a class
                # whose only bases are built-in errors is outside the family.
                if bases and bases <= _BUILT_IN_ERRORS:
                    offenders.append(f"{path.relative_to(root.parent).as_posix()}:{node.name}")
    allowed = {"astrometricslib/foundation/errors.py:AstrometricsError"}
    assert set(offenders) <= allowed, (
        f"error classes outside the categories: {sorted(set(offenders) - allowed)}"
    )


_BUILT_IN_ERRORS = frozenset({
    "Exception",
    "BaseException",
    "ValueError",
    "TypeError",
    "KeyError",
    "LookupError",
    "RuntimeError",
    "OSError",
    "IOError",
    "ConnectionError",
    "TimeoutError",
})
"""Built-in exception classes a library error class must not inherit alone."""
