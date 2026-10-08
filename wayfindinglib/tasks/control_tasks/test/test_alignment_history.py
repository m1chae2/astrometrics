"""Purpose: Tests for `control.history.query(kind="alignment")`.

Description: The alignment history lists one summary per night, built on
the log database's `get_alignment_sessions`, with the night's mean
pointing and tracking jitter, or one night's attempts grouped by target.
The mean right ascension must wrap at 0h/24h. The tests use a stand-in
log database.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import NotFoundError
from wayfindinglib.tasks.control_tasks import night_history


class _Logs:
    """A stand-in log database with two nights of plate solves."""

    def get_alignment_sessions(self) -> list[dict[str, Any]]:
        """Return the night summaries, newest first.

        Returns
        -------
        sessions : `list` [`dict` [`str`, `Any`]]
            Two nights.
        """
        return [
            {
                "session_id": "2026-09-25",
                "session_date": "2026-09-25",
                "sync_count": 2,
                "start_time": 10.0,
                "end_time": 20.0,
                "avg_error_arcsec": 12.5,
                "polar_error_arcsec": None,
                "polar_alt_error_arcsec": None,
                "polar_az_error_arcsec": None,
            },
            {"session_id": "2026-09-24", "session_date": "2026-09-24", "sync_count": 0},
        ]

    def get_session_alignment_attempts(self, session_id: str) -> list[dict[str, Any]]:
        """Return one night's attempts, oldest first.

        Returns
        -------
        attempts : `list` [`dict` [`str`, `Any`]]
            Two solves either side of 0h for the newer night.
        """
        if session_id != "2026-09-25":
            return []
        return [
            {
                "status": "aligned",
                "mount_ra": 359.0,
                "mount_dec": 10.0,
                "timestamp": 10.0,
                "session_id": session_id,
            },
            {
                "status": "synced",
                "mount_ra": 1.0,
                "mount_dec": 20.0,
                "timestamp": 20.0,
                "session_id": session_id,
            },
        ]

    def get_polar_alignments(
        self, session_id: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return no polar alignment runs.

        Returns
        -------
        logs : `list`
            Always empty.
        """
        return []


def _context() -> SimpleNamespace:
    """Build the parts of a `ControlContext` the alignment history reads.

    Returns
    -------
    context : `types.SimpleNamespace`
        The log database and a target catalog that counts one target on
        the newer night.
    """
    targets = SimpleNamespace(query=lambda detail: {"nights": {"2026-09-25": 1}})
    return SimpleNamespace(records=_Logs(), astrometrics=SimpleNamespace(targets=targets))


def test_nights_are_listed_with_their_mean_pointing_and_target_count() -> None:
    """Each night has the app's camelCase summary and its mean pointing."""
    reply = night_history.build_night_history(_context(), "alignment", limit=10)

    assert reply["total"] == 2
    newest, older = reply["sessions"]
    assert newest["sessionId"] == "2026-09-25"
    assert newest["syncCount"] == 2
    assert newest["targetCount"] == 1
    assert newest["avgErrorArcsec"] == pytest.approx(12.5)
    assert newest["meanRaDeg"] % 360.0 == pytest.approx(0.0, abs=1e-9)
    assert older["meanRaDeg"] is None
    assert older["targetCount"] == 0
    assert older["rmsJitterArcsec"] is None


def test_one_night_lists_its_attempts_in_time_order() -> None:
    """A night's attempts come back in order; odd statuses read as aligned."""
    reply = night_history.build_night_history(_context(), "alignment", session_id="2026-09-25")

    assert [attempt["ra"] for attempt in reply["attempts"]] == [359.0, 1.0]
    assert [attempt["status"] for attempt in reply["attempts"]] == ["aligned", "aligned"]
    assert reply["polar_alignment"] is None


def test_one_night_groups_its_attempts_by_target() -> None:
    """Unnamed solves ten degrees apart are two targets, without attempts."""
    reply = night_history.build_night_history(_context(), "alignment", session_id="2026-09-25")

    targets = reply["targets"]
    assert [target["frameCount"] for target in targets] == [1, 1]
    assert [target["meanRaDeg"] for target in targets] == [pytest.approx(359.0), pytest.approx(1.0)]
    assert [target["targetName"] for target in targets] == ["Sync #1", "Sync #2"]
    assert all("attempts" not in target for target in targets)


def test_a_night_with_no_attempts_is_not_found() -> None:
    """Asking for a night with nothing recorded raises `NotFoundError`."""
    with pytest.raises(NotFoundError):
        night_history.build_night_history(_context(), "alignment", session_id="2026-09-24")
