"""Tests for analysing mount tracking one observing session at a time.

Tracking error should only be measured across a single continuous
observing session. If we measure across different nights, the
telescope pointing changes and it looks like a massive tracking error.
This test ensures we analyze sessions individually.
"""

from astrometricslib.pipelines.stacking import tracking_analysis as tracking
from astrometricslib.pipelines.stacking.tracking_analysis import (
    IMPLAUSIBLE_REGISTRATION_SHIFT_PX,
    analyze_guiding,
    count_implausible_shifts,
    split_frames_into_sessions,
)

ONE_HOUR = 3600.0


class _Frame:
    """A frame record stand-in carrying a timestamp and a shift."""

    def __init__(self, timestamp, dx=0.0, dy=0.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.timestamp = timestamp
        self.registration_dx_px = dx
        self.registration_dy_px = dy
        self.pier_side = None


class _Target:
    """A target stand-in exposing only the frames list."""

    def __init__(self, frames):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = "TrackingTestTarget"
        self.frames = frames


def _session(start, count, spacing=120.0, dx_step=0.1):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build one night's worth of frames drifting gently.

    Returns
    -------
    frames : `list` [`_Frame`]
        Frames spaced `spacing` seconds apart from `start`.
    """
    return [_Frame(start + index * spacing, dx=index * dx_step, dy=index * dx_step) for index in range(count)]


def test_one_continuous_night_is_a_single_session():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Frames minutes apart belong together."""
    assert len(split_frames_into_sessions(_session(0.0, 10))) == 1


def test_a_night_apart_splits_into_two_sessions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A day's gap is a new session, not a 24-hour tracking run."""
    first = _session(0.0, 5)
    second = _session(24 * ONE_HOUR, 5)

    assert len(split_frames_into_sessions(first + second)) == 2


def test_a_within_night_pause_does_not_split():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A meridian flip or cloud break is still the same session."""
    first = _session(0.0, 5)
    second = _session(2 * ONE_HOUR, 5)

    assert len(split_frames_into_sessions(first + second)) == 1


def test_no_frames_yields_no_sessions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An empty target is not an error."""
    assert split_frames_into_sessions([]) == []


def test_span_reflects_one_night_not_the_whole_campaign():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A multi-night target must not report a months-long span."""
    nights = []
    for night_index in range(3):
        nights.extend(_session(night_index * 30 * 24 * ONE_HOUR, 10))

    analysis = analyze_guiding(_Target(nights))

    assert analysis["sessions_analyzed"] == 3
    # Ten frames two minutes apart is 18 minutes, not 60 days.
    assert analysis["span_hours"] < 1.0


def test_repointing_between_nights_is_not_an_excursion():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A new night starts from a different mount position, by definition."""
    first = _session(0.0, 8, dx_step=0.1)
    second = [_Frame(48 * ONE_HOUR + index * 120.0, dx=500.0, dy=400.0) for index in range(8)]

    analysis = analyze_guiding(_Target(first + second))

    # The 500px jump lies between sessions, so it must not be reported.
    assert analysis["max_excursion_px"] < 10.0


def test_meridian_flip_count_is_not_summed_across_sessions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """One real flip must be reported once, not once per session.

    detect_meridian_flips used to be handed the whole target's frames
    regardless of which session was being analysed, so every session
    reported the same target-wide flip count and the combined total
    summed that count once per session -- a single real flip on a
    3-session target came back as 3.
    """
    first = _session(0.0, 5)
    for frame in first:
        frame.pier_side = "EAST"

    second = _session(24 * ONE_HOUR, 5)
    for index, frame in enumerate(second):
        frame.pier_side = "EAST" if index < 2 else "WEST"

    analysis = analyze_guiding(_Target(first + second))

    assert analysis["sessions_analyzed"] == 2
    assert analysis["meridian_flips"] == 1


def test_frame_sized_shifts_are_excluded():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A shift the size of the frame is a failed transform, not tracking."""
    frames = _session(0.0, 10)
    frames.append(_Frame(10 * 120.0, dx=6023.7, dy=3947.0))

    analysis = analyze_guiding(_Target(frames))

    assert analysis["usable_frames"] == 10
    assert analysis["max_excursion_px"] < 10.0


def test_implausible_shifts_are_counted_for_reporting():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Excluding them silently would hide a real registration problem."""
    frames = _session(0.0, 5)
    frames.append(_Frame(600.0, dx=IMPLAUSIBLE_REGISTRATION_SHIFT_PX + 1, dy=0.0))

    assert count_implausible_shifts(frames) == 1


def test_a_shift_just_inside_the_limit_is_kept():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The filter must not trim genuine large excursions."""
    frames = _session(0.0, 5)
    frames.append(_Frame(600.0, dx=IMPLAUSIBLE_REGISTRATION_SHIFT_PX - 1, dy=0.0))

    assert count_implausible_shifts(frames) == 0


def test_a_short_session_is_counted_but_not_analysed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Three points cannot support a drift rate or a periodogram."""
    analysis = analyze_guiding(_Target(_session(0.0, 3)))

    assert analysis["sessions_found"] == 1
    assert analysis["sessions_analyzed"] == 0


def test_multiple_short_sessions_report_the_longest_one_not_all_joined():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """When no session reaches the minimum, nights must still not be joined.

    Falling back to every session's frames concatenated together is
    exactly the cross-night join `analyze_guiding`'s docstring exists to
    prevent -- it must fall back to the longest single session instead.
    """
    first_night = _session(0.0, 3)
    second_night = _session(48 * ONE_HOUR, 4)

    analysis = analyze_guiding(_Target(first_night + second_night))

    assert analysis["sessions_found"] == 2
    assert analysis["sessions_analyzed"] == 0
    # A real span across the two nights would be ~48 hours; the longest
    # single session (4 frames, 2 minutes apart) spans a few minutes.
    assert analysis["usable_frames"] == 4
    assert analysis["span_hours"] < 1.0


def test_the_worst_session_is_the_one_reported(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Averaging a bad night against good ones would hide it."""
    calm = _session(0.0, 10, dx_step=0.01)
    disturbed = _session(48 * ONE_HOUR, 10, dx_step=0.01)
    # One frame in the second session jumps well beyond the first's drift.
    disturbed[5].registration_dx_px = 60.0

    analysis = analyze_guiding(_Target(calm + disturbed))

    assert analysis["sessions_analyzed"] == 2
    assert analysis["max_excursion_px"] > 50.0


def test_each_session_keeps_its_own_analysis():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The per-session detail is what makes a bad night identifiable."""
    frames = _session(0.0, 8) + _session(48 * ONE_HOUR, 8)

    analysis = analyze_guiding(_Target(frames))

    assert len(analysis["sessions"]) == 2
    assert all(session["usable_frames"] == 8 for session in analysis["sessions"])


def test_a_target_with_no_shifts_reports_nothing_analysed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A never-stacked target has no tracking data to report."""
    analysis = analyze_guiding(_Target([]))

    assert analysis["usable_frames"] == 0
    assert analysis["sessions_analyzed"] == 0


def test_session_gap_constant_is_between_a_pause_and_a_night():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The threshold must separate nights without splitting one."""
    assert 2.0 < tracking.SESSION_GAP_HOURS < 12.0


def test_a_recurring_period_across_sessions_is_corroborated():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Two independent sessions agreeing on period is what confirms it.

    A worm's period is a mechanical constant, so it should recur across
    independent nights. That agreement is the corroboration a single
    session's finding explicitly says it lacks.
    """
    import math

    def _session_with_period(start, period_seconds, count=80, spacing=30.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return [
            _Frame(
                start + index * spacing,
                dx=3.0 * math.sin(2 * math.pi * (index * spacing) / period_seconds),
            )
            for index in range(count)
        ]

    night_one = _session_with_period(0.0, 480.0)
    night_two = _session_with_period(24 * ONE_HOUR, 480.0)
    target = _Target(night_one + night_two)

    analysis = analyze_guiding(target)

    assert any("recurs across independent sessions" in finding for finding in analysis["findings"])


def test_unrelated_session_periods_are_not_corroborated():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Two sessions with different periods must not claim agreement."""
    import math

    def _session_with_period(start, period_seconds, count=80, spacing=30.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return [
            _Frame(
                start + index * spacing,
                dx=3.0 * math.sin(2 * math.pi * (index * spacing) / period_seconds),
            )
            for index in range(count)
        ]

    night_one = _session_with_period(0.0, 200.0)
    night_two = _session_with_period(24 * ONE_HOUR, 850.0)
    target = _Target(night_one + night_two)

    analysis = analyze_guiding(target)

    assert not any("recurs across independent sessions" in finding for finding in analysis["findings"])
