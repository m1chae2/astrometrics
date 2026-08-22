"""Purpose: Unit tests for focus_model_tasks.

Description: Verifies backlash, thermal coefficient, and per-filter
offset measurement recover known values from simulated sequences --
the case `Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

import pytest

from wayfindinglib.tasks.control_tasks.focus_model_tasks import (
    fit_thermal_coefficient_steps_per_c,
    measure_backlash_steps,
    measure_filter_offsets,
)


def test_measure_backlash_steps_recovers_known_lost_motion():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the gap between expected and actual position is the backlash."""
    assert measure_backlash_steps(expected_position=5000, actual_position=4950) == 50


def test_measure_backlash_steps_is_zero_for_a_perfect_reversal():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no lost motion yields zero backlash steps."""
    assert measure_backlash_steps(expected_position=5000, actual_position=5000) == 0


def test_fit_thermal_coefficient_recovers_known_slope():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a clean linear temperature/position curve recovers slope."""
    # 10 steps per degree C, positive slope.
    pairs = [(0.0, 5000), (5.0, 5050), (10.0, 5100), (-5.0, 4950)]
    coefficient = fit_thermal_coefficient_steps_per_c(pairs)
    assert coefficient == pytest.approx(10.0)


def test_fit_thermal_coefficient_requires_at_least_two_pairs():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify fewer than 2 recorded pairs raises rather than proceeding."""
    with pytest.raises(ValueError, match="at least 2"):
        fit_thermal_coefficient_steps_per_c([(0.0, 5000)])


def test_measure_filter_offsets_recovers_known_offsets():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify offsets are relative to the baseline filter, which is omitted."""
    offsets = measure_filter_offsets(
        "Luminance",
        {"Luminance": 5000, "Red": 5020, "Green": 5010, "Blue": 4990},
    )
    offsets_by_filter = {offset.filter: offset.offset_steps for offset in offsets}
    assert offsets_by_filter == {"Red": 20, "Green": 10, "Blue": -10}
    assert "Luminance" not in offsets_by_filter


def test_measure_filter_offsets_requires_baseline_measured():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a missing baseline filter measurement raises."""
    with pytest.raises(ValueError, match="Luminance"):
        measure_filter_offsets("Luminance", {"Red": 5020})
