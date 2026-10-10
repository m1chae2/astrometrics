"""Tests for the observing-session RPC service.

These cover the path from the session RPC methods to wayfindinglib: the
session list and session view read through `planning.get_plan`, and abort
reaches `execution.abort_session`, which records the aborted session.
"""

from datetime import date

import pytest

from astrometricslib import NotFoundError
from backend.services.observatory.execution_service import ExecutionService


@pytest.fixture
def execution_service(config_in_tmp_path: object) -> ExecutionService:
    """Build an ExecutionService over a library of its own.

    Each test gets a new, empty library, so a session one test stores cannot
    show up in another test, whatever order they run in.

    Parameters
    ----------
    config_in_tmp_path : `AppConfiguration`
        The test's own settings, with its own library.

    Returns
    -------
    service : `ExecutionService`
        A service wired to the test's own library.
    """
    from astrometricslib import Astrometrics
    from wayfindinglib import Wayfinder

    config = config_in_tmp_path
    return ExecutionService(wayfinder=Wayfinder(config, astrometrics=Astrometrics(config)))


def _record_session(service: ExecutionService, session_id: str) -> object:
    """Store a minimal observation session.

    Parameters
    ----------
    service : `ExecutionService`
        The service whose Wayfinder's storage receives the session.
    session_id : `str`
        The new session's id.

    Returns
    -------
    session : `ObservationSession`
        The session that was recorded.
    """
    from wayfindinglib import ObservationSession

    session = ObservationSession(
        id=session_id,
        night_date=date(2026, 8, 7),
        site_profile_id="test-site",
        telescope_id="test-scope",
        camera_id="test-camera",
    )
    service.wayfinder.planning._butler.put(session, "observation_session", {"session_id": session_id})
    return session


def test_list_sessions_is_empty_before_anything_is_stored(execution_service: ExecutionService) -> None:
    """A fresh library reports no sessions rather than failing."""
    assert execution_service.list_sessions() == []


def test_stored_session_is_listed_and_retrievable(execution_service: ExecutionService) -> None:
    """A recorded session round-trips through list and get."""
    _record_session(execution_service, "session-round-trip")

    summaries = execution_service.list_sessions()
    assert [summary.id for summary in summaries] == ["session-round-trip"]
    assert summaries[0].model_dump(by_alias=True)["entryCount"] == 0

    session = execution_service.get_session("session-round-trip")
    assert session.id == "session-round-trip"
    assert session.model_dump(by_alias=True)["cameraId"] == "test-camera"


def test_missing_session_raises_rather_than_returning_none(execution_service: ExecutionService) -> None:
    """An unknown id must be an explicit error, not a silent null."""
    with pytest.raises(NotFoundError, match="No observation session"):
        execution_service.get_session("does-not-exist")


def test_abort_session_marks_it_aborted_and_records_it(execution_service: ExecutionService) -> None:
    """Aborting changes the status, and the stored session shows it."""
    _record_session(execution_service, "session-to-abort")

    aborted = execution_service.abort_session("session-to-abort", "clouds rolled in")

    assert aborted.id == "session-to-abort"
    assert aborted.status.value == "ABORTED"
    assert execution_service.get_session("session-to-abort").status.value == "ABORTED"


def test_abort_of_missing_session_raises(execution_service: ExecutionService) -> None:
    """Abort checks that the session exists."""
    with pytest.raises(NotFoundError, match="No observation session"):
        execution_service.abort_session("does-not-exist", "reason")
