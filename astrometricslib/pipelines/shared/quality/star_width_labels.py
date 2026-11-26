"""Name the estimator behind each star width a public result gives back.

The library measures star width in several ways: a Gaussian fit to the
brightest stars, a Moffat fit to many stars, a Gaussian fit on a stack, and a
few others. The numbers differ for the same stars, so a width is only useful
with the name of the way it was measured. `StarWidthEstimator` lists the
estimators that have a measured ratio to the one star-width scale (the
per-frame Gaussian of the brightest stars, validation register AST-17); a
width from one of them can be put on that scale. The other estimators named
here have no measured ratio, so their widths are compared only with widths
from the same estimator.

This module is the one place that says which estimator each stored `frame.*`
width metric comes from, and why a width has no value on the scale.
"""

from astrometricslib.foundation.run_bases import StarWidthEstimator, star_width_on_scale
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
    FRAME_STAR_WIDTH_EFFECTIVE_PX,
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
    FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
    FRAME_STAR_WIDTH_MOFFAT_PX,
    FRAME_STAR_WIDTH_REGION_PX,
)

__all__ = [
    "EFFECTIVE_WIDTH_ESTIMATOR",
    "RAW_CHECK_WIDTH_ESTIMATOR",
    "SIRIL_REGISTRATION_WIDTH_ESTIMATOR",
    "no_ratio_reason",
    "width_metric_estimator",
    "width_metric_not_on_scale_reason",
    "width_metric_on_scale_px",
]

EFFECTIVE_WIDTH_ESTIMATOR = "frame Moffat, square pixel blur added"
"""The effective width: the frame Moffat width with the pixel's blur added."""

RAW_CHECK_WIDTH_ESTIMATOR = "raw check second moment"
"""The raw frame check's width: the second moment of the brightest stars'
boxes, as the FWHM of a Gaussian of that spread."""

SIRIL_REGISTRATION_WIDTH_ESTIMATOR = "Siril registration"
"""The plain FWHM Siril writes for each frame when it registers a stack."""

_PIXEL_SCALE_REASON = (
    "the one star-width scale is in pixels; the matching pixel metric gives the width on the scale"
)
"""Why a width in arcseconds has no value on the scale."""

_ESTIMATOR_BY_METRIC: dict[str, str] = {
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX: StarWidthEstimator.FRAME_GAUSSIAN_BRIGHTEST.value,
    FRAME_STAR_WIDTH_MOFFAT_PX: StarWidthEstimator.FRAME_MOFFAT.value,
    FRAME_STAR_WIDTH_MOFFAT_ARCSEC: StarWidthEstimator.FRAME_MOFFAT.value,
    # The region widths are the same per-star Moffat widths, for the stars of
    # one region of the field, so they take the frame Moffat ratio too. That
    # ratio was measured on whole-frame medians (AST-17); off-axis star
    # profiles can differ a little, so a corner's scale value is rougher.
    **dict.fromkeys(FRAME_STAR_WIDTH_REGION_PX.values(), StarWidthEstimator.FRAME_MOFFAT.value),
    FRAME_STAR_WIDTH_EFFECTIVE_PX: EFFECTIVE_WIDTH_ESTIMATOR,
    FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC: EFFECTIVE_WIDTH_ESTIMATOR,
}
"""The estimator of each stored star-width metric, by metric name."""

_PIXEL_METRICS = frozenset(name for name in _ESTIMATOR_BY_METRIC if name.endswith("_px"))
"""The width metrics given in pixels."""


def no_ratio_reason(estimator: str) -> str:
    """Say why a width from an estimator has no value on the one scale.

    Parameters
    ----------
    estimator : `str`
        The estimator's name.

    Returns
    -------
    reason : `str`
        A sentence fragment naming the estimator and the estimators that do
        have a ratio.
    """
    with_ratio = "; ".join(repr(member.value) for member in StarWidthEstimator)
    return (
        f"no ratio of the {estimator!r} width to the one star-width scale has been measured "
        f"(validation register AST-17 has ratios for: {with_ratio})"
    )


def _metric_name(key: str) -> str:
    """Strip a color plane from a value key.

    Returns
    -------
    name : `str`
        The metric name of a key such as ``"frame.star_width_moffat_px[R]"``.
    """
    return key.split("[", 1)[0]


def width_metric_estimator(key: str) -> str | None:
    """Name the estimator behind a stored star-width metric.

    Parameters
    ----------
    key : `str`
        A metric name, with ``[plane]`` after it for a color plane.

    Returns
    -------
    estimator : `str` or `None`
        The estimator's name, or `None` when the metric is not a star width.
    """
    return _ESTIMATOR_BY_METRIC.get(_metric_name(key))


def width_metric_not_on_scale_reason(key: str) -> str | None:
    """Say why a star-width metric has no value on the one scale.

    Parameters
    ----------
    key : `str`
        A metric name, with ``[plane]`` after it for a color plane.

    Returns
    -------
    reason : `str` or `None`
        Why the metric cannot be put on the scale, or `None` when it can or
        when it is not a star width.
    """
    estimator = width_metric_estimator(key)
    if estimator is None:
        return None
    if _metric_name(key) not in _PIXEL_METRICS:
        return _PIXEL_SCALE_REASON
    if estimator not in {member.value for member in StarWidthEstimator}:
        return no_ratio_reason(estimator)
    return None


def width_metric_on_scale_px(key: str, value: float) -> float | None:
    """Put a stored star-width metric's value on the one star-width scale.

    Parameters
    ----------
    key : `str`
        A metric name, with ``[plane]`` after it for a color plane.
    value : `float`
        The metric's value, in pixels.

    Returns
    -------
    width : `float` or `None`
        The width on the scale of the per-frame Gaussian of the brightest
        stars, in pixels; `None` when the metric is not a star width or has
        no ratio to the scale (`width_metric_not_on_scale_reason` says why).
    """
    estimator = width_metric_estimator(key)
    if estimator is None or width_metric_not_on_scale_reason(key) is not None:
        return None
    return float(star_width_on_scale(float(value), StarWidthEstimator(estimator)))
