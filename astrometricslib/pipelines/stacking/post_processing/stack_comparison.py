"""Measures two stacks and says how they differ.

Four numbers describe how good a stacked picture is, apart from what it shows:

- the sky level, to see whether calibration moved the background;
- the pixel noise, as a fraction of the sky;
- the large-scale flatness, which shows leftover vignetting and dust shadows
  (the part a flat frame is meant to fix);
- the star width (FWHM).

The comparison reports each number for both stacks and the change, in plain
sentences. It does not decide which stack is better, because that depends on
what the change was for: new flats should lower the flatness number and leave
the noise alone, while more frames should lower the noise and leave the
flatness alone.

Both stacks are measured the same way, so the numbers can be compared with
each other. They are not comparable with numbers from another tool.
"""

import json
import logging
import os

import numpy as np
from skimage.registration import phase_cross_correlation

from astrometricslib.drivers.fits_access import collapse_to_2d, read_data
from astrometricslib.models.stack_comparison import StackComparison, StackMeasurements
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data

logger = logging.getLogger(__name__)

# The window, in pixels, matched to find how far a stack was trimmed
# (see `_crop_offset`), and the smallest window worth matching. A star field
# this size holds hundreds of stars, enough to fix a whole-pixel offset.
_CROP_MATCH_WINDOW_PIXELS = 1024
_CROP_MATCH_MINIMUM_PIXELS = 128

# The flatness is measured on blocks of this many pixels on a side. A star is
# a few pixels wide and is ignored by the median of such a block, while
# vignetting and dust shadows (tens to hundreds of pixels) show up. Chosen on
# the ASI533MM Pro stacks (3008 x 3008): 64 px resolves a vignette across the
# frame and still averages 4096 pixels per block.
FLATNESS_BLOCK_PIXELS = 64

# A block is left out of the flatness when its median is more than this many
# robust standard deviations above the median of all blocks. That drops blocks
# that hold a bright star, a galaxy or a nebula, so the number measures the sky
# and not the target. Only bright blocks are dropped: a vignette makes the
# corners dark, and dropping them would hide the very pattern the number is
# meant to show. Checked on the Bubble Nebula and M 27 stacks, where the
# dropped blocks sit on the nebula and the brightest stars.
FLATNESS_BLOCK_OUTLIER_SIGMA = 4.0

# Pixels brighter than the sky by more than this many robust standard
# deviations are treated as stars and left out of the noise estimate.
NOISE_STAR_SIGMA = 3.0

# The robust standard deviation (from the median absolute deviation) is
# 1.4826 times the median absolute deviation for Gaussian noise.
_MAD_TO_SIGMA = 1.4826

# A change smaller than this fraction is reported as "unchanged". Two
# measurements of the same stack agree to about this much (the noise
# estimate repeated on the Bubble Nebula and M 27 stacks differed by under
# 1%), so a smaller change is not a real one.
UNCHANGED_FRACTION = 0.02


def _crop_offset(reference: np.ndarray, combined: np.ndarray) -> tuple[int, int] | None:
    """Find where a cropped image sits inside the larger one it came from.

    The stacking stage trims the noisy edges off the combined stack after it
    is made, and does not record how much it trimmed from each side. The
    offset is a whole number of pixels, so matching a window of the two
    images (phase correlation) recovers it.

    Parameters
    ----------
    reference : `numpy.ndarray`
        The larger 2-D image.
    combined : `numpy.ndarray`
        The smaller 2-D image, a crop of `reference` (up to the changes
        from combining).

    Returns
    -------
    offset : `tuple` [`int`, `int`] or `None`
        The row and column of `combined`'s first pixel in `reference`, or
        `None` if the images do not fit that way.
    """
    extra_rows = reference.shape[0] - combined.shape[0]
    extra_columns = reference.shape[1] - combined.shape[1]
    window = min(_CROP_MATCH_WINDOW_PIXELS, *combined.shape)
    if extra_rows < 0 or extra_columns < 0 or window < _CROP_MATCH_MINIMUM_PIXELS:
        return None
    row = (combined.shape[0] - window) // 2
    column = (combined.shape[1] - window) // 2
    shift, _, _ = phase_cross_correlation(
        reference[row : row + window, column : column + window],
        combined[row : row + window, column : column + window],
        normalization=None,
    )
    offset = (round(shift[0]), round(shift[1]))
    if not (0 <= offset[0] <= extra_rows and 0 <= offset[1] <= extra_columns):
        return None
    return offset


def combined_stack_excluded_mask(path: str, shape: tuple[int, int]) -> np.ndarray | None:
    """Find the pixels of a combined stack that came from a shorter exposure.

    A stack built from several exposure groups keeps each group's own stack
    in a ``groups`` folder beside it, with a manifest naming them. Where the
    group the stack is built around (the heaviest one) clips, the combined
    image holds another group's data, so a bright star's core is patched and
    reads too wide. The stacking stage leaves those stars out of its star
    width (see `measure_fwhm_from_data`). This rebuilds that mask from the
    files, so a check run later on the combined file gives the stage's
    number. Only the heaviest group's clipped pixels are used, which are the
    pixels that were patched.

    Parameters
    ----------
    path : `str`
        Path of the combined stack's FITS file.
    shape : `tuple` [`int`, `int`]
        The shape of the combined image, rows then columns.

    Returns
    -------
    mask : `numpy.ndarray` or `None`
        `True` where that group is saturated, in the combined image's frame.
        `None` when the stack has no manifest, the group stack is missing, or
        it cannot be matched to the combined image, so the caller measures
        the whole image.
    """
    from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import GROUPS_FOLDER_NAME
    from astrometricslib.pipelines.stacking.processing.exposure_groups import saturated_pixel_mask

    directory = os.path.join(os.path.dirname(path), GROUPS_FOLDER_NAME)
    stem = os.path.splitext(os.path.basename(path))[0]
    manifest_path = os.path.join(directory, f"{stem}_manifest.json")
    try:
        with open(manifest_path) as manifest_file:
            groups = [group for group in json.load(manifest_file)["groups"] if group.get("stack_path")]
        if not groups:
            return None
        heaviest = max(groups, key=lambda group: group.get("weight") or 0.0)
        group_path = heaviest["stack_path"]
        group_path = group_path if os.path.isabs(group_path) else os.path.join(directory, group_path)
        group_image = np.asarray(read_data(group_path), dtype=np.float64)
        mask = saturated_pixel_mask([group_image])
        if mask.shape == shape:
            return mask
        offset = _crop_offset(
            np.asarray(collapse_to_2d(group_image)),
            np.asarray(collapse_to_2d(read_data(path).astype(np.float64))),
        )
        if offset is None:
            return None
        row, column = offset
        return mask[row : row + shape[0], column : column + shape[1]]
    except OSError, ValueError, KeyError, TypeError:
        return None


def measure_stack_fwhm(path: str) -> float | None:
    """Measure a stack's star width the way the stacking stage does.

    Parameters
    ----------
    path : `str`
        Path of the stack's FITS file.

    Returns
    -------
    fwhm : `float` or `None`
        The median star width in pixels, leaving out stars on patched cores
        when the stack has exposure groups. `None` if it could not be
        measured.
    """
    image = np.asarray(collapse_to_2d(np.asarray(read_data(path), dtype=np.float64)))
    return measure_fwhm_from_data(image, excluded_mask=combined_stack_excluded_mask(path, image.shape))


def measure_stack_array(data: np.ndarray, path: str = "") -> StackMeasurements:
    """Measure one stacked image held in memory.

    Parameters
    ----------
    data : `numpy.ndarray`
        The stack's pixels. A colour stack is averaged over its channels first.
    path : `str`, optional
        The file the pixels came from, recorded in the result.

    Returns
    -------
    measurements : `StackMeasurements`
        The sky level, noise, flatness, star width and zero fraction.

    Raises
    ------
    ValueError
        If the image has no usable sky (blank or too small).
    """
    image = np.asarray(collapse_to_2d(np.asarray(data, dtype=np.float64)))
    sky = float(np.nanmedian(image))
    robust_sigma = _MAD_TO_SIGMA * float(np.nanmedian(np.abs(image - sky)))
    if not np.isfinite(sky) or sky <= 0 or robust_sigma <= 0:
        raise ValueError("The stack has no measurable sky.")

    sky_pixels = (image[:, 1:] < sky + NOISE_STAR_SIGMA * robust_sigma) & (
        image[:, :-1] < sky + NOISE_STAR_SIGMA * robust_sigma
    )
    differences = (image[:, 1:] - image[:, :-1])[sky_pixels] / np.sqrt(2.0)
    noise = _MAD_TO_SIGMA * float(np.median(np.abs(differences - np.median(differences))))

    block = FLATNESS_BLOCK_PIXELS
    rows, columns = (image.shape[0] // block) * block, (image.shape[1] // block) * block
    if rows == 0 or columns == 0:
        raise ValueError("The stack is smaller than one flatness block.")
    blocks = image[:rows, :columns].reshape(rows // block, block, columns // block, block)
    block_medians = np.median(blocks, axis=(1, 3))
    centre = float(np.median(block_medians))
    spread = _MAD_TO_SIGMA * float(np.median(np.abs(block_medians - centre)))
    kept = block_medians[block_medians - centre < FLATNESS_BLOCK_OUTLIER_SIGMA * spread]
    if kept.size < 4:
        kept = block_medians.ravel()

    return StackMeasurements(
        path=path,
        sky_level=sky,
        noise_fraction=noise / sky,
        flatness_rms=float(np.std(kept) / centre),
        flatness_peak_to_peak=float((kept.max() - kept.min()) / centre),
        fwhm_px=measure_fwhm_from_data(
            image, excluded_mask=combined_stack_excluded_mask(path, image.shape) if path else None
        ),
        zero_fraction=float(np.mean(image == 0.0)),  # ruff: ignore[float-equality-comparison] -- counting pixels that are exactly zero
    )


def measure_stack(path: str) -> StackMeasurements:
    """Measure one stacked image on disk.

    Parameters
    ----------
    path : `str`
        Path of the stack's FITS file.

    Returns
    -------
    measurements : `StackMeasurements`
        See `measure_stack_array`.
    """
    return measure_stack_array(read_data(path), path)


def _sentence(label: str, before: float | None, after: float | None, unit: str, scale: float = 1.0) -> str:
    """Describe the change in one measurement in a sentence.

    Returns
    -------
    sentence : `str`
        For example ``noise 0.62% to 0.61% (down 2%)``, or a note that the
        measurement is missing.
    """
    if before is None or after is None:
        return f"{label}: could not be measured on both stacks."
    shown_before, shown_after = before * scale, after * scale
    if before == 0:
        return f"{label} {shown_before:.3g}{unit} to {shown_after:.3g}{unit}."
    change = after / before - 1.0
    if abs(change) < UNCHANGED_FRACTION:
        word = "unchanged"
    else:
        word = f"{'down' if change < 0 else 'up'} {abs(change):.0%}"
    return f"{label} {shown_before:.3g}{unit} to {shown_after:.3g}{unit} ({word})."


def compare_measurements(before: StackMeasurements, after: StackMeasurements) -> StackComparison:
    """Compare two sets of stack measurements.

    Parameters
    ----------
    before : `StackMeasurements`
        The older stack.
    after : `StackMeasurements`
        The newer stack.

    Returns
    -------
    comparison : `StackComparison`
        The two measurements, the relative change of each, and a sentence for
        each.
    """
    fields = {
        "sky_level": ("sky level", "", 1.0),
        "noise_fraction": ("pixel noise", "% of the sky", 100.0),
        "flatness_rms": ("large-scale flatness (rms)", "% of the sky", 100.0),
        "flatness_peak_to_peak": ("large-scale flatness (peak to peak)", "% of the sky", 100.0),
        "fwhm_px": ("star width (FWHM)", " px", 1.0),
    }
    changes: dict[str, float | None] = {}
    summary = []
    for name, (label, unit, scale) in fields.items():
        old, new = getattr(before, name), getattr(after, name)
        changes[name] = None if old in (None, 0) or new is None else new / old - 1.0
        summary.append(_sentence(label, old, new, unit, scale))
    return StackComparison(before=before, after=after, changes=changes, summary=summary)


def compare_stacks(before_path: str, after_path: str) -> StackComparison:
    """Measure two stacks on disk and compare them.

    Parameters
    ----------
    before_path : `str`
        Path of the older stack's FITS file.
    after_path : `str`
        Path of the newer stack's FITS file.

    Returns
    -------
    comparison : `StackComparison`
        See `compare_measurements`.
    """
    return compare_measurements(measure_stack(before_path), measure_stack(after_path))
