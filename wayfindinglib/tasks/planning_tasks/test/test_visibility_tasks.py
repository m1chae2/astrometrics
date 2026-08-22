"""Purpose: Unit tests for visibility policy.

Description: Verifies effective_altitude_floor's stricter-of-two
resolution, and visibility_state_at/find_earliest_visible_window/
ever_clears_floor against synthetic, deterministic altitude profiles --
patching compute_altaz rather than deriving real RA/Dec/time
combinations that happen to produce a desired altitude curve, since the
astropy math itself is already covered by wayfindinglib/astronomy/'s
tests and what matters here is the visibility *policy* logic.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import AvoidanceZone, SiteProfile
from wayfindinglib.tasks.planning_tasks import visibility_tasks


def _telescope(**overrides):  # ruff: ignore[missing-type-kwargs, missing-return-type-private-function]
    defaults = {
        "id": "t1",
        "name": "Test Scope",
        "focal_length_mm": 450.0,
        "min_altitude_deg": 0.0,
        "max_altitude_deg": 90.0,
    }
    defaults.update(overrides)
    return Telescope(**defaults)


def _site(**overrides):  # ruff: ignore[missing-type-kwargs, missing-return-type-private-function]
    defaults = {"id": "s1", "name": "Test Site", "latitude_deg": 39.7392, "longitude_deg": -104.9903}
    defaults.update(overrides)
    return SiteProfile(**defaults)


def test_effective_altitude_floor_uses_stricter_of_telescope_and_package():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the effective floor is the stricter (higher) of the two."""
    telescope = _telescope(min_altitude_deg=10.0)
    assert visibility_tasks.effective_altitude_floor(telescope, 30.0) == pytest.approx(30.0)
    assert visibility_tasks.effective_altitude_floor(telescope, 5.0) == pytest.approx(10.0)
    assert visibility_tasks.effective_altitude_floor(telescope, None) == pytest.approx(10.0)


def test_effective_altitude_floor_ignores_telescope_limit_when_disabled():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a disabled telescope limit no longer constrains the floor."""
    telescope = _telescope(altitude_limits_enabled=False, min_altitude_deg=30.0)
    assert visibility_tasks.effective_altitude_floor(telescope, None) == pytest.approx(-90.0)


def test_visibility_state_at_visible_above_floor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a target above the floor and outside any zone is visible."""
    telescope = _telescope()
    site = _site()
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(45.0, 180.0)
    ):
        visible, zone = visibility_tasks.visibility_state_at(
            1.0, 1.0, site, telescope, datetime(2026, 8, 10), 20.0
        )
    assert visible is True
    assert zone is None


def test_visibility_state_at_blocked_below_floor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a target below the floor is not visible."""
    telescope = _telescope()
    site = _site()
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(15.0, 180.0)
    ):
        visible, zone = visibility_tasks.visibility_state_at(
            1.0, 1.0, site, telescope, datetime(2026, 8, 10), 20.0
        )
    assert visible is False
    assert zone is None


def test_visibility_state_at_blocked_by_avoidance_zone():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a stricter avoidance zone blocks a target above the floor."""
    telescope = _telescope()
    zone = AvoidanceZone(
        id="z1", name="Tree", azimuth_start_deg=170.0, azimuth_end_deg=190.0, min_clear_altitude_deg=60.0
    )
    site = _site(avoidance_zones=[zone])
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(45.0, 180.0)
    ):
        visible, blocking_zone = visibility_tasks.visibility_state_at(
            1.0, 1.0, site, telescope, datetime(2026, 8, 10), 20.0
        )
    assert visible is False
    assert blocking_zone is zone


def test_visibility_state_at_zone_outside_azimuth_range_does_not_block():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a zone at a different azimuth does not block visibility."""
    telescope = _telescope()
    zone = AvoidanceZone(
        id="z1", name="Tree", azimuth_start_deg=0.0, azimuth_end_deg=10.0, min_clear_altitude_deg=80.0
    )
    site = _site(avoidance_zones=[zone])
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(45.0, 180.0)
    ):
        visible, blocking_zone = visibility_tasks.visibility_state_at(
            1.0, 1.0, site, telescope, datetime(2026, 8, 10), 20.0
        )
    assert visible is True
    assert blocking_zone is None


def test_find_earliest_visible_window_finds_continuous_span():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the first sufficiently long continuous span is found."""
    telescope = _telescope()
    site = _site()
    window_start = datetime(2026, 8, 10, 0, 0, 0)
    window_end = window_start + timedelta(hours=2)
    step = timedelta(minutes=10)

    # Altitude climbs above the floor starting 30 minutes in and stays there.
    def fake_altaz(ra_deg, dec_deg, location, obstime):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        elapsed_min = (obstime.datetime - window_start).total_seconds() / 60.0
        return (40.0 if elapsed_min >= 30 else 5.0), 180.0

    with patch("wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=fake_altaz):
        span = visibility_tasks.find_earliest_visible_window(
            1.0, 1.0, site, telescope, window_start, window_end, 20.0, duration_sec=1800, step=step
        )
    assert span is not None
    assert span[0] == window_start + timedelta(minutes=30)


def test_find_earliest_visible_window_none_when_never_long_enough():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify None is returned when no span meets the required duration."""
    telescope = _telescope()
    site = _site()
    window_start = datetime(2026, 8, 10, 0, 0, 0)
    window_end = window_start + timedelta(hours=1)
    step = timedelta(minutes=10)

    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(5.0, 180.0)
    ):
        span = visibility_tasks.find_earliest_visible_window(
            1.0, 1.0, site, telescope, window_start, window_end, 20.0, duration_sec=1800, step=step
        )
    assert span is None


def test_ever_clears_floor_true_when_any_sample_clears():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ever_clears_floor detects a single clearing sample."""
    telescope = _telescope()
    site = _site()
    window_start = datetime(2026, 8, 10, 0, 0, 0)
    window_end = window_start + timedelta(hours=1)
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(45.0, 180.0)
    ):
        assert (
            visibility_tasks.ever_clears_floor(
                1.0, 1.0, site, telescope, window_start, window_end, 20.0, timedelta(minutes=10)
            )
            is True
        )


def test_ever_clears_floor_false_when_never_clears():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ever_clears_floor is False when the altitude never clears."""
    telescope = _telescope()
    site = _site()
    window_start = datetime(2026, 8, 10, 0, 0, 0)
    window_end = window_start + timedelta(hours=1)
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(5.0, 180.0)
    ):
        assert (
            visibility_tasks.ever_clears_floor(
                1.0, 1.0, site, telescope, window_start, window_end, 20.0, timedelta(minutes=10)
            )
            is False
        )


def test_ever_visible_detects_single_isolated_clear_sample():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ever_visible detects a lone clear sample a run-search misses.

    A run's first sample always has zero elapsed time from itself, so
    searching for a continuous span (even of length ~0) can never
    confirm a single isolated instant is visible -- this is the
    per-sample check that actually can.
    """
    telescope = _telescope()
    site = _site()
    window_start = datetime(2026, 8, 10, 0, 0, 0)
    window_end = window_start + timedelta(hours=1)
    step = timedelta(minutes=10)

    def single_clear_sample(ra_deg, dec_deg, location, obstime):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return (40.0 if obstime.datetime == window_start else 5.0), 180.0

    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=single_clear_sample
    ):
        assert (
            visibility_tasks.ever_visible(1.0, 1.0, site, telescope, window_start, window_end, 20.0, step)
            is True
        )
        # Confirm the contrast: a continuous-run search for the same
        # scenario cannot detect it.
        assert (
            visibility_tasks.find_earliest_visible_window(
                1.0, 1.0, site, telescope, window_start, window_end, 20.0, duration_sec=1.0, step=step
            )
            is None
        )


def test_ever_visible_false_when_blocked_by_zone_everywhere_it_clears():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ever_visible is False when a zone blocks every clear instant."""
    telescope = _telescope()
    zone = AvoidanceZone(
        id="z1",
        name="Ring",
        azimuth_start_deg=0.0,
        azimuth_end_deg=359.9,
        min_clear_altitude_deg=89.0,
    )
    site = _site(avoidance_zones=[zone])
    window_start = datetime(2026, 8, 10, 0, 0, 0)
    window_end = window_start + timedelta(hours=1)
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", return_value=(40.0, 180.0)
    ):
        assert (
            visibility_tasks.ever_visible(
                1.0, 1.0, site, telescope, window_start, window_end, 20.0, timedelta(minutes=10)
            )
            is False
        )
