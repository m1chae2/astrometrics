"""The noise a star should have, by source.

The sources are photon, sky, read and scintillation noise.

A star's brightness measurement is noisy for four separate reasons. This
module writes them down as a model, so a reader can see which one limits a
run, and so a number measured on the light curves (the empirical noise model
in `variability_indices`) has a model to be compared with.

* **Photon noise.** Light arrives as separate photons, so the count of a
  star's own electrons ``F`` scatters with a variance of ``F`` electrons
  squared.
* **Sky noise.** The sky adds its own electrons to every pixel of the star
  circle, and the sky level is itself measured from a finite ring, so that
  measurement adds noise too.
* **Read noise.** Each pixel read adds the camera's read noise ``RN`` in
  electrons, again in the circle and in the ring.
* **Scintillation.** Turbulence high in the air makes a star twinkle. The
  variation is a fraction of the star's light, so it is the same in
  magnitudes for every star. It does not depend on the camera.

The first three are the terms of the CCD equation that
`frame_photometry.aperture_flux_error_adu` already uses. Its variance is::

    variance = F + (n_pix + n_pix**2 / n_sky) * (S + RN**2)

where ``n_pix`` is the number of pixels in the star circle, ``n_sky`` the
number in the sky ring, and ``S`` the sky electrons per pixel. The photometry
saved the error of every point but not the sky level behind it, so
`background_variance_e2` works the sky term back out of the saved errors:
``variance - F`` is exactly ``(n_pix + n_pix**2 / n_sky) * (S + RN**2)``
because the error was made with this equation. The read term is then
``RN**2`` times the same geometry factor, and the sky term is what is left.

Scintillation uses Young's formula as Dravins and others later wrote it::

    sigma_rel = 0.09 * D**(-2/3) * X**1.75 * exp(-h / 8000) / sqrt(2 t)

with the aperture diameter ``D`` in centimeters, the airmass ``X``, the site
height ``h`` in meters and the exposure time ``t`` in seconds. It is a model,
not a measurement, and the sites it was fitted to scatter around it by tens of
percent. No site-dependent factor is applied (see the design-estimate row
PHO-D27 in the validation register).

This module only calculates. It reads no files and no configuration.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    APERTURE_RADIUS_PX,
    SKY_ANNULUS_INNER_RADIUS_PX,
    SKY_ANNULUS_OUTER_RADIUS_PX,
)

__all__ = [
    "MAGNITUDES_PER_FRACTION",
    "NoiseBudgetInputs",
    "NoiseVariances",
    "SessionNoiseInputs",
    "aperture_geometry_factor",
    "background_variance_e2",
    "magnitude_noise",
    "median_header_airmass",
    "noise_variances_at_magnitude",
    "scintillation_relative_noise",
]

# The change of magnitude for a small fractional change of flux:
# 2.5 / ln(10) = 1.0857. A relative noise of 1% is 10.857 millimagnitudes.
MAGNITUDES_PER_FRACTION = 2.5 / math.log(10.0)

# Young's scintillation formula (Young 1967), with the airmass power of 1.75
# that Dravins et al. (1998) measured instead of Young's 1.5. The factor 0.09
# is for the aperture diameter in centimeters, the exposure in seconds and a
# site at sea level. Model-derived, and not tuned on this observatory's
# frames (PHO-D27 in the validation register).
YOUNG_COEFFICIENT = 0.09
YOUNG_AIRMASS_POWER = 1.75

# The height over which the turbulence thins out, in meters (Young 1967).
SCINTILLATION_SCALE_HEIGHT_M = 8000.0


def aperture_geometry_factor() -> float:
    """Find how many pixels' worth of background noise one measurement carries.

    The sky level in the circle is subtracted using a level measured in a
    ring of ``n_sky`` pixels, so the background term of the CCD equation is
    ``n_pix + n_pix**2 / n_sky`` pixel variances. The circle's area is the
    exact area (the photometry weights the boundary pixels), and the ring is
    the pixels whose centers lie inside it (the photometry counts them the
    same way). The ring is counted for a ring centered on a pixel center; a
    ring centered elsewhere has a count that differs by under 1 percent.

    Returns
    -------
    factor : `float`
        ``n_pix + n_pix**2 / n_sky`` for the photometry's circle and ring.
    """
    circle_area = math.pi * APERTURE_RADIUS_PX**2
    half_width = math.ceil(SKY_ANNULUS_OUTER_RADIUS_PX) + 1
    offsets = np.arange(-half_width, half_width + 1, dtype=float)
    distance = np.hypot(offsets[:, None], offsets[None, :])
    ring_pixel_count = int(
        np.count_nonzero((distance >= SKY_ANNULUS_INNER_RADIUS_PX) & (distance < SKY_ANNULUS_OUTER_RADIUS_PX))
    )
    return circle_area + circle_area**2 / ring_pixel_count


def scintillation_relative_noise(
    aperture_diameter_mm: float,
    airmass: float,
    exposure_seconds: float,
    site_elevation_m: float = 0.0,
) -> float:
    """Find the fractional noise from scintillation (Young's formula).

    Parameters
    ----------
    aperture_diameter_mm : `float`
        The diameter of the telescope's aperture, in millimeters.
    airmass : `float`
        The airmass of the observation, 1.0 at the zenith.
    exposure_seconds : `float`
        The exposure time, in seconds.
    site_elevation_m : `float`, optional
        The site height above sea level, in meters. The default is sea
        level. A real site is higher, which lowers the value by about 12
        percent for each kilometer.

    Returns
    -------
    relative_noise : `float`
        The standard deviation of the star's flux as a fraction of its flux.

    Raises
    ------
    ValueError
        If the diameter, airmass or exposure is not above zero.
    """
    if aperture_diameter_mm <= 0 or airmass <= 0 or exposure_seconds <= 0:
        raise ValueError("The aperture, the airmass and the exposure must all be above zero.")
    diameter_cm = aperture_diameter_mm / 10.0
    return (
        YOUNG_COEFFICIENT
        * diameter_cm ** (-2.0 / 3.0)
        * airmass**YOUNG_AIRMASS_POWER
        * math.exp(-site_elevation_m / SCINTILLATION_SCALE_HEIGHT_M)
        / math.sqrt(2.0 * exposure_seconds)
    )


def background_variance_e2(
    fluxes_adu_per_second: np.ndarray,
    errors_adu_per_second: np.ndarray,
    exposure_seconds: np.ndarray,
    gain_e_per_adu: float,
) -> tuple[float | None, int]:
    """Work the background term out of the saved flux errors.

    The photometry made each error with the CCD equation, so the error
    squared in electrons, minus the star's own electrons, is the background
    term (sky and read noise in the circle and in the sky ring), exactly.

    Parameters
    ----------
    fluxes_adu_per_second, errors_adu_per_second : `numpy.ndarray`
        The saved fluxes and their 1-sigma errors, in ADU per second. The
        two arrays have the same length.
    exposure_seconds : `numpy.ndarray`
        The exposure time of each point, in seconds.
    gain_e_per_adu : `float`
        The camera gain the errors were made with, in electrons per ADU.

    Returns
    -------
    background_e2 : `float` or `None`
        The median background variance of one measurement, in electrons
        squared. `None` when no point has a positive error.
    point_count : `int`
        How many points the median rests on.
    """
    fluxes = np.asarray(fluxes_adu_per_second, dtype=float)
    errors = np.asarray(errors_adu_per_second, dtype=float)
    exposures = np.asarray(exposure_seconds, dtype=float)
    usable = (
        np.isfinite(fluxes) & np.isfinite(errors) & np.isfinite(exposures) & (errors > 0) & (exposures > 0)
    )
    if not usable.any():
        return None, 0
    star_electrons = np.clip(fluxes[usable], 0.0, None) * exposures[usable] * gain_e_per_adu
    error_electrons = errors[usable] * exposures[usable] * gain_e_per_adu
    return float(np.median(error_electrons**2 - star_electrons)), int(usable.sum())


@dataclass(frozen=True)
class SessionNoiseInputs:
    """What one session's frames say about their own noise.

    Parameters
    ----------
    session_id : `str`
        The session's id.
    exposure_seconds : `float` or `None`
        The median exposure of the session's frames, in seconds.
    gain_e_per_adu : `float`
        The gain the flux errors were made with.
    gain_is_assumed : `bool`
        Whether that gain was assumed to be 1 electron per ADU.
    read_noise_e : `float`
        The read noise the flux errors were made with, in electrons.
    read_noise_is_assumed : `bool`
        Whether the read noise was assumed to be zero.
    background_variance_e2 : `float` or `None`
        The median background variance of one measurement, in electrons
        squared (see `background_variance_e2`).
    point_count : `int`
        How many points that median rests on.
    """

    session_id: str
    exposure_seconds: float | None
    gain_e_per_adu: float
    gain_is_assumed: bool
    read_noise_e: float
    read_noise_is_assumed: bool
    background_variance_e2: float | None
    point_count: int

    @classmethod
    def from_analyzer_results(
        cls,
        session_id: str,
        stellar_objects: Sequence[Any],
        frame_exposure_seconds: dict[datetime, float],
        detector_noise: Any,
    ) -> SessionNoiseInputs:
        """Collect one session's noise inputs from what its analysis kept.

        Parameters
        ----------
        session_id : `str`
            The session's id.
        stellar_objects : `Sequence` [`StellarObject`]
            The session's stars, with their raw light curves.
        frame_exposure_seconds : `dict` [`datetime`, `float`]
            The exposure of each frame, keyed by its exposure start.
        detector_noise : `DetectorNoise`
            The gain and read noise the errors were made with.

        Returns
        -------
        inputs : `SessionNoiseInputs`
            The session's noise inputs.
        """
        flux_parts: list[np.ndarray] = []
        error_parts: list[np.ndarray] = []
        exposure_parts: list[np.ndarray] = []
        for star in stellar_objects:
            photometry = getattr(star, "photometry", None)
            if photometry is None or not photometry.flux_errors:
                continue
            if not (len(photometry.fluxes) == len(photometry.flux_errors) == len(photometry.timestamps)):
                continue
            exposures = [
                frame_exposure_seconds.get(timestamp, math.nan) for timestamp in photometry.timestamps
            ]
            flux_parts.append(np.asarray(photometry.fluxes, dtype=float))
            error_parts.append(np.asarray(photometry.flux_errors, dtype=float))
            exposure_parts.append(np.asarray(exposures, dtype=float))
        background, point_count = (None, 0)
        if flux_parts:
            background, point_count = background_variance_e2(
                np.concatenate(flux_parts),
                np.concatenate(error_parts),
                np.concatenate(exposure_parts),
                detector_noise.gain_e_per_adu,
            )
        exposure_values = [value for value in frame_exposure_seconds.values() if value > 0]
        return cls(
            session_id=session_id,
            exposure_seconds=float(np.median(exposure_values)) if exposure_values else None,
            gain_e_per_adu=float(detector_noise.gain_e_per_adu),
            gain_is_assumed=bool(detector_noise.gain_is_assumed),
            read_noise_e=float(detector_noise.read_noise_e),
            read_noise_is_assumed=bool(detector_noise.read_noise_is_assumed),
            background_variance_e2=background,
            point_count=point_count,
        )


@dataclass(frozen=True)
class NoiseBudgetInputs:
    """Everything the noise model of one run needs, and why any is missing.

    Parameters
    ----------
    session : `SessionNoiseInputs` or `None`
        The run's dominant session (the one with the most points). `None`
        when no session had noise inputs.
    zero_point_mag : `float` or `None`
        The median frame zero point of that session: the magnitude that one
        ADU per second has.
    zero_point_frame_count : `int`
        How many frames the zero point is the median of.
    zero_point_reason : `str`
        Why there is no zero point. Empty when there is one.
    band_name : `str`
        The catalog band of the zero point, in words. Empty with no zero point.
    aperture_diameter_mm : `float` or `None`
        The telescope's aperture diameter, from its focal length and focal
        ratio.
    aperture_reason : `str`
        Why the diameter is unknown. Empty when it is known.
    airmass : `float` or `None`
        The median airmass the frame headers gave.
    airmass_frame_count : `int`
        How many frames gave an airmass.
    airmass_reason : `str`
        Why there is no airmass. Empty when there is one.
    site_elevation_m : `float` or `None`
        The site's height above sea level, in meters. `None` when it is not
        known (the configuration holds 0 for "unknown").
    """

    session: SessionNoiseInputs | None
    zero_point_mag: float | None = None
    zero_point_frame_count: int = 0
    zero_point_reason: str = ""
    band_name: str = ""
    aperture_diameter_mm: float | None = None
    aperture_reason: str = ""
    airmass: float | None = None
    airmass_frame_count: int = 0
    airmass_reason: str = ""
    site_elevation_m: float | None = None


@dataclass(frozen=True)
class NoiseVariances:
    """The noise of one measurement of a star of a stated magnitude, by source.

    Parameters
    ----------
    star_electrons : `float`
        The star's net electrons in one exposure.
    photon_e2, sky_e2, read_e2 : `float` or `None`
        The variances, in electrons squared. `None` when they cannot be
        found (the read noise is only assumed, so the sky and read terms
        cannot be told apart).
    scintillation_relative : `float` or `None`
        The scintillation, as a fraction of the star's flux. `None` when the
        aperture, airmass or exposure is missing.
    """

    star_electrons: float
    photon_e2: float
    sky_e2: float | None
    read_e2: float | None
    scintillation_relative: float | None

    @property
    def scintillation_e2(self) -> float | None:
        """Give the scintillation as a variance in electrons squared.

        Returns
        -------
        variance : `float` or `None`
            ``(sigma_rel * F)**2``, or `None` when scintillation is unknown.
        """
        if self.scintillation_relative is None:
            return None
        return (self.scintillation_relative * self.star_electrons) ** 2


def noise_variances_at_magnitude(magnitude: float, inputs: NoiseBudgetInputs) -> NoiseVariances | None:
    """Find the noise terms of a star of a stated catalog magnitude.

    The star's flux follows from the zero point: ``f = 10**(0.4 (ZP - m))``
    ADU per second. It is measured in the photometry's circle with no
    aperture correction, as the zero point is.

    Parameters
    ----------
    magnitude : `float`
        The star's catalog magnitude in the zero point's band.
    inputs : `NoiseBudgetInputs`
        The run's noise inputs.

    Returns
    -------
    variances : `NoiseVariances` or `None`
        The terms. `None` when the run has no zero point, exposure or
        background term, so no term can be found.
    """
    session = inputs.session
    if (
        session is None
        or inputs.zero_point_mag is None
        or session.exposure_seconds is None
        or session.background_variance_e2 is None
    ):
        return None
    flux_adu_per_second = 10.0 ** (0.4 * (inputs.zero_point_mag - magnitude))
    star_electrons = flux_adu_per_second * session.exposure_seconds * session.gain_e_per_adu
    sky_e2: float | None = None
    read_e2: float | None = None
    if not session.read_noise_is_assumed:
        read_e2 = session.read_noise_e**2 * aperture_geometry_factor()
        sky_e2 = session.background_variance_e2 - read_e2
        if sky_e2 < 0:
            # The sky term cannot be negative. The saved errors then carry less
            # background than the read noise alone gives, so the read noise
            # or the geometry does not match the errors; report neither.
            sky_e2, read_e2 = None, None
    scintillation: float | None = None
    if (
        inputs.aperture_diameter_mm is not None
        and inputs.airmass is not None
        and session.exposure_seconds > 0
    ):
        scintillation = scintillation_relative_noise(
            inputs.aperture_diameter_mm,
            inputs.airmass,
            session.exposure_seconds,
            inputs.site_elevation_m if inputs.site_elevation_m is not None else 0.0,
        )
    return NoiseVariances(
        star_electrons=star_electrons,
        photon_e2=star_electrons,
        sky_e2=sky_e2,
        read_e2=read_e2,
        scintillation_relative=scintillation,
    )


def magnitude_noise(variance_e2: float, star_electrons: float) -> float:
    """Turn a variance in electrons squared into a noise in magnitudes.

    Parameters
    ----------
    variance_e2 : `float`
        The variance, in electrons squared.
    star_electrons : `float`
        The star's net electrons in the exposure.

    Returns
    -------
    noise_mag : `float`
        ``1.0857 * sqrt(variance) / F``, the 1-sigma noise in magnitudes.
    """
    return MAGNITUDES_PER_FRACTION * math.sqrt(max(variance_e2, 0.0)) / star_electrons


def median_header_airmass(airmasses: Sequence[float | None]) -> tuple[float | None, int]:
    """Summarize the airmass the frame headers gave.

    Parameters
    ----------
    airmasses : `Sequence` [`float` or `None`]
        The airmass of each frame, `None` for a frame whose header gave none.

    Returns
    -------
    airmass : `float` or `None`
        The median of the airmasses that were given, `None` when none was.
    count : `int`
        How many frames gave one.
    """
    given = [value for value in airmasses if value is not None and math.isfinite(value) and value >= 1.0]
    if not given:
        return None, 0
    return float(np.median(given)), len(given)
