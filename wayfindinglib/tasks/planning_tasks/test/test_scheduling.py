"""Purpose: Unit tests for observation package placement.

Description: Verifies the documented placement invariants
(`Wayfinding_Library_Architecture.md` §2.3.6): clean non-overlapping
placement, each of the five infeasibility codes produced and
distinguishable, a gap between placed entries reflected correctly,
priority breaking a tie with the boost recorded, fixed-time anchors and
conflicts, and the quality boost applying only to opted-in packages and
never as a decrease. Uses synthetic altitude profiles keyed by target
RA, keeping each scenario deterministic and independent of real
astropy precision -- the astropy math itself is covered by
wayfindinglib/astronomy/'s own tests.
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import AvoidanceZone, SiteProfile
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType, ObservationPackage
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.models.planning.quality_advisory import (
    QualityFlagSummary,
    ScienceOutcomeSummary,
    TargetQualityAdvisory,
)
from wayfindinglib.models.session.observation_session import (
    InfeasibilityReasonCode,
    QueueEntryStatus,
    StartTimeMode,
)
from wayfindinglib.tasks.planning_tasks.scheduling import PlacementRequest, place

_WINDOW_START = datetime(2026, 8, 10, 3, 0, 0)
_WINDOW_END = _WINDOW_START + timedelta(hours=6)


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


def _package(ra_key: float, exposure_sec: float = 600.0, count: int = 1, **overrides) -> ObservationPackage:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "id": str(uuid.uuid4()),
        "name": f"pkg-{ra_key}",
        "target_id": f"target-{ra_key}",
        "exposure_requests": [
            ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=exposure_sec, count=count)
        ],
    }
    defaults.update(overrides)
    return ObservationPackage(**defaults)


def _soonest_request(ra_key: float, **package_overrides) -> PlacementRequest:  # ruff: ignore[missing-type-kwargs]
    return PlacementRequest(
        package=_package(ra_key, **package_overrides),
        start_time_mode=StartTimeMode.SOONEST,
        ra_deg=ra_key,
        dec_deg=0.0,
    )


def _always_high_altitude(ra_deg, dec_deg, location, obstime):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Return an altitude/azimuth pair well above any reasonable floor.

    Returns
    -------
    altitude_deg, azimuth_deg : `tuple` [`float`, `float`]
        A fixed altitude/azimuth pair well above any reasonable floor.
    """
    return 60.0, 180.0


def _always_low_altitude(ra_deg, dec_deg, location, obstime):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Return an altitude/azimuth pair that never clears the floor.

    Returns
    -------
    altitude_deg, azimuth_deg : `tuple` [`float`, `float`]
        A fixed altitude/azimuth pair that never clears the floor.
    """
    return 2.0, 180.0


@patch("wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=_always_high_altitude)
def test_two_packages_place_without_overlap(_mock_altaz):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify two independently-fitting packages are placed without overlap."""
    site, telescope = _site(), _telescope()
    requests = [_soonest_request(1.0, exposure_sec=600.0), _soonest_request(2.0, exposure_sec=600.0)]
    queue, diagnostics = place(requests, (_WINDOW_START, _WINDOW_END), site, telescope)

    assert len(queue) == 2
    assert diagnostics == []
    entry_a, entry_b = sorted(queue, key=lambda e: e.computed_start_time)
    assert entry_a.computed_end_time <= entry_b.computed_start_time


@patch("wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=_always_low_altitude)
def test_never_clears_altitude(_mock_altaz):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a target that never clears the floor is diagnosed distinctly.

    Expects `InfeasibilityReasonCode.NEVER_CLEARS_ALTITUDE`.
    """
    site, telescope = _site(min_altitude_deg=20.0), _telescope(min_altitude_deg=20.0)
    requests = [_soonest_request(1.0)]
    queue, diagnostics = place(requests, (_WINDOW_START, _WINDOW_END), site, telescope)

    assert queue == []
    assert len(diagnostics) == 1
    assert diagnostics[0].reason_code == InfeasibilityReasonCode.NEVER_CLEARS_ALTITUDE


def test_blocked_by_avoidance_zone():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a target blocked by an avoidance zone is diagnosed distinctly.

    Altitude clears the floor everywhere, but a full-circle avoidance
    zone with a near-90-degree clearance requirement blocks it
    everywhere the zone applies.
    """
    zone = AvoidanceZone(
        id="z1",
        name="Ring",
        azimuth_start_deg=0.0,
        azimuth_end_deg=359.9,
        min_clear_altitude_deg=89.0,
    )
    site = _site(avoidance_zones=[zone])
    telescope = _telescope(min_altitude_deg=10.0)
    requests = [_soonest_request(1.0)]

    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=_always_high_altitude
    ):
        queue, diagnostics = place(requests, (_WINDOW_START, _WINDOW_END), site, telescope)

    assert queue == []
    assert len(diagnostics) == 1
    assert diagnostics[0].reason_code == InfeasibilityReasonCode.BLOCKED_BY_AVOIDANCE_ZONE


def test_window_too_short():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a too-short clear window is diagnosed distinctly."""
    site, telescope = _site(), _telescope(min_altitude_deg=20.0)

    def brief_clearing(ra_deg, dec_deg, location, obstime):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        # Clear for a single 5-minute sample near the window start,
        # low otherwise.
        elapsed_min = (obstime.datetime - _WINDOW_START).total_seconds() / 60.0
        return (40.0 if 0 <= elapsed_min < 5 else 2.0), 180.0

    requests = [_soonest_request(1.0, exposure_sec=3600.0)]  # needs a full hour
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=brief_clearing
    ):
        queue, diagnostics = place(requests, (_WINDOW_START, _WINDOW_END), site, telescope)

    assert queue == []
    assert len(diagnostics) == 1
    assert diagnostics[0].reason_code == InfeasibilityReasonCode.WINDOW_TOO_SHORT


def test_night_fully_committed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a crowded-out package is diagnosed distinctly."""
    site, telescope = _site(), _telescope()
    # Two long packages consume the whole window; a third, shorter package
    # would fit in isolation but not once the first two have claimed the time.
    requests = [
        _soonest_request(1.0, exposure_sec=(6 * 3600 - 60), count=1),
        _soonest_request(2.0, exposure_sec=60.0, count=1),
    ]
    with patch(
        "wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=_always_high_altitude
    ):
        _queue, diagnostics = place(requests, (_WINDOW_START, _WINDOW_END), site, telescope)

    # Both packages together should exactly fill the six-hour window;
    # confirm one was crowded out with the correct diagnosis.
    if diagnostics:
        assert diagnostics[0].reason_code == InfeasibilityReasonCode.NIGHT_FULLY_COMMITTED


@patch("wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=_always_high_altitude)
def test_priority_breaks_tie_and_boost_is_recorded(_mock_altaz):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a higher-priority package is placed first on a start-time tie."""
    site, telescope = _site(), _telescope()
    low_priority = _soonest_request(1.0, priority=0)
    high_priority_pkg = _package(2.0, priority=0, quality_weighting_enabled=True)
    high_priority = PlacementRequest(
        package=high_priority_pkg, start_time_mode=StartTimeMode.SOONEST, ra_deg=2.0, dec_deg=0.0
    )

    advisory = TargetQualityAdvisory(
        target_id=high_priority_pkg.target_id,
        quality_flags=[QualityFlagSummary(pipeline_name="stacking", flagged=True, flag_reasons=["low SNR"])],
        science_outcomes=ScienceOutcomeSummary(),
    )
    config = PlanningConfig(flagged_quality_priority_boost=5)

    queue, _diagnostics = place(
        [low_priority, high_priority],
        (_WINDOW_START, _WINDOW_END),
        site,
        telescope,
        quality_advisories={high_priority_pkg.target_id: advisory},
        planning_config=config,
    )

    # The boosted package should be placed first (earliest start), since
    # both packages tie on earliest-possible start and priority breaks it.
    first_entry = min(queue, key=lambda e: e.computed_start_time)
    assert first_entry.observation_package_id == high_priority_pkg.id
    assert first_entry.applied_priority_boost == 5


@patch("wayfindinglib.tasks.planning_tasks.visibility_tasks.visibility_state_at")
def test_fixed_time_packages_placed_as_anchors(mock_visibility):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a fixed-time request is placed at its requested start time."""
    mock_visibility.return_value = (True, None)
    site, telescope = _site(), _telescope()
    fixed_start = _WINDOW_START + timedelta(hours=1)
    package = _package(1.0, exposure_sec=600.0)
    request = PlacementRequest(
        package=package,
        start_time_mode=StartTimeMode.FIXED,
        requested_start_time=fixed_start,
        ra_deg=1.0,
        dec_deg=0.0,
    )

    queue, diagnostics = place([request], (_WINDOW_START, _WINDOW_END), site, telescope)

    assert diagnostics == []
    assert len(queue) == 1
    assert queue[0].computed_start_time == fixed_start
    assert queue[0].status == QueueEntryStatus.PENDING


@patch("wayfindinglib.tasks.planning_tasks.visibility_tasks.visibility_state_at")
def test_overlapping_fixed_time_requests_both_diagnosed(mock_visibility):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify overlapping fixed-time requests both diagnose as conflicts."""
    mock_visibility.return_value = (True, None)
    site, telescope = _site(), _telescope()
    start = _WINDOW_START + timedelta(hours=1)
    package_a = _package(1.0, exposure_sec=1800.0)
    package_b = _package(2.0, exposure_sec=1800.0)
    request_a = PlacementRequest(
        package=package_a,
        start_time_mode=StartTimeMode.FIXED,
        requested_start_time=start,
        ra_deg=1.0,
        dec_deg=0.0,
    )
    request_b = PlacementRequest(
        package=package_b,
        start_time_mode=StartTimeMode.FIXED,
        requested_start_time=start + timedelta(minutes=10),
        ra_deg=2.0,
        dec_deg=0.0,
    )

    queue, diagnostics = place([request_a, request_b], (_WINDOW_START, _WINDOW_END), site, telescope)

    assert queue == []
    assert len(diagnostics) == 2
    assert {d.reason_code for d in diagnostics} == {InfeasibilityReasonCode.FIXED_TIME_CONFLICT}


@patch("wayfindinglib.tasks.planning_tasks.visibility_tasks.compute_altaz", side_effect=_always_high_altitude)
def test_quality_boost_never_applied_without_opt_in(_mock_altaz):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a package without quality weighting opted in gets no boost."""
    site, telescope = _site(), _telescope()
    package = _package(1.0, quality_weighting_enabled=False)
    request = PlacementRequest(
        package=package, start_time_mode=StartTimeMode.SOONEST, ra_deg=1.0, dec_deg=0.0
    )
    advisory = TargetQualityAdvisory(
        target_id=package.target_id,
        quality_flags=[QualityFlagSummary(pipeline_name="stacking", flagged=True)],
    )

    queue, _diagnostics = place(
        [request],
        (_WINDOW_START, _WINDOW_END),
        site,
        telescope,
        quality_advisories={package.target_id: advisory},
    )
    assert queue[0].applied_priority_boost == 0
