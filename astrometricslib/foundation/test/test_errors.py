"""Tests for the shared error family and its JSON form."""

import json

import pytest

from astrometricslib.foundation.errors import (
    RPC_CODES,
    AstrometricsError,
    ConflictError,
    ErrorInfo,
    ExternalServiceError,
    HardwareError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    ProcessingError,
    StorageError,
    to_error_info,
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


def test_to_error_info_reports_a_bare_value_error_as_an_invalid_argument() -> None:
    """Report a bare `ValueError` as an invalid argument."""
    info = to_error_info(ValueError("detail must be one of: summary"))
    assert info.code == "invalid_argument"
    assert info.message == "detail must be one of: summary"


@pytest.mark.parametrize("exc", [KeyError("secret"), RuntimeError("secret path /home/x"), OSError("disk")])
def test_to_error_info_hides_the_text_of_a_bug(exc: BaseException) -> None:
    """Report any other exception as `internal`, with the request id."""
    info = to_error_info(exc, request_id="req42")
    assert info.code == "internal"
    assert "secret" not in info.message
    assert "disk" not in info.message
    assert "req42" in info.message
    assert info.rpc_code == -32603


def test_error_info_serializes_with_camel_case_keys() -> None:
    """The JSON form uses the camelCase names the UI reads."""
    info = ErrorInfo(code="hardware", message="Mount offline.", retryable=True, request_id="r1")
    data = json.loads(info.model_dump_json(by_alias=True))
    assert data == {
        "code": "hardware",
        "message": "Mount offline.",
        "details": {},
        "retryable": True,
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
    import astrometricslib
    import wayfindinglib.data_access.delegation_policy_reader  # ruff: ignore[unused-import]

    subclasses = _all_subclasses(AstrometricsError)
    assert astrometricslib.PlateSolveFailedError in subclasses
    for subclass in subclasses:
        assert subclass.code in RPC_CODES, f"{subclass.__name__} has an unknown code {subclass.code!r}"
