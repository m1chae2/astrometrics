"""Purpose: Tests of `ObservationReview.query_alignment_records`.

Description: Checks that the stored alignment attempts, their grouping by
target and the latest polar alignment run are read through the review
and come back as one result model.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from wayfindinglib import ObservatoryControl
from wayfindinglib.api.review import ObservationReview


class _Records:
    """A stand-in for the stored records, with fixed rows."""

    def __init__(self) -> None:
        """Remember the calls made and prepare the rows."""
        self.polar_calls: list[dict[str, Any]] = []
        self.rows = [
            {
                "status": "aligned",
                "delta_ra_arcsec": 1.0,
                "delta_dec_arcsec": 2.0,
                "mount_ra": 10.0,
                "mount_dec": 20.0,
                "pointing_error_arcsec": 2.2,
                "timestamp": 200.0,
                "target_name": "M 31",
                "session_id": "2026-09-24",
            },
            {
                "status": "odd",
                "delta_ra_arcsec": 0.5,
                "delta_dec_arcsec": 0.5,
                "mount_ra": 10.0,
                "mount_dec": 20.0,
                "pointing_error_arcsec": 0.7,
                "timestamp": 100.0,
                "target_name": "M 31",
                "session_id": "2026-09-24",
            },
        ]

    def get_alignment_attempts(self, target_name: str | None = None, limit: int = 100) -> list[dict]:
        """Return the rows, newest first.

        Returns
        -------
        rows : `list` [`dict`]
            The newest `limit` rows.
        """
        return sorted(self.rows, key=lambda row: -row["timestamp"])[:limit]

    def get_session_alignment_attempts(self, session_id: str | None = None) -> list[dict]:
        """Return the rows, oldest first.

        Returns
        -------
        rows : `list` [`dict`]
            Every row.
        """
        return sorted(self.rows, key=lambda row: row["timestamp"])

    def get_session_target_telemetry(self, session_id: str | None, targets: list) -> list[dict]:
        """Return one tracking row per target given.

        Returns
        -------
        rows : `list` [`dict`]
            The tracking rows.
        """
        return [
            {
                "status": "aligned",
                "deltaRaArcsec": 0.0,
                "deltaDecArcsec": 0.0,
                "ra": 30.0,
                "dec": 40.0,
                "pointingErrorArcsec": 0.0,
                "timestamp": 300.0,
                "targetName": target.id,
            }
            for target in targets
        ]

    def get_polar_alignments(self, session_id: str | None = None, limit: int = 10) -> list[dict]:
        """Return one stored polar run and remember how it was asked for.

        Returns
        -------
        runs : `list` [`dict`]
            One stored run.
        """
        self.polar_calls.append({"session_id": session_id, "limit": limit})
        return [{"status": "warning", "total_error_arcsec": 42.0, "paa_points": [{"x": 1}], "timestamp": 5.0}]


@pytest.fixture
def review(control: ObservatoryControl) -> tuple[ObservationReview, _Records]:
    """Build a review over stand-in records.

    Returns
    -------
    review, records : `tuple`
        The review and its stand-in records.
    """
    records = _Records()
    control._context._records = records
    return ObservationReview(control), records


def test_the_live_view_lists_the_newest_attempts_oldest_first(
    review: tuple[ObservationReview, _Records],
) -> None:
    """Without a night, the newest attempts come back oldest first."""
    observation_review, _ = review

    result = observation_review.query_alignment_records()

    assert [attempt.timestamp for attempt in result.attempts] == [100.0, 200.0]
    assert result.attempts[0].status == "aligned"
    assert result.attempts[0].session_id is None
    assert result.polar_alignment is None
    assert [target.target_name for target in result.targets] == ["M 31"]


def test_the_live_view_drops_attempts_before_since(review: tuple[ObservationReview, _Records]) -> None:
    """`since` removes the older attempts."""
    observation_review, _ = review

    result = observation_review.query_alignment_records(since=150.0)

    assert [attempt.timestamp for attempt in result.attempts] == [200.0]


def test_the_night_view_adds_tracking_rows_and_the_polar_run(
    review: tuple[ObservationReview, _Records],
) -> None:
    """With a night, tracking rows and the latest polar run are added."""
    observation_review, records = review

    result = observation_review.query_alignment_records("2026-09-24", targets=[SimpleNamespace(id="M 42")])

    assert [attempt.target_name for attempt in result.attempts] == ["M 31", "M 31", "M 42"]
    assert result.attempts[0].session_id == "2026-09-24"
    assert result.polar_alignment is not None
    assert result.polar_alignment.status == "warning"
    assert result.polar_alignment.paa_points == [{"x": 1}]
    assert records.polar_calls == [{"session_id": "2026-09-24", "limit": 1}]


def test_the_all_nights_view_asks_for_the_latest_polar_run_of_any_night(
    review: tuple[ObservationReview, _Records],
) -> None:
    """``"all"`` reads the latest polar run without naming a night."""
    observation_review, records = review

    observation_review.query_alignment_records("all")

    assert records.polar_calls == [{"session_id": None, "limit": 1}]
