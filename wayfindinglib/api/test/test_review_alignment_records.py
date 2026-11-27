"""Purpose: Tests of `review.alignment.query`.

Description: Checks that the stored alignment attempts, their grouping by
target and the latest polar alignment run are read through the review
and come back as one result model, with the night, time and size filters
and the optional tracking rows.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import InvalidArgumentError
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

    def get_session_alignment_attempts(
        self, session_id: str | None = None, limit: int | None = None
    ) -> list[dict]:
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


def test_the_newest_attempts_come_back_oldest_first(
    review: tuple[ObservationReview, _Records],
) -> None:
    """With a limit and no night, the newest attempts come oldest first."""
    observation_review, _ = review

    result = observation_review.alignment.query(limit=50)

    assert [attempt.timestamp for attempt in result.attempts] == [100.0, 200.0]
    assert result.attempts[0].status == "aligned"
    assert result.attempts[0].session_id == "2026-09-24"
    assert [target.target_name for target in result.targets] == ["M 31"]
    assert [attempt.timestamp for attempt in observation_review.alignment.query(limit=1).attempts] == [200.0]


def test_since_and_until_drop_attempts_outside_the_range(review: tuple[ObservationReview, _Records]) -> None:
    """`since` removes the older attempts and `until` the newer ones."""
    observation_review, _ = review

    result = observation_review.alignment.query(since=datetime.fromtimestamp(150.0, UTC))
    as_text = observation_review.alignment.query(since="1970-01-01T00:02:30Z")
    older = observation_review.alignment.query(until="1970-01-01T00:02:30Z")

    assert [attempt.timestamp for attempt in result.attempts] == [200.0]
    assert [attempt.timestamp for attempt in as_text.attempts] == [200.0]
    assert [attempt.timestamp for attempt in older.attempts] == [100.0]
    with pytest.raises(InvalidArgumentError):
        observation_review.alignment.query(since="yesterday")


def test_a_night_with_tracking_adds_tracking_rows_and_the_polar_run(
    review: tuple[ObservationReview, _Records], control: ObservatoryControl
) -> None:
    """A night with tracking adds the targets' rows and the polar run."""
    observation_review, records = review
    library = SimpleNamespace(id="M 42")
    control._context._astrometrics = SimpleNamespace(
        targets=SimpleNamespace(query=lambda **_: SimpleNamespace(items=[library]))
    )

    result = observation_review.alignment.query(night="2026-09-24", include=["tracking"])

    assert [attempt.target_name for attempt in result.attempts] == ["M 31", "M 31", "M 42"]
    assert result.attempts[0].session_id == "2026-09-24"
    assert result.polar_alignment is not None
    assert result.polar_alignment.status == "warning"
    assert result.polar_alignment.paa_points == [{"x": 1}]
    assert records.polar_calls == [{"session_id": "2026-09-24", "limit": 1}]


def test_every_night_asks_for_the_latest_polar_run_of_any_night(
    review: tuple[ObservationReview, _Records],
) -> None:
    """Without a night, the latest polar run of any night is read."""
    observation_review, records = review

    observation_review.alignment.query()

    assert records.polar_calls == [{"session_id": None, "limit": 1}]
    with pytest.raises(InvalidArgumentError):
        observation_review.alignment.query(include=["guiding"])
