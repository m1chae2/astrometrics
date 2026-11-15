"""Tests for the stacking values that follow a basis measured each run.

The alignment, star-gain and star-pattern values that stand for a size in
pixels are written as a factor times a basis (the star width or the plate
scale). With no basis measured they must give their old absolute values, and
once the run records a basis they must scale with it.
"""

import numpy as np
import pytest

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import RunBasis, TunedParameter
from astrometricslib.pipelines.stacking.processing import group_alignment, group_star_gains
from astrometricslib.pipelines.stacking.processing.star_pattern_candidates import (
    PATTERN_MINIMUM_LONGEST_SIDE_PIXELS,
)

STAR_WIDTH_VALUES: list[tuple[TunedParameter, float]] = [
    (group_alignment.ALIGNMENT_HIGH_PASS_SIGMA_PIXELS, 8.0),
    (group_alignment.ZERO_ORDER_SMOOTHING_SIGMA_PIXELS, 2.0),
    (group_alignment.ZERO_ORDER_CENTROID_HALF_WINDOW_PIXELS, 25),
    (group_alignment.ZERO_ORDER_RIVAL_SEPARATION_PIXELS, 40),
    (group_alignment.REFINEMENT_CENTROID_HALF_WINDOW_PIXELS, 4),
    (group_alignment.REFINEMENT_MATCH_RADIUS_PIXELS, 3.0),
    (group_alignment.REFINEMENT_MAX_RESIDUAL_PIXELS, 0.5),
    (group_alignment.PATTERN_MAX_RESIDUAL_PIXELS, 1.5),
    (group_star_gains.STAR_GAIN_SKY_INNER_PIXELS, 19.0),
    (group_star_gains.STAR_GAIN_SKY_OUTER_PIXELS, 25.0),
    (PATTERN_MINIMUM_LONGEST_SIDE_PIXELS, 20.0),
]

PLATE_SCALE_VALUES: list[tuple[TunedParameter, float]] = [
    (group_alignment.ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS, 150),
    (group_alignment.REFINEMENT_EDGE_MARGIN_PIXELS, 60),
]


@pytest.mark.parametrize(("parameter", "old_value"), STAR_WIDTH_VALUES + PLATE_SCALE_VALUES)
def test_with_no_basis_measured_the_old_value_stands(parameter: TunedParameter, old_value: float) -> None:
    """With no basis known, each converted value gives its old default."""
    with parameter_context():
        assert resolve(parameter).value == pytest.approx(old_value)


@pytest.mark.parametrize(("parameter", "old_value"), STAR_WIDTH_VALUES)
def test_a_recorded_star_width_scales_the_value(parameter: TunedParameter, old_value: float) -> None:
    """A star width twice the reference width doubles each star-width value."""
    with parameter_context():
        record_basis(RunBasis.STAR_WIDTH_PX, 6.0)
        resolved = resolve(parameter).value
    assert resolved == pytest.approx(2.0 * old_value, abs=0.5 if isinstance(old_value, int) else 1e-9)


@pytest.mark.parametrize(("parameter", "old_value"), PLATE_SCALE_VALUES)
def test_a_recorded_plate_scale_divides_the_value(parameter: TunedParameter, old_value: float) -> None:
    """A plate scale twice as coarse halves each sky angle in pixels."""
    reference_scale = 206.265 * 3.76 / 405.0
    with parameter_context():
        record_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, 2.0 * reference_scale)
        resolved = resolve(parameter).value
    assert resolved == pytest.approx(0.5 * old_value, abs=0.5)


def test_the_rival_window_follows_the_search_window() -> None:
    """The rival half width is twice the search half width, in a run or not."""
    with parameter_context():
        assert group_alignment.extended_target_rival_half_width_pixels() == pytest.approx(300.0)
        reference_scale = 206.265 * 3.76 / 405.0
        record_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, 2.0 * reference_scale)
        assert group_alignment.extended_target_rival_half_width_pixels() == pytest.approx(150.0, abs=1.0)


def test_aligning_records_the_image_short_side() -> None:
    """Aligning group stacks records the shorter side of their images."""
    image = np.zeros((40, 60), dtype=np.float32)
    with parameter_context() as context:
        group_alignment.align_images_to_reference([image], reference_index=0)
        assert context.bases[RunBasis.IMAGE_SHORT_SIDE_PX] == pytest.approx(40.0)
