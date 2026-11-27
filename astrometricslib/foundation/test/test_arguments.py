"""Tests for the argument rules both libraries share.

The rules are in `astrometricslib.foundation.arguments` and public as
`astrometricslib.arguments`. Each refuses a bad value with
`InvalidArgumentError`, never by quietly changing it.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from astropy.time import Time

import astrometricslib.arguments as public_arguments
from astrometricslib.foundation.arguments import (
    check_choice,
    check_include,
    check_limit,
    reject_unused_arguments,
    to_epoch_seconds,
)
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.utilities.observing_night import observing_night_id


def test_check_choice_accepts_a_listed_value_and_refuses_another() -> None:
    """A value outside the choices names the choices in its details."""
    check_choice("kind", "imaging", ("imaging", "spectral"))
    with pytest.raises(InvalidArgumentError) as caught:
        check_choice("kind", "radio", ("imaging", "spectral"))
    assert caught.value.details == {"kind": "radio", "choices": ["imaging", "spectral"]}


def test_check_include_returns_the_parts_asked_for() -> None:
    """`None` means no parts; a list gives its parts as a set."""
    assert check_include(None, ("frames",)) == frozenset()
    assert check_include(["frames"], ("frames", "spectra")) == frozenset({"frames"})


@pytest.mark.parametrize("include", ["frames", ["nope"]])
def test_check_include_refuses_one_string_or_an_unknown_part(include: object) -> None:
    """A bare string or an unknown part is refused."""
    with pytest.raises(InvalidArgumentError):
        check_include(include, ("frames",))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 50, None])
def test_check_limit_accepts_a_count_in_range(value: int | None) -> None:
    """A count in range, or `None`, passes."""
    check_limit(value, 0, 50)


@pytest.mark.parametrize("value", [-1, 51, 2.5, True, "3"])
def test_check_limit_refuses_and_never_clamps(value: object) -> None:
    """A count out of range or not a whole number raises."""
    with pytest.raises(InvalidArgumentError):
        check_limit(value, 0, 50)  # type: ignore[arg-type]


def test_check_limit_can_refuse_none() -> None:
    """With ``allow_none=False`` a missing count is refused."""
    with pytest.raises(InvalidArgumentError, match="lines"):
        check_limit(None, 1, None, name="lines", allow_none=False)


def test_reject_unused_arguments_names_the_unused_ones() -> None:
    """An argument the kind does not use is refused by name."""
    reject_unused_arguments("growth", {"growth": ("steps",)}, {"steps": True, "halves": False})
    with pytest.raises(InvalidArgumentError, match="halves"):
        reject_unused_arguments("growth", {"growth": ("steps",)}, {"halves": True})


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-04T03:30:00Z",
        "2026-10-03T21:30:00-06:00",
        "2026-10-04T03:30:00",
        datetime(2026, 10, 4, 3, 30, tzinfo=UTC),
        datetime(2026, 10, 3, 21, 30, tzinfo=timezone(timedelta(hours=-6))),
        datetime(2026, 10, 4, 3, 30),
        Time("2026-10-04T03:30:00", scale="utc"),
    ],
)
def test_to_epoch_seconds_reads_every_time_form_and_takes_no_zone_as_utc(value: object) -> None:
    """Text, `datetime` and `Time` give the same moment; no zone is UTC."""
    expected = datetime(2026, 10, 4, 3, 30, tzinfo=UTC).timestamp()
    assert to_epoch_seconds(value) == pytest.approx(expected)  # type: ignore[arg-type]


def test_to_epoch_seconds_gives_none_for_no_time_and_refuses_bad_text() -> None:
    """`None` and empty text are no time; unreadable text raises."""
    assert to_epoch_seconds(None) is None
    assert to_epoch_seconds("  ") is None
    with pytest.raises(InvalidArgumentError, match="since"):
        to_epoch_seconds("last tuesday", "since")
    with pytest.raises(InvalidArgumentError):
        to_epoch_seconds(42)  # type: ignore[arg-type]


def test_time_input_does_not_include_none() -> None:
    """`TimeInput` is a time; an optional one is ``TimeInput | None``."""
    assert type(None) not in public_arguments.TimeInput.__args__


def test_the_public_module_offers_the_owner_functions() -> None:
    """`astrometricslib.arguments` re-exports the owners, not copies."""
    assert public_arguments.check_limit is check_limit
    assert public_arguments.to_epoch_seconds is to_epoch_seconds
    assert public_arguments.observing_night_id is observing_night_id
