"""Purpose: Unit tests for observation package domain models.

Description: Verifies ExposureRequest's total_exposure_sec pacing
arithmetic and ObservationPackage's total_duration_sec sums mixed
frame types including inter-exposure delay.
"""

import pytest

from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    FrameType,
    ObservationPackage,
)


def test_exposure_request_total_time_with_no_delay():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify total_exposure_sec is exposure x count with zero pacing delay."""
    request = ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=10)
    assert request.total_exposure_sec() == pytest.approx(3000.0)


def test_exposure_request_total_time_includes_pacing_delay():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify delay_sec is applied between frames, not after the last one.

    10 frames have only 9 gaps between them.
    """
    request = ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=10, delay_sec=5.0)
    assert request.total_exposure_sec() == pytest.approx(3000.0 + 9 * 5.0)


def test_exposure_request_single_frame_has_no_delay_applied():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a single-frame request applies no pacing delay."""
    request = ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=1, delay_sec=5.0)
    assert request.total_exposure_sec() == pytest.approx(300.0)


def test_observation_package_sums_mixed_frame_types():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify total_duration_sec sums light and calibration exposures."""
    package = ObservationPackage(
        id="pkg1",
        name="M 81 LRGB",
        target_id="M 81",
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=10),
            ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=5),
        ],
    )
    assert package.total_duration_sec() == pytest.approx(3000.0 + 1500.0)


def test_observation_package_defaults():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify default priority, quality weighting, and optional fields."""
    package = ObservationPackage(id="pkg1", name="M 81", target_id="M 81")
    assert package.priority == 0
    assert package.quality_weighting_enabled is False
    assert package.dither_config is None
    assert package.minimum_altitude_deg is None


def test_dither_config_defaults():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify DitherConfig's default cadence and amplitude."""
    dither = DitherConfig()
    assert dither.enabled is False
    assert dither.every_n_frames == 3
    assert dither.pixels == pytest.approx(3.0)
