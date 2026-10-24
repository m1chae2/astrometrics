"""Purpose: Unit tests for calibration advisory computation.

Description: Verifies build_calibration_advisory returns the matching
existing count for a requested frame type/exposure/filter, and a zero
count when none exists, without influencing anything beyond the
returned advisory.
"""

from pathlib import Path

import pytest

from astrometricslib import FilterType
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.calibration import CalibrationEntry, CalibrationStats
from wayfindinglib.models.planning.observation_package import FrameType
from wayfindinglib.tasks.planning_tasks.calibration_advisory_tasks import build_calibration_advisory


@pytest.fixture
def isolated_butler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler backed by a fully isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def test_returns_matching_existing_count(isolated_butler: DiskButler) -> None:
    """Verify a matching frame_type/exposure/filter returns the count."""
    stats = CalibrationStats(
        camera_id="c1",
        darks=[
            CalibrationEntry(camera_id="c1", frame_type=FrameType.DARK, exposure_sec=300.0, count=40),
            CalibrationEntry(camera_id="c1", frame_type=FrameType.DARK, exposure_sec=600.0, count=10),
        ],
    )
    isolated_butler.put(stats, "calibration_stats", {"camera_id": "c1"})

    advisory = build_calibration_advisory(isolated_butler, "c1", FrameType.DARK, exposure_seconds=300.0)
    assert advisory.existing_count == 40


def test_returns_zero_when_no_matching_entry(isolated_butler: DiskButler) -> None:
    """Verify a zero count is returned, not an error, when no entry matches."""
    advisory = build_calibration_advisory(isolated_butler, "c1", FrameType.FLAT, exposure_seconds=1.0)
    assert advisory.existing_count == 0


def test_returns_zero_when_no_stats_persisted_at_all(isolated_butler: DiskButler) -> None:
    """Verify an unconfigured camera resolves to zero, not a raise."""
    advisory = build_calibration_advisory(isolated_butler, "unconfigured-camera", FrameType.BIAS)
    assert advisory.existing_count == 0


def test_filter_matching_for_flats(isolated_butler: DiskButler) -> None:
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


def test_gain_offset_binning_and_temperature_narrow_the_match(isolated_butler: DiskButler) -> None:
    """Entries with a different setting are not counted; unstated ones are."""
    stats = CalibrationStats(
        camera_id="c1",
        darks=[
            CalibrationEntry(
                camera_id="c1",
                frame_type=FrameType.DARK,
                exposure_sec=60.0,
                gain_setting=100,
                offset=50,
                binning=1,
                sensor_temperature_c=-10.0,
                count=20,
            ),
            CalibrationEntry(
                camera_id="c1", frame_type=FrameType.DARK, exposure_sec=60.0, gain_setting=200, count=5
            ),
            CalibrationEntry(camera_id="c1", frame_type=FrameType.DARK, exposure_sec=60.0, count=3),
        ],
    )
    isolated_butler.put(stats, "calibration_stats", {"camera_id": "c1"})

    def count(**keywords: float) -> int:
        """Count the darks of 60 s that match the given settings.

        Returns
        -------
        count : `int`
            The matching frame count.
        """
        return build_calibration_advisory(
            isolated_butler, "c1", FrameType.DARK, exposure_seconds=60.0, **keywords
        ).existing_count

    assert count() == 28
    # The 200-gain entry differs; the entry with no settings still counts.
    assert count(gain_setting=100) == 23
    assert count(gain_setting=100, offset=10) == 3
    assert count(gain_setting=100, binning=2) == 3
    # 2 degrees away is inside the 3 degree tolerance; 8 is not.
    assert count(gain_setting=100, sensor_temperature_c=-8.0) == 23
    assert count(gain_setting=100, sensor_temperature_c=-2.0) == 3
