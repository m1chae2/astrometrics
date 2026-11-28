"""Tests that photometry's star-width values follow the measured star width.

Each size in pixels that photometry takes from the star width (the aperture
and sky ring, the centroid box and the corner displacement limit) resolves
to its old absolute value when no run has measured a star width, and to its
factor times the measured width when one has.
"""

import pytest

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import RunBasis, TunedParameter
from astrometricslib.pipelines.photometry.post_processing.record_quality import (
    MAXIMUM_CORNER_DISPLACEMENT_PX,
)
from astrometricslib.pipelines.photometry.pre_processing import frame_photometry

STAR_WIDTH_VALUES: list[tuple[TunedParameter, float | int]] = [
    (MAXIMUM_CORNER_DISPLACEMENT_PX, 1.0),
    (frame_photometry.CENTROID_BOX_HALF_WIDTH_PX, 5),
    (frame_photometry.MAX_CENTROID_SHIFT_PX, 1.5),
    (frame_photometry.CENTROID_SKY_CUTOUT_HALF_WIDTH_PX, 15),
    (frame_photometry.APERTURE_RADIUS_PX, 4.0),
    (frame_photometry.SKY_ANNULUS_INNER_RADIUS_PX, 7.0),
    (frame_photometry.SKY_ANNULUS_OUTER_RADIUS_PX, 12.0),
]


@pytest.mark.parametrize(("parameter", "old_value"), STAR_WIDTH_VALUES)
def test_with_no_star_width_measured_the_old_value_is_kept(
    parameter: TunedParameter, old_value: float | int
) -> None:
    """With no basis measured, a value resolves to its old default."""
    with parameter_context():
        assert resolve(parameter).value == pytest.approx(old_value)


@pytest.mark.parametrize(("parameter", "old_value"), STAR_WIDTH_VALUES)
def test_a_measured_star_width_scales_the_value(parameter: TunedParameter, old_value: float | int) -> None:
    """A recorded 6 px star width (twice the reference) doubles it."""
    with parameter_context():
        # Twice the reference star width; the reference is what the factor
        # was fitted to (reference value ÷ factor).
        record_basis(
            RunBasis.STAR_WIDTH_PX, 2.0 * float(parameter.reference_value) / float(parameter.default)
        )
        assert resolve(parameter).value == pytest.approx(2.0 * old_value)
