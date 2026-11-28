"""Purpose: The one parser of what Siril prints and writes about a run.

Description: Siril reports a run in two ways, and both are read here, so no
warning Siril gives is parsed in two places or lost:

* The lines it prints while it runs: how many frames were registered and
  stacked, frames with many negative pixels, and the warning that a colour
  calibration fitted the stars poorly. The pipe session (`session`) reads
  them into the run's diagnostics, which the stacking pipeline turns into job
  warnings; the preview reads the colour calibration warning into its notices.
* The files it writes: the ``.seq`` registration summaries, the ``.lst`` star
  lists and the ``_RejMap.fits`` rejection map.

They are specific to how Siril reports its results, not a general image
quality measurement, so they live in the Siril driver rather than with the
quality math in ``pipelines/shared/quality/``.
"""

import math
import os
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.fits_access import read_data

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


# The sentence Siril logs when its photometric colour calibration fits the
# stars poorly. The text follows Siril 1.4.
_IMPRECISE_COLOR_CALIBRATION_TEXT = "imprecise solution"


def reports_imprecise_color_calibration(output: str) -> bool:
    """Say whether Siril's output warns of a poor colour calibration fit.

    Parameters
    ----------
    output : `str`
        What Siril printed while it ran.

    Returns
    -------
    imprecise : `bool`
        `True` if the output holds Siril's "imprecise solution" warning.
    """
    return _IMPRECISE_COLOR_CALIBRATION_TEXT in output


# What Siril prints for each image while ``seqplatesolve`` works through a
# sequence one image at a time (the run sets ``setcpu 1`` so the lines of two
# images never mix):
#
#   Found 158 Gaussian profile stars in image, channel #0 (FWHM 3.167741)
#   order: 3, nmatched: 49, sigx:0.258079, sigy:0.269835
#   Image s_00005.fits platesolved and updated        (solved)
#   Image s_00001 did not solve                       (not solved)
_STARS_FOUND_PATTERN = re.compile(r"Found (\d+) Gaussian profile stars")
_SEQUENCE_FIT_PATTERN = re.compile(r"order: (\d+), nmatched: (\d+), sigx:([\d.eE+-]+), sigy:([\d.eE+-]+)")
_SEQUENCE_SOLVED_PATTERN = re.compile(r"Image \S*?_(\d+)\.fits? platesolved and updated")
_SEQUENCE_NOT_SOLVED_PATTERN = re.compile(r"Image \S*?_(\d+) did not solve")


@dataclass(frozen=True)
class SequenceSolveRecord:
    """What Siril printed about one image of a plate-solved sequence.

    Attributes
    ----------
    image_number : `int`
        The image's number in the sequence, counted from 1.
    solved : `bool`
        Whether Siril solved the image.
    stars_found : `int` or `None`
        How many stars Siril found in the image, or `None` if it printed none.
    matched_star_count : `int` or `None`
        How many of them Siril matched to the Gaia catalog (solved images
        only).
    fit_residual_rms_arcsec : `float` or `None`
        The two axes' spreads of the matched stars' position errors,
        combined as a root mean square, in arcseconds (solved images only).
    fit_order : `int` or `None`
        The order of the distortion fit Siril kept (solved images only). It
        falls to 1 when the image holds too few stars for a higher one.
    """

    image_number: int
    solved: bool
    stars_found: int | None = None
    matched_star_count: int | None = None
    fit_residual_rms_arcsec: float | None = None
    fit_order: int | None = None


def parse_sequence_solve(output: str) -> dict[int, SequenceSolveRecord]:
    """Read, for each image, how a sequence plate solve went.

    Parameters
    ----------
    output : `str`
        What Siril printed while ``seqplatesolve`` ran with one thread.

    Returns
    -------
    records : `dict` [`int`, `SequenceSolveRecord`]
        One record for each image Siril reported on, keyed by the image's
        number in the sequence. An image Siril never reported on has none.
    """
    records: dict[int, SequenceSolveRecord] = {}
    stars_found: int | None = None
    fit: tuple[int, int, float] | None = None
    for line in output.splitlines():
        found_match = _STARS_FOUND_PATTERN.search(line)
        if found_match:
            stars_found, fit = int(found_match.group(1)), None
            continue
        fit_match = _SEQUENCE_FIT_PATTERN.search(line)
        if fit_match:
            fit = (
                int(fit_match.group(1)),
                int(fit_match.group(2)),
                math.hypot(float(fit_match.group(3)), float(fit_match.group(4))),
            )
            continue
        solved_match = _SEQUENCE_SOLVED_PATTERN.search(line)
        if solved_match:
            number = int(solved_match.group(1))
            records[number] = SequenceSolveRecord(
                number,
                True,
                stars_found,
                None if fit is None else fit[1],
                None if fit is None else fit[2],
                None if fit is None else fit[0],
            )
            stars_found, fit = None, None
            continue
        failed_match = _SEQUENCE_NOT_SOLVED_PATTERN.search(line)
        if failed_match:
            number = int(failed_match.group(1))
            records[number] = SequenceSolveRecord(number, False, stars_found)
            stars_found, fit = None, None
    return records


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
