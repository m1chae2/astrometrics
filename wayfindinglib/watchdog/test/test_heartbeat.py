"""Purpose: Unit tests for the heartbeat protocol.

Description: Verifies a written heartbeat round-trips, a missing or
unparseable heartbeat reads as None, and staleness is fail-closed: a
missing heartbeat and one older than the timeout are both stale, one
within the timeout is not.
"""

import pytest

from wayfindinglib.watchdog.heartbeat import heartbeat_is_stale, read_heartbeat, write_heartbeat


def test_write_then_read_round_trips(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a written heartbeat reads back as the same value."""
    heartbeat_path = tmp_path / "heartbeat"
    write_heartbeat(heartbeat_path, 12345.678)
    assert read_heartbeat(heartbeat_path) == pytest.approx(12345.678)


def test_read_missing_file_returns_none(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify reading a heartbeat that was never written returns None."""
    assert read_heartbeat(tmp_path / "does-not-exist") is None


def test_read_unparseable_content_returns_none(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a corrupted heartbeat file reads as None rather than raising."""
    heartbeat_path = tmp_path / "heartbeat"
    heartbeat_path.write_text("not-a-float")
    assert read_heartbeat(heartbeat_path) is None


def test_missing_heartbeat_is_always_stale():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a None heartbeat is stale -- absence is never treated as live."""
    assert heartbeat_is_stale(None, now=1000.0, watchdog_timeout_sec=60) is True


def test_heartbeat_within_timeout_is_not_stale():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a heartbeat younger than the timeout is not stale."""
    assert heartbeat_is_stale(last_heartbeat=1000.0, now=1030.0, watchdog_timeout_sec=60) is False


def test_heartbeat_past_timeout_is_stale():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a heartbeat older than the timeout is stale."""
    assert heartbeat_is_stale(last_heartbeat=1000.0, now=1061.0, watchdog_timeout_sec=60) is True


def test_heartbeat_exactly_at_timeout_boundary_is_not_stale():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a heartbeat exactly at the timeout boundary is not yet stale."""
    assert heartbeat_is_stale(last_heartbeat=1000.0, now=1060.0, watchdog_timeout_sec=60) is False
