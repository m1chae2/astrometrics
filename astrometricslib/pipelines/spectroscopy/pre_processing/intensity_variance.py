"""Purpose: Work out how uncertain each extracted brightness is.

Description: The extractor adds up the light in a box across the streak and
subtracts the sky. This module holds the noise model behind that sum and the
rules for carrying the resulting uncertainty through the later steps of
pre-processing.

The noise model (the CCD model, for a camera that counts electrons):

1. Light arrives as separate particles, so a pixel that collected `N`
   electrons scatters by `sqrt(N)` electrons from one exposure to the next
   (Poisson noise). The pixel holds the source and the sky together, so this
   term uses the whole pixel value. The camera's gain (electrons per ADU,
   the step of the stored number) turns ADU into electrons.
2. Every pixel gets the read noise, in electrons, each time the sensor is read.
3. The box total subtracts a sky level, and that level is itself measured
   from a few pixels, so it carries its own error (see `measure_sky` in
   `spectrum_extractor`).

The variance (the square of the 1-sigma error) of one pixel `p`, in stored
units squared, is ``a * p + b``. See `PixelNoiseModel`. A box total with
pixel weights `w` has variance ``sum(w**2 * (a * p + b))`` plus the sky
term. The weight is squared because a pixel counted for a fraction `w` of
itself adds `w` times its value and so `w**2` times its variance. A box of
whole pixels has ``sum(w**2) = n_pix``, which is the "n_pix times read noise
squared" form of the read term.

The later steps in pre-processing each multiply a sample by a factor (the
usable-sample mask keeps or drops it, the quantum-efficiency correction, the
instrument response and the extinction correction each scale it). A sample
scaled by `f` has its error scaled by `abs(f)`, so its variance scales by
`f**2`. `propagate_calibration_errors` applies the same factors the
brightness received.

Two limits of this model, which a reader should keep in mind:

* The extractor reads each sample at a position that is usually between two
  columns, and it combines the two columns with linear weights ``1 - u`` and
  ``u``. The variance of that combination is ``(1 - u)**2 * V1 + u**2 * V2``.
  Neighbouring samples share a column, so their errors are correlated (not
  independent). The error array holds each sample's own 1-sigma error. It
  does not hold the covariance between samples, so a sum over several
  samples that treats them as independent understates the error of the sum.
* The model assumes the image is a bias-subtracted, dark-subtracted frame in
  which one stored unit is a fixed number of ADU. A flat-fielded frame has a
  gain that varies a little from pixel to pixel, and this model ignores that.
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import resolve_detector_noise
from astrometricslib.pipelines.shared.quality.saturation import is_normalised_stack_scale
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction import (
    ExtinctionCorrection,
    extinction_correction_factor,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    InstrumentResponse,
    instrument_response_correction_factor,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    QuantumEfficiencyCurve,
    quantum_efficiency_correction_factor,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import ResolutionProfile
from astrometricslib.pipelines.stacking.processing.exposure_groups import FULL_SCALE_COUNTS

# The variance of the median of `n` normal numbers is `pi / 2` times the
# variance of one number divided by `n`, for large `n`. For the few pixels in
# one sky band the true value is lower, and it depends on whether `n` is even
# or odd (an even count averages the two middle numbers, which lowers the
# variance). A simulation of 200,000 draws gave the ratio of the true variance
# to the large-`n` value as 0.755, 0.819, 0.856, 0.878, 0.897, 0.908 and 0.933
# for n = 4, 6, 8, 10, 12, 14 and 20, and 0.910, 0.943, 0.954, 0.965, 0.970 and
# 0.982 for n = 5, 7, 9, 11, 15 and 21. The forms `n / (n + 1.4)` for even `n`
# and `n / (n + 0.4)` for odd `n` reproduce every one of those to within 0.012.
MEDIAN_VARIANCE_PI_OVER_TWO = math.pi / 2.0
MEDIAN_VARIANCE_EVEN_OFFSET = 1.4
MEDIAN_VARIANCE_ODD_OFFSET = 0.4

# The wavelength range over which the signal-to-noise numbers are taken, in
# Angstroms. Below 4200 A the spectrum is faint and the Balmer lines crowd
# together, and above 8000 A the sensor's quantum efficiency falls steeply
# (see `instrument_response`). This is the range the instrument response is
# fitted over, so it is the range later stages trust.
SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM = 4200.0
SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM = 8000.0

# A sample whose own signal-to-noise is below this counts toward
# `fraction_samples_snr_below_5`. Five is the usual rule of thumb for "a
# measurement you can read by eye"; it is a convention, not a measured limit.
LOW_SAMPLE_SIGNAL_TO_NOISE = 5.0

# The fewest resolution elements the like-for-like signal-to-noise needs, the
# same minimum as the post-hoc estimator (`spectrum_signal`).
_MINIMUM_ELEMENTS = 8

# A resolution element window is used only when at least this share of the
# samples inside it have a finite brightness and error.
_MINIMUM_WINDOW_VALID_FRACTION = 0.8


def median_variance_factor(sample_count: int) -> float:
    """Give the variance of a median relative to the variance of one pixel.

    Parameters
    ----------
    sample_count : `int`
        How many pixels the median was taken from.

    Returns
    -------
    factor : `float`
        The variance of the median of `sample_count` normal pixels divided by
        the variance of one pixel: ``(pi / 2) / (n + 1.4)`` for an even `n`
        and ``(pi / 2) / (n + 0.4)`` for an odd one. Multiply it by the pixel
        variance to get the variance of the sky level.
    """
    offset = MEDIAN_VARIANCE_EVEN_OFFSET if sample_count % 2 == 0 else MEDIAN_VARIANCE_ODD_OFFSET
    return MEDIAN_VARIANCE_PI_OVER_TWO / (sample_count + offset)


@dataclass(frozen=True)
class PixelNoiseModel:
    """How much a stored pixel value scatters from one exposure to the next.

    The stored value `p` is the ADU count divided by `adu_per_stored_unit`
    and averaged over `frames_averaged` frames. A raw camera frame has
    ``adu_per_stored_unit = 1`` and ``frames_averaged = 1``. A Siril stack
    saved as floating point holds the average of its frames as a fraction of
    full scale, so there ``adu_per_stored_unit`` is 65535 and
    ``frames_averaged`` is the number of stacked frames.

    One pixel's variance, in stored units squared, is ``a * p + b`` with
    ``a = 1 / (gain * adu_per_stored_unit * frames_averaged)`` and
    ``b = (read_noise / gain)**2 / (adu_per_stored_unit**2 * frames)``.
    A raw frame gives the familiar ``p / gain + (read_noise / gain)**2`` in
    ADU squared. Stacking and registration smooth the noise a little and make
    neighbouring pixels slightly correlated, which this model ignores, so
    the variance of a stack is approximate.

    Attributes
    ----------
    gain_e_per_adu : `float`
        Electrons per ADU. Above zero.
    read_noise_e : `float`
        Read noise in electrons, root mean square. Zero or more.
    gain_is_assumed : `bool`
        `True` when no source gave the gain and 1 electron per ADU was
        assumed.
    read_noise_is_assumed : `bool`
        `True` when no source gave the read noise and zero was assumed.
    adu_per_stored_unit : `float`
        ADU in one stored unit. Above zero.
    frames_averaged : `int`
        How many frames were averaged into each stored pixel. At least 1.
    frames_are_assumed : `bool`
        `True` when the frame is a stack but its header gives no frame count,
        so one frame was assumed. The variance is then too large by the true
        frame count.
    """

    gain_e_per_adu: float = 1.0
    read_noise_e: float = 0.0
    gain_is_assumed: bool = True
    read_noise_is_assumed: bool = True
    adu_per_stored_unit: float = 1.0
    frames_averaged: int = 1
    frames_are_assumed: bool = False

    @property
    def poisson_coefficient(self) -> float:
        """Give `a`, the variance added per stored unit of pixel value.

        Returns
        -------
        coefficient : `float`
            ``1 / (gain * adu_per_stored_unit * frames_averaged)``.
        """
        return 1.0 / (self.gain_e_per_adu * self.adu_per_stored_unit * self.frames_averaged)

    @property
    def read_variance(self) -> float:
        """Give `b`, the read-noise variance of one pixel.

        Returns
        -------
        variance : `float`
            ``(read_noise / gain)**2 / (adu_per_stored_unit**2 * frames)``,
            in stored units squared.
        """
        return (
            self.read_noise_e / self.gain_e_per_adu / self.adu_per_stored_unit
        ) ** 2 / self.frames_averaged

    def box_variance(self, pixels: np.ndarray, weights: np.ndarray) -> float:
        """Give the variance of a weighted sum of pixels, before the sky term.

        The Poisson term sums the raw pixel values without clipping them at
        zero. A pixel of a faint sky can read slightly below zero from read
        noise, and clipping each one would bias the sum high. The total is
        clipped at zero at the end.

        Parameters
        ----------
        pixels : `numpy.ndarray`
            The raw pixel values in the box (source and sky together), in
            stored units.
        weights : `numpy.ndarray`
            The fraction of each pixel inside the box, 0 to 1.

        Returns
        -------
        variance : `float`
            ``a * sum(w**2 * p)`` (not below zero) plus ``b * sum(w**2)``, in
            stored units squared.
        """
        squared_weights = np.asarray(weights, dtype=float) ** 2
        poisson = max(self.poisson_coefficient * float(np.sum(squared_weights * pixels)), 0.0)
        return poisson + self.read_variance * float(np.sum(squared_weights))

    def as_record(self) -> dict[str, object]:
        """Give the model's numbers as plain Python values.

        Returns
        -------
        record : `dict`
            ``gain_e_per_adu``, ``read_noise_e``, ``gain_is_assumed``,
            ``read_noise_is_assumed``, ``adu_per_stored_unit``,
            ``frames_averaged`` and ``frames_are_assumed``.
        """
        return {
            "gain_e_per_adu": float(self.gain_e_per_adu),
            "read_noise_e": float(self.read_noise_e),
            "gain_is_assumed": bool(self.gain_is_assumed),
            "read_noise_is_assumed": bool(self.read_noise_is_assumed),
            "adu_per_stored_unit": float(self.adu_per_stored_unit),
            "frames_averaged": int(self.frames_averaged),
            "frames_are_assumed": bool(self.frames_are_assumed),
        }


def build_pixel_noise_model(profile: CameraProfile | None, header: Any, data: np.ndarray) -> PixelNoiseModel:
    """Pick the noise model for one frame.

    The gain and read noise come from `resolve_detector_noise` (the camera
    profile first, then the ``EGAIN`` and ``RDNOISE`` header cards, then an
    assumed 1 electron per ADU and no read noise). A frame whose brightest
    pixel is at or below 1.0 is a Siril stack in fractions of full scale (see
    `intensity_scale`), so one stored unit is 65535 ADU and the frame count
    comes from the ``STACKCNT`` card.

    Parameters
    ----------
    profile : `CameraProfile` or `None`
        The camera's profile.
    header : `astropy.io.fits.Header` or `dict` or `None`
        The frame's header.
    data : `numpy.ndarray`
        The frame's pixels. Only used to tell a normalised stack from a raw
        frame.

    Returns
    -------
    model : `PixelNoiseModel`
        The model, with a flag for each assumption.
    """
    noise = resolve_detector_noise(profile, header)
    adu_per_unit = 1.0
    frames = 1
    frames_assumed = False
    if is_normalised_stack_scale(data):
        adu_per_unit = float(FULL_SCALE_COUNTS)
        try:
            frames = int(header.get("STACKCNT", 0) or 0) if header is not None else 0
        except TypeError, ValueError:
            frames = 0
        if frames < 1:
            frames = 1
            frames_assumed = True
    return PixelNoiseModel(
        gain_e_per_adu=noise.gain_e_per_adu,
        read_noise_e=noise.read_noise_e,
        gain_is_assumed=noise.gain_is_assumed,
        read_noise_is_assumed=noise.read_noise_is_assumed,
        adu_per_stored_unit=adu_per_unit,
        frames_averaged=frames,
        frames_are_assumed=frames_assumed,
    )


def combine_neighbour_variances(lower_variance: float, upper_variance: float, upper_weight: float) -> float:
    """Give the variance of a linear blend of two independent readings.

    Parameters
    ----------
    lower_variance : `float`
        Variance of the reading on the lower side.
    upper_variance : `float`
        Variance of the reading on the upper side.
    upper_weight : `float`
        The weight `u` of the upper reading, 0 to 1. The lower reading has
        weight ``1 - u``.

    Returns
    -------
    variance : `float`
        ``(1 - u)**2 * lower_variance + u**2 * upper_variance``.
    """
    return (1.0 - upper_weight) ** 2 * lower_variance + upper_weight**2 * upper_variance


def scale_errors(errors: np.ndarray, factor: np.ndarray | float) -> np.ndarray:
    """Scale 1-sigma errors by the factor their brightness was scaled by.

    Parameters
    ----------
    errors : `numpy.ndarray`
        The 1-sigma errors before the correction.
    factor : `numpy.ndarray` or `float`
        The number the brightness was multiplied by. A NaN factor gives a NaN
        error.

    Returns
    -------
    scaled : `numpy.ndarray`
        ``errors * abs(factor)``, so the variance scales by ``factor**2``.
    """
    return np.asarray(errors, dtype=float) * np.abs(np.asarray(factor, dtype=float))


@dataclass(frozen=True)
class PropagatedErrors:
    """The 1-sigma errors after each brightness correction.

    Attributes
    ----------
    quantum_efficiency_corrected : `numpy.ndarray` or `None`
        Errors of the quantum-efficiency-corrected brightness. `None` when no
        quantum-efficiency curve is known.
    response_corrected : `numpy.ndarray` or `None`
        Errors of the response-corrected brightness (after the extinction
        correction when it was applied). `None` when no instrument response
        was applied.
    """

    quantum_efficiency_corrected: np.ndarray | None
    response_corrected: np.ndarray | None


def propagate_calibration_errors(
    wavelength_angstrom: np.ndarray,
    errors: np.ndarray,
    curve: QuantumEfficiencyCurve | None,
    response: InstrumentResponse | None,
    extinction: ExtinctionCorrection | None,
) -> PropagatedErrors:
    """Carry the extracted errors through the brightness corrections.

    Each correction multiplies the brightness by a factor, so each scales the
    error by the absolute value of that factor: the quantum-efficiency
    correction by ``1 / QE``, the instrument response by ``1 / response``
    (NaN outside the response's range, like the brightness), and the
    extinction correction by its factor when it was applied. The curve and
    the response are taken to be exact. Their own uncertainty is not part of
    these errors.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelength of each sample, in Angstroms.
    errors : `numpy.ndarray`
        The 1-sigma error of each extracted sample.
    curve : `QuantumEfficiencyCurve` or `None`
        The camera's sensitivity curve, or `None` when none is known.
    response : `InstrumentResponse` or `None`
        The response divided out of the brightness, or `None` when none was.
    extinction : `ExtinctionCorrection` or `None`
        The record of the extinction correction, or `None` when none was
        tried. A record whose `is_applied` is `False` changes nothing.

    Returns
    -------
    propagated : `PropagatedErrors`
        The errors after each correction that was applied.
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    errors = np.asarray(errors, dtype=float)
    if curve is None:
        # The response is only ever applied to a quantum-efficiency-corrected
        # spectrum, so without a curve there is neither correction.
        return PropagatedErrors(None, None)
    qe_errors = scale_errors(errors, quantum_efficiency_correction_factor(wavelength_angstrom / 10.0, curve))
    if response is None:
        return PropagatedErrors(qe_errors, None)
    response_errors = scale_errors(
        qe_errors, instrument_response_correction_factor(wavelength_angstrom, response)
    )
    if extinction is not None and extinction.is_applied:
        response_errors = scale_errors(
            response_errors,
            extinction_correction_factor(
                wavelength_angstrom, float(extinction.target_airmass), float(extinction.reference_airmass)
            ),
        )
    return PropagatedErrors(qe_errors, response_errors)


@dataclass(frozen=True)
class SpectrumNoiseSummary:
    """The signal-to-noise numbers that checkpoint 1 reports.

    Attributes
    ----------
    median_snr_per_resolution_element : `float` or `None`
        The median, over `SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM` to
        `SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM`, of the brightness
        summed over one resolution element divided by the error of that
        sum. `None` when the range holds too few usable samples.
    fraction_samples_snr_below_5 : `float` or `None`
        The share of samples in that range with ``brightness / error``
        below `LOW_SAMPLE_SIGNAL_TO_NOISE`. `None` when no sample is usable.
    snr_estimate_ratio : `float` or `None`
        The variance-based signal-to-noise (`variance_based_signal_to_noise`,
        cut into elements the way the post-hoc estimator cuts them) divided by
        the post-hoc estimate from `estimate_spectrum_signal_to_noise`. `None`
        when either is missing or the post-hoc estimate is not a positive
        finite number.
    """

    median_snr_per_resolution_element: float | None
    fraction_samples_snr_below_5: float | None
    snr_estimate_ratio: float | None


def resolution_element_signal_to_noise(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    resolution_element_angstrom: float,
    resolution_profile: ResolutionProfile | None = None,
) -> np.ndarray:
    """Give each sample's signal-to-noise over one resolution element.

    For every sample, the samples within half a resolution element on each
    side are summed. The signal is the sum of the brightness, and the noise
    is the square root of the sum of the variances. The sum treats the
    samples as independent, which understates the noise a little where the
    extractor blended two columns (see the module description). The width of
    one resolution element comes from the line-spread profile at the
    sample's wavelength when a profile is given, and from
    `resolution_element_angstrom` otherwise.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The sample wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The brightness of each sample.
    errors : `numpy.ndarray`
        The 1-sigma error of each sample, in the same units.
    resolution_element_angstrom : `float`
        The width of one resolution element, in Angstroms, used where there
        is no profile.
    resolution_profile : `ResolutionProfile`, optional
        How the width changes along the spectrum.

    Returns
    -------
    signal_to_noise : `numpy.ndarray`
        One value per sample, in the order of the input arrays. NaN where
        fewer than `_MINIMUM_WINDOW_VALID_FRACTION` of the window's samples
        are usable.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    values = np.asarray(intensity, dtype=float)
    sigma = np.asarray(errors, dtype=float)
    order = np.argsort(wavelength)
    wavelength, values, sigma = wavelength[order], values[order], sigma[order]

    usable = np.isfinite(wavelength) & np.isfinite(values) & np.isfinite(sigma) & (sigma > 0)
    signal_cumulative = np.concatenate([[0.0], np.cumsum(np.where(usable, values, 0.0))])
    variance_cumulative = np.concatenate([[0.0], np.cumsum(np.where(usable, sigma**2, 0.0))])
    count_cumulative = np.concatenate([[0], np.cumsum(usable.astype(int))])

    if resolution_profile is not None:
        half_width = 0.5 * resolution_profile.at(wavelength)
    else:
        half_width = np.full(wavelength.shape, 0.5 * resolution_element_angstrom)
    safe_wavelength = np.where(np.isfinite(wavelength), wavelength, -np.inf)
    sorted_wavelength = np.where(np.isfinite(wavelength), wavelength, np.inf)
    first = np.searchsorted(sorted_wavelength, safe_wavelength - half_width, side="left")
    last = np.searchsorted(sorted_wavelength, safe_wavelength + half_width, side="right")

    window_size = last - first
    window_usable = count_cumulative[last] - count_cumulative[first]
    signal = signal_cumulative[last] - signal_cumulative[first]
    variance = variance_cumulative[last] - variance_cumulative[first]
    enough = (window_size > 0) & (
        window_usable >= _MINIMUM_WINDOW_VALID_FRACTION * np.maximum(window_size, 1)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(enough & (variance > 0), signal / np.sqrt(variance), np.nan)

    restored = np.empty_like(ratio)
    restored[order] = ratio
    return restored


def variance_based_signal_to_noise(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    resolution_element_angstrom: float,
) -> float | None:
    """Predict what `estimate_spectrum_signal_to_noise` should read.

    That estimator cuts the spectrum into non-overlapping resolution
    elements, takes the median element brightness as the signal, and measures
    the noise as the scatter between neighbouring elements. This function cuts
    the spectrum the same way. It takes the same signal and replaces the
    measured scatter with the noise the variance predicts for the mean of each
    element, ``sqrt(sum(variance)) / n``, using the median over the elements.
    The two numbers then estimate the same quantity, so their ratio tests the
    variance against the spectrum's own scatter.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The sample wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The brightness of each sample.
    errors : `numpy.ndarray`
        The 1-sigma error of each sample.
    resolution_element_angstrom : `float`
        The width of one resolution element, in Angstroms (one number for the
        whole spectrum, as the post-hoc estimator uses).

    Returns
    -------
    signal_to_noise : `float` or `None`
        The median element brightness over the median predicted noise of an
        element mean, or `None` when there are too few elements, the signal
        is not above zero or no sample has a usable error.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    values = np.asarray(intensity, dtype=float)
    sigma = np.asarray(errors, dtype=float)
    usable = np.isfinite(wavelength) & np.isfinite(values) & np.isfinite(sigma) & (sigma > 0)
    if not usable.any() or resolution_element_angstrom <= 0:
        return None
    wavelength, values, sigma = wavelength[usable], values[usable], sigma[usable]
    order = np.argsort(wavelength)
    wavelength, values, sigma = wavelength[order], values[order], sigma[order]

    element_index = np.floor((wavelength - wavelength[0]) / resolution_element_angstrom).astype(int)
    _, inverse, counts = np.unique(element_index, return_inverse=True, return_counts=True)
    element_means = np.bincount(inverse, weights=values) / counts
    element_noise = np.sqrt(np.bincount(inverse, weights=sigma**2)) / counts
    if element_means.size < _MINIMUM_ELEMENTS:
        return None
    signal = float(np.median(element_means))
    noise = float(np.median(element_noise))
    if signal <= 0 or noise <= 0:
        return None
    return signal / noise


def summarize_spectrum_noise(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    resolution_element_angstrom: float,
    post_hoc_signal_to_noise: float | None,
    resolution_profile: ResolutionProfile | None = None,
) -> SpectrumNoiseSummary:
    """Work out the signal-to-noise numbers for checkpoint 1.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The sample wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The brightness of each sample (the quantum-efficiency-corrected
        brightness when there is one: the post-hoc estimate used the same).
    errors : `numpy.ndarray`
        The 1-sigma error of each sample, in the same units as `intensity`.
    resolution_element_angstrom : `float`
        The width of one resolution element, in Angstroms.
    post_hoc_signal_to_noise : `float` or `None`
        The estimate from `estimate_spectrum_signal_to_noise`, for the ratio.
    resolution_profile : `ResolutionProfile`, optional
        How the resolution element changes along the spectrum.

    Returns
    -------
    summary : `SpectrumNoiseSummary`
        The three numbers. Each is `None` when it cannot be measured.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    values = np.asarray(intensity, dtype=float)
    sigma = np.asarray(errors, dtype=float)
    in_range = (
        np.isfinite(wavelength)
        & (wavelength >= SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM)
        & (wavelength <= SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM)
    )

    element_snr = resolution_element_signal_to_noise(
        wavelength, values, sigma, resolution_element_angstrom, resolution_profile
    )[in_range]
    element_snr = element_snr[np.isfinite(element_snr)]
    median_snr = float(np.median(element_snr)) if element_snr.size else None

    sample_ok = in_range & np.isfinite(values) & np.isfinite(sigma) & (sigma > 0)
    sample_snr = values[sample_ok] / sigma[sample_ok]
    fraction_low = float(np.mean(sample_snr < LOW_SAMPLE_SIGNAL_TO_NOISE)) if sample_snr.size else None

    ratio = None
    comparable = variance_based_signal_to_noise(wavelength, values, sigma, resolution_element_angstrom)
    if (
        comparable is not None
        and post_hoc_signal_to_noise is not None
        and math.isfinite(post_hoc_signal_to_noise)
        and post_hoc_signal_to_noise > 0
    ):
        ratio = comparable / post_hoc_signal_to_noise
    return SpectrumNoiseSummary(median_snr, fraction_low, ratio)
