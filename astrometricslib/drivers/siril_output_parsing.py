"""Parsers for the files Siril writes about a run.

These read Siril's own output file formats (`.seq` registration
summaries, `.lst` star lists, the `_RejMap.fits` rejection map) -- they're
specific to how Siril reports its results, not a generic image-quality
measurement, so they live alongside the Siril driver rather than with the
quality math in `pipelines/shared/quality/`.
"""

import math
import os
import re
from typing import Any

import numpy as np

from astrometricslib.drivers.fits_access import read_data

_REGISTRATION_LINE_PATTERN = re.compile(r"^R(\d+) ")

# An image line of a Siril sequence file: "I <image number> <included> ...".
# The second number is 1 for an image still in the sequence and 0 for one that
# was dropped (for example when registration found no stars in it).
_IMAGE_LINE_PATTERN = re.compile(r"^I\s+\d+\s+([01])(?:\s|$)")

# Siril reports how registration went as "Total: 46 failed, 94 registered."
_REGISTRATION_TOTAL_PATTERN = re.compile(r"Total:\s*(\d+)\s+failed,\s*(\d+)\s+registered")

# ... and how stacking went as "Rejection stacking complete. 94 images have
# been stacked."
_STACKED_COUNT_PATTERN = re.compile(r"stacking complete\.\s*(\d+)\s+images? have been stacked")

# After subtracting the dark master Siril warns, once per frame, when the
# result is mostly below zero: "After dark subtraction, the image contains
# many negative pixels (99%), calibration frames are probably incorrect".
_NEGATIVE_PIXELS_PATTERN = re.compile(r"contains many negative pixels \((\d+)%\)")


def parse_registration_totals(line: str) -> tuple[int, int] | None:
    """Read how many frames Siril failed and managed to register.

    Parameters
    ----------
    line : `str`
        One line of Siril's output.

    Returns
    -------
    totals : `tuple` [`int`, `int`] or `None`
        ``(failed, registered)`` when the line is Siril's registration
        summary, otherwise `None`.
    """
    match = _REGISTRATION_TOTAL_PATTERN.search(line)
    return (int(match.group(1)), int(match.group(2))) if match else None


def parse_negative_pixel_percentage(line: str) -> int | None:
    """Read the share of negative pixels Siril warns about.

    Parameters
    ----------
    line : `str`
        One line of Siril's output.

    Returns
    -------
    percentage : `int` or `None`
        The percentage of pixels below zero when the line is Siril's
        "many negative pixels" warning, otherwise `None`.
    """
    match = _NEGATIVE_PIXELS_PATTERN.search(line)
    return int(match.group(1)) if match else None


def parse_stacked_image_count(line: str) -> int | None:
    """Read how many images Siril stacked.

    Parameters
    ----------
    line : `str`
        One line of Siril's output.

    Returns
    -------
    count : `int` or `None`
        The number of images stacked when the line is Siril's stacking
        summary, otherwise `None`.
    """
    match = _STACKED_COUNT_PATTERN.search(line)
    return int(match.group(1)) if match else None


def parse_seq_file(seq_path: str) -> list[dict[str, Any]]:
    """Read the registration results file from Siril.

    Siril creates a `.seq` file when it aligns images. This function reads
    that file to get the quality measurements (like how blurry the stars are)
    for each image.

    Each registration line has the form
    ``R<layer> fwhm weighted_fwhm roundness quality background_level
    star_count H h00 h01 h02 h10 h11 h12 h20 h21 h22`` (checked against
    Siril's own reader, ``src/io/seqfile.c``). The line holds no
    registration error. Older versions of this function read the
    ``background_level`` field as ``rmse`` and the ``weighted_fwhm`` field
    as ``fwhm_y``; both names were wrong.

    Parameters
    ----------
    seq_path : `str`
        The file path to the Siril `.seq` file.

    Returns
    -------
    frames : `list` of `dict`
        A list containing the quality measurements for each image, in file
        order. Returns an empty list if the file doesn't exist. Each
        dictionary holds:

        ``fwhm_px``
            Width of the stars in pixels (Siril's plain FWHM).
        ``weighted_fwhm_px``
            Siril's weighted FWHM in pixels, which counts each star by how
            well its fit went.
        ``roundness``
            Star roundness, 0 to 1 (1 is a perfect circle).
        ``quality``
            Siril's registration quality score for the image.
        ``background_level``
            Sky background level, normalized to the 0 to 1 image range. It
            is a brightness, not an alignment error.
        ``star_count``
            Number of stars Siril matched in the image.
        ``homography``
            The 3 by 3 transform that moves the image onto the reference,
            as nine numbers in row order.
        ``dx``, ``dy``
            The shift in pixels: the homography's ``h02`` and ``h12``.
        ``rotation_deg``
            Rotation in degrees, `atan2(h10, h00)` of the homography's top
            left 2 by 2 part, in Siril's image coordinates.
        ``scale``
            Scale factor, the square root of the absolute determinant of
            that 2 by 2 part (1.0 means the same size as the reference).
    """
    frames = []
    if not os.path.exists(seq_path):
        return frames
    with open(seq_path) as seq_file:
        for line in seq_file:
            if not _REGISTRATION_LINE_PATTERN.match(line):
                continue
            parts = line.split()
            homography = [float(value) for value in parts[8:17]]
            h00, h01, h02, h10, h11, h12 = homography[:6]
            frames.append({
                "fwhm_px": float(parts[1]),
                "weighted_fwhm_px": float(parts[2]),
                "roundness": float(parts[3]),
                "quality": float(parts[4]),
                "background_level": float(parts[5]),
                "star_count": int(parts[6]),
                "homography": homography,
                "dx": h02,
                "dy": h12,
                "rotation_deg": math.degrees(math.atan2(h10, h00)),
                "scale": math.sqrt(abs(h00 * h11 - h01 * h10)),
            })
    return frames


def parse_seq_inclusion(seq_path: str) -> list[bool]:
    """Read which images a Siril sequence file still includes.

    Siril marks an image it could not register as not included. The stack
    leaves that image out, so it is not a member of the stack.

    Parameters
    ----------
    seq_path : `str`
        The file path to the Siril `.seq` file.

    Returns
    -------
    included : `list` of `bool`
        One flag per image, in file order. Empty if the file does not exist
        or has no image lines.
    """
    flags: list[bool] = []
    if not os.path.exists(seq_path):
        return flags
    with open(seq_path) as seq_file:
        for line in seq_file:
            match = _IMAGE_LINE_PATTERN.match(line)
            if match:
                flags.append(match.group(1) == "1")
    return flags


def parse_zero_order_star(lst_path: str) -> dict[str, float] | None:
    """Find information about the brightest star from a Siril list file.

    Siril writes a `.lst` file with information about all the stars it found.
    This function reads the file and returns information about the first
    (brightest) star, which is often used for spectroscopy alignment.

    Parameters
    ----------
    lst_path : `str`
        The file path to the Siril `.lst` file.

    Returns
    -------
    result : `dict` or `None`
        The star's properties, or None if the file doesn't exist or is empty.
        ``psf_fit_rmse`` is the root-mean-square residual of Siril's fit of
        the star's profile (column 12 of the star list). It is a fit error
        for this one star, not a registration error. Earlier versions named
        it ``rmse``.
    """
    if not os.path.exists(lst_path):
        return None
    with open(lst_path) as lst_file:
        for line in lst_file:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.split("\t")
            background = float(fields[2])
            amplitude = float(fields[3])
            return {
                "background": background,
                "amplitude": amplitude,
                "peak_to_background_ratio": amplitude / background if background else None,
                "x": float(fields[5]),
                "y": float(fields[6]),
                "fwhm_x_px": float(fields[7]),
                "fwhm_y_px": float(fields[8]),
                "psf_fit_rmse": float(fields[12]),
            }
    return None


def measure_rejected_fraction(stacked_path: str) -> float | None:
    """Calculate what share of pixel values were thrown out during stacking.

    Siril writes a rejection map beside a stack when asked (its ``-rejmap``
    option), named after the stack with ``_RejMap`` added. Each pixel of the
    map holds the fraction of frames whose value was rejected at that pixel,
    so the map's mean is the share of all pixel values thrown out (for
    example by satellites or clouds).

    Parameters
    ----------
    stacked_path : `str`
        The file path to the final stacked image.

    Returns
    -------
    fraction : `float` or `None`
        The mean of the rejection map, 0 to 1. `None` if the rejection map
        file can't be found or holds no data.
    """
    rejmap_path = os.path.splitext(stacked_path)[0] + "_RejMap.fits"
    if not os.path.exists(rejmap_path):
        return None
    data = read_data(rejmap_path)
    if data is None:
        return None
    return float(np.mean(data))
