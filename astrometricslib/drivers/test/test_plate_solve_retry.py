"""Tests for transient-network retry around online plate solving.

Regression coverage for a real loss: one M 13 session's reference frame
died on a dropped connection to nova.astrometry.net, so the session got
no WCS and all 100 of its stars were discarded for having no sky
position. The very next solve in the same run succeeded.

Also covers two local-solve time-saving guards added after a separate
M 13 incident: a single underexposed reference frame burned two full
five-minute local solve attempts (hinted, then blind) before falling
through to the online path, because nothing checked the detected star
count first or noticed the hinted attempt had already searched
exhaustively.
"""

import http.client

import pytest
from astropy.io import fits

from astrometricslib.drivers import astrometry_net_driver
from astrometricslib.drivers.astrometry_net_driver import (
    MINIMUM_SOURCES_FOR_LOCAL_SOLVE,
    ONLINE_SOLVE_ATTEMPT_LIMIT,
    AstrometryNetPlateSolveDriver,
    _call_with_transient_retry,
    _is_transient_network_error,
)


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Keep the backoff from making these tests wait for real seconds."""
    monkeypatch.setattr(astrometry_net_driver.time, "sleep", lambda _seconds: None)


def test_dropped_connection_is_transient():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The exact failure that cost M 13 a session must be retryable."""
    error = ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection'))")

    assert _is_transient_network_error(error) is True


def test_remote_disconnected_is_transient():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A bare RemoteDisconnected is a transport fault, not a solve verdict."""
    assert _is_transient_network_error(http.client.RemoteDisconnected("closed")) is True


def test_wrapped_transient_error_is_detected_through_the_cause_chain():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Astroquery re-wraps transport errors, so the cause chain is walked."""
    inner = http.client.RemoteDisconnected("closed")
    outer = RuntimeError("upload failed")
    outer.__cause__ = inner

    assert _is_transient_network_error(outer) is True


def test_unsolvable_field_is_not_transient():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A genuine "could not solve" verdict must not be retried."""
    assert _is_transient_network_error(Exception("could not solve field")) is False


def test_solve_job_timeout_is_not_transient():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A slow solve job is the field's fault, not the network's."""
    assert _is_transient_network_error(TimeoutError("solve timed out")) is False


def test_retry_recovers_after_a_dropped_connection():  # ruff: ignore[missing-return-type-undocumented-public-function]
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


def test_unsolvable_field_is_attempted_only_once():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Non-transient failures must not burn extra uploads (e.g. the Moon)."""
    attempts = []

    def solve_call():  # ruff: ignore[missing-return-type-private-function]
        attempts.append(1)
        raise RuntimeError("could not solve field")

    result = _call_with_transient_retry(solve_call, description="Online image solve")

    assert result is None
    assert len(attempts) == 1


def test_persistent_network_failure_stops_at_the_attempt_limit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A service that is genuinely down is not hammered indefinitely."""
    attempts = []

    def solve_call():  # ruff: ignore[missing-return-type-private-function]
        attempts.append(1)
        raise ConnectionError("connection refused")

    result = _call_with_transient_retry(solve_call, description="Online source solve")

    assert result is None
    assert len(attempts) == ONLINE_SOLVE_ATTEMPT_LIMIT


def test_successful_first_attempt_does_not_retry():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The happy path costs exactly one upload."""
    header = fits.Header()
    attempts = []

    def solve_call():  # ruff: ignore[missing-return-type-private-function]
        attempts.append(1)
        return header

    assert _call_with_transient_retry(solve_call, description="Online image solve") is header
    assert len(attempts) == 1


def test_solve_skips_the_local_solver_when_too_few_sources_are_detected(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A frame with too few detected stars never reaches the local solver.

    Regression test for the M 13 incident: a single underexposed
    reference frame had too few sources to ever solve, but the code
    tried the local solver anyway, burning two full five-minute
    timeouts before falling through to the (much faster) online path.
    """
    solver = AstrometryNetPlateSolveDriver(api_key="fake-key")
    local_solve_calls = []
    monkeypatch.setattr(solver, "_solve_locally", lambda *a, **k: local_solve_calls.append(1))
    header = fits.Header()
    header["CRVAL1"] = 250.4  # a non-empty header is truthy; an empty one is not
    monkeypatch.setattr(solver, "_solve_online_sources", lambda *a, **k: header)

    too_few_sources = [{"x_centroid": i, "y_centroid": i} for i in range(MINIMUM_SOURCES_FOR_LOCAL_SOLVE - 1)]
    result = solver.solve(
        image_path="/fake/image.fits", sources=too_few_sources, image_width=100, image_height=100
    )

    assert local_solve_calls == []
    assert result is header


def test_solve_still_tries_the_local_solver_with_enough_sources(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A normal, well-populated frame is unaffected by the new guard."""
    solver = AstrometryNetPlateSolveDriver(api_key="fake-key")
    header = fits.Header()
    header["CRVAL1"] = 250.4  # a non-empty header is truthy; an empty one is not
    local_solve_calls = []
    monkeypatch.setattr(solver, "_solve_locally", lambda *a, **k: local_solve_calls.append(1) or header)

    enough_sources = [{"x_centroid": i, "y_centroid": i} for i in range(MINIMUM_SOURCES_FOR_LOCAL_SOLVE)]
    result = solver.solve(
        image_path="/fake/image.fits", sources=enough_sources, image_width=100, image_height=100
    )

    assert local_solve_calls == [1]
    assert result is header


def test_hinted_solve_exhausting_its_timeout_skips_the_blind_retry(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A hinted solve that searched its full budget does not retry blind.

    Regression test for the M 13 incident: the hinted attempt used
    nearly all 300s without succeeding, so the blind retry (an even
    harder, unconstrained search) was always going to fail too --
    trying it anyway doubled the wasted time.
    """
    solver = AstrometryNetPlateSolveDriver()
    monotonic_values = iter([0.0, 295.0])
    monkeypatch.setattr(astrometry_net_driver.time, "monotonic", lambda: next(monotonic_values))
    run_solve_field_calls = []
    monkeypatch.setattr(
        solver,
        "_run_solve_field",
        lambda command, working_directory, timeout: run_solve_field_calls.append(command) or None,
    )

    result = solver._solve_locally(
        "/fake/image.fits", scale_units="arcsecperpix", scale_lower=1.0, scale_upper=2.0, solve_timeout=300
    )

    assert result is None
    assert len(run_solve_field_calls) == 1


def test_hinted_solve_failing_quickly_still_retries_blind(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A hinted solve that fails fast still gets the blind retry.

    Likely a hint mismatch, not an exhaustive search -- this is the
    real rescue case the blind retry exists for.
    """
    solver = AstrometryNetPlateSolveDriver()
    monotonic_values = iter([0.0, 5.0])
    monkeypatch.setattr(astrometry_net_driver.time, "monotonic", lambda: next(monotonic_values))
    run_solve_field_calls = []
    monkeypatch.setattr(
        solver,
        "_run_solve_field",
        lambda command, working_directory, timeout: run_solve_field_calls.append(command) or None,
    )

    result = solver._solve_locally(
        "/fake/image.fits", scale_units="arcsecperpix", scale_lower=1.0, scale_upper=2.0, solve_timeout=300
    )

    assert result is None
    assert len(run_solve_field_calls) == 2
