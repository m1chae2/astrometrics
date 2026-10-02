"""Purpose: line up spectral exposure-group stacks onto one trail tilt.

Description: A slitless spectrum's tilt on the sensor is not fixed from one
session to the next -- it depends on exactly how the grating sits in its
filter-wheel slot, which can shift a little between sessions. Vega's own
three sessions measured 0.95-0.99 (2026-09-22), 2.12-2.13 (2026-05-24) and
4.70-4.72 (2026-09-25) degrees. Exposure groups are otherwise only shifted
onto each other before they are combined (see `group_alignment`), which
assumes every group shares one tilt; mixing tilts smears the merged trail
into a curve and biases every line depth along it (on Vega's stack, before
this fix, H-gamma's measured depth fell from 0.208 to 0.102 once a group
with a different tilt came to dominate the merge).

This module rotates each group's own stack about its own zero-order star
onto one common tilt -- the tilt of whichever group measured its trail most
clearly -- before `group_alignment`'s shift-only alignment runs. A group
whose own tilt could not be measured clearly enough is left as it is and
reported, since nothing tells it how far to turn; the caller can then choose
to leave that group out instead of guessing.

Only used for spectral stacks made of more than one exposure group: a single
group has nothing to line its tilt up against, and derotating it against
itself would only blur it for no reason.
"""

import logging

import numpy as np
from scipy.ndimage import rotate as rotate_image
from scipy.ndimage import shift as shift_image

from astrometricslib.pipelines.stacking.processing.group_alignment import find_zero_order_position

logger = logging.getLogger(__name__)

# A trail angle measured with less contrast than this (see
# `SpectroscopyPipeline.measure_dispersion_trail`'s own return) is not
# trusted as a rotation, or as a candidate for the common tilt every other
# group is rotated onto: nothing to rotate by is safer than rotating by
# noise. A clearly visible streak scores in the hundreds to thousands;
# Vega's weakest group (2026-09-22, a short, faint exposure) still scored
# about 55, so the bar is set well below that and has not been tested lower.
MINIMUM_TRAIL_CONTRAST_SIGMA = 10.0

# A rotation smaller than this, in degrees, is not applied: the group is
# already close enough to the reference tilt that resampling it would only
# add the interpolation's own blur for no visible straightening.
NEGLIGIBLE_ROTATION_DEGREES = 0.05


def rotate_about_point(
    image: np.ndarray, center_row: float, center_column: float, angle_degrees: float, order: int = 3
) -> np.ndarray:
    """Rotate a 2-D image about a chosen point, not the array's own centre.

    `scipy.ndimage.rotate` always turns an image about its array centre. To
    turn it about another point instead -- here, a star's zero order, which
    must stay in the same pixel after the turn -- that point is first moved
    to the array centre, the image is turned, and the move is undone.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image to rotate.
    center_row : `float`
        The row of the point to rotate about.
    center_column : `float`
        The column of the point to rotate about.
    angle_degrees : `float`
        How far to turn the image, in degrees, in the same sense
        `SpectroscopyPipeline.measure_dispersion_trail` reports a trail's
        tilt: rotating an image by its own measured angle brings that trail
        to 0 degrees.
    order : `int`, optional
        The spline order used to resample the image. Defaults to 3 (cubic).

    Returns
    -------
    rotated : `numpy.ndarray`
        The rotated image, as `float32`, the same shape as `image`. Pixels
        the turn brings in from outside the original frame are zero.
    """
    if abs(angle_degrees) < NEGLIGIBLE_ROTATION_DEGREES:
        return np.asarray(image, dtype=np.float32).copy()
    array = np.asarray(image, dtype=np.float64)
    height, width = array.shape
    array_center_row, array_center_column = (height - 1) / 2.0, (width - 1) / 2.0
    centred = shift_image(
        array,
        (array_center_row - center_row, array_center_column - center_column),
        order=order,
        mode="constant",
        cval=0.0,
    )
    spun = rotate_image(centred, angle_degrees, reshape=False, order=order, mode="constant", cval=0.0)
    restored = shift_image(
        spun,
        (center_row - array_center_row, center_column - array_center_column),
        order=order,
        mode="constant",
        cval=0.0,
    )
    return restored.astype(np.float32)


def measure_trail_angle_degrees(
    image_path: str, star_position_xy: tuple[float, float], camera_name: str
) -> tuple[float, float]:
    """Measure one image's trail tilt, in degrees, and how clearly it shows.

    A thin wrapper around the spectroscopy pipeline's own trail-following
    measurement, which already handles both dispersion orientations and is
    validated on real trails; this module only decides how to use its
    answer to line groups up, not how to measure one. The import is local
    because `astrometricslib.pipelines.spectroscopy` is a sibling pipeline,
    not a dependency of stacking in general (see `intensity_scale.py` for
    the one place spectroscopy depends on stacking, the other direction).

    Parameters
    ----------
    image_path : `str`
        The image to measure, read from disk.
    star_position_xy : `tuple` [`float`, `float`]
        The star's zero-order position in that image, `(x, y)`.
    camera_name : `str`
        The camera the image was taken with, so the right instrument
        geometry (dispersion direction, grating spacing) is used.

    Returns
    -------
    angle_degrees : `float`
        The trail's tilt, in degrees (0.0 when it could not be measured).
    contrast_sigma : `float`
        How clearly the trail stood out above the background noise.
    """
    from astrometricslib.drivers.image import AstrometricsImage
    from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
    from astrometricslib.utilities import ConfigLoader

    pipeline = SpectroscopyPipeline(ConfigLoader.load_spectroscopy_config(camera_name=camera_name))
    return pipeline.measure_dispersion_trail(AstrometricsImage(image_path), star_position_xy)


def derotate_groups_to_common_tilt(
    images: list[np.ndarray],
    image_paths: list[str],
    camera_name: str,
    minimum_contrast_sigma: float = MINIMUM_TRAIL_CONTRAST_SIGMA,
) -> tuple[list[np.ndarray], list[float | None]]:
    """Rotate every spectral exposure-group stack onto one common trail tilt.

    Each group's own zero order and trail tilt are measured first. Every
    group whose tilt was measured clearly enough is then rotated about its
    own zero order onto the tilt of whichever group measured its own trail
    most clearly (the reference tilt); a group already within
    `NEGLIGIBLE_ROTATION_DEGREES` of it is left untouched. A group whose
    zero order or trail could not be measured clearly enough is left
    exactly as it is, since nothing tells it how far to turn, and its own
    angle is reported as `None` so the caller knows it was not lined up.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        Each group's stacked image.
    image_paths : `list` [`str`]
        Each group's own file on disk, one to one with `images` -- read
        again here only to measure the trail tilt; the arrays that are
        rotated and returned are `images`, not a re-read of these files.
    camera_name : `str`
        The camera the images were taken with.
    minimum_contrast_sigma : `float`, optional
        A group's own trail must stand out at least this clearly to be
        rotated, or to be used as the reference tilt.

    Returns
    -------
    rotated : `list` [`numpy.ndarray`]
        Each group's image, rotated onto the reference tilt where that
        could be trusted, unchanged otherwise.
    angles_degrees : `list` [`float` or `None`]
        Each group's own measured trail tilt, or `None` when it could not
        be measured clearly enough to trust.

    Raises
    ------
    ValueError
        If `images` and `image_paths` are not the same length.
    """
    if len(images) != len(image_paths):
        raise ValueError("Each image needs one path to re-measure its trail tilt from.")

    positions = [find_zero_order_position(np.asarray(image, dtype=float)) for image in images]
    measurements: list[tuple[float, float] | None] = []
    for image_path, position in zip(image_paths, positions, strict=True):
        if position is None:
            measurements.append(None)
            continue
        row, column = position
        angle, contrast = measure_trail_angle_degrees(image_path, (column, row), camera_name)
        measurements.append((angle, contrast) if contrast >= minimum_contrast_sigma else None)

    trusted_indexes = [index for index, measurement in enumerate(measurements) if measurement is not None]
    if not trusted_indexes:
        logger.warning(
            "None of the %d spectral exposure groups had a trail tilt clear enough to trust; "
            "combining them without lining up their tilts.",
            len(images),
        )
        return [np.asarray(image, dtype=np.float32) for image in images], [None] * len(images)

    reference_index = max(trusted_indexes, key=lambda index: measurements[index][1])
    reference_angle = measurements[reference_index][0]
    logger.info(
        "Lining up %d of %d spectral exposure groups onto a trail tilt of %.2f degrees (from %s).",
        len(trusted_indexes),
        len(images),
        reference_angle,
        image_paths[reference_index],
    )

    rotated: list[np.ndarray] = []
    angles_out: list[float | None] = []
    for index, image in enumerate(images):
        measurement = measurements[index]
        if measurement is None:
            rotated.append(np.asarray(image, dtype=np.float32))
            angles_out.append(None)
            continue
        angle, _contrast = measurement
        row, column = positions[index]
        rotated.append(rotate_about_point(image, row, column, angle - reference_angle))
        angles_out.append(angle)
    return rotated, angles_out
