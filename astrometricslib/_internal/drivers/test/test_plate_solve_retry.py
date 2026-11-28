"""Tests for transient-network retry around online plate solving.

Regression coverage for a real loss: one M 13 session's reference frame
died on a dropped connection to nova.astrometry.net, so the session got
no WCS and all 100 of its stars were discarded for having no sky
position. The very next solve in the same run succeeded.

"""

import http.client
from typing import Never

import pytest
from astropy.io import fits

from astrometricslib._internal.drivers import astrometry_net_client
from astrometricslib._internal.drivers.astrometry_net_client import (
    ONLINE_SOLVE_ATTEMPT_LIMIT,
    _call_with_transient_retry,
    _is_transient_network_error,
)


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the backoff from making these tests wait for real seconds."""
    monkeypatch.setattr(astrometry_net_client.time, "sleep", lambda _seconds: None)


def test_dropped_connection_is_transient() -> None:
    """The exact failure that cost M 13 a session must be retryable."""
    error = ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection'))")

    assert _is_transient_network_error(error) is True


def test_remote_disconnected_is_transient() -> None:
    """A bare RemoteDisconnected is a transport fault, not a solve verdict."""
    assert _is_transient_network_error(http.client.RemoteDisconnected("closed")) is True


def test_wrapped_transient_error_is_detected_through_the_cause_chain() -> None:
    """Astroquery re-wraps transport errors, so the cause chain is walked."""
    inner = http.client.RemoteDisconnected("closed")
    outer = RuntimeError("upload failed")
    outer.__cause__ = inner

    assert _is_transient_network_error(outer) is True


def test_unsolvable_field_is_not_transient() -> None:
    """A genuine "could not solve" verdict must not be retried."""
    assert _is_transient_network_error(Exception("could not solve field")) is False


def test_solve_job_timeout_is_not_transient() -> None:
    """A slow solve job is the field's fault, not the network's."""
    assert _is_transient_network_error(TimeoutError("solve timed out")) is False


def test_retry_recovers_after_a_dropped_connection() -> None:
    """One transient failure followed by success returns the solved header."""
    header = fits.Header()
    header["CRVAL1"] = 250.4
    attempts = []

    def solve_call():  # ruff: ignore[missing-return-type-private-function]
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("('Connection aborted.', RemoteDisconnected())")
        return header

    result = _call_with_transient_retry(solve_call, description="Online image solve")

    assert result is header
    assert len(attempts) == 2


def test_unsolvable_field_is_attempted_only_once() -> None:
    """Non-transient failures must not burn extra uploads (e.g. the Moon)."""
    attempts = []

    def solve_call() -> Never:
        attempts.append(1)
        raise RuntimeError("could not solve field")

    result = _call_with_transient_retry(solve_call, description="Online image solve")

    assert result is None
    assert len(attempts) == 1


def test_persistent_network_failure_stops_at_the_attempt_limit() -> None:
    """A service that is genuinely down is not hammered indefinitely."""
    attempts = []

    def solve_call() -> Never:
        attempts.append(1)
        raise ConnectionError("connection refused")

    result = _call_with_transient_retry(solve_call, description="Online source solve")

    assert result is None
    assert len(attempts) == ONLINE_SOLVE_ATTEMPT_LIMIT


def test_successful_first_attempt_does_not_retry() -> None:
    """The happy path costs exactly one upload."""
    header = fits.Header()
    attempts = []

    def solve_call():  # ruff: ignore[missing-return-type-private-function]
        attempts.append(1)
        return header

    assert _call_with_transient_retry(solve_call, description="Online image solve") is header
    assert len(attempts) == 1


def test_each_upload_is_counted_in_the_runs_own_tally() -> None:
    """Retries count; each run keeps its own total."""
    from astrometricslib._internal.drivers.astrometry_net_client import bind_plate_solve_attempts
    from astrometricslib.foundation.run_tally import RunTally

    calls = []

    def dropped_once():  # ruff: ignore[missing-return-type-private-function]
        calls.append(1)
        if len(calls) % 2 == 1:
            raise ConnectionError("connection refused")
        return fits.Header()

    first, second = RunTally(), RunTally()
    _call_with_transient_retry(dropped_once, description="not counted")
    with bind_plate_solve_attempts(first):
        _call_with_transient_retry(dropped_once, description="two uploads")
    with bind_plate_solve_attempts(second):
        _call_with_transient_retry(dropped_once, description="two uploads")

    assert first.total == pytest.approx(2)
    assert second.total == pytest.approx(2)
