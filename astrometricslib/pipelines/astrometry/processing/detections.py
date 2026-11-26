"""Showing what the astrometry run's star finder detects in one image.

The viewer's Detections overlay needs the stars the run would find in an
image, computed now from the pixels, so it works on every image without
running the analysis again. This module reads one file and calls
`find_unique_stars`, the same function `StarIdentifier.detect_stars` calls,
so the two cannot drift apart. The viewer never reads a star's own radius,
so the finder is asked to skip it, which is most of the time.

A finished list is kept so the next request is instant. The astrometry run
saves its list next to a stack (``<stack>_detections.json``, derived data in
the stacks folder). Any other image, or a stack with no such file, gets a copy
in the app's cache folder. Each copy carries the file's size and modification
time and the finder's settings. It is used only while all of them still match,
so a changed file or a changed setting always finds the stars again. Nothing
is ever written next to a raw frame.

The photometry circle is sized from this image's own star width, measured
first the way a run measures it, so it fits the stars on screen whichever
setup is active. Only when no star can be measured does it fall back to the
active setup's typical width. The finder itself is built exactly as the run
builds it: for a mono image it sizes its kernel from the image's measured
blob width, and for a color image it uses the setup's width.
"""

import hashlib
import logging
import os
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.fits_access import collapse_to_2d
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.parameter_context import setup_parameter_context
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.paths import scratch_directory
from astrometricslib.foundation.run_bases import StarWidthEstimator, record_star_width, star_width_on_scale
from astrometricslib.models.quality.detections import DetectionsMeasurement, StarDetection
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import (
    SOURCE_DETECTOR_KERNEL_FWHM_PX,
    SOURCE_DETECTOR_ROUNDNESS_RANGE,
    VIEWING_SEPARATION_KERNEL_WIDTHS,
    SourceDetector,
)
from astrometricslib.pipelines.astrometry.processing.star_identifier import (
    _COLOR_DETECTION_BIN_FACTOR,
    find_unique_stars,
)
from astrometricslib.pipelines.shared.quality.star_profile_fitting import STAR_FINDER_THRESHOLD_SIGMA
from astrometricslib.pipelines.shared.stack_sidecar import (
    read_stamped_record,
    sidecar_path,
    write_stamped_record,
)

__all__ = [
    "DETECTIONS_SIDECAR_SUFFIX",
    "find_star_centers",
    "measure_detections_in_file",
    "save_run_detections",
]

logger = logging.getLogger(__name__)

# What follows a stack's own name (without .fits) in the name of its saved
# detection list, like the other files kept next to a stack.
DETECTIONS_SIDECAR_SUFFIX = "_detections.json"

# Raise this when the layout of a saved list changes, or when a fix changes
# the positions it holds, so an old file is not read as a new one. Version 3:
# a color stack's single-star positions were scaled back twice, so most of its
# saved stars sat outside the image.
DETECTIONS_FILE_VERSION = 3

# The key that holds the image's stamp (size and modification time) in a saved
# list.
_STAMP_KEY = "image_stamp"

# The cache folder keeps at most this many lists; the oldest are deleted.
DETECTIONS_CACHE_LIMIT = 200


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
    detector = SourceDetector(keep_close_pairs=True)
    _, unique_sources = find_unique_stars(detector, np.asarray(data, dtype=float))
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

    A saved list is used when it is still valid (see `_load_saved`): first
    the one the run saved next to a stack, then the one in the cache folder.
    Otherwise the finder runs and the result goes to the cache folder.

    Parameters
    ----------
    path : `str`
        A FITS image.

    Returns
    -------
    measurement : `DetectionsMeasurement`
        The detected stars, brightest first, with the kernel width used.

    Notes
    -----
    A file that does not exist raises `NotFoundError`. An image with no pixel
    data raises `InvalidArgumentError`.
    """
    saved = _load_saved(path, sidecar_path(path, DETECTIONS_SIDECAR_SUFFIX)) or _load_saved(
        path, _cache_path(path)
    )
    if saved is not None:
        return saved
    measurement = _find_detections(path)
    _write_saved(_cache_path(path), path, measurement)
    return measurement


def save_run_detections(path: str) -> bool:
    """Save the viewing list of a stack, next to the stack.

    The run itself finds the separated list that plate solving needs, which
    drops the fainter star of a close pair. The viewer and the star naming
    need the pairs, so this finds the viewing list (`_find_detections`) and
    saves that. Call it after the run has finished changing the file (it
    writes the solved coordinates into the header), so the saved size and
    modification time are the ones the viewer will see. Only a stack gets a
    file: a single frame belongs to the raw folders, where nothing is written.

    Parameters
    ----------
    path : `str`
        The stack's FITS file.

    Returns
    -------
    saved : `bool`
        `True` when a file was written.
    """
    try:
        image = AstrometricsImage(path)
        if int(image.header.get("STACKCNT", 0) or 0) <= 1:
            return False
        measurement = _find_detections(path)
    except (OSError, ValueError, TypeError, InvalidArgumentError) as error:
        logger.debug("Could not save the detection list of %s: %s", path, error)
        return False
    return _write_saved(sidecar_path(path, DETECTIONS_SIDECAR_SUFFIX), path, measurement)


def _find_detections(path: str) -> DetectionsMeasurement:
    """Run the star finder on an image and describe what it found.

    Parameters
    ----------
    path : `str`
        A FITS image.

    Returns
    -------
    measurement : `DetectionsMeasurement`
        The detected stars.

    Raises
    ------
    InvalidArgumentError
        If the image has no pixel data.
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
    # The finder is built exactly as the run builds it, so the markers are
    # the stars a run finds. The run reads each star's radius; the viewer
    # does not, so it is skipped here.
    detector = SourceDetector(keep_close_pairs=True)
    _, unique_sources = find_unique_stars(detector, data, is_color_frame=is_color_frame, with_radius=False)
    return _build_measurement(path, data, image.header, unique_sources, float(detector.fwhm), is_color_frame)


def _build_measurement(
    path: str,
    data: np.ndarray,
    header: Any,
    unique_sources: list[dict[str, Any]],
    kernel_fwhm_px: float,
    binned_on_color: bool,
) -> DetectionsMeasurement:
    """Turn the finder's stars into the result, with the circle size.

    The image's own star width is recorded in a context of its own, so it
    sizes the circle and nothing leaks into a run going on.

    Parameters
    ----------
    path : `str`
        The image.
    data : `numpy.ndarray`
        Its two-dimensional pixels.
    header : `astropy.io.fits.Header`
        Its header.
    unique_sources : `list` [`dict`]
        The merged stars from `find_unique_stars`.
    kernel_fwhm_px : `float`
        The kernel width the finder used.
    binned_on_color : `bool`
        Whether the finder searched a binned copy.

    Returns
    -------
    measurement : `DetectionsMeasurement`
        The result.
    """
    height, width = data.shape
    try:
        stack_frame_count = int(header.get("STACKCNT", 0) or 0)
    except TypeError, ValueError:
        stack_frame_count = 0
    estimator = _star_width_estimator(stack_frame_count)

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
        kernel_fwhm_px=kernel_fwhm_px,
        binned_on_color=binned_on_color,
        star_width_px=star_width_px,
        aperture_radius_px=aperture_radius_px,
    )


def _finder_settings() -> dict[str, Any]:
    """List the settings that decide which stars the finder reports.

    A saved list is valid only for the settings it was made with, so tuning
    one of them finds the stars again.

    Returns
    -------
    settings : `dict`
        The values, as plain data.
    """
    return {
        "threshold_sigma": float(resolve(STAR_FINDER_THRESHOLD_SIGMA).value),
        "kernel_fwhm_px": float(resolve(SOURCE_DETECTOR_KERNEL_FWHM_PX).value),
        "roundness_range": [float(value) for value in resolve(SOURCE_DETECTOR_ROUNDNESS_RANGE).value],
        "color_bin_factor": int(resolve(_COLOR_DETECTION_BIN_FACTOR).value),
        "viewing_separation_kernel_widths": float(resolve(VIEWING_SEPARATION_KERNEL_WIDTHS).value),
    }


def _cache_path(path: str) -> str:
    """Give the path of an image's list in the cache folder.

    Parameters
    ----------
    path : `str`
        The image.

    Returns
    -------
    cache_path : `str`
        A file in ``<scratch folder>/detections``, named from the image's
        real path.
    """
    key = hashlib.sha256(os.path.realpath(path).encode("utf-8")).hexdigest()
    return os.path.join(scratch_directory(), "detections", f"{key}.json")


def _load_saved(path: str, saved_path: str) -> DetectionsMeasurement | None:
    """Read a saved list, if it is for this file as it is now.

    Parameters
    ----------
    path : `str`
        The image.
    saved_path : `str`
        The saved file to read.

    Returns
    -------
    measurement : `DetectionsMeasurement` or `None`
        The list, or `None` when there is no file, it cannot be read, or its
        version, image stamp or finder settings differ from now (see
        `stack_sidecar.read_stamped_record`).
    """
    record = read_stamped_record(saved_path, path, DETECTIONS_FILE_VERSION, _STAMP_KEY)
    if record is None or record.get("finder_settings") != _finder_settings():
        return None
    try:
        measurement = DetectionsMeasurement.model_validate(record["measurement"])
    except (ValueError, KeyError, TypeError) as error:
        logger.debug("Could not read the detection list %s: %s", saved_path, error)
        return None
    measurement.path = path
    return measurement


def _write_saved(saved_path: str, path: str, measurement: DetectionsMeasurement) -> bool:
    """Save a list with the image's stamp and the finder's settings.

    A failure to write is logged and ignored: the list is only a shortcut.

    Parameters
    ----------
    saved_path : `str`
        The file to write.
    path : `str`
        The image the list is for.
    measurement : `DetectionsMeasurement`
        The list.

    Returns
    -------
    written : `bool`
        `True` when the file was written.
    """
    contents = {"finder_settings": _finder_settings(), "measurement": measurement.model_dump()}
    written = write_stamped_record(saved_path, path, DETECTIONS_FILE_VERSION, _STAMP_KEY, contents)
    if written and os.path.basename(os.path.dirname(saved_path)) == "detections":
        _prune_cache(os.path.dirname(saved_path))
    return written


def _prune_cache(folder: str) -> None:
    """Delete the oldest lists when the cache folder holds too many.

    Parameters
    ----------
    folder : `str`
        The cache folder.
    """
    try:
        entries = [entry for entry in os.scandir(folder) if entry.name.endswith(".json")]
        if len(entries) <= DETECTIONS_CACHE_LIMIT:
            return
        entries.sort(key=lambda entry: entry.stat().st_mtime)
        for entry in entries[: len(entries) - DETECTIONS_CACHE_LIMIT]:
            os.remove(entry.path)
    except OSError:
        return


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
