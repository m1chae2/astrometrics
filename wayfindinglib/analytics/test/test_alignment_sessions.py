"""Purpose: Tests for grouping plate-solve attempts into target sessions.

Description: `AlignmentTargetSession.from_attempts` (built on
`group_alignment_attempts`) replaced the planetarium's own grouping code.
These tests carry over that code's checks: jitter is the scatter around
the settled offset, not the offset itself; runs split at long gaps; a
single sync has no jitter; right ascension stays in degrees. They also
check what the move fixed or added: the average right ascension wraps at
0h/24h, unnamed solves group across 0h, and drift rates and the pooled
night jitter.
"""

import math

import pytest

from wayfindinglib import AlignmentAttempt, AlignmentTargetSession
from wayfindinglib.analytics.alignment_sessions import mean_position_deg, pooled_jitter_arcsec


def _solve(name: str | None, ra: float, dec: float, d_ra: float, d_dec: float, t: float) -> AlignmentAttempt:
    """Build one aligned solve.

    Parameters
    ----------
    name : `str` or `None`
        Target name.
    ra, dec : `float`
        Mount position in degrees.
    d_ra, d_dec : `float`
        Offsets in arcseconds.
    t : `float`
        Unix time in seconds.

    Returns
    -------
    attempt : `AlignmentAttempt`
        The solve.
    """
    return AlignmentAttempt(
        status="aligned",
        target_name=name,
        ra=ra,
        dec=dec,
        delta_ra_arcsec=d_ra,
        delta_dec_arcsec=d_dec,
        timestamp=t,
    )


def test_the_mean_right_ascension_wraps_at_zero_hours() -> None:
    """359 and 1 degrees average to 0, not 180."""
    ra_deg, dec_deg = mean_position_deg([(359.0, 10.0), (1.0, 20.0)])
    assert ra_deg % 360.0 == pytest.approx(0.0, abs=1e-9)
    assert dec_deg == pytest.approx(15.0)


def test_an_empty_list_has_no_mean_position() -> None:
    """No solves give no mean."""
    assert mean_position_deg([]) == (None, None)


def test_jitter_is_scatter_around_the_settled_offset() -> None:
    """A steady 50 arcsecond framing offset is not tracking error."""
    offsets = [(50.0, 0.0), (50.5, 0.3), (49.5, -0.3), (50.2, 0.1), (49.8, -0.1)]
    attempts = [
        _solve("Vega", 279.2, 38.8, dra, ddec, 1000 + 10 * i) for i, (dra, ddec) in enumerate(offsets)
    ]

    (session,) = AlignmentTargetSession.from_attempts(attempts)

    assert session.target_name == "Vega"
    assert session.frame_count == 5
    assert session.initial_error_arcsec == pytest.approx(50.0, abs=0.1)
    assert 0.2 < session.rms_total_arcsec < 1.0
    assert session.elapsed_seconds == pytest.approx(40.0)


def test_runs_split_at_long_gaps_and_their_times_add() -> None:
    """Two one-minute runs a day apart give two minutes, and pooled jitter."""
    night1 = [(10.0, 5.0), (10.4, 5.2), (9.6, 4.8)]
    night2 = [(-20.0, 15.0), (-19.6, 15.3), (-20.4, 14.7)]
    attempts = [_solve("M 27", 300.0, 22.7, dra, ddec, 1000 + 30 * i) for i, (dra, ddec) in enumerate(night1)]
    attempts += [
        _solve("M 27", 300.0, 22.7, dra, ddec, 100000 + 30 * i) for i, (dra, ddec) in enumerate(night2)
    ]

    (session,) = AlignmentTargetSession.from_attempts(attempts)

    assert session.frame_count == 6
    assert session.elapsed_seconds == pytest.approx(120.0)
    assert 0.2 < session.rms_total_arcsec < 1.0


def test_a_single_sync_has_no_jitter() -> None:
    """One solve keeps its pointing error and has zero jitter."""
    (session,) = AlignmentTargetSession.from_attempts([_solve("Sync #1", 150.0, 60.0, 14.2, -8.1, 500)])

    assert session.frame_count == 1
    assert session.rms_total_arcsec == pytest.approx(0.0)
    assert session.initial_error_arcsec == pytest.approx(math.hypot(14.2, 8.1))


def test_right_ascension_stays_in_degrees() -> None:
    """Targets in the first hour of RA keep their degree values."""
    attempts = [
        _solve("Navi", 14.177, 60.717, 0.2, -0.1, 100),
        _solve("Navi", 14.177, 60.717, -0.2, 0.1, 120),
        _solve("Schedar", 10.127, 56.537, 0.1, 0.2, 200),
    ]

    navi, schedar = AlignmentTargetSession.from_attempts(attempts)

    assert navi.mean_ra_deg == pytest.approx(14.177)
    assert schedar.mean_ra_deg == pytest.approx(10.127)
    assert schedar.target_name == "Schedar"


def test_unnamed_solves_group_across_zero_hours() -> None:
    """Unnamed solves at 359.9 and 0.1 degrees are one target near 0h."""
    attempts = [_solve(None, 359.9, 30.0, 1.0, 1.0, 10), _solve(None, 0.1, 30.0, 1.2, 0.9, 20)]

    (session,) = AlignmentTargetSession.from_attempts(attempts)

    assert session.frame_count == 2
    assert session.target_name == "Target Session #1"
    assert min(session.mean_ra_deg, 360.0 - session.mean_ra_deg) == pytest.approx(0.0, abs=1e-6)


def test_drift_rate_is_the_slope_of_the_offsets() -> None:
    """Offsets growing by 2 arcseconds a minute in RA give that drift rate."""
    attempts = [_solve("M 13", 250.4, 36.5, 2.0 * i, -1.0 * i, 60.0 * i) for i in range(5)]

    (session,) = AlignmentTargetSession.from_attempts(attempts)

    assert session.drift_ra_arcsec_per_min == pytest.approx(2.0)
    assert session.drift_dec_arcsec_per_min == pytest.approx(-1.0)
    assert [p.elapsed_seconds for p in session.time_series] == [0.0, 60.0, 120.0, 180.0, 240.0]


def test_repeats_and_positionless_solves_are_dropped() -> None:
    """A repeat counts once; a solve without a position is skipped."""
    solve = _solve("M 31", 10.7, 41.3, 1.0, 1.0, 50)
    blank = AlignmentAttempt(status="solving")

    (session,) = AlignmentTargetSession.from_attempts([solve, solve, blank])

    assert session.frame_count == 1
    assert AlignmentTargetSession.from_attempts([blank]) == []


def test_pooled_jitter_weights_targets_by_solve_count() -> None:
    """Single-solve targets are ignored; others weigh by solve count."""
    sessions = AlignmentTargetSession.from_attempts([
        _solve("A", 10.0, 10.0, 0.0, 0.0, 1),
        _solve("A", 10.0, 10.0, 2.0, 0.0, 2),
        _solve("B", 50.0, 10.0, 5.0, 5.0, 3),
    ])

    assert pooled_jitter_arcsec(sessions) == pytest.approx(1.0)
    assert pooled_jitter_arcsec(sessions[1:]) is None
