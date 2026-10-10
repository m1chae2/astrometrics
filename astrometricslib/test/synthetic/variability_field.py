"""Purpose: Build a synthetic field of light curves with known variables.

Description: Tests of variable-star detection need a field where the
answer is known: which stars vary, by how much, and how noisy the
measurements are. This file builds the raw light curves of a field of
stars (flux in ADU per second, with the error of each point) as the
photometry pipeline would hold them before normalization. The stars are
constant across several magnitudes of brightness. A small fraction carry an
injected sinusoid or an eclipsing dip of a chosen size.

Noise model of the generator, per star and per frame:

* Photon noise of the star plus a fixed background variance (sky, read
  noise), in ADU squared per exposure. The error of the point written to
  ``flux_errors`` is exactly this, as the CCD equation gives it (see
  ``frame_photometry.aperture_flux_error_adu``).
* A systematic term, a fraction of the star's flux, drawn independently for
  each star and frame. It stands for centroid jitter and flat-field errors.
  It is added to the flux but NOT to ``flux_errors``, so the bright stars'
  scatter sits above their recorded errors, as in real data.
* Extinction that dims every star by the same factor as the airmass rises,
  and a common transparency change per frame. Normalization against the
  comparison stars removes both.

Conventions: magnitudes are instrumental, ``-2.5 log10(flux in ADU per
second)``. Amplitudes are in magnitudes. A sinusoid of amplitude ``a`` has a
semi-amplitude of ``a`` (peak to peak ``2a``). An eclipsing dip of amplitude
``a`` is the depth of the dip at its centre.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject

SYNTHETIC_FIELD_START = datetime(2026, 5, 24, 3, 0, 0)

# Width (standard deviation) of an eclipse as a fraction of the period.
ECLIPSE_SIGMA_PHASE = 0.04

# Extinction, in magnitudes per airmass, common to every star.
EXTINCTION_MAG_PER_AIRMASS = 0.25


@dataclass(frozen=True)
class InjectedVariable:
    """The truth about one star that varies.

    Attributes
    ----------
    star_index : `int`
        The index of the star in the field.
    kind : `str`
        ``"sinusoid"`` or ``"eclipsing"``.
    amplitude_mag : `float`
        The semi-amplitude of a sinusoid, or the depth of an eclipse, in
        magnitudes.
    period_frames : `float`
        The period, in frames.
    """

    star_index: int
    kind: str
    amplitude_mag: float
    period_frames: float


@dataclass
class SyntheticVariabilityField:
    """A field of raw light curves and the truth that went into it.

    Attributes
    ----------
    stars : `list` [`StellarObject`]
        The stars, with raw light curves (``timestamps``, ``fluxes`` in ADU
        per second, ``flux_errors``, ``is_saturated``, ``airmasses``).
    timestamps : `list` [`datetime.datetime`]
        The exposure start of every frame.
    true_magnitudes : `numpy.ndarray`
        The true instrumental magnitude of each star.
    is_variable : `numpy.ndarray`
        ``True`` for each star that carries an injected signal.
    injected : `list` [`InjectedVariable`]
        The injected signals.
    """

    stars: list[StellarObject]
    timestamps: list[datetime]
    true_magnitudes: np.ndarray
    is_variable: np.ndarray
    injected: list[InjectedVariable]


def variable_magnitude_signal(
    kind: str, amplitude_mag: float, period_frames: float, frames: np.ndarray, phase: float
) -> np.ndarray:
    """Give the magnitude change of a variable star at each frame.

    Parameters
    ----------
    kind : `str`
        ``"sinusoid"`` or ``"eclipsing"``.
    amplitude_mag : `float`
        Semi-amplitude of a sinusoid, or depth of an eclipse, in magnitudes.
    period_frames : `float`
        The period in frames.
    frames : `numpy.ndarray`
        Frame numbers.
    phase : `float`
        Phase offset, in cycles, between 0 and 1.

    Returns
    -------
    delta_mag : `numpy.ndarray`
        The magnitude change at each frame. Positive means fainter.

    Raises
    ------
    ValueError
        If `kind` is not ``"sinusoid"`` or ``"eclipsing"``.
    """
    cycle = (frames / period_frames + phase) % 1.0
    if kind == "sinusoid":
        return amplitude_mag * np.sin(2.0 * np.pi * cycle)
    if kind == "eclipsing":
        distance = np.minimum(cycle, 1.0 - cycle)
        return amplitude_mag * np.exp(-0.5 * (distance / ECLIPSE_SIGMA_PHASE) ** 2)
    raise ValueError(f"unknown variable kind {kind!r}")


def make_variability_field(
    *,
    star_count: int = 400,
    frame_count: int = 48,
    magnitude_span: float = 6.0,
    brightest_flux_adu_s: float = 7.5e4,
    variable_fraction: float = 0.05,
    amplitude_range_mag: tuple[float, float] = (0.01, 0.3),
    exposure_s: float = 30.0,
    cadence_min: float = 10.0,
    background_variance_adu2: float = 3.0e5,
    systematic_fraction: float = 0.003,
    flicker_sigma: float = 0.01,
    outlier_fraction: float = 0.0,
    outlier_scale: float = 5.0,
    seed: int = 0,
) -> SyntheticVariabilityField:
    """Build a field of raw light curves with injected variables.

    The stars' true fluxes are spread evenly in magnitude over
    ``magnitude_span`` magnitudes, from ``brightest_flux_adu_s`` downward. A
    random ``variable_fraction`` of them carry a sinusoid (two thirds) or an
    eclipsing dip (one third). Amplitudes are log-uniform over
    ``amplitude_range_mag`` and do not depend on brightness. Periods are
    log-uniform from 4 frames to the length of the run.

    Parameters
    ----------
    star_count : `int`, optional
        Number of stars.
    frame_count : `int`, optional
        Number of frames.
    magnitude_span : `float`, optional
        Brightness range of the field, in magnitudes.
    brightest_flux_adu_s : `float`, optional
        True flux of the brightest star, in ADU per second.
    variable_fraction : `float`, optional
        Share of stars that carry an injected signal.
    amplitude_range_mag : `tuple` [`float`, `float`], optional
        Smallest and largest injected amplitude, in magnitudes.
    exposure_s : `float`, optional
        Exposure time of each frame, in seconds.
    cadence_min : `float`, optional
        Time between frame starts, in minutes.
    background_variance_adu2 : `float`, optional
        Sky and read-noise variance in the aperture, in ADU squared per
        exposure.
    systematic_fraction : `float`, optional
        Standard deviation of the unrecorded systematic term, as a fraction
        of the flux.
    flicker_sigma : `float`, optional
        Standard deviation of the common transparency change per frame, as
        a fraction of the flux.
    outlier_fraction : `float`, optional
        Share of points whose random error is multiplied by `outlier_scale`.
        These points stand for cosmic rays, bad centroids and passing
        clouds. The recorded error does not include the extra scatter.
    outlier_scale : `float`, optional
        The factor on the random error of an outlier point.
    seed : `int`, optional
        Seed of every random draw.

    Returns
    -------
    field : `SyntheticVariabilityField`
        The stars and the truth.
    """
    rng = np.random.default_rng(seed)
    frames = np.arange(frame_count, dtype=float)
    timestamps = [SYNTHETIC_FIELD_START + timedelta(minutes=cadence_min * float(k)) for k in frames]
    airmass = 1.0 + frames / max(frame_count - 1, 1)
    extinction = 10.0 ** (-0.4 * EXTINCTION_MAG_PER_AIRMASS * (airmass - 1.0))
    flicker = np.exp(rng.normal(0.0, flicker_sigma, frame_count))

    true_magnitudes = -2.5 * np.log10(brightest_flux_adu_s) + np.linspace(0.0, magnitude_span, star_count)
    variable_count = round(variable_fraction * star_count)
    variable_indices = rng.choice(star_count, size=variable_count, replace=False)
    log_low, log_high = np.log(amplitude_range_mag[0]), np.log(amplitude_range_mag[1])
    injected: list[InjectedVariable] = []
    for ordinal, index in enumerate(sorted(int(i) for i in variable_indices)):
        injected.append(
            InjectedVariable(
                star_index=index,
                kind="eclipsing" if ordinal % 3 == 2 else "sinusoid",
                amplitude_mag=float(np.exp(rng.uniform(log_low, log_high))),
                period_frames=float(np.exp(rng.uniform(np.log(4.0), np.log(float(frame_count))))),
            )
        )
    injected_by_index = {entry.star_index: entry for entry in injected}

    stars: list[StellarObject] = []
    for index in range(star_count):
        true_flux = 10.0 ** (-0.4 * true_magnitudes[index])
        multiplier = np.ones(frame_count)
        if index in injected_by_index:
            entry = injected_by_index[index]
            delta_mag = variable_magnitude_signal(
                entry.kind, entry.amplitude_mag, entry.period_frames, frames, float(rng.uniform())
            )
            multiplier = 10.0 ** (-0.4 * delta_mag)
        expected = true_flux * extinction * flicker * multiplier
        sigma = np.sqrt(expected * exposure_s + background_variance_adu2) / exposure_s
        systematic = systematic_fraction * expected * rng.normal(0.0, 1.0, frame_count)
        noise_scale = np.where(rng.uniform(size=frame_count) < outlier_fraction, outlier_scale, 1.0)
        flux = expected + noise_scale * sigma * rng.normal(0.0, 1.0, frame_count) + systematic
        star = StellarObject(id=f"Star_{index}")
        star.flux = float(true_flux)
        star.photometry = PhotometryResult(
            timestamps=list(timestamps),
            fluxes=flux.tolist(),
            flux_errors=sigma.tolist(),
            is_saturated=[False] * frame_count,
            airmasses=airmass.tolist(),
        )
        stars.append(star)

    is_variable = np.zeros(star_count, dtype=bool)
    is_variable[list(injected_by_index)] = True
    return SyntheticVariabilityField(stars, timestamps, true_magnitudes, is_variable, injected)
