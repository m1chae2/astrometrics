"""Measuring how stretched each star of one image is, and in which direction.

The viewer's Shapes overlay needs the shape of every star across the image,
not only the median of the frame. This module reads one file and calls
`measure_star_profiles`, the function the frame measurement uses, so the stars
are chosen by the same rules (bright enough, not saturated, isolated, at most
one star for each cell of a 14 x 14 grid) and fitted the same way. No star is
measured twice and nothing is stored.

The frame measurement keeps only medians. This module keeps what each star
measured, and turns it into the numbers the viewer draws: the width, the
stretch ``1 - b / a`` and the direction of the long axis.
"""

import math

import numpy as np

from astrometricslib._internal.drivers.fits_access import collapse_to_2d
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.star_shapes import StarShape, StarShapesMeasurement
from astrometricslib.pipelines.shared.quality.metrics import REGION_CENTER
from astrometricslib.pipelines.shared.quality.star_profile_fitting import (
    MOFFAT_MINIMUM_STAR_COUNT,
    StarProfileResult,
    StarWidthSample,
    field_region_of_sample,
    measure_star_profiles,
)

__all__ = ["measure_star_shapes_in_file"]


def _angle_from_plus_y_degrees(angle_from_plus_x_degrees: float) -> float:
    """Turn a direction measured from +x toward +y into one from +y toward +x.

    The frame measurement gives the long axis direction from the x axis
    toward the y axis. The star profile cut is measured from the +y axis
    toward the +x axis. A line at angle ``t`` in the first convention has angle
    ``90 - t`` in the second. A line has no arrow, so the result is wrapped to
    0 up to but not including 180.

    Parameters
    ----------
    angle_from_plus_x_degrees : `float`
        A direction measured from +x toward +y, in degrees.

    Returns
    -------
    angle_degrees : `float`
        The same line, from +y toward +x, from 0 up to but not including 180.
    """
    return (90.0 - angle_from_plus_x_degrees) % 180.0


def _star_shape(sample: StarWidthSample) -> StarShape:
    """Turn one fitted star into the shape the viewer draws.

    Parameters
    ----------
    sample : `StarWidthSample`
        The star's position, width and stretch from the frame measurement.

    Returns
    -------
    shape : `StarShape`
        The star in 1-based pixels, with the stretch ``1 - b / a`` and the
        direction of its long axis from +y toward +x.
    """
    # Half the angle of the ellipticity vector is the long axis direction,
    # measured from x toward y (see `_measure_second_moments`).
    angle_from_x = math.degrees(math.atan2(sample.ellipticity_second, sample.ellipticity_first)) / 2.0
    return StarShape(
        x=float(sample.x_px) + 1.0,
        y=float(sample.y_px) + 1.0,
        width_px=float(sample.width_px),
        elongation=float(1.0 - 1.0 / max(sample.elongation_ratio, 1.0)),
        angle_degrees=float(_angle_from_plus_y_degrees(angle_from_x)),
    )


def _center_and_corner_widths(result: StarProfileResult) -> tuple[float | None, float | None]:
    """Find the median star width in the center and in the corners together.

    The regions are those of the frame measurement: the middle 4 x 4 cells of
    the 14 x 14 grid, and the 4 x 4 cells at each corner. The four corners are
    pooled so one number says how the corners compare with the center.

    Parameters
    ----------
    result : `StarProfileResult`
        The frame's star profile result with its stars.

    Returns
    -------
    center_width_px, corner_width_px : `float` or `None`
        The two medians in pixels. Each is `None` when its regions hold fewer
        stars than `MOFFAT_MINIMUM_STAR_COUNT`.
    """
    center: list[float] = []
    corners: list[float] = []
    for sample in result.samples:
        region = field_region_of_sample(result, sample)
        if region is None:
            continue
        (center if region == REGION_CENTER else corners).append(sample.width_px)
    minimum = resolve(MOFFAT_MINIMUM_STAR_COUNT).value
    return (
        float(np.median(center)) if len(center) >= minimum else None,
        float(np.median(corners)) if len(corners) >= minimum else None,
    )


def measure_star_shapes_in_file(path: str) -> StarShapesMeasurement:
    """Measure the width, stretch and direction of the stars of an image.

    Parameters
    ----------
    path : `str`
        A FITS image.

    Returns
    -------
    measurement : `StarShapesMeasurement`
        One entry for each measured star, and the frame summary. When the image
        has too few usable stars the list is empty and ``reason`` says why.

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
    raw = np.asarray(raw)
    data = collapse_to_2d(raw) if raw.ndim == 3 else raw
    data = np.asarray(data, dtype=float)
    if data.ndim != 2:
        raise InvalidArgumentError("The image has no pixel data.")
    height, width = data.shape
    # A raw color mosaic has one plane and a Bayer pattern card. Its stars are
    # measured on 2 x 2 super-pixels, as the frame measurement does.
    is_mosaic = raw.ndim == 2 and bool(str(image.header.get("BAYERPAT", "")).strip())
    result = measure_star_profiles(data, None, bayer_mosaic=is_mosaic)
    if not result.samples:
        return StarShapesMeasurement(
            path=path,
            width=int(width),
            height=int(height),
            shapes=[],
            star_count=0,
            reason=result.reason or "no stars could be measured",
        )
    shapes = [_star_shape(sample) for sample in result.samples]
    center_width_px, corner_width_px = _center_and_corner_widths(result)
    return StarShapesMeasurement(
        path=path,
        width=int(width),
        height=int(height),
        shapes=shapes,
        star_count=len(shapes),
        median_width_px=None if result.width_px is None else float(result.width_px),
        median_elongation=float(np.median([shape.elongation for shape in shapes])),
        direction_degrees=(
            None if result.angle_deg is None else float(_angle_from_plus_y_degrees(result.angle_deg))
        ),
        direction_coherence=None if result.angle_coherence is None else float(result.angle_coherence),
        center_width_px=center_width_px,
        corner_width_px=corner_width_px,
    )
