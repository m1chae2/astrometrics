"""Tests for naming the estimator behind each stored star width.

A width is only useful with the name of the way it was measured, and only a
width whose estimator has a measured ratio can be put on the one star-width
scale. These tests check the estimator of each width metric, the value on the
scale and the reason a width has none.
"""

import pytest

from astrometricslib.foundation.run_bases import (
    STAR_WIDTH_RATIO_FRAME_MOFFAT,
    StarWidthEstimator,
    star_width_on_scale,
)
from astrometricslib.pipelines.shared.quality import star_width_labels as labels
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_SKY_NOISE_E,
    FRAME_STAR_WIDTH_CENTER_PX,
    FRAME_STAR_WIDTH_EFFECTIVE_PX,
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
    FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
    FRAME_STAR_WIDTH_MOFFAT_PX,
)


def test_the_moffat_width_is_put_on_the_scale_with_its_ratio() -> None:
    """The Moffat width of a frame, plane or region uses the Moffat ratio."""
    ratio = float(STAR_WIDTH_RATIO_FRAME_MOFFAT.default)  # type: ignore[arg-type]
    for key in (FRAME_STAR_WIDTH_MOFFAT_PX, f"{FRAME_STAR_WIDTH_MOFFAT_PX}[R]", FRAME_STAR_WIDTH_CENTER_PX):
        assert labels.width_metric_estimator(key) == StarWidthEstimator.FRAME_MOFFAT.value
        assert labels.width_metric_on_scale_px(key, 2.0) == pytest.approx(2.0 * ratio)
        assert labels.width_metric_not_on_scale_reason(key) is None


def test_the_brightest_star_gaussian_is_the_scale_itself() -> None:
    """The Gaussian width of the brightest stars comes back unchanged."""
    key = FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX
    assert labels.width_metric_estimator(key) == StarWidthEstimator.FRAME_GAUSSIAN_BRIGHTEST.value
    assert labels.width_metric_on_scale_px(key, 2.33) == pytest.approx(
        star_width_on_scale(2.33, StarWidthEstimator.FRAME_GAUSSIAN_BRIGHTEST)
    )


def test_the_effective_width_has_no_ratio_and_says_so() -> None:
    """The effective width names its estimator but has no scale value."""
    key = FRAME_STAR_WIDTH_EFFECTIVE_PX
    assert labels.width_metric_estimator(key) == labels.EFFECTIVE_WIDTH_ESTIMATOR
    assert labels.width_metric_on_scale_px(key, 2.0) is None
    reason = labels.width_metric_not_on_scale_reason(key)
    assert reason is not None and "AST-17" in reason


def test_a_width_in_arcseconds_points_at_the_pixel_metric() -> None:
    """An arcsecond width keeps its estimator, and the reason names pixels."""
    key = FRAME_STAR_WIDTH_MOFFAT_ARCSEC
    assert labels.width_metric_estimator(key) == StarWidthEstimator.FRAME_MOFFAT.value
    assert labels.width_metric_on_scale_px(key, 2.0) is None
    assert "pixels" in (labels.width_metric_not_on_scale_reason(key) or "")


def test_a_metric_that_is_not_a_width_has_no_label() -> None:
    """A sky metric gets no estimator, no scale value and no reason."""
    assert labels.width_metric_estimator(FRAME_SKY_NOISE_E) is None
    assert labels.width_metric_on_scale_px(FRAME_SKY_NOISE_E, 1.0) is None
    assert labels.width_metric_not_on_scale_reason(FRAME_SKY_NOISE_E) is None


def test_the_no_ratio_reason_names_the_estimator_and_those_with_ratios() -> None:
    """The reason quotes the estimator and every estimator that has a ratio."""
    reason = labels.no_ratio_reason(labels.RAW_CHECK_WIDTH_ESTIMATOR)
    assert repr(labels.RAW_CHECK_WIDTH_ESTIMATOR) in reason
    for member in StarWidthEstimator:
        assert repr(member.value) in reason
