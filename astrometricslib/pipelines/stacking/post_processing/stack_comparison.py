"""Measures two stacks and says how they differ.

Four numbers describe how good a stacked picture is, apart from what it shows:

- the sky level (the median of the usable pixels), to see whether calibration
  moved the background;
- the noise in empty sky, the same number the target report stores
  (`stacking.empty_aperture_noise_normalized`), measured in the apertures the
  library stack chose;
- the large-scale flatness, which shows leftover vignetting and dust shadows
  (the part a flat frame is meant to fix);
- the star width (FWHM).

A fine-grain pixel noise is also kept, labelled as such. A number that cannot
be measured is reported as not measured, with the reason.

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
import warnings

import numpy as np
from skimage.registration import phase_cross_correlation

from astrometricslib._internal.drivers.fits_access import collapse_to_2d, read_data
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.quality.stack_comparison import StackComparison, StackMeasurements
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.stacking.post_processing.aperture_storage import APERTURE_FILE_NAME
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_TO_SIGMA,
    robust_sigma,
    sky_level_of_plane,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome

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

# A block of the flatness grid is used only if at least this share of its
# pixels hold data. The median of a mostly empty block (the ragged edge of a
# registered stack) reflects the edge, not the sky. A design choice, not tuned.
MINIMUM_USABLE_BLOCK_SHARE = 0.5

# Fewest usable blocks for a flatness number to mean anything.
MINIMUM_FLATNESS_BLOCKS = 4

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
    from astrometricslib.pipelines.stacking.processing.group_gains import saturated_pixel_mask

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


def _usable_pixels(image: np.ndarray) -> np.ndarray:
    """Mark the pixels that hold data: finite and not exactly zero.

    The empty edge of a registered stack is exactly zero. Counting it
    would pull every sky estimate toward zero.

    Parameters
    ----------
    image : `numpy.ndarray`
        The stack as one 2-D image.

    Returns
    -------
    usable : `numpy.ndarray`
        `True` where the pixel is finite and not zero.
    """
    return np.isfinite(image) & (image != 0)


def measure_stack_array(data: np.ndarray, path: str = "") -> StackMeasurements:
    """Measure one stacked image held in memory, apart from the aperture noise.

    The empty-aperture noise needs the stack's file and its apertures, so
    `measure_stack` adds it. Pixels that are zero or not finite are left out of
    the sky, the fine-grain noise and the flatness.

    Parameters
    ----------
    data : `numpy.ndarray`
        The stack's pixels. A colour stack is averaged over its channels first.
    path : `str`, optional
        The file the pixels came from, recorded in the result.

    Returns
    -------
    measurements : `StackMeasurements`
        The sky level, fine-grain noise, flatness, star width and zero
        fraction. ``normalized_noise`` is `None` with a reason.

    Raises
    ------
    ProcessingError
        If the image has no usable sky (blank or too small).
    """
    image = np.asarray(collapse_to_2d(np.asarray(data, dtype=np.float64)))
    usable = _usable_pixels(image)
    sky = sky_level_of_plane(image)
    if sky is None:
        raise ProcessingError("The stack has no measurable sky.")
    sky_sigma = MAD_TO_SIGMA * float(np.median(np.abs(image[usable] - sky)))
    if sky_sigma <= 0:
        raise ProcessingError("The stack has no measurable sky.")

    limit = sky + NOISE_STAR_SIGMA * sky_sigma
    left, right = image[:, :-1], image[:, 1:]
    sky_pairs = usable[:, :-1] & usable[:, 1:] & (left < limit) & (right < limit)
    fine_grain = (
        float(robust_sigma((right - left)[sky_pairs] / np.sqrt(2.0))) / sky if sky_pairs.any() else None
    )

    measurements = StackMeasurements(
        path=path,
        sky_level=sky,
        fine_grain_noise_fraction=fine_grain,
        normalized_noise=None,
        zero_fraction=float(np.mean(image == 0)),
        not_measured={"normalized_noise": "the noise in empty sky is measured on a stack file"},
    )
    if fine_grain is None:
        measurements.not_measured["fine_grain_noise_fraction"] = "no pairs of neighbouring sky pixels"

    block = FLATNESS_BLOCK_PIXELS
    rows, columns = (image.shape[0] // block) * block, (image.shape[1] // block) * block
    if rows == 0 or columns == 0:
        reason = "the stack is smaller than one flatness block"
        measurements.not_measured["flatness_rms"] = measurements.not_measured["flatness_peak_to_peak"] = (
            reason
        )
    else:
        shape = (rows // block, block, columns // block, block)
        filled = np.where(usable, image, np.nan)[:rows, :columns].reshape(shape)
        share = usable[:rows, :columns].reshape(shape).mean(axis=(1, 3))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            block_medians = np.nanmedian(filled, axis=(1, 3))
        block_medians = block_medians[share >= MINIMUM_USABLE_BLOCK_SHARE]
        block_medians = block_medians[np.isfinite(block_medians)]
        if block_medians.size < MINIMUM_FLATNESS_BLOCKS:
            reason = f"only {block_medians.size} flatness blocks hold enough data"
            measurements.not_measured["flatness_rms"] = measurements.not_measured["flatness_peak_to_peak"] = (
                reason
            )
        else:
            centre = float(np.median(block_medians))
            spread = MAD_TO_SIGMA * float(np.median(np.abs(block_medians - centre)))
            kept = block_medians[block_medians - centre < FLATNESS_BLOCK_OUTLIER_SIGMA * spread]
            if kept.size < 4:
                kept = block_medians
            measurements.flatness_rms = float(np.std(kept) / centre)
            measurements.flatness_peak_to_peak = float((kept.max() - kept.min()) / centre)

    measurements.fwhm_px = measure_fwhm_from_data(
        image, excluded_mask=combined_stack_excluded_mask(path, image.shape) if path else None
    )
    if measurements.fwhm_px is None:
        measurements.not_measured["fwhm_px"] = "too few stars could be fitted"
    return measurements


def measure_empty_aperture_noise(
    stack_path: str, apertures_path: str, is_spectral: bool = False
) -> tuple[StackNoiseOutcome, list[str]]:
    """Measure a stack's noise in the target's stored empty apertures.

    This is the definition the target report stores
    (``stacking.empty_aperture_noise_normalized``). The stack never chooses the
    apertures: it only reads the ones the library stack chose. The reference
    frame and the trimmed edges come from the registration record beside the
    stack. A stack with no record falls back to the frames in its
    ``_inputs.json``, with no trimmed edges known. A stack with neither is
    measured without them. The notes say which case applied.

    Parameters
    ----------
    stack_path : `str`
        The stack.
    apertures_path : `str`
        The target's stored apertures.
    is_spectral : `bool`, optional
        Whether the stack is spectral (its noise is not measured).

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement, or the reason there is none.
    notes : `list` [`str`]
        How the reference frame was found, and what could not be recovered.
    """
    from astrometricslib.pipelines.shared.registration_record import read_registration_record
    from astrometricslib.pipelines.stacking.post_processing.record_quality import measure_noise_safely
    from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import read_stack_inputs

    notes: list[str] = []
    reference: str | None = None
    crop_offset = (0, 0)
    member_frames: tuple[str, ...] = ()
    record = read_registration_record(stack_path)
    if record is not None:
        crop_offset = record.crop_offset
        if record.reference_frame and os.path.isfile(record.reference_frame):
            reference = record.reference_frame
        elif record.reference_frame:
            notes.append("The registration reference frame is no longer on disk.")
        else:
            notes.append("The registration record does not name the reference frame.")
    else:
        saved = read_stack_inputs(stack_path)
        if saved is not None:
            member_frames = tuple(str(entry[0]) for entry in saved["record"].get("frames", []))
            notes.append(
                "No registration record was kept, so the reference frame and the trimmed edges are not "
                "known; the frames listed in _inputs.json stand in for the reference."
            )
        else:
            notes.append(
                "Neither a registration record nor an _inputs.json was kept, so the reference frame "
                "and the trimmed edges are not recoverable."
            )
    outcome = measure_noise_safely(
        stack_path,
        reference,
        is_spectral,
        crop_offset,
        apertures_path,
        may_choose_apertures=False,
        member_frame_paths=member_frames,
    )
    return outcome, notes


def measure_stack(
    path: str, apertures_path: str | None = None, is_spectral: bool = False
) -> StackMeasurements:
    """Measure one stacked image on disk, including its noise in empty sky.

    Parameters
    ----------
    path : `str`
        Path of the stack's FITS file.
    apertures_path : `str`, optional
        The target's stored apertures. Left out, the file beside the stack.
    is_spectral : `bool`, optional
        Whether the stack is spectral.

    Returns
    -------
    measurements : `StackMeasurements`
        See `measure_stack_array`. A stack that cannot be read or has no sky
        comes back with every number `None` and the reason.
    """
    try:
        measurements = measure_stack_array(read_data(path), path)
    except (ProcessingError, OSError, ValueError) as error:
        return StackMeasurements(path=path, not_measured={"all": f"the stack could not be measured: {error}"})
    if apertures_path is None:
        apertures_path = os.path.join(os.path.dirname(path), APERTURE_FILE_NAME)
    outcome, notes = measure_empty_aperture_noise(path, apertures_path, is_spectral)
    measurements.notes = notes
    if outcome.result is not None:
        measurements.normalized_noise = outcome.result.normalized_noise
        measurements.normalized_noise_uncertainty = outcome.result.normalized_uncertainty
        measurements.not_measured.pop("normalized_noise", None)
    else:
        measurements.not_measured["normalized_noise"] = outcome.reason or "no reason was given"
    return measurements


def _sentence(
    label: str,
    before: float | None,
    after: float | None,
    unit: str,
    scale: float = 1.0,
    error: float | None = None,
    reasons: tuple[str, str] = ("", ""),
) -> str:
    """Describe the change in one measurement in a sentence.

    Parameters
    ----------
    label, unit, scale
        The name, the unit text and the factor into that unit.
    before, after : `float` or `None`
        The two values.
    error : `float`, optional
        The 1-sigma error of the difference, in the value's own units. A change
        smaller than it is reported as unchanged within the error.
    reasons : `tuple` [`str`, `str`], optional
        Why the before and after values are missing.

    Returns
    -------
    sentence : `str`
        For example ``noise 0.62% to 0.61% (down 2%)``, or the reason a value
        was not measured.
    """
    if before is None or after is None:
        missing = [
            f"{side} stack: {why}"
            for side, why, value in (("older", reasons[0], before), ("newer", reasons[1], after))
            if value is None
        ]
        return f"{label}: not measured ({'; '.join(missing)})."
    shown_before, shown_after = before * scale, after * scale
    if before == 0:
        return f"{label} {shown_before:.3g}{unit} to {shown_after:.3g}{unit}."
    change = after / before - 1.0
    if error is not None and abs(after - before) < error:
        word = "unchanged within the measurement error"
    elif error is None and abs(change) < UNCHANGED_FRACTION:
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
        each. No stack is called better.
    """
    fields = {
        "sky_level": ("sky level", "", 1.0),
        "normalized_noise": ("noise in empty sky", "% of the reference stars' brightness", 100.0),
        "flatness_rms": ("large-scale flatness (rms)", "% of the sky", 100.0),
        "flatness_peak_to_peak": ("large-scale flatness (peak to peak)", "% of the sky", 100.0),
        "fwhm_px": ("star width (FWHM)", " px", 1.0),
        "fine_grain_noise_fraction": (
            "fine-grain noise (pixel to pixel, not the noise number of the target report)",
            "% of the sky",
            100.0,
        ),
    }
    changes: dict[str, float | None] = {}
    summary = []
    for name, (label, unit, scale) in fields.items():
        old, new = getattr(before, name), getattr(after, name)
        changes[name] = None if old in (None, 0) or new is None else new / old - 1.0
        error = None
        if name == "normalized_noise" and None not in (
            before.normalized_noise_uncertainty,
            after.normalized_noise_uncertainty,
        ):
            error = float(np.hypot(before.normalized_noise_uncertainty, after.normalized_noise_uncertainty))
        reasons = (
            before.not_measured.get(name, before.not_measured.get("all", "not measured")),
            after.not_measured.get(name, after.not_measured.get("all", "not measured")),
        )
        summary.append(_sentence(label, old, new, unit, scale, error, reasons))
    return StackComparison(before=before, after=after, changes=changes, summary=summary)


def compare_stacks(
    before_path: str, after_path: str, apertures_path: str | None = None, is_spectral: bool = False
) -> StackComparison:
    """Measure two stacks on disk and compare them.

    Both stacks are measured in the same apertures, those beside the newer
    stack (the library stack), so their noise numbers can be compared.

    Parameters
    ----------
    before_path : `str`
        Path of the older stack's FITS file.
    after_path : `str`
        Path of the newer stack's FITS file.
    apertures_path : `str`, optional
        The target's stored apertures. Left out, the file beside the
        newer stack.
    is_spectral : `bool`, optional
        Whether the stacks are spectral.

    Returns
    -------
    comparison : `StackComparison`
        See `compare_measurements`.
    """
    if apertures_path is None:
        apertures_path = os.path.join(os.path.dirname(after_path), APERTURE_FILE_NAME)
    return compare_measurements(
        measure_stack(before_path, apertures_path, is_spectral),
        measure_stack(after_path, apertures_path, is_spectral),
    )
