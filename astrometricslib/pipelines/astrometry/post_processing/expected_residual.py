"""The position residual that noise alone should leave.

It is stored beside the measured one.

After the Gaia refit, the stars sit some distance from their Gaia positions
(`astrometry.gaia_fit_rms_arcsec`). Part of that distance is not a fault of
the solution: the star finder cannot place a faint star exactly, and no
centroid is exact when the star falls at one place inside its pixel. A
reader who wants to know whether the rest is distortion or tilt needs the
part that noise already explains. This module finds it.

The expectation is made of parts, and each part is stored on its own:

* **Centroid noise.** A star's centroid is uncertain because the sky pixels
  around it are noisy. For a Gaussian star on a white-noise background the
  best any estimator can do (the Cramer-Rao bound) is a position error of
  ``sqrt(2 / pi) * n / A`` pixels along each axis, where ``A`` is the star's
  peak above the sky and ``n`` the standard deviation of the sky pixels.
  With ``SNR = A * sqrt(pi) * s / n`` the signal-to-noise ratio of an optimal
  aperture (``s`` the Gaussian width), this is ``sqrt(2) * s / SNR``, which is
  ``sqrt(2) * FWHM / (2.355 * SNR)``: the textbook ``FWHM / (2.355 SNR)``
  with the factor the bound adds. The width cancels, so only ``A / n`` is
  needed.
* **Pixel phase.** A star centered on a pixel and one centered on a pixel's
  corner put different amounts of light in the neighboring pixels, and every
  centroid method reads that difference as a small shift. The shift repeats
  with the star's position inside the pixel. It is large for a star whose
  width is near two pixels and shrinks as the star spreads over more pixels. It
  was found by injecting noise-free stars into a blank frame and finding them
  with the project's star finder (register entry AST-21).
* **Gaia position error at the image's epoch.** Gaia's own errors, and the
  growth of the proper-motion error since 2016.0. The cache of Gaia stars
  holds no error columns, so this part is never found. It is stored as not
  measured with that reason, so the gap shows.

Each part is a root mean square of the distance between a star and its Gaia
position, so it has two axes. The parts are stored; their sum in quadrature is
for the reader to take.

This module measures. It does not say whether the measured residual is
acceptable.
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.pipelines.astrometry.metrics import (
    ASTROMETRY_EXPECTED_CENTROID_NOISE_RMS_ARCSEC,
    ASTROMETRY_EXPECTED_GAIA_EPOCH_ERROR_RMS_ARCSEC,
    ASTROMETRY_EXPECTED_PIXEL_PHASE_RMS_ARCSEC,
    ASTROMETRY_EXPECTED_RESIDUAL_STAR_COUNT,
    ASTROMETRY_METRICS,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GAIA_EPOCH_ERROR_REASON",
    "PIXEL_PHASE_RMS_PX_BY_FWHM_PX",
    "ExpectedResidual",
    "centroid_error_per_axis_px",
    "expected_residual",
    "expected_residual_measurements",
    "pixel_phase_rms_per_axis_px",
    "record_expected_residual_quality",
]

_SPEC_BY_NAME = {spec.name: spec for spec in ASTROMETRY_METRICS}

# The position error of a Gaussian star on a white-noise background is at
# least this many times n / A pixels along an axis (the Cramer-Rao bound for
# the position of a Gaussian of known width and amplitude A, noise n per
# pixel): sqrt(2 / pi) = 0.798. Derived by integrating the Fisher information
# of the position, I = (A / n)**2 * pi / 2 per axis; a theory value, not tuned.
CRAMER_RAO_PEAK_FACTOR = math.sqrt(2.0 / math.pi)

# The root mean square error of the project's star finder from the pixel phase
# alone, per axis, in pixels, by the star's width (FWHM) in pixels. Found on
# 2026-10-23 by injecting 800 noise-free, pixel-integrated Gaussian stars at
# random positions for each width and finding them with
# `find_sources_above_background`; the entry is the mean of the x and y
# values (AST-21). The points at 1.75 and 2.25 px were added on the same day
# because the error falls steeply between 1.5 and 2.5 px, where this
# telescope's stars lie. Below 1.5 px the finder's kernel no longer fits the
# star and above 5 px no star of this telescope was seen, so the table stops
# there.
PIXEL_PHASE_RMS_PX_BY_FWHM_PX: tuple[tuple[float, float], ...] = (
    (1.5, 0.0918),
    (1.75, 0.0503),
    (2.0, 0.0331),
    (2.25, 0.0272),
    (2.5, 0.0260),
    (3.0, 0.0216),
    (4.0, 0.0144),
    (5.0, 0.0082),
)

GAIA_EPOCH_ERROR_REASON = (
    "The cached Gaia stars hold no position or proper-motion error columns, so the error of the Gaia "
    "positions at the image's epoch cannot be found."
)

_ARCSEC_PER_PIXEL_MISSING_REASON = "The stack was not solved, so it has no pixel scale."


@dataclass(frozen=True)
class ExpectedResidual:
    """The parts of the expected position residual, or the reason for a gap.

    Parameters
    ----------
    centroid_noise_rms_arcsec : `float` or `None`
        The root mean square distance that centroid noise leaves, in
        arcseconds. `None` when it cannot be found.
    centroid_noise_reason : `str`
        Why `centroid_noise_rms_arcsec` is `None`. Empty when it is not.
    pixel_phase_rms_arcsec : `float` or `None`
        The root mean square distance that the pixel phase leaves, in
        arcseconds. `None` when it cannot be found.
    pixel_phase_reason : `str`
        Why `pixel_phase_rms_arcsec` is `None`. Empty when it is not.
    star_count : `int`
        How many stars the centroid noise is over.
    """

    centroid_noise_rms_arcsec: float | None
    centroid_noise_reason: str
    pixel_phase_rms_arcsec: float | None
    pixel_phase_reason: str
    star_count: int


def centroid_error_per_axis_px(peak_over_noise: Sequence[float] | np.ndarray) -> np.ndarray:
    """Find the least centroid error of each star along one axis.

    Parameters
    ----------
    peak_over_noise : `Sequence` [`float`] or `numpy.ndarray`
        Each star's peak above the sky, over the standard deviation of the
        sky pixels (both in the same units).

    Returns
    -------
    error_px : `numpy.ndarray`
        ``sqrt(2 / pi) / (A / n)`` for each star, in pixels.
    """
    return CRAMER_RAO_PEAK_FACTOR / np.asarray(peak_over_noise, dtype=float)


def pixel_phase_rms_per_axis_px(fwhm_px: float | None) -> float | None:
    """Read the pixel-phase error of the star finder for a star width.

    Parameters
    ----------
    fwhm_px : `float` or `None`
        The stars' width (FWHM), in pixels.

    Returns
    -------
    error_px : `float` or `None`
        The error along one axis, in pixels, by straight-line interpolation
        in `PIXEL_PHASE_RMS_PX_BY_FWHM_PX`. `None` when the width is missing,
        not finite or outside the table.
    """
    if fwhm_px is None or not math.isfinite(fwhm_px):
        return None
    widths = [width for width, _ in PIXEL_PHASE_RMS_PX_BY_FWHM_PX]
    errors = [error for _, error in PIXEL_PHASE_RMS_PX_BY_FWHM_PX]
    if fwhm_px < widths[0] or fwhm_px > widths[-1]:
        return None
    return float(np.interp(fwhm_px, widths, errors))


def expected_residual(
    peak_over_noise: Sequence[float],
    signal_reason: str,
    fwhm_px: float | None,
    plate_scale_arcsec_per_pixel: float | None,
) -> ExpectedResidual:
    """Find the parts of the position residual that noise should leave.

    Parameters
    ----------
    peak_over_noise : `Sequence` [`float`]
        The peak over the sky noise of each star the Gaia refit used. Empty
        when there is none.
    signal_reason : `str`
        Why `peak_over_noise` is empty. Empty when it is not.
    fwhm_px : `float` or `None`
        The stack's star width (FWHM), in pixels, for the pixel-phase part.
    plate_scale_arcsec_per_pixel : `float` or `None`
        The solved pixel scale, in arcseconds per pixel.

    Returns
    -------
    expected : `ExpectedResidual`
        The two parts, each `None` with a reason when it cannot be found.
    """
    if plate_scale_arcsec_per_pixel is None or plate_scale_arcsec_per_pixel <= 0:
        return ExpectedResidual(
            None,
            _ARCSEC_PER_PIXEL_MISSING_REASON,
            None,
            _ARCSEC_PER_PIXEL_MISSING_REASON,
            len(peak_over_noise),
        )
    ratios = np.asarray(peak_over_noise, dtype=float)
    if ratios.size == 0:
        centroid_noise, centroid_reason = None, signal_reason or "The Gaia fit gave no stars with a peak."
    else:
        per_axis_px = centroid_error_per_axis_px(ratios)
        # Two axes: the distance to the Gaia position adds the two squared
        # errors.
        centroid_noise = float(math.sqrt(np.mean(2.0 * per_axis_px**2)) * plate_scale_arcsec_per_pixel)
        centroid_reason = ""
    phase_px = pixel_phase_rms_per_axis_px(fwhm_px)
    if phase_px is None:
        phase, phase_reason = (
            None,
            (
                "The stack has no star width, or it lies outside the 1.5 to 5 px that the "
                "pixel-phase table covers."
            ),
        )
    else:
        phase = float(math.sqrt(2.0) * phase_px * plate_scale_arcsec_per_pixel)
        phase_reason = ""
    return ExpectedResidual(centroid_noise, centroid_reason, phase, phase_reason, int(ratios.size))


def expected_residual_measurements(
    expected: ExpectedResidual, context: PipelineRunContext, stack_file_name: str
) -> list[QualityMeasurement]:
    """Build the four expected-residual measurements of one stack.

    Parameters
    ----------
    expected : `ExpectedResidual`
        The parts from `expected_residual`.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_file_name : `str`
        The analyzed stacked image's file name (not a mount path).

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        The centroid-noise part, the pixel-phase part, the Gaia epoch part
        (never measured) and the star count, all on the stack's subject.
    """
    subject = context.stack_subject(stack_file_name)
    star_count = expected.star_count if expected.centroid_noise_rms_arcsec is not None else None
    return [
        build_measurement(
            _SPEC_BY_NAME[ASTROMETRY_EXPECTED_CENTROID_NOISE_RMS_ARCSEC],
            expected.centroid_noise_rms_arcsec,
            context,
            subject,
            reason=expected.centroid_noise_reason,
            sample_count=star_count,
        ),
        build_measurement(
            _SPEC_BY_NAME[ASTROMETRY_EXPECTED_PIXEL_PHASE_RMS_ARCSEC],
            expected.pixel_phase_rms_arcsec,
            context,
            subject,
            reason=expected.pixel_phase_reason,
        ),
        build_measurement(
            _SPEC_BY_NAME[ASTROMETRY_EXPECTED_GAIA_EPOCH_ERROR_RMS_ARCSEC],
            None,
            context,
            subject,
            reason=GAIA_EPOCH_ERROR_REASON,
        ),
        build_measurement(
            _SPEC_BY_NAME[ASTROMETRY_EXPECTED_RESIDUAL_STAR_COUNT],
            None if star_count is None else float(star_count),
            context,
            subject,
            reason=expected.centroid_noise_reason,
        ),
    ]


def record_expected_residual_quality(
    peak_over_noise: Sequence[float],
    signal_reason: str,
    fwhm_px: float | None,
    plate_scale_arcsec_per_pixel: float | None,
    context: PipelineRunContext,
    stack_file_name: str,
    *,
    config: object = None,
) -> int:
    """Store the expected residual of one stack; no failure here fails the run.

    Parameters
    ----------
    peak_over_noise : `Sequence` [`float`]
        The peak over the sky noise of each star the Gaia refit used.
    signal_reason : `str`
        Why that list is empty, when it is.
    fwhm_px : `float` or `None`
        The stack's star width, in pixels.
    plate_scale_arcsec_per_pixel : `float` or `None`
        The solved pixel scale.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_file_name : `str`
        The analyzed stacked image's file name.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong (logged).
    """
    return build_and_record_measurements(
        lambda: expected_residual_measurements(
            expected_residual(peak_over_noise, signal_reason, fwhm_px, plate_scale_arcsec_per_pixel),
            context,
            stack_file_name,
        ),
        f"expected position residual of the astrometry run on {context.target_id}",
        config=config,
    )
