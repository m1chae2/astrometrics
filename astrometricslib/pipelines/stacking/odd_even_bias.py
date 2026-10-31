"""Purpose: Measure the bias of the odd–even difference by injection.

Description: The odd–even difference
(`odd_even_difference_run.run_odd_even_difference_noise`) measures a stack's
random noise from the difference of two half stacks. Planck
found its half-difference low by about one percent, from correlations between
the halves and from clipping that removes the noise's positive tail. Ours has
both causes: the halves share the registration reference frame, and Siril
rejects outlier pixels. A low random part makes fixed-pattern noise look
larger (plan decision D16).

This module measures the bias with a test whose answer is known. Every frame
of a real library stack is replaced by a simulated frame that holds only
noise:

1. The real frame is smoothed where it holds only sky, and kept where it holds
   a star or another source (`simulate_frame`). Stars stay where the real
   frame had them, at the real frame's own shift, so registration lines the
   simulated frames up exactly as it lined up the real ones. A frame of
   noise alone has no stars to register on, so this is needed. The hot pixels
   and the sky level stay too, so the calibration frames the library applies
   (dark, flat, bias) act on the simulated frame as on the real one.
2. Gaussian noise is added to the smoothed sky, with the standard deviation
   the real frame is predicted to have: the square root of its sky electrons
   plus its read noise squared, divided by the gain. The smoothing leaves a
   little of the real noise behind; the added noise is lowered by that amount
   so the total matches the prediction.
3. The simulated frames go through the same steps as the real ones: the same
   odd and even halves with the same shared registration reference frame, the
   same stacker settings (so the same rejection), the same empty apertures.
   Besides the two halves, the whole set is stacked too.

The whole simulated stack has only random noise, so its noise in the empty
apertures is the true random noise of that stack. The difference of the halves,
turned into the noise of the whole stack the way the production job turns it
(`odd_even_difference_run.full_stack_noise_equivalent`), should equal it. The
ratio of the two is the bias: below 1 when the difference reads low. The
production measurement divides by it
(`difference_noise.ODD_EVEN_DIFFERENCE_NOISE_BIAS`).

What the ratio contains. It is the bias of the whole production recipe on
frames of this camera: clipping, the shared reference frame, resampling
correlation, and the equal-noise assumption of the conversion (the simulated
frames differ in noise as the real ones do). It does not contain fixed-pattern
noise, because the simulated frames hold none beyond what the calibration
frames add identically to both halves.

Limits. The sky of a simulated frame is smooth, so the test says nothing about
a structured sky. The result belongs to the camera and the stacker settings of
the run; the plan's decision D16 is to repeat it once for each camera and again
whenever the rejection settings change. Raw frames of a color camera are
smoothed within each Bayer position. It is a Design estimate until the result
is entered in the validation register.
"""

import logging
import math
import os
import shutil
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.fits_access import open_primary_hdu, write_image
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.stacking.odd_even_difference_run import (
    ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF,
    HalfStacks,
    difference_variance_factor,
    full_stack_noise_equivalent,
    split_into_halves,
    weight_shares,
)
from astrometricslib.pipelines.stacking.post_processing.difference_noise import measure_difference_noise
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    APERTURE_FILE_NAME,
    faint_source_distance,
    load_apertures_with_reason,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    ExpectedNoiseOutcome,
    FrameNoiseInput,
)
from astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution import (
    smoothed_noise_factor,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import (
    frame_noise_inputs_from_store,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    determine_stack_members,
    effective_frame_count,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import measure_stack_noise
from astrometricslib.pipelines.stacking.subset_noise_run import read_library_run
from astrometricslib.pipelines.stacking.subset_stack import stack_stem
from astrometricslib.pipelines.stacking.target_stack_guard import subset_job_guard

logger = logging.getLogger(__name__)

__all__ = [
    "BiasMeasurement",
    "BiasRepeat",
    "injected_noise_sigma_adu",
    "measure_odd_even_bias",
    "simulate_frame",
    "smoothed_sky_frame",
    "summarize_repeats",
]

# The width of the Gaussian that smooths the sky of a real frame. Smoothing
# with width s leaves the fraction 1 / (2 s sqrt(pi)) of white noise: 9.4%
# for s = 3 px, under 1% of the variance. A wider smoothing would leave less
# but would flatten more of the sky's real structure. Design estimate; no data
# set chose it.
SMOOTHING_SIGMA_PX = 3.0

# Real pixels are kept within this distance of any pixel the faint-source mask
# flags (stars included), so a star keeps its real core and wings. The empty
# apertures stay 13 px from flagged pixels, so none of their pixels is a real
# one. Design estimate; no data set chose it.
KEEP_RADIUS_PX = 4.0


@dataclass(frozen=True)
class BiasRepeat:
    """One simulated run of the bias test.

    Parameters
    ----------
    equivalent_noise_normalized : `float`
        The whole stack's random noise implied by the halves' difference, as a
        fraction of the reference stars' brightness.
    true_noise_normalized : `float`
        The noise of the whole simulated stack in the same apertures, on the
        same scale. Only random noise went in, so it is the true random noise.
    ratio : `float`
        ``equivalent / true``: below 1 when the difference reads low.
    ratio_error : `float`
        Its 1-sigma uncertainty from the two noises' relative errors added in
        quadrature. The two share apertures and frames, so they are positively
        correlated and the true error is smaller.
    difference_noise_normalized : `float`
        The (uncorrected) noise of the difference itself.
    variance_factor : `float`
        The difference's variance in units of one frame's variance.
    full_effective_frame_count : `float`
        The whole simulated stack's effective frame count.
    aperture_count : `int`
        The apertures behind the difference noise.
    """

    equivalent_noise_normalized: float
    true_noise_normalized: float
    ratio: float
    ratio_error: float
    difference_noise_normalized: float
    variance_factor: float
    full_effective_frame_count: float
    aperture_count: int


@dataclass
class BiasMeasurement:
    """The result of the bias test for one target and one camera.

    Parameters
    ----------
    target_id : `str`
        The target whose frames were simulated.
    camera : `str`
        The camera of those frames.
    ratio : `float` or `None`
        The mean of the repeats' ratios: the value for
        `difference_noise.ODD_EVEN_DIFFERENCE_NOISE_BIAS`. `None` when no
        repeat finished.
    ratio_error : `float` or `None`
        Its 1-sigma uncertainty: the standard error of the repeats when there
        are two or more, otherwise the one repeat's own error.
    repeats : `list` [`BiasRepeat`]
        Each simulated run.
    frame_count : `int`
        How many frames were simulated.
    odd_half_frames, even_half_frames : `int`
        The halves' sizes, the shared frame counted in both.
    stack_weight : `str` or `None`
        The weighting mode the stacker used (part of the settings the result
        belongs to).
    notes : `list` [`str`]
        Plain sentences about what was done and what the result does not hold.
    """

    target_id: str
    camera: str
    ratio: float | None = None
    ratio_error: float | None = None
    repeats: list[BiasRepeat] = field(default_factory=list)
    frame_count: int = 0
    odd_half_frames: int = 0
    even_half_frames: int = 0
    stack_weight: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Give the result as plain data for a JSON file.

        Returns
        -------
        data : `dict`
            All fields, the repeats as dicts.
        """
        return asdict(self)


def smoothed_sky_frame(data: np.ndarray, bayer: bool) -> np.ndarray:
    """Smooth a frame with a Gaussian of `SMOOTHING_SIGMA_PX`.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.
    bayer : `bool`
        Whether the frame is a color camera's mosaic. The smoothing is then
        done within each of the four Bayer positions, so colors do not mix.

    Returns
    -------
    smoothed : `numpy.ndarray`
        A 32-bit float array of the same shape.
    """
    image = np.asarray(data, dtype=np.float32)
    if not bayer:
        return gaussian_filter(image, SMOOTHING_SIGMA_PX)
    out = np.empty_like(image)
    for row_offset in (0, 1):
        for column_offset in (0, 1):
            plane = image[row_offset::2, column_offset::2]
            out[row_offset::2, column_offset::2] = gaussian_filter(plane, SMOOTHING_SIGMA_PX / 2.0)
    return out


def injected_noise_sigma_adu(sky_electrons: float, read_noise_electrons: float, gain: float) -> float:
    """Give the noise standard deviation a frame is predicted to have.

    Parameters
    ----------
    sky_electrons : `float`
        The sky in one pixel, in electrons (Poisson, so also its variance).
    read_noise_electrons : `float`
        The read noise, in electrons.
    gain : `float`
        The conversion gain, in electrons per ADU.

    Returns
    -------
    sigma : `float`
        ``sqrt(sky + read^2) / gain`` in ADU.
    """
    return math.sqrt(sky_electrons + read_noise_electrons**2) / gain


def simulate_frame(real_path: str, output_path: str, sigma_adu: float, rng: np.random.Generator) -> None:
    """Write a frame that keeps the real frame's sources and has known noise.

    Pixels within `KEEP_RADIUS_PX` of a source (a pixel the faint-source mask
    flags) are copied from the real frame. All other pixels take the smoothed
    real frame, and Gaussian noise of the predicted standard deviation is added
    to the whole frame (lowered by the noise the smoothing leaves behind in the
    sky). The result is rounded and clipped to the real frame's integer type,
    as a camera would store it. The header is copied, so the simulated frame
    has the same exposure, camera, time and sky coordinates.

    Parameters
    ----------
    real_path : `str`
        The real raw frame (a 2-D FITS image).
    output_path : `str`
        Where to write the simulated frame.
    sigma_adu : `float`
        The standard deviation of the noise to inject.
    rng : `numpy.random.Generator`
        The random numbers to draw from.

    Raises
    ------
    InvalidArgumentError
        If the frame is not a 2-D image.
    """
    with open_primary_hdu(real_path) as hdu:
        if hdu.data is None or hdu.data.ndim != 2:
            raise InvalidArgumentError(f"{real_path} is not a 2-D raw image, so it cannot be simulated.")
        data = np.array(hdu.data)
        header = hdu.header.copy()
    bayer = bool(str(header.get("BAYERPAT", "")).strip())
    smooth = smoothed_sky_frame(data, bayer)
    keep = faint_source_distance(np.asarray(data, dtype=np.float32)) <= KEEP_RADIUS_PX
    leftover = smoothed_noise_factor(SMOOTHING_SIGMA_PX / 2.0 if bayer else SMOOTHING_SIGMA_PX)
    added_sigma = sigma_adu * math.sqrt(max(1.0 - leftover**2, 0.0))
    simulated = np.where(keep, np.asarray(data, dtype=np.float32), smooth)
    simulated = simulated + rng.normal(0.0, added_sigma, size=simulated.shape).astype(np.float32)
    if np.issubdtype(data.dtype, np.integer):
        limits = np.iinfo(data.dtype)
        simulated = np.clip(np.rint(simulated), limits.min, limits.max)
    for key in ("BZERO", "BSCALE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "EXTEND"):
        header.remove(key, ignore_missing=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_image(output_path, simulated.astype(data.dtype), header)


def summarize_repeats(repeats: Sequence[BiasRepeat]) -> tuple[float | None, float | None]:
    """Combine the repeats into one ratio and its uncertainty.

    Parameters
    ----------
    repeats : `Sequence` [`BiasRepeat`]
        The finished runs.

    Returns
    -------
    ratio, error : `tuple` [`float` or `None`, `float` or `None`]
        The mean ratio and its 1-sigma uncertainty: the standard error of the
        mean when there are two or more repeats, otherwise the single repeat's
        own error. Both `None` when there are no repeats.
    """
    if not repeats:
        return None, None
    ratios = np.array([repeat.ratio for repeat in repeats])
    if len(repeats) == 1:
        return float(ratios[0]), repeats[0].ratio_error
    return float(ratios.mean()), float(ratios.std(ddof=1) / math.sqrt(len(repeats)))


def _noise_sigmas(
    members: Sequence[FrameRecord], target_id: str, configuration: Any, store: Any
) -> dict[str, float]:
    """Find the noise each member frame is predicted to have, in ADU.

    Parameters
    ----------
    members : `Sequence` [`FrameRecord`]
        The library stack's frames.
    target_id : `str`
        The target.
    configuration : `AppConfiguration`
        Where the quality store and the camera profiles live.
    store : `QualityStore` or `None`
        The quality store the library run read.

    Returns
    -------
    sigmas : `dict` [`str`, `float`]
        The standard deviation to inject for each frame, by frame path.

    Raises
    ------
    NotFoundError
        If a frame has no stored sky measurement or its camera profile has no
        gain and read noise (the reason is in the message).
    """
    inputs = frame_noise_inputs_from_store(
        list(members), [1.0] * len(members), target_id, configuration, store
    )
    if isinstance(inputs, ExpectedNoiseOutcome):
        raise NotFoundError(f"the frames' predicted noise is not available: {inputs.reason}")
    return {
        frame.path: injected_noise_sigma_adu(
            item.sky_electrons, item.read_noise_electrons, item.gain_electrons_per_adu
        )
        for frame, item in zip(members, inputs, strict=True)
        if isinstance(item, FrameNoiseInput)
    }


def measure_odd_even_bias(
    target: Target,
    library_stack_path: str,
    *,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    work_folder: str,
    repeats: int = 1,
    seed: int = 20261026,
    progress: Callable[[str], None] | None = None,
) -> BiasMeasurement:
    """Measure the odd–even difference's bias on simulated frames.

    Nothing is stored in the quality database and the library stack and its
    files are not changed. The half and whole stacks are made in the target's
    ``_subsets`` folder and deleted afterwards. Each repeat costs three
    stacks (the two halves and the whole set) and the time to simulate every
    frame. The target's subset-job lock is held for the duration.

    Parameters
    ----------
    target : `Target`
        The target; its library stack supplies the frames, the halves and the
        apertures.
    library_stack_path : `str`
        The target's library stack.
    configuration : `AppConfiguration`
        The settings (weighting, rejection and the quality store).
    drivers : `Drivers`, optional
        The stacking program. Siril when left out.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    work_folder : `str`
        An empty folder for the simulated frames (about the size of the real
        frames in all). It is created if missing, and the simulated frames are
        deleted afterwards; the folder itself stays.
    repeats : `int`, optional
        How many times to simulate with new noise.
    seed : `int`, optional
        The seed of the random numbers. Repeat ``r`` and frame ``i`` use the
        stream ``(seed, r, i)``, so a run repeats exactly.
    progress : `Callable`, optional
        Called with a sentence at each step.

    Returns
    -------
    measurement : `BiasMeasurement`
        The ratio, its uncertainty and every repeat.

    Raises
    ------
    InvalidArgumentError
        If the stack is spectral, a half would have fewer than
        `ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF` frames, or the target has
        no recorded registration reference frame.
    NotFoundError
        If the library stack has no saved noise sums or apertures, or the
        frames' predicted noise is not available.
    """

    def say(text: str) -> None:
        """Report a step.

        Parameters
        ----------
        text : `str`
            The sentence.
        """
        logger.info(text)
        if progress is not None:
            progress(text)

    with subset_job_guard(target.id):
        library = read_library_run(target, library_stack_path, configuration)
        if any(frame_is_spectral(frame) for frame in library.members):
            raise InvalidArgumentError("the bias is measured for imaging stacks only")
        if library.noise_sums is None:
            raise NotFoundError(
                "the library stack's per-aperture noise sums were not saved; stack the target again"
            )
        summary = getattr(target.stacking, "quality_summary", None)
        reference_path = getattr(
            getattr(summary, "stacking_metrics", None), "registration_reference_frame", None
        )
        if not reference_path:
            raise InvalidArgumentError("the library stack's registration reference frame is not recorded")
        split = split_into_halves(library.members, reference_path)
        smaller = min(len(split.odd), len(split.even))
        if smaller < ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF:
            raise InvalidArgumentError(
                f"a half would have {smaller} frames, fewer than the "
                f"{ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF} the odd–even difference needs"
            )
        library_folder = os.path.dirname(library_stack_path)
        apertures_path = os.path.join(library_folder, APERTURE_FILE_NAME)
        apertures, apertures_reason = load_apertures_with_reason(apertures_path)
        if apertures is None:
            raise NotFoundError(apertures_reason)
        sigmas = _noise_sigmas(library.members, target.id, configuration, library.store)
        dated_members = [frame for frame in library.members if frame.timestamp is not None]
        measurement = BiasMeasurement(
            target_id=target.id,
            camera=library.members[0].camera if library.members else "Unknown",
            frame_count=len(dated_members),
            odd_half_frames=len(split.odd),
            even_half_frames=len(split.even),
        )
        measurement.notes.append(
            "Simulated frames keep the real frames' sources and calibration and hold Gaussian noise of "
            "each frame's predicted standard deviation; see odd_even_bias.py for what the ratio contains."
        )
        stem = stack_stem(library_stack_path)
        half_stacks = HalfStacks(
            target_id=target.id,
            library_stack_path=library_stack_path,
            shared_path=reference_path,
            labels={"odd": f"{stem}.bias-odd", "even": f"{stem}.bias-even", "full": f"{stem}.bias-full"},
            configuration=configuration,
            drivers=drivers,
            stacking_slot=stacking_slot,
        )
        os.makedirs(work_folder, exist_ok=True)
        try:
            for repeat in range(repeats):
                outcome = _one_repeat(
                    repeat,
                    seed,
                    dated_members,
                    split,
                    sigmas,
                    apertures_path,
                    half_stacks,
                    work_folder,
                    measurement,
                    say,
                )
                measurement.repeats.append(outcome)
        finally:
            half_stacks.delete_folders()
            shutil.rmtree(os.path.join(work_folder, "simulated"), ignore_errors=True)
        measurement.ratio, measurement.ratio_error = summarize_repeats(measurement.repeats)
        return measurement


def _one_repeat(
    repeat: int,
    seed: int,
    members: Sequence[FrameRecord],
    split: Any,
    sigmas: dict[str, float],
    apertures_path: str,
    half_stacks: HalfStacks,
    work_folder: str,
    measurement: BiasMeasurement,
    say: Callable[[str], None],
) -> BiasRepeat:
    """Simulate every frame once, stack the halves and the whole, and compare.

    Parameters
    ----------
    repeat : `int`
        The repeat's number, for the random streams and the folder.
    seed : `int`
        The seed of the run.
    members : `Sequence` [`FrameRecord`]
        The dated library frames.
    split : `HalfSplit`
        The odd and even halves and the shared frame (real paths).
    sigmas : `dict` [`str`, `float`]
        The noise to inject by real frame path.
    apertures_path : `str`
        The target's stored apertures.
    half_stacks : `HalfStacks`
        Stacks the odd, even and whole sets. Its shared frame is the real
        reference frame; this repeat stacks a copy of it with the simulated
        frames.
    work_folder : `str`
        Holds the simulated frames.
    measurement : `BiasMeasurement`
        The result being built; the stacker's weighting mode is set on it.
    say : `Callable`
        Reports a step.

    Returns
    -------
    result : `BiasRepeat`
        The comparison of this repeat.

    Raises
    ------
    NotFoundError
        If a stack or a measurement fails; the message says which.
    """
    folder = os.path.join(work_folder, "simulated", f"repeat-{repeat}")
    simulated: dict[str, FrameRecord] = {}
    say(f"Repeat {repeat + 1}: simulating {len(members)} frames")
    for index, frame in enumerate(members):
        path = os.path.join(folder, f"{index:05d}_{os.path.basename(frame.path)}")
        simulate_frame(frame.path, path, sigmas[frame.path], np.random.default_rng([seed, repeat, index]))
        simulated[frame.path] = frame.model_copy(update={"path": path})
    shared = simulated[split.shared_path].path
    sets = {
        "odd": [simulated[frame.path] for frame in split.odd],
        "even": [simulated[frame.path] for frame in split.even],
        "full": [simulated[frame.path] for frame in members],
    }
    stacks = replace(half_stacks, shared_path=shared, stacked={})
    for name, frames in sets.items():
        say(f"Repeat {repeat + 1}: stacking the {name} set ({len(frames)} frames)")
        stacks.stack(name, frames)
        if not stacks.made_stack(name):
            raise NotFoundError(f"the stacker made no stack from the simulated {name} set")
        if not stacks.lined_up_on_shared(name):
            raise NotFoundError(
                f"the simulated {name} set was lined up with another reference frame than the shared one"
            )
    stacked = stacks.stacked
    measurement.stack_weight = stacked["odd"].stack_weight
    say(f"Repeat {repeat + 1}: measuring")
    difference = measure_difference_noise(
        stacked["odd"].stack_path, stacked["even"].stack_path, shared, apertures_path, bias=1.0
    )
    whole = measure_stack_noise(
        stacked["full"].stack_path, shared, apertures_path, (0, 0), may_choose_apertures=False
    )
    if difference.result is None or whole.result is None:
        raise NotFoundError(
            "a simulated stack's noise was not measured: "
            f"{difference.reason or whole.reason or 'no reason given'}"
        )
    shares = {}
    memberships = {}
    for name in ("odd", "even", "full"):
        memberships[name] = determine_stack_members(
            sets[name], stacked[name].diagnostics, stacked[name].stack_weight
        )
        shares[name] = weight_shares(memberships[name])
    full_weights = shares["full"]
    if full_weights is None or shares["odd"] is None or shares["even"] is None:
        raise NotFoundError("the simulated stacks' weights are unknown")
    full_count = effective_frame_count(list(full_weights.values()))
    equivalent = full_stack_noise_equivalent(
        difference.result.normalized_noise, full_count, shares["odd"], shares["even"]
    )
    if equivalent is None or full_count is None:
        raise NotFoundError("the whole noise could not be worked out from the difference")
    true_noise = whole.result.normalized_noise
    ratio = equivalent / true_noise
    relative_error = math.hypot(
        difference.result.normalized_uncertainty / difference.result.normalized_noise,
        whole.result.normalized_uncertainty / true_noise,
    )
    variance_factor = difference_variance_factor(shares["odd"], shares["even"]) or float("nan")
    return BiasRepeat(
        equivalent_noise_normalized=equivalent,
        true_noise_normalized=true_noise,
        ratio=ratio,
        ratio_error=ratio * relative_error,
        difference_noise_normalized=difference.result.normalized_noise,
        variance_factor=variance_factor,
        full_effective_frame_count=float(full_count),
        aperture_count=difference.result.aperture_count,
    )
