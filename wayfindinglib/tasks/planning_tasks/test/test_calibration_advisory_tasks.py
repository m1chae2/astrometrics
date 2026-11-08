"""Purpose: Unit tests for calibration advisory computation.

Description: Verifies build_calibration_advisory counts the frames the
calibration library holds (the source the Calibration Library view shows)
for a requested frame type, exposure, filter and settings, and returns zero
when none match. The library is the real `CalibrationCatalog` over a stub
index, so the library's own matching rules are exercised.
"""

from types import SimpleNamespace
from typing import Any

from astrometricslib import FilterType
from astrometricslib.api.processing import CalibrationCatalog
from wayfindinglib.models.planning.observation_package import FrameType
from wayfindinglib.tasks.planning_tasks.calibration_advisory_tasks import build_calibration_advisory

CAMERA = "ZWO ASI 533MM Pro"


def _catalog() -> CalibrationCatalog:
    """Build a calibration catalog over a small stub index.

    Returns
    -------
    catalog : `CalibrationCatalog`
        A catalog whose index holds darks at gain 0 and 100, flats through
        two filters and biases, all written the way the real index writes
        them (gain ``"0.0"``, binning ``"1x1"``).
    """
    stats: dict[str, Any] = {
        "darks": [
            {
                "camera": CAMERA,
                "iso": "0.0",
                "offset": 10.0,
                "binning": "1x1",
                "temperature_c": -9.0,
                "exposure": 60.0,
                "count": 121,
            },
            {
                "camera": CAMERA,
                "iso": "0.0",
                "offset": 10.0,
                "binning": "1x1",
                "temperature_c": -9.0,
                "exposure": 120.0,
                "count": 30,
            },
            {
                "camera": CAMERA,
                "iso": "100.0",
                "offset": 10.0,
                "binning": "1x1",
                "temperature_c": -9.0,
                "exposure": 60.0,
                "count": 7,
            },
            {
                "camera": CAMERA,
                "iso": "0.0",
                "offset": 0.0,
                "binning": "1x1",
                "temperature_c": 0.0,
                "exposure": 60.0,
                "count": 5,
            },
        ],
        "biases": [
            {"camera": CAMERA, "iso": "0.0", "offset": 0.0, "binning": "1x1", "count": 100},
        ],
        "flats": [
            {
                "camera": CAMERA,
                "filter": "Luminance",
                "iso": "0.0",
                "offset": 10.0,
                "binning": "1x1",
                "count": 30,
            },
            {
                "camera": CAMERA,
                "filter": "Star Analyzer 200",
                "iso": "0.0",
                "offset": 10.0,
                "binning": "1x1",
                "count": 30,
            },
        ],
    }
    catalog = CalibrationCatalog(SimpleNamespace(), None)
    catalog._library = SimpleNamespace(get_stats=lambda: stats, load_library=lambda: None)
    return catalog


def test_counts_darks_the_library_holds_with_integer_settings() -> None:
    """Gain 0 matches a ``"0.0"`` index entry; unrelated rows are left out."""
    advisory = build_calibration_advisory(
        _catalog(),
        CAMERA,
        FrameType.DARK,
        60.0,
        gain_setting=0,
        offset=10,
        sensor_temperature_c=-10.0,
    )
    assert advisory.existing_count == 121


def test_exposure_and_no_other_filters() -> None:
    """With only an exposure, every gain and offset of that exposure counts."""
    assert build_calibration_advisory(_catalog(), CAMERA, FrameType.DARK, 60.0).existing_count == 133


def test_all_darks_without_filters() -> None:
    """With no filters at all, every dark of the camera counts."""
    assert build_calibration_advisory(_catalog(), CAMERA, FrameType.DARK).existing_count == 163


def test_camera_is_matched_ignoring_case() -> None:
    """The camera name's case does not matter."""
    assert build_calibration_advisory(_catalog(), CAMERA.lower(), FrameType.BIAS).existing_count == 100


def test_other_camera_has_none() -> None:
    """A camera the library does not hold gives zero, not an error."""
    assert build_calibration_advisory(_catalog(), "Other", FrameType.DARK).existing_count == 0


def test_flat_filter_by_short_or_long_name() -> None:
    """``L`` and ``Luminance`` are the same filter, and others are excluded."""
    luminance = build_calibration_advisory(
        _catalog(), CAMERA, FrameType.FLAT, filter=FilterType.L, gain_setting=0, offset=10
    )
    assert luminance.existing_count == 30
    spectrum = build_calibration_advisory(_catalog(), CAMERA, FrameType.FLAT, filter=FilterType.SPEC)
    assert spectrum.existing_count == 30
    assert build_calibration_advisory(_catalog(), CAMERA, FrameType.FLAT).existing_count == 60


def test_gain_offset_binning_and_temperature_narrow_the_match() -> None:
    """A row with a different setting is not counted."""
    catalog = _catalog()

    def count(**keywords: float) -> int:
        """Count the darks of 60 s that match the given settings.

        Returns
        -------
        count : `int`
            The matching frame count.
        """
        return build_calibration_advisory(catalog, CAMERA, FrameType.DARK, 60.0, **keywords).existing_count

    assert count(gain_setting=100) == 7
    assert count(offset=0) == 5
    assert count(binning=1) == 133
    assert count(binning=2) == 0
    assert count(sensor_temperature_c=-8.0) == 128
    assert count(sensor_temperature_c=-25.0) == 0
