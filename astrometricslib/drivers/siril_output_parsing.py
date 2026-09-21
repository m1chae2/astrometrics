"""Parsers for the text files Siril writes about a run.

These read Siril's own output file formats (`.seq` registration
summaries, `.lst` star lists) -- they're specific to how Siril reports
its results, not a generic image-quality measurement, so they live
alongside the Siril driver rather than with the quality math in
`pipelines/shared/quality/`.
"""

import os
import re

_REGISTRATION_LINE_PATTERN = re.compile(r"^R(\d+) ")

# Siril reports how registration went as "Total: 46 failed, 94 registered."
_REGISTRATION_TOTAL_PATTERN = re.compile(r"Total:\s*(\d+)\s+failed,\s*(\d+)\s+registered")

# ... and how stacking went as "Rejection stacking complete. 94 images have
# been stacked."
_STACKED_COUNT_PATTERN = re.compile(r"stacking complete\.\s*(\d+)\s+images? have been stacked")


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


def parse_seq_file(seq_path: str) -> list[dict[str, float]]:
    """Read the registration results file from Siril.

    Siril creates a `.seq` file when it aligns images. This function reads
    that file to get the quality measurements (like how blurry the stars are)
    for each image.

    Parameters
    ----------
    seq_path : `str`
        The file path to the Siril `.seq` file.

    Returns
    -------
    frames : `list` of `dict`
        A list containing the quality measurements for each image. Returns
        an empty list if the file doesn't exist.
    """
    frames = []
    if not os.path.exists(seq_path):
        return frames
    with open(seq_path) as seq_file:
        for line in seq_file:
            if not _REGISTRATION_LINE_PATTERN.match(line):
                continue
            parts = line.split()
            frames.append({
                "fwhm_x": float(parts[1]),
                "fwhm_y": float(parts[2]),
                "roundness": float(parts[3]),
                "rmse": float(parts[5]),
                "nb_stars": int(parts[6]),
                "dx": float(parts[10]),
                "dy": float(parts[13]),
            })
    return frames


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
                "rmse": float(fields[12]),
            }
    return None
