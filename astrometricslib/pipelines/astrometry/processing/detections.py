"""Showing what the astrometry run's star finder detects in one image.

The viewer's Detections overlay needs the stars the run would find in an
image, computed now from the pixels, so it works on every image without
running the analysis again. This module reads one file and calls
`find_unique_stars`, the same function `StarIdentifier.detect_stars` calls,
so the two cannot drift apart. It changes nothing on disk.

Positions inside the finder are 0-based array indices. They are converted to
1-based pixels (FITS and DS9) at the end.
"""

import numpy as np

from astrometricslib._internal.drivers.fits_access import collapse_to_2d
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.detections import DetectionsMeasurement, StarDetection
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.astrometry.processing.star_identifier import find_unique_stars

__all__ = ["measure_detections_in_file"]


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

    detector = SourceDetector()
    _, unique_sources = find_unique_stars(detector, data, is_color_frame=is_color_frame)

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
    )
