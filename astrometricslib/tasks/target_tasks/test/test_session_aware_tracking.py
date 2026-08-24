"""Tests for analysing mount tracking one observing session at a time.

Concatenating a target's frames across nights reported the re-pointing
between them as tracking error. On the 2026-08-24 catalog NGC 7023's 535
frames span 9 separate nights and produced a "span" of 8,094 hours with
a 9,779 px excursion -- 1.6x the sensor width -- while M 51's three
nights over 15 months reported 10,962 hours.

A second defect surfaced in the same data: 670 of 1,666 registered
frames (40%) carried shifts of order the frame size itself, clustering
at dx~6023/dy~3947 on 6000x4000 colour frames. NGC 7023 was worst at 456
of 535, yet stacked all 535 with its FWHM improving 8.15px -> 5.68px, so
the alignment applied was sound and only the recorded numbers are wrong.
"""

from astrometricslib.tasks.target_tasks import tracking_analysis_tasks as tracking
from astrometricslib.tasks.target_tasks.tracking_analysis_tasks import (
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
