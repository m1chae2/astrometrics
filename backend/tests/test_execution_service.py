"""Tests for the Observation Execution backend adapter.

These cover the seam that had no route into the application at all before:
wayfindinglib's third root function was reachable from no RPC namespace, so
session abort and reconciliation were library-only capabilities.
"""

from datetime import date

import pytest

from backend.services.observatory.execution_service import ExecutionService


@pytest.fixture
def execution_service():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Build an ExecutionService against the sandboxed test config.

    Returns
    -------
    service : `ExecutionService`
        A service wired to the temporary library the test suite configures.
    """
    from astrometricslib import Astrometrics, get_configuration
    from wayfindinglib import Wayfinder

    config = get_configuration()
    return ExecutionService(wayfinder=Wayfinder(config), astrometrics=Astrometrics(config), config=config)


def _record_session(service: ExecutionService, session_id: str):  # ruff: ignore[missing-return-type-private-function]
    """Store a minimal observation session.

    Returns
    -------
    session : `ObservationSession`
        The session that was recorded.
    """
    from wayfindinglib.models.session.observation_session import ObservationSession

    session = ObservationSession(
        id=session_id,
        night_date=date(2026, 8, 7),
        site_profile_id="test-site",
        telescope_id="test-scope",
        camera_id="test-camera",
    )
    service._butler.put(session, "observation_session", {"session_id": session_id})
    return session


def test_list_sessions_is_empty_before_anything_is_stored(execution_service):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A fresh library reports no sessions rather than failing."""
    assert execution_service.list_sessions() == []


def test_stored_session_is_listed_and_retrievable(execution_service):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A recorded session round-trips through list and get."""
    _record_session(execution_service, "session-round-trip")

    summaries = execution_service.list_sessions()
    assert any(entry["id"] == "session-round-trip" for entry in summaries)

    session = execution_service.get_session("session-round-trip")
    assert session["id"] == "session-round-trip"
    assert session["camera_id"] == "test-camera"


def test_missing_session_raises_rather_than_returning_none(execution_service):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """An unknown id must be an explicit error, not a silent null."""
    with pytest.raises(ValueError, match="No observation session found"):
        execution_service.get_session("does-not-exist")


def test_abort_session_marks_it_aborted(execution_service):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Aborting reaches the high-level interface and changes session status."""
    _record_session(execution_service, "session-to-abort")

    aborted = execution_service.abort_session("session-to-abort", "clouds rolled in")

    assert aborted["id"] == "session-to-abort"
    assert "abort" in str(aborted["status"]).lower()


def test_abort_of_missing_session_raises(execution_service):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Abort validates the session exists before calling the interface."""
    with pytest.raises(ValueError, match="No observation session found"):
        execution_service.abort_session("does-not-exist", "reason")
