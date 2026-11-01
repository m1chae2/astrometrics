"""Purpose: Unit tests for observatory_session_recorder's _ensure_session.

Description: Verifies an existing session is returned unchanged, and that a
missing session is created through `wayfinder.planning.create_session`. The
recorder holds no logic of its own for equipment or sites: the library
creates the session for the active telescope and camera, and raises when it
cannot.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from documentation.notebooks.wayfinding.execution.scripts.observatory_session_recorder import (
    _ensure_session,
)
from wayfindinglib.models.session.observation_session import ObservationSession, SessionStatus


class _FakePlanning:
    """A stand-in `ObservationPlanning` with one stored session."""

    def __init__(self, stored: ObservationSession | None = None, refuse: bool = False) -> None:
        """Hold the stored session and whether creating is refused."""
        self._stored = stored
        self._refuse = refuse
        self.created: list[str] = []

    def get_plan(self, session_id: str) -> ObservationSession | None:
        """Read the stored session.

        Returns
        -------
        session : `ObservationSession` or `None`
            The stored session when its id matches.
        """
        return self._stored if self._stored is not None and self._stored.id == session_id else None

    def create_session(self, observing_night_id: str) -> ObservationSession:
        """Create an empty session for a night, or refuse.

        Returns
        -------
        session : `ObservationSession`
            A new empty session.

        Raises
        ------
        InvalidArgumentError
            When the stand-in is set to refuse, as the library does without an
            active telescope and camera.
        """
        if self._refuse:
            raise InvalidArgumentError("No active telescope and camera.")
        self.created.append(observing_night_id)
        return ObservationSession(
            id=f"created-{observing_night_id}",
            night_date=date(2026, 8, 5),
            status=SessionStatus.PLANNED,
            site_profile_id="site-1",
            telescope_id="scope-1",
            camera_id="cam-1",
        )


def test_returns_existing_session_unchanged() -> None:
    """Verify an already-recorded session is returned as-is."""
    existing = ObservationSession(
        id="session-1",
        night_date=date(2026, 8, 5),
        status=SessionStatus.RUNNING,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id="cam-1",
    )
    planning = _FakePlanning(existing)

    result = _ensure_session(SimpleNamespace(planning=planning), "session-1")

    assert result is existing
    assert planning.created == []


def test_creates_a_session_for_a_night_that_has_none() -> None:
    """Verify a missing session is created through the planning API."""
    planning = _FakePlanning()

    result = _ensure_session(SimpleNamespace(planning=planning), "2026-08-05")

    assert planning.created == ["2026-08-05"]
    assert result.id == "created-2026-08-05"


def test_the_librarys_refusal_reaches_the_caller() -> None:
    """Verify an error from the library is not hidden by the recorder."""
    wayfinder = SimpleNamespace(planning=_FakePlanning(refuse=True))

    with pytest.raises(InvalidArgumentError):
        _ensure_session(wayfinder, "2026-08-05")
