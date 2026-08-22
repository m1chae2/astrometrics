"""Purpose: Unit tests for calibration advisory computation.

Description: Verifies build_calibration_advisory returns the matching
existing count for a requested frame type/exposure/filter, and a zero
count when none exists, without influencing anything beyond the
returned advisory.
"""

import pytest

from astrometricslib import FilterType
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.calibration import CalibrationEntry, CalibrationStats
from wayfindinglib.models.planning.observation_package import FrameType
from wayfindinglib.tasks.planning_tasks.calibration_advisory_tasks import build_calibration_advisory


@pytest.fixture
def isolated_butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by a fully isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def test_returns_matching_existing_count(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a matching frame_type/exposure/filter returns the count."""
    stats = CalibrationStats(
        camera_id="c1",
        darks=[
            CalibrationEntry(camera_id="c1", frame_type=FrameType.DARK, exposure_sec=300.0, count=40),
            CalibrationEntry(camera_id="c1", frame_type=FrameType.DARK, exposure_sec=600.0, count=10),
        ],
    )
    isolated_butler.put(stats, "calibration_stats", {"camera_id": "c1"})

    advisory = build_calibration_advisory(isolated_butler, "c1", FrameType.DARK, exposure_sec=300.0)
    assert advisory.existing_count == 40


def test_returns_zero_when_no_matching_entry(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a zero count is returned, not an error, when no entry matches."""
    advisory = build_calibration_advisory(isolated_butler, "c1", FrameType.FLAT, exposure_sec=1.0)
    assert advisory.existing_count == 0


def test_returns_zero_when_no_stats_persisted_at_all(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unconfigured camera resolves to zero, not a raise."""
    advisory = build_calibration_advisory(isolated_butler, "unconfigured-camera", FrameType.BIAS)
    assert advisory.existing_count == 0


def test_filter_matching_for_flats(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify filter distinguishes matching entries for flats."""
    stats = CalibrationStats(
        camera_id="c1",
        flats=[
            CalibrationEntry(camera_id="c1", frame_type=FrameType.FLAT, filter=FilterType.L, count=20),
            CalibrationEntry(camera_id="c1", frame_type=FrameType.FLAT, filter=FilterType.Ha, count=5),
        ],
    )
    isolated_butler.put(stats, "calibration_stats", {"camera_id": "c1"})

    advisory = build_calibration_advisory(isolated_butler, "c1", FrameType.FLAT, filter=FilterType.Ha)
    assert advisory.existing_count == 5
