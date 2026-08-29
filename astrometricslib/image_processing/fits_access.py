"""Provide access to image data and metadata within FITS files.

A FITS file stores data in blocks called Header Data Units (HDUs).
The pixel data usually sits in the first block (index 0). Sometimes,
the first block only contains text information (keywords), and the
actual image is in the second block (index 1). This module provides
tools to safely locate and read the correct block so that other code
does not have to worry about this structural difference.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
from astropy.io import fits

logger = logging.getLogger(__name__)


@contextmanager
def open_primary_hdu(path: str) -> Iterator[fits.hdu.base._BaseHDU]:
    """Open a FITS file and yield the HDU containing the pixel data.

    Parameters
    ----------
    path : str
        Path to the FITS file.

    Yields
    ------
    hdu : astropy.io.fits.hdu.base._BaseHDU
        The Header Data Unit (HDU) that holds the actual image data.
        This is HDU 0, or HDU 1 if HDU 0 has no data.
    """
    with fits.open(path, memmap=False) as hdul:
        if len(hdul) > 1 and hdul[0].data is None:
            yield hdul[1]
        else:
            yield hdul[0]


def read_header(path: str) -> fits.Header:
    """Read the header from the HDU that holds the pixel data.

    Parameters
    ----------
    path : str
        Path to the FITS file.

    Returns
    -------
    header : astropy.io.fits.Header
        A safe copy of the header dictionary containing the image metadata.
    """
    with open_primary_hdu(path) as hdu:
        return hdu.header.copy()


def read_data(path: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Read the raw pixel array from the FITS file.

    Parameters
    ----------
    path : str
        Path to the FITS file.

    Returns
    -------
    data : numpy.ndarray or None
        The original pixel array. It is not modified or cast to a
        different data type. Returns None if there is no data.
    """
    with open_primary_hdu(path) as hdu:
        return hdu.data


def frame_dimensions(path: str) -> tuple[int, int] | None:
    """Find the width and height of the image from its header.

    Parameters
    ----------
    path : str
        Path to the FITS file.

    Returns
    -------
    dimensions : tuple[int, int] or None
        The width and height of the image as (NAXIS1, NAXIS2). Returns
        None if the header cannot be read or is missing these values.
    """
    try:
        header = read_header(path)
        return (int(header["NAXIS1"]), int(header["NAXIS2"]))
    except Exception as read_error:
        logger.debug("Could not read dimensions of %s: %s", path, read_error)
        return None


def frame_uses_color_filter_array(path: str) -> bool | None:
    """Check if the image was taken with a color camera.

    A color camera uses a Bayer pattern (a specific arrangement of red,
    green, and blue filters) on its sensor. The FITS header usually
    indicates this with a 'BAYERPAT' keyword. If this keyword is missing,
    we assume the image is from a monochrome (black-and-white) camera.

    Parameters
    ----------
    path : str
        Path to the FITS file.

    Returns
    -------
    uses_color_filter_array : bool or None
        True if the image uses a color filter array. False if it does
        not. Returns None if the header cannot be read.
    """
    try:
        header = read_header(path)
    except Exception as read_error:
        logger.debug("Could not read header of %s for CFA detection: %s", path, read_error)
        return None
    bayer_pattern = header.get("BAYERPAT")
    return bool(bayer_pattern is not None and str(bayer_pattern).strip())


def select_dominant_frame_dimensions(frame_paths: list[str]) -> tuple[set[str], tuple[int, int] | None]:
    """Filter a list of frames to keep only the most common image size.

    Image processing tools like Siril require all images in a sequence
    to have the exact same dimensions. A few images with different sizes
    will cause the entire processing step to fail. This function finds
    the most common image size (width and height) in a list and returns
    only the files that match it.

    Parameters
    ----------
    frame_paths : list[str]
        A list of paths to FITS files.

    Returns
    -------
    kept_paths : set[str]
        A set of file paths that match the most common dimensions.
        Files that cannot be read are also kept.
    dominant_dimensions : tuple[int, int] or None
        The most common (width, height) pair, or None if no files
        could be read.
    """
    paths_by_dimensions: dict[tuple[int, int], list[str]] = {}
    unreadable_paths: list[str] = []
    for frame_path in frame_paths:
        dimensions = frame_dimensions(frame_path)
        if dimensions is None:
            unreadable_paths.append(frame_path)
            continue
        paths_by_dimensions.setdefault(dimensions, []).append(frame_path)

    if not paths_by_dimensions:
        return set(frame_paths), None

    dominant_dimensions = max(
        paths_by_dimensions,
        key=lambda dimensions: (
            len(paths_by_dimensions[dimensions]),
            dimensions[0] * dimensions[1],
        ),
    )
    return set(paths_by_dimensions[dominant_dimensions]) | set(unreadable_paths), dominant_dimensions


def collapse_to_2d(data: np.ndarray) -> np.ndarray:
    """Combine a multi-channel image into a single flat image.

    Color images often have a third dimension representing the color
    channels (like Red, Green, Blue). This function takes a 3D image
    and averages the color channels together to create a flat 2D image.
    If the image is already 2D, it is returned without changes.

    Parameters
    ----------
    data : numpy.ndarray
        The input image array, which can be 2D or 3D.

    Returns
    -------
    collapsed : numpy.ndarray
        The 2D image array.
    """
    if data.ndim != 3:
        return data
    if data.shape[0] in (1, 3, 4):
        return np.mean(data, axis=0)
    return np.mean(data, axis=-1)
