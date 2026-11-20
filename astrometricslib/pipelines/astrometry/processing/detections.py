"""Showing what the astrometry run's star finder detects in one image.

The viewer's Detections overlay needs the stars the run would find in an
image, computed now from the pixels, so it works on every image without
running the analysis again. This module reads one file and calls
`find_unique_stars`, the same function `StarIdentifier.detect_stars` calls,
so the two cannot drift apart. It changes nothing on disk.

The photometry circle is sized from this image's own star width, measured
first the way a run measures it, so it fits the stars on screen whichever
setup is active. Only when no star can be measured does it fall back to the
active setup's typical width. The finder itself is built exactly as the run
builds it: for a mono image it sizes its kernel from the image's measured
blob width, and for a color image it uses the setup's width.
"""

import numpy as np

from astrometricslib._internal.drivers.fits_access import collapse_to_2d
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.parameter_context import setup_parameter_context
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import StarWidthEstimator, record_star_width, star_width_on_scale
from astrometricslib.models.quality.detections import DetectionsMeasurement, StarDetection
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.astrometry.processing.star_identifier import find_unique_stars

__all__ = ["find_star_centers", "measure_detections_in_file"]


def find_star_centers(data: np.ndarray) -> list[tuple[float, float]]:
    """List the stars the astrometry star finder finds in an image piece.

    The viewer's ring analysis uses this to find the neighbors of one star,
    on a small box around it. It is the same finder and the same merging of
    double reports as the Detections overlay.

    Parameters
    ----------
    data : `numpy.ndarray`
        A two-dimensional piece of an image.

    Returns
    -------
    centers : `list` [`tuple` [`float`, `float`]]
        The 0-based ``(column, row)`` of each star, as the finder gives it.
    """
    _, unique_sources = find_unique_stars(SourceDetector(), np.asarray(data, dtype=float))
    return [
        (
            float(source.get("x_centroid", source.get("xcentroid"))),
            float(source.get("y_centroid", source.get("ycentroid"))),
        )
        for source in unique_sources
    ]


def _star_width_estimator(stack_frame_count: int) -> StarWidthEstimator:
    """Choose how the measured width converts to the one star-width scale.

    Parameters
    ----------
    stack_frame_count : `int`
        The ``STACKCNT`` card of the image, 0 when it has none.

    Returns
    -------
    estimator : `StarWidthEstimator`
        The stack estimator for a stack of two or more frames, else the frame
        estimator of the brightest stars, which is the scale itself.
    """
    return (
        StarWidthEstimator.STACK_GAUSSIAN
        if stack_frame_count > 1
        else StarWidthEstimator.FRAME_GAUSSIAN_BRIGHTEST
    )


def measure_detections_in_file(path: str) -> DetectionsMeasurement:
    """List the stars the astrometry run's star finder detects in an image.

    Parameters
    ----------
    path : `str`
        A FITS image.

    Returns
    -------
    measurement : `DetectionsMeasurement`
        The detected stars, brightest first, with the kernel width used.

    Raises
    ------
    InvalidArgumentError
        If the image has no pixel data. A file that does not exist raises
        `NotFoundError`.
    """
    image = AstrometricsImage(path)
    raw = image.data
    if raw is None or np.asarray(raw).size == 0:
        raise InvalidArgumentError("The image has no pixel data.")
    # The run reads the image, averages the channels of a color frame into
    # one plane, and tells the finder it was color. Do the same.
    is_color_frame = np.asarray(raw).ndim == 3
    data = collapse_to_2d(raw) if is_color_frame else raw
    data = np.asarray(data, dtype=float)
    if data.ndim != 2:
        raise InvalidArgumentError("The image has no pixel data.")
    height, width = data.shape

    try:
        stack_frame_count = int(image.header.get("STACKCNT", 0) or 0)
    except TypeError, ValueError:
        stack_frame_count = 0
    estimator = _star_width_estimator(stack_frame_count)

    # A context of its own, so the width measured here sizes everything below
    # and nothing leaks into a run that may be going on.
    # The finder first, as the run builds it, so the markers are the stars a
    # run finds. Then this image's star width is recorded in a context of its
    # own, so it sizes the circle and nothing leaks into a run going on.
    detector = SourceDetector()
    _, unique_sources = find_unique_stars(detector, data, is_color_frame=is_color_frame)
    with setup_parameter_context():
        measured_width_px = measure_fwhm_from_data(data)
        record_star_width(measured_width_px, estimator)
        aperture_radius_px = _photometry_aperture_radius_px()

    star_width_px = (
        star_width_on_scale(float(measured_width_px), estimator)
        if measured_width_px is not None and np.isfinite(measured_width_px) and measured_width_px > 0
        else None
    )

    detections = []
    for source in unique_sources:
        x_zero_based = source.get("x_centroid", source.get("xcentroid"))
        y_zero_based = source.get("y_centroid", source.get("ycentroid"))
        detections.append(
            StarDetection(
                x=float(x_zero_based) + 1.0,
                y=float(y_zero_based) + 1.0,
                flux=float(source.get("flux", 0.0)),
                merged=int(source.get("merged_detection_count", 1)) > 1,
            )
        )
    return DetectionsMeasurement(
        path=path,
        width=int(width),
        height=int(height),
        detections=detections,
        detection_count=len(detections),
        merged_count=sum(1 for detection in detections if detection.merged),
        kernel_fwhm_px=float(detector.fwhm),
        binned_on_color=is_color_frame,
        star_width_px=star_width_px,
        aperture_radius_px=aperture_radius_px,
    )


def _photometry_aperture_radius_px() -> float:
    """Give the radius of the circle photometry measures each star in.

    It is read in the caller's parameter context, so it follows the star
    width recorded there. The import is local because photometry is another
    pipeline: only this viewer helper needs its circle.

    Returns
    -------
    radius : `float`
        The radius in image pixels, the default of
        `measure_aperture_photometry`.
    """
    from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import APERTURE_RADIUS_PX

    return float(resolve(APERTURE_RADIUS_PX).value)
