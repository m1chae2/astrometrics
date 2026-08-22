"""Purpose: Unit tests for TargetSession derivation.

Description: Verifies compute_session_night's noon-to-noon boundary and
derive_target_sessions's bucketing by (night, gain, offset), including
non-contiguous frames rejoining the same session.
"""

from datetime import date, datetime

from astrometricslib.models.target import FrameRecord
from astrometricslib.tasks.target_tasks.target_session_tasks import (
    compute_session_night,
    derive_target_sessions,
)


def _timestamp(year, month, day, hour, minute=0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Return a Unix timestamp for a local naive datetime, for readability.

    Returns
    -------
    float
        The Unix timestamp for the given local naive datetime.
    """
    return datetime(year, month, day, hour, minute).timestamp()


def test_compute_session_night_before_noon_belongs_to_previous_night():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a post-midnight capture belongs to the prior night."""
    timestamp = _timestamp(2026, 7, 15, 3, 30)
    assert compute_session_night(timestamp) == date(2026, 7, 14)


def test_compute_session_night_after_noon_belongs_to_same_night():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an evening capture belongs to that calendar date's night."""
    timestamp = _timestamp(2026, 7, 15, 21, 0)
    assert compute_session_night(timestamp) == date(2026, 7, 15)


def test_derive_target_sessions_groups_by_night_gain_and_offset():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies frames from one night/config land in a single TargetSession."""
    frames = [
        FrameRecord(path="a.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 15, 21, 0)),
        FrameRecord(path="b.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 16, 1, 0)),
    ]
    sessions = derive_target_sessions("M 13", frames)
    assert len(sessions) == 1
    assert sessions[0].night_date == date(2026, 7, 15)
    assert sorted(sessions[0].frame_paths) == ["a.fits", "b.fits"]


def test_derive_target_sessions_rejoins_non_contiguous_frames_same_night():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify same night/config frames merge even if not contiguous."""
    frames = [
        FrameRecord(path="early.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 15, 20, 0)),
        FrameRecord(
            path="other_target.fits", iso="200", offset="10", timestamp=_timestamp(2026, 7, 15, 22, 0)
        ),
        FrameRecord(path="late.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 16, 2, 0)),
    ]
    sessions = derive_target_sessions("M 13", frames)
    matching_gain_sessions = [session for session in sessions if session.gain == "100"]
    assert len(matching_gain_sessions) == 1
    assert sorted(matching_gain_sessions[0].frame_paths) == ["early.fits", "late.fits"]


def test_derive_target_sessions_splits_on_gain_change():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a gain change within the same night starts a new session."""
    frames = [
        FrameRecord(path="a.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 15, 21, 0)),
        FrameRecord(path="b.fits", iso="200", offset="10", timestamp=_timestamp(2026, 7, 15, 22, 0)),
    ]
    sessions = derive_target_sessions("M 13", frames)
    assert len(sessions) == 2
    assert {session.gain for session in sessions} == {"100", "200"}


def test_derive_target_sessions_splits_on_offset_change():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an offset change within the same night starts a new session."""
    frames = [
        FrameRecord(path="a.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 15, 21, 0)),
        FrameRecord(path="b.fits", iso="100", offset="20", timestamp=_timestamp(2026, 7, 15, 22, 0)),
    ]
    sessions = derive_target_sessions("M 13", frames)
    assert len(sessions) == 2
    assert {session.offset for session in sessions} == {"10", "20"}


def test_derive_target_sessions_ignores_filter_and_exposure_changes():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies filter/exposure changes do not split a session."""
    frames = [
        FrameRecord(
            path="a.fits", iso="100", offset="10", exposure="60", timestamp=_timestamp(2026, 7, 15, 21, 0)
        ),
        FrameRecord(
            path="b.fits", iso="100", offset="10", exposure="120", timestamp=_timestamp(2026, 7, 15, 22, 0)
        ),
    ]
    sessions = derive_target_sessions("M 13", frames)
    assert len(sessions) == 1


def test_derive_target_sessions_produces_deterministic_ids():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the session id derives from target_id, night, gain, offset."""
    frames = [
        FrameRecord(path="a.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 15, 21, 0)),
    ]
    sessions = derive_target_sessions("M 13", frames)
    assert sessions[0].id == "M 13:2026-07-15:100:10"


def test_derive_target_sessions_skips_frames_without_timestamp():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify frames with no capture timestamp are excluded, not crashed on."""
    frames = [
        FrameRecord(path="a.fits", iso="100", offset="10", timestamp=None),
        FrameRecord(path="b.fits", iso="100", offset="10", timestamp=_timestamp(2026, 7, 15, 21, 0)),
    ]
    sessions = derive_target_sessions("M 13", frames)
    assert len(sessions) == 1
    assert sessions[0].frame_paths == ["b.fits"]
