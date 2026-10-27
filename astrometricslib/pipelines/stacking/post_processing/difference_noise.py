"""Measures the noise of two stacks' difference, and pairs their sums.

The difference of two stacks cancels everything their frames share (sky
structure, faint stars, nebula) and keeps their random noise.
`measure_difference_noise` measures that noise in the target's stored apertures
with the same estimator and flux scale as `measure_stack_noise`.

Two stacks measured in the same apertures can also be compared aperture by
aperture. A library stack's per-aperture sums are kept in a small file beside
the stack (`save_noise_sums`, `load_noise_sums`, `discard_noise_sums`), so a
later job can pair them with the sums of a subset stack without measuring the
library stack again. `paired_noise_difference` and `paired_noise_ratio`
resample the apertures together for both stacks, which gives a smaller error
than the two separate errors give. Two stacks are paired only when their
aperture sets have the same identity.

It imports `stack_image`, `empty_apertures` and `stack_noise`. The estimator
and the numbers are declared in `pipelines/stacking/metrics.py`.
"""

import json
import logging
import math
import os
from dataclasses import replace

import numpy as np

from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    MINIMUM_USABLE_APERTURES,
    ApertureSetMismatchError,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_TO_SIGMA,
    read_stack_plane,
    sky_level_of_plane,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    StackNoiseOutcome,
    StackNoiseResult,
    _measure_noise_in_plane,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ODD_EVEN_DIFFERENCE_NOISE_BIAS",
    "discard_noise_sums",
    "load_noise_sums",
    "measure_difference_noise",
    "noise_sums_path",
    "paired_noise_difference",
    "paired_noise_ratio",
    "save_noise_sums",
]

# The ratio of the odd–even difference's measured noise to the true random
# noise of the same frames: below 1 when the difference reads low. Planck found
# its half-difference low by about a percent, from correlations between the
# halves and from clipping that removes the noise's positive tail (plan
# decision D16, sources [P3], [P4]). Ours has both causes: the halves share the
# registration reference frame, and Siril rejects outlier pixels. The measured
# noise is divided by this number in one place, `measure_difference_noise`. 1.0
# means no correction. It stays 1.0 until `scripts/measure_odd_even_bias.py`
# has measured it by injection (simulated frames of pure noise through the same
# half stacks and rejection) and the result is entered here with its register
# entry. The plan's decision D16 is to rerun that script once for each camera
# and again whenever the stacker's rejection settings change. Status: Design
# estimate (no measurement yet). If the script finds the camera matters, the
# value belongs in the camera profile instead of here.
ODD_EVEN_DIFFERENCE_NOISE_BIAS = 1.0

# Two stacks measured in the same apertures can be compared aperture by
# aperture. The library stack's per-aperture sums are kept in a file beside the
# stack (the stack's name, then this ending), so a later job can pair them with
# the sums of a subset stack without measuring the library stack again.
NOISE_SUMS_SUFFIX = "_noise_sums.json"

# The paired comparison resamples the apertures (with replacement, the same
# apertures for both stacks) this many times. The error of a standard error
# from B resamples is 1 / sqrt(2 (B - 1)) of itself: 2.2% for 1000, small
# against the 12% error of the noise itself. The fixed seed makes the number
# repeat exactly.
PAIRED_BOOTSTRAP_COUNT = 1000
PAIRED_BOOTSTRAP_SEED = 20261024


def measure_difference_noise(
    first_stack_path: str,
    second_stack_path: str,
    reference_frame_path: str | None,
    apertures_path: str,
    *,
    first_plane: np.ndarray | None = None,
    second_plane: np.ndarray | None = None,
    target_sky_deg: tuple[float, float] | None = None,
    bias: float = ODD_EVEN_DIFFERENCE_NOISE_BIAS,
) -> StackNoiseOutcome:
    """Measure the noise of the difference of two stacks in empty sky.

    The two stacks must be on one pixel grid: stacked from disjoint sets of
    frames that were all registered to the same reference frame, and not
    trimmed. Subtracting one from the other cancels everything the frames
    share (sky structure, faint stars, nebula) and keeps the random noise of
    both stacks. The apertures are the target's stored ones and are never
    chosen here. The estimator, the uncertainty and the flux scale are those
    of `measure_stack_noise`: the sums are taken on the difference image, and
    the reference stars are read from the first stack. The result is the noise
    of the difference, not of either stack; for two halves of equal weight the
    stack's own random noise is half of it.

    Parameters
    ----------
    first_stack_path : `str`
        The first stack. The apertures are moved onto its stars.
    second_stack_path : `str`
        The second stack, on the same pixel grid.
    reference_frame_path : `str` or `None`
        The registration reference frame both stacks share.
    apertures_path : `str`
        Where the target's apertures are kept.
    first_plane, second_plane : `numpy.ndarray`, optional
        The two stacks' images, when the caller has already read them (see
        `read_stack_plane`), so the files are not read again. Both must be
        given, or both are read here.
    target_sky_deg : `tuple` [`float`, `float`], optional
        The target's (RA, Dec) in degrees, so the result gives its pixel
        position on the stack (for the per-aperture record, see
        `odd_even_apertures.py`).
    bias : `float`, optional
        The measured-to-true ratio of the difference's noise
        (`ODD_EVEN_DIFFERENCE_NOISE_BIAS`). The noise, its normalized value
        and their uncertainties are divided by it. The bias measurement itself
        passes 1 to see the uncorrected noise. The per-aperture sums are never
        corrected.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement of the difference, or the reason there is none (a
        stack with no pixels, stacks on different grids, or any reason
        `measure_stack_noise` gives).

    Raises
    ------
    ValueError
        If ``bias`` is not a positive number.
    """
    if not (math.isfinite(bias) and bias > 0):
        raise ValueError(f"the bias must be a positive number, not {bias!r}")
    if first_plane is None or second_plane is None:
        first_plane = read_stack_plane(first_stack_path)
        second_plane = read_stack_plane(second_stack_path)
    if first_plane is None or second_plane is None:
        return StackNoiseOutcome(None, "a half stack file holds no pixels")
    if first_plane.shape != second_plane.shape:
        return StackNoiseOutcome(
            None,
            f"the two stacks are on different pixel grids ({first_plane.shape} and {second_plane.shape})",
        )
    outcome = _measure_noise_in_plane(
        first_plane,
        first_stack_path,
        reference_frame_path,
        apertures_path,
        (0, 0),
        False,
        None,
        subtracted_plane=second_plane,
        target_sky_deg=target_sky_deg,
    )
    if outcome.result is not None and not math.isclose(bias, 1.0):
        result = outcome.result
        outcome = replace(
            outcome,
            result=replace(
                result,
                noise_adu=result.noise_adu / bias,
                noise_uncertainty_adu=result.noise_uncertainty_adu / bias,
                normalized_noise=result.normalized_noise / bias,
                normalized_uncertainty=result.normalized_uncertainty / bias,
            ),
        )
    return replace(outcome, stack_sky_level=sky_level_of_plane(first_plane))


def noise_sums_path(stack_path: str) -> str:
    """Name the file that keeps a stack's per-aperture sums.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    path : `str`
        The stack's path without its extension, then `NOISE_SUMS_SUFFIX`.
    """
    return os.path.splitext(stack_path)[0] + NOISE_SUMS_SUFFIX


def save_noise_sums(stack_path: str, result: StackNoiseResult) -> None:
    """Keep a stack's per-aperture sums beside it.

    A failure to write is logged and ignored: the file only makes a later
    paired comparison possible.

    Parameters
    ----------
    stack_path : `str`
        The stack the sums were measured on.
    result : `StackNoiseResult`
        Its noise measurement, holding the sums.
    """
    data = {
        "stack": os.path.basename(stack_path),
        "aperture_set_origin": result.aperture_set_origin,
        "aperture_identity": result.aperture_identity,
        "aperture_definition_note": result.aperture_definition_note,
        "reference_flux_adu": result.reference_flux_adu,
        "aperture_sums": [value if math.isfinite(value) else None for value in result.aperture_sums],
    }
    try:
        with open(noise_sums_path(stack_path), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError as error:
        logger.warning("The per-aperture noise sums of %s could not be saved: %s", stack_path, error)


def discard_noise_sums(stack_path: str) -> None:
    """Delete a stack's saved per-aperture sums, which no longer match it.

    Called when a new measurement of the stack failed, so that a later paired
    comparison cannot read the sums of an older stack file of the same name.

    Parameters
    ----------
    stack_path : `str`
        The stack.
    """
    try:
        os.unlink(noise_sums_path(stack_path))
    except FileNotFoundError:
        return
    except OSError as error:
        logger.warning("The stale per-aperture noise sums of %s could not be removed: %s", stack_path, error)


def load_noise_sums(stack_path: str) -> tuple[np.ndarray, float, str] | None:
    """Read the per-aperture sums that `save_noise_sums` kept for a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    sums : `tuple` or `None`
        The sums (NaN where an aperture was not usable), the reference flux
        they are read against, and the identity of the apertures they were
        measured in (empty for a file saved before identities existed, which
        never matches); or `None` when the file is missing or cannot be read.
    """
    try:
        with open(noise_sums_path(stack_path), encoding="utf-8") as handle:
            data = json.load(handle)
        sums = np.array([np.nan if value is None else value for value in data["aperture_sums"]], dtype=float)
        return sums, float(data["reference_flux_adu"]), str(data.get("aperture_identity", ""))
    except OSError, ValueError, KeyError, TypeError:
        return None


def _robust_spread(values: np.ndarray) -> np.ndarray:
    """Give the robust spread of each row of a table of values.

    Parameters
    ----------
    values : `numpy.ndarray`
        Shape ``(rows, n)`` or ``(n,)``.

    Returns
    -------
    spreads : `numpy.ndarray`
        1.4826 times the median absolute deviation of each row.
    """
    centers = np.median(values, axis=-1, keepdims=True)
    return MAD_TO_SIGMA * np.median(np.abs(values - centers), axis=-1)


def _different_apertures_message(full_identity: str, other_identity: str) -> str:
    """Say why two stacks' sums cannot be paired.

    Parameters
    ----------
    full_identity, other_identity : `str`
        The identities of the aperture sets (empty when unknown).

    Returns
    -------
    message : `str`
        A sentence naming both identities.
    """
    return (
        f"the two stacks were measured in different apertures (identities {full_identity or 'unknown'} "
        f"and {other_identity or 'unknown'}), so their noise cannot be paired"
    )


def _paired_scaled_sums(
    full_sums: np.ndarray,
    full_flux: float,
    other_sums: np.ndarray,
    other_flux: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Put two stacks' sums on the common scale where both are usable.

    Parameters
    ----------
    full_sums, other_sums : `numpy.ndarray`
        Each stack's sum at every stored aperture, NaN where it could not be
        measured.
    full_flux, other_flux : `float`
        The reference fluxes that put each stack on the common scale.

    Returns
    -------
    scaled : `tuple` or `None`
        The two scaled arrays, or `None` when the arrays differ in length,
        fewer than `MINIMUM_USABLE_APERTURES` apertures are usable in both, or
        a flux is not positive.

    """
    if full_sums.shape != other_sums.shape or full_flux <= 0 or other_flux <= 0:
        return None
    usable = np.isfinite(full_sums) & np.isfinite(other_sums)
    if int(np.count_nonzero(usable)) < MINIMUM_USABLE_APERTURES:
        return None
    return full_sums[usable] / full_flux, other_sums[usable] / other_flux


def paired_noise_difference(
    full_sums: np.ndarray,
    full_flux: float,
    full_identity: str,
    other_sums: np.ndarray,
    other_flux: float,
    other_identity: str,
) -> tuple[float, float, int] | None:
    """Compare the noise of two stacks measured in the same apertures.

    The two stacks share most of their frames, so the same apertures are high
    or low in both. Comparing their separate errors would overstate how
    uncertain the difference is. The apertures are resampled together
    (the same draw for both stacks), the difference of the two noises is found
    for each draw, and the spread of those differences is the error.

    Parameters
    ----------
    full_sums, other_sums : `numpy.ndarray`
        Shape ``(n,)``: each stack's sum at every stored aperture, NaN where
        it could not be measured.
    full_flux, other_flux : `float`
        The reference fluxes that put each stack on the common scale.
    full_identity, other_identity : `str`
        The identities of the aperture sets (`ApertureSet.identity`) each
        stack was measured in.

    Returns
    -------
    comparison : `tuple` or `None`
        The noise of the other stack minus the noise of the full stack, both
        as fractions of their reference flux; its error; and the number of
        apertures usable in both. `None` when the arrays differ in length,
        fewer than `MINIMUM_USABLE_APERTURES` apertures are usable in both, or
        a flux is not positive.

    Raises
    ------
    ApertureSetMismatchError
        If the identities are empty or differ.
    """
    if not full_identity or full_identity != other_identity:
        raise ApertureSetMismatchError(_different_apertures_message(full_identity, other_identity))
    scaled = _paired_scaled_sums(full_sums, full_flux, other_sums, other_flux)
    if scaled is None:
        return None
    first, second = scaled
    difference = float(_robust_spread(second) - _robust_spread(first))
    draws = np.random.default_rng(PAIRED_BOOTSTRAP_SEED).integers(
        0, first.size, size=(PAIRED_BOOTSTRAP_COUNT, first.size)
    )
    error = float(np.std(_robust_spread(second[draws]) - _robust_spread(first[draws]), ddof=1))
    return difference, error, int(first.size)


def paired_noise_ratio(
    full_sums: np.ndarray,
    full_flux: float,
    full_identity: str,
    other_sums: np.ndarray,
    other_flux: float,
    other_identity: str,
) -> tuple[float, float, int] | None:
    """Give the other stack's noise over the full stack's noise.

    This is the test of the noise forecast: a subset's noise over the library
    stack's should equal the square root of the ratio of their effective frame
    counts. The ratio of two spreads that share apertures has a smaller error
    than the two separate errors give, so the apertures are resampled together
    as in `paired_noise_difference`. Frames are not resampled, so the error
    does not include which frames the subset holds.

    Parameters
    ----------
    full_sums, other_sums, full_flux, other_flux, full_identity, other_identity
        As for `paired_noise_difference`.

    Returns
    -------
    comparison : `tuple` or `None`
        The ratio, its bootstrap error, and the number of apertures usable in
        both; `None` under the same conditions as `paired_noise_difference`.

    Raises
    ------
    ApertureSetMismatchError
        If the identities are empty or differ.
    """
    if not full_identity or full_identity != other_identity:
        raise ApertureSetMismatchError(_different_apertures_message(full_identity, other_identity))
    scaled = _paired_scaled_sums(full_sums, full_flux, other_sums, other_flux)
    if scaled is None:
        return None
    first, second = scaled
    first_spread = float(_robust_spread(first))
    if first_spread <= 0:
        return None
    ratio = float(_robust_spread(second)) / first_spread
    draws = np.random.default_rng(PAIRED_BOOTSTRAP_SEED).integers(
        0, first.size, size=(PAIRED_BOOTSTRAP_COUNT, first.size)
    )
    first_draws = _robust_spread(first[draws])
    valid = first_draws > 0
    error = float(np.std(_robust_spread(second[draws])[valid] / first_draws[valid], ddof=1))
    return ratio, error, int(first.size)
