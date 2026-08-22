"""Purpose: Unit tests for compute_focus_correction and sample_focus_curve.

Description: Verifies a known minimum is recovered from a synthetic
V-curve, an ill-conditioned fit is rejected without moving, a fitted
minimum outside the sampled span is clamped to it, and every sampled
position is approached from one direction -- the cases
`Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

import pytest

from wayfindinglib.models.equipment_and_site.focus_model import ApproachDirection
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import FocusCurvePoint
from wayfindinglib.tasks.control_tasks.focus_correction import (
    compute_focus_correction,
    sample_focus_curve,
)


def _v_curve(vertex_position: float, positions: list[float]) -> list[FocusCurvePoint]:
    return [
        FocusCurvePoint(
            focuser_position=round(position),
            measured_fwhm_px=0.0001 * (position - vertex_position) ** 2 + 2.0,
            star_count=20,
        )
        for position in positions
    ]


def test_recovers_known_minimum_from_synthetic_v_curve():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a clean parabola recovers its known vertex position."""
    curve = _v_curve(5000.0, [4900.0, 4950.0, 5000.0, 5050.0, 5100.0])
    config = CorrectionConfig(focus_fit_quality_floor=0.90)

    correction = compute_focus_correction(
        "run-1", curve, starting_position=4900, trigger_reason="scheduled", config=config
    )

    assert correction.converged is True
    assert correction.selected_position == pytest.approx(5000, abs=2)
    assert correction.fit_quality > 0.99


def test_ill_conditioned_fit_is_rejected_without_moving():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a noisy, non-parabolic curve is rejected and does not move."""
    curve = [
        FocusCurvePoint(focuser_position=4900, measured_fwhm_px=3.5, star_count=10),
        FocusCurvePoint(focuser_position=4950, measured_fwhm_px=2.1, star_count=15),
        FocusCurvePoint(focuser_position=5000, measured_fwhm_px=4.8, star_count=8),
        FocusCurvePoint(focuser_position=5050, measured_fwhm_px=1.9, star_count=18),
        FocusCurvePoint(focuser_position=5100, measured_fwhm_px=3.9, star_count=9),
    ]
    config = CorrectionConfig(focus_fit_quality_floor=0.90)

    correction = compute_focus_correction(
        "run-2", curve, starting_position=4900, trigger_reason="scheduled", config=config
    )

    assert correction.converged is False
    assert correction.selected_position == 4900


def test_fitted_minimum_outside_span_is_clamped():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a vertex beyond the sampled range clamps to the sampled edge."""
    # True vertex at 10000, but only sampled on the monotonically
    # decreasing side (100-500) -- the raw fit would extrapolate past 500.
    curve = _v_curve(10000.0, [100.0, 200.0, 300.0, 400.0, 500.0])
    config = CorrectionConfig(focus_fit_quality_floor=0.90)

    correction = compute_focus_correction(
        "run-3", curve, starting_position=100, trigger_reason="scheduled", config=config
    )

    assert correction.converged is True
    assert correction.selected_position == 500


def test_requires_at_least_three_points():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify fewer than 3 sampled points raises rather than proceeding."""
    curve = _v_curve(5000.0, [4900.0, 5000.0])
    config = CorrectionConfig()
    with pytest.raises(ValueError, match="at least 3"):
        compute_focus_correction(
            "run-4", curve, starting_position=4900, trigger_reason="scheduled", config=config
        )


def test_sample_focus_curve_approaches_every_point_from_one_direction():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify every focuser move after run-up proceeds monotonically inward."""
    positions_commanded: list[int] = []
    current_position = {"value": 5000}

    def move_focuser(target: int) -> None:
        positions_commanded.append(target)
        current_position["value"] = target

    def get_position() -> int:
        return current_position["value"]

    def measure_fwhm():  # ruff: ignore[missing-return-type-private-function]
        return (2.0, 20)

    curve = sample_focus_curve(
        move_focuser,
        get_position,
        measure_fwhm,
        starting_position=5000,
        sample_count=5,
        sample_span_steps=200,
        approach_direction=ApproachDirection.INWARD,
    )

    assert len(curve) == 5
    # Every commanded move -- including the run-up -- must proceed
    # monotonically in one direction; the sweep never reverses.
    assert positions_commanded == sorted(positions_commanded) or positions_commanded == sorted(
        positions_commanded, reverse=True
    )


def test_sample_focus_curve_skips_failed_measurements():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a position where measurement fails is dropped from the curve."""
    current_position = {"value": 5000}
    call_count = {"value": 0}

    def move_focuser(target: int) -> None:
        current_position["value"] = target

    def get_position() -> int:
        return current_position["value"]

    def measure_fwhm():  # ruff: ignore[missing-return-type-private-function]
        call_count["value"] += 1
        if call_count["value"] == 2:
            return None
        return (2.0, 20)

    curve = sample_focus_curve(
        move_focuser,
        get_position,
        measure_fwhm,
        starting_position=5000,
        sample_count=5,
        sample_span_steps=200,
        approach_direction=ApproachDirection.OUTWARD,
    )

    assert len(curve) == 4
