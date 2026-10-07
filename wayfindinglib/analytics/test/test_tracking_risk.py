"""Purpose: Tests for the tracking-risk grid on the performance envelope.

Description: `build_tracking_risk_map` replaced the scoring the
planetarium's tracking-risk overlay did itself. These tests carry over
that overlay's checks of the jitter score against the plate scale, and
check the geometric prior, the grid's shape, and how measured targets
pull the score near where they were observed.
"""

import pytest

from wayfindinglib.analytics.tracking_risk import (
    CAUTION_SCORE,
    HIGH_SCORE,
    TrackedTarget,
    build_tracking_risk_map,
    prior_risk,
    rms_risk_score,
)
from wayfindinglib.models.equipment_and_site.performance_envelope import TrackingRiskMap


def _score_at(risk_map: TrackingRiskMap, ha_deg: float, dec_deg: float) -> float:
    """Read the grid score at an exact grid point.

    Parameters
    ----------
    risk_map : `TrackingRiskMap`
        The grid.
    ha_deg, dec_deg : `float`
        A point on the grid.

    Returns
    -------
    score : `float`
        Its score.
    """
    return risk_map.scores[risk_map.dec_deg.index(dec_deg)][risk_map.ha_deg.index(ha_deg)]


def test_jitter_is_judged_against_the_plate_scale() -> None:
    """Under 0.75 pixel is safe, to 1.25 pixels caution, beyond that high."""
    plate_scale = 1.91
    for rms in (0.82, 0.87, 0.93, 1.0, 1.2):
        assert rms_risk_score(rms, plate_scale) < CAUTION_SCORE
    assert CAUTION_SCORE <= rms_risk_score(1.8, plate_scale) < HIGH_SCORE
    assert rms_risk_score(2.8, plate_scale) >= HIGH_SCORE


def test_without_a_plate_scale_the_limits_are_1_2_and_2_arcseconds() -> None:
    """The fallback limits match a typical amateur mount."""
    assert rms_risk_score(0.8) < CAUTION_SCORE
    assert rms_risk_score(1.2) <= CAUTION_SCORE
    assert CAUTION_SCORE <= rms_risk_score(1.6) < HIGH_SCORE
    assert rms_risk_score(2.4) >= HIGH_SCORE


def test_small_jitter_is_high_risk_on_a_long_focal_length() -> None:
    """0.8 arcseconds is two pixels at 0.4 arcseconds per pixel."""
    assert rms_risk_score(0.8, 0.4) >= HIGH_SCORE


def test_the_prior_rises_west_of_the_meridian_near_the_pole_and_near_the_horizon() -> None:
    """Each geometric risk adds to the base score; below the horizon is 1."""
    base = prior_risk(-30.0, 20.0, 60.0)
    assert base == pytest.approx(0.05)
    assert prior_risk(75.0, 20.0, 60.0) == pytest.approx(0.15)
    assert prior_risk(-30.0, 90.0, 60.0) == pytest.approx(0.13)
    assert prior_risk(-30.0, 20.0, 0.0) == pytest.approx(0.45)
    assert prior_risk(-30.0, 20.0, -1.0) == pytest.approx(1.0)


def test_the_grid_covers_the_whole_mount() -> None:
    """Hour angle -180..180 by 10 and declination -90..90 by 5, prior only."""
    risk_map = build_tracking_risk_map(40.0, [], 1.91, 0)

    assert risk_map.ha_deg[0] == pytest.approx(-180.0)
    assert risk_map.ha_deg[-1] == pytest.approx(180.0)
    assert risk_map.dec_deg[0] == pytest.approx(-90.0)
    assert risk_map.dec_deg[-1] == pytest.approx(90.0)
    assert len(risk_map.scores) == 37
    assert all(len(row) == 37 for row in risk_map.scores)
    assert risk_map.measured_target_count == 0
    assert risk_map.ideal_rms_arcsec == pytest.approx(0.75 * 1.91)
    # Below the horizon at latitude 40: the far south.
    assert _score_at(risk_map, 0.0, -80.0) == pytest.approx(1.0)
    # High in the east.
    assert _score_at(risk_map, -30.0, 40.0) == pytest.approx(0.05)


def test_a_measured_target_pulls_nearby_scores_toward_its_jitter() -> None:
    """Bad jitter raises scores where it was seen, not across the sky."""
    bad = TrackedTarget(ha_deg=-30.0, dec_deg=40.0, rms_arcsec=5.0, solve_count=30)
    risk_map = build_tracking_risk_map(40.0, [bad], 1.0, 30)

    assert _score_at(risk_map, -30.0, 40.0) >= HIGH_SCORE
    assert _score_at(risk_map, 150.0, -40.0) == _score_at(
        build_tracking_risk_map(40.0, [], 1.0, 0), 150.0, -40.0
    )
    assert risk_map.measured_target_count == 1
    assert risk_map.solve_count == 30


def test_targets_with_one_solve_or_no_jitter_are_ignored() -> None:
    """A single sync or a zero jitter does not count as a measurement."""
    ignored = [
        TrackedTarget(ha_deg=0.0, dec_deg=40.0, rms_arcsec=5.0, solve_count=1),
        TrackedTarget(ha_deg=0.0, dec_deg=40.0, rms_arcsec=0.0, solve_count=10),
    ]
    assert build_tracking_risk_map(40.0, ignored, 1.0, 11).measured_target_count == 0
