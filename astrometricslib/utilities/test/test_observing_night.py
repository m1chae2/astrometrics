"""Tests for naming the observing night a moment belongs to.

Checks that a night that crosses midnight keeps one name, and that the
Python rule matches the SQL rule the log database uses.
"""

import sqlite3
from datetime import datetime

from astrometricslib.utilities.observing_night import observing_night_id


def _local_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    """Build a Unix timestamp from a local wall-clock time.

    Returns
    -------
    timestamp : `float`
        Seconds since the Unix epoch for that local time.
    """
    return datetime(year, month, day, hour, minute).timestamp()


def test_evening_and_after_midnight_belong_to_the_same_night() -> None:
    """Check that 21:00 on the 24th and 01:30 on the 25th share a name."""
    evening = _local_timestamp(2026, 9, 24, 21)
    after_midnight = _local_timestamp(2026, 9, 25, 1, 30)
    assert observing_night_id(evening) == "2026-09-24"
    assert observing_night_id(after_midnight) == "2026-09-24"


def test_the_night_changes_at_local_noon() -> None:
    """Check that the date rolls over at noon, in daylight."""
    assert observing_night_id(_local_timestamp(2026, 9, 25, 11, 59)) == "2026-09-24"
    assert observing_night_id(_local_timestamp(2026, 9, 25, 12, 1)) == "2026-09-25"


def test_python_rule_matches_the_sql_rule_used_by_the_log_database() -> None:
    """Check that the two implementations never disagree."""
    connection = sqlite3.connect(":memory:")
    for timestamp in (
        _local_timestamp(2026, 9, 24, 21),
        _local_timestamp(2026, 9, 25, 1, 30),
        _local_timestamp(2026, 9, 25, 11, 59),
        _local_timestamp(2026, 9, 25, 12, 1),
        _local_timestamp(2026, 1, 1, 0, 0),
    ):
        (sql_night,) = connection.execute(
            "SELECT strftime('%Y-%m-%d', ? - 43200, 'unixepoch', 'localtime')", (timestamp,)
        ).fetchone()
        assert observing_night_id(timestamp) == sql_night
