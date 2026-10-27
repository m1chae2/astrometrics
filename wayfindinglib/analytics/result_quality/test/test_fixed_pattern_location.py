"""Purpose: Tests for the fixed-pattern shape tests and the masters' share.

Description: Builds made-up per-aperture records (random noise in both the sum
and the difference, plus a known extra pattern in the sum) and checks that each
of the four shape tests (radial trend, gradient, rise near the target, banding)
finds its own pattern and not the others, that a record with no pattern finds
nothing, and that the calibration masters' share of the fixed-pattern variance
is worked out in stack units squared.
"""

from collections.abc import Callable

import numpy as np
import pytest

from wayfindinglib.analytics.result_quality.fixed_pattern_location import (
    MINIMUM_SHAPE_APERTURES,
    SHAPE_SIGMAS,
    FixedPatternAperture,
    FixedPatternLocation,
    FixedPatternShape,
    LinePatternScatters,
    apertures_from_record,
    explain_with_calibration_masters,
    locate_fixed_pattern,
)

WIDTH = 6000.0
HEIGHT = 4000.0
CENTER = (WIDTH / 2.0, HEIGHT / 2.0)
OFF_CENTER_TARGET = (1200.0, 900.0)
"""A target far from the center, so the two distances differ."""


def _apertures(
    seed: int,
    count: int = 120,
    target: tuple[float, float] | None = OFF_CENTER_TARGET,
    extra_variance: Callable[[float, float, float, float | None], float] | None = None,
    plane: tuple[float, float] = (0.0, 0.0),
) -> list[FixedPatternAperture]:
    """Build apertures whose sum and difference share one random variance.

    Parameters
    ----------
    seed : `int`
        The random seed.
    count : `int`, optional
        How many apertures.
    target : `tuple` [`float`, `float`] or `None`, optional
        The target's pixel position, or ``None`` for an unknown one.
    extra_variance : `Callable` or `None`, optional
        Gives the extra variance the sum carries at a position
        ``(x, y, distance_from_center, distance_from_target)``.
    plane : `tuple` [`float`, `float`], optional
        The slope of a plane added to the sum across the image width and
        height, in units of the random noise.

    Returns
    -------
    apertures : `list` [`FixedPatternAperture`]
        The apertures.
    """
    rng = np.random.default_rng(seed)
    result = []
    for _ in range(count):
        x = rng.uniform(0.0, WIDTH)
        y = rng.uniform(0.0, HEIGHT)
        from_center = float(np.hypot(x - CENTER[0], y - CENTER[1]))
        from_target = None if target is None else float(np.hypot(x - target[0], y - target[1]))
        extra = 0.0 if extra_variance is None else extra_variance(x, y, from_center, from_target)
        sum_value = (
            rng.normal() + np.sqrt(extra) * rng.normal() + plane[0] * x / WIDTH + plane[1] * y / HEIGHT
        )
        result.append(
            FixedPatternAperture(
                x=x,
                y=y,
                distance_from_center_px=from_center,
                distance_from_target_px=from_target,
                sum_value=float(sum_value),
                difference_value=float(rng.normal()),
            )
        )
    return result


def _significant(location: FixedPatternLocation) -> set[FixedPatternShape]:
    """List the shapes a location found.

    Parameters
    ----------
    location : `FixedPatternLocation`
        The result.

    Returns
    -------
    shapes : `set` [`FixedPatternShape`]
        The significant shapes.
    """
    return set(location.significant_shapes)


def test_a_record_with_no_pattern_finds_no_shape() -> None:
    """Sum and difference with one random variance give no shape."""
    location = locate_fixed_pattern(_apertures(3))
    assert _significant(location) == set()
    assert "None of the tested shapes" in location.sentence()


def test_the_false_alarm_rate_with_no_pattern_is_small() -> None:
    """Over many records with no pattern each shape fires rarely."""
    fired = dict.fromkeys(FixedPatternShape, 0)
    scatters = LinePatternScatters(
        difference_row=1.0,
        difference_column=1.0,
        sum_row=1.0,
        sum_column=1.0,
        row_count=100,
        column_count=100,
    )
    runs = 200
    for seed in range(runs):
        for shape in locate_fixed_pattern(_apertures(1000 + seed), scatters).significant_shapes:
            fired[shape] += 1
    # Each test is run at 2 standard errors; the radial one is two-sided, so
    # about 5 percent is expected, the others about 2.3 percent.
    assert fired[FixedPatternShape.RADIAL_TREND] / runs < 0.12
    assert fired[FixedPatternShape.NEAR_TARGET] / runs < 0.10
    assert fired[FixedPatternShape.GRADIENT] / runs < 0.10
    assert fired[FixedPatternShape.BANDING] == 0


def test_an_excess_that_grows_with_the_distance_from_the_center_is_a_radial_trend() -> None:
    """A flat-field residual grows toward the edge and is named alone."""
    location = locate_fixed_pattern(
        _apertures(5, extra_variance=lambda x, y, center, target: 6.0 * (center / 3600.0) ** 2)
    )
    assert FixedPatternShape.RADIAL_TREND in _significant(location)
    assert FixedPatternShape.NEAR_TARGET not in _significant(location)
    radial = location.test_for(FixedPatternShape.RADIAL_TREND)
    assert radial is not None
    assert radial.sigmas is not None
    assert radial.sigmas >= SHAPE_SIGMAS
    assert "grows" in radial.reason


def test_a_plane_in_the_sums_only_is_a_gradient() -> None:
    """A sky gradient is in the sum and not in the difference."""
    location = locate_fixed_pattern(_apertures(7, plane=(2.5, 1.5)))
    assert FixedPatternShape.GRADIENT in _significant(location)
    assert FixedPatternShape.RADIAL_TREND not in _significant(location)
    gradient = location.test_for(FixedPatternShape.GRADIENT)
    assert gradient is not None
    assert gradient.sigmas is not None
    assert gradient.sigmas >= SHAPE_SIGMAS


def test_an_excess_near_the_target_is_named_and_not_a_radial_trend() -> None:
    """Faint light around an off-center target is a rise near the target."""
    location = locate_fixed_pattern(
        _apertures(11, extra_variance=lambda x, y, center, target: 14.0 * np.exp(-((target / 1300.0) ** 2)))
    )
    assert FixedPatternShape.NEAR_TARGET in _significant(location)
    assert FixedPatternShape.RADIAL_TREND not in _significant(location)


def test_a_central_excess_with_the_target_at_the_center_is_ambiguous_not_radial() -> None:
    """A central excess at a central target is not blamed on flats."""
    location = locate_fixed_pattern(
        _apertures(
            13,
            target=CENTER,
            extra_variance=lambda x, y, center, target: 8.0 * np.exp(-((center / 1200.0) ** 2)),
        )
    )
    near = location.test_for(FixedPatternShape.NEAR_TARGET)
    radial = location.test_for(FixedPatternShape.RADIAL_TREND)
    assert near is not None
    assert radial is not None
    assert near.significant
    assert not radial.significant
    assert radial.ambiguous
    assert FixedPatternShape.RADIAL_TREND not in _significant(location)


def test_without_a_target_position_the_near_target_test_is_not_made() -> None:
    """Apertures with no target distance leave that test out and say why."""
    location = locate_fixed_pattern(_apertures(17, target=None))
    near = location.test_for(FixedPatternShape.NEAR_TARGET)
    assert near is not None
    assert not near.tested
    assert "position" in near.reason


def test_row_scatter_of_the_sum_well_above_the_difference_is_banding() -> None:
    """The four stored scatters decide banding: rows or columns."""
    scatters = LinePatternScatters(
        difference_row=1.0,
        difference_column=1.0,
        sum_row=1.6,
        sum_column=1.05,
        row_count=100,
        column_count=150,
    )
    location = locate_fixed_pattern(_apertures(19), scatters)
    banding = location.test_for(FixedPatternShape.BANDING)
    assert banding is not None
    assert banding.significant
    assert "rows" in banding.statistic
    assert FixedPatternShape.BANDING in _significant(location)


def test_equal_row_and_column_scatters_are_no_banding() -> None:
    """Equal scatters in the sum and the difference give no banding."""
    scatters = LinePatternScatters(
        difference_row=1.0,
        difference_column=1.0,
        sum_row=1.02,
        sum_column=0.98,
        row_count=100,
        column_count=100,
    )
    banding = locate_fixed_pattern(_apertures(19), scatters).test_for(FixedPatternShape.BANDING)
    assert banding is not None
    assert not banding.significant


def test_without_line_pattern_rows_banding_is_not_tested() -> None:
    """A stack without the line-pattern rows leaves banding out."""
    banding = locate_fixed_pattern(_apertures(19)).test_for(FixedPatternShape.BANDING)
    assert banding is not None
    assert not banding.tested


def test_too_few_apertures_leave_the_aperture_tests_out() -> None:
    """Fewer than the minimum apertures make no aperture test."""
    location = locate_fixed_pattern(_apertures(23, count=MINIMUM_SHAPE_APERTURES - 1))
    assert not any(test.tested for test in location.tests if test.shape != FixedPatternShape.BANDING)
    assert location.significant_shapes == []


def test_a_record_file_is_read_into_apertures_and_bad_entries_are_dropped() -> None:
    """The loader's dict becomes apertures; a bad entry is skipped."""
    record = {
        "apertures": [
            {
                "x": 1.0,
                "y": 2.0,
                "distance_from_center_px": 3.0,
                "distance_from_target_px": None,
                "sum": 0.5,
                "difference": -0.2,
            },
            {"x": 1.0},
            {
                "x": 1.0,
                "y": 2.0,
                "distance_from_center_px": 3.0,
                "distance_from_target_px": 4.0,
                "sum": float("nan"),
                "difference": 0.0,
            },
        ]
    }
    apertures = apertures_from_record(record)
    assert len(apertures) == 1
    assert apertures[0].distance_from_target_px is None
    assert apertures_from_record(None) == []


def test_masters_that_explain_all_of_the_excess_leave_nothing() -> None:
    """Flat and dark variances that add up to the excess leave about zero."""
    breakdown = explain_with_calibration_masters(
        fixed_pattern_variance=3.69 + 0.2, fixed_pattern_variance_error=0.5, flat_noise=1.5, dark_noise=1.2
    )
    assert breakdown is not None
    assert breakdown.flat_variance == pytest.approx(2.25)
    assert breakdown.dark_variance == pytest.approx(1.44)
    assert breakdown.remaining_variance == pytest.approx(0.2)
    assert breakdown.consistent_with_zero
    text = breakdown.sentence()
    assert "consistent with zero" in text
    assert "lower bound" in text


def test_masters_that_explain_part_of_the_excess_leave_a_remainder() -> None:
    """A remainder beyond two standard errors is reported as left."""
    breakdown = explain_with_calibration_masters(
        fixed_pattern_variance=4.0, fixed_pattern_variance_error=0.5, flat_noise=1.2, dark_noise=1.0
    )
    assert breakdown is not None
    assert breakdown.explained_variance == pytest.approx(2.44)
    assert breakdown.remaining_variance == pytest.approx(1.56)
    assert breakdown.remaining_sigmas == pytest.approx(3.12)
    assert not breakdown.consistent_with_zero
    assert "consistent with zero" not in breakdown.sentence()


def test_masters_larger_than_the_excess_are_said_to_be_larger() -> None:
    """A prediction above the whole measured excess is reported, not hidden."""
    breakdown = explain_with_calibration_masters(
        fixed_pattern_variance=1.0, fixed_pattern_variance_error=0.2, flat_noise=2.0, dark_noise=None
    )
    assert breakdown is not None
    assert breakdown.remaining_variance < 0.0
    assert not breakdown.consistent_with_zero
    assert "larger than the measured excess" in breakdown.sentence()


def test_the_dark_alone_or_nothing_is_handled() -> None:
    """One master is enough; none (or no excess) gives no breakdown."""
    only_dark = explain_with_calibration_masters(2.0, 0.3, None, 1.0)
    assert only_dark is not None
    assert only_dark.flat_variance is None
    assert explain_with_calibration_masters(2.0, 0.3, None, None) is None
    assert explain_with_calibration_masters(0.0, 0.3, 1.0, 1.0) is None
