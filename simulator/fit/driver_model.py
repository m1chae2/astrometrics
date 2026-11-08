"""Purpose: The INDI simulators' own formulas, inverted by hand.

Description: The fit does not tune the simulators by trial and error. It reads
how the INDI 2.2.5 camera simulators turn a setting into pixel values
(``drivers/ccd/ccd_simulator.cpp`` and ``guide_simulator.cpp``) and solves
those formulas for the setting. Each function names the lines it inverts.

Sky brightness. Both drivers set a "saturation magnitude" star to the maximum
pixel value in one second and a "limiting magnitude" star to one ADU in one
second. The main camera simulator's flux for a magnitude ``m`` is
``flux(m) = MAXVAL ** ((LIMITING - m) / (LIMITING - SATURATION))``
(``ccd_simulator.cpp:528-535``, which is ``pow(10, (z - m) * k / 2.5)`` with
``z`` the limiting magnitude and ``k = 2.5 log10(MAXVAL) / (z - saturation)``).
The sky is ``flux(1.3 * SIM_SKYGLOW) * exposure * (1 + sqrt(gain))``
(``:827`` for the factor, ``:838`` for the product, ``:551`` for the gain
factor), times a vignetting factor ``exp(-1.4 d^2 / v^2)`` where ``d`` is the
distance from the frame centre and ``v`` is the shorter side of the frame
(``:856-860``).
The guide camera simulator does the same with its own constants: the glow is
not multiplied by 1.3, and ``v`` is the frame width (``guide_simulator.cpp:
822-865``). Its zero point uses natural logarithms, which makes
``flux(m) = 10 ** ((m - Z) * K / -2.5)`` with
``K = (SAT - LIM) / (-2.5 ln(MAXVAL) + 2.5 ln(0.5))`` and
``Z = SAT - K * (-2.5 ln(MAXVAL))`` (``:617-618``).

Noise. A pixel gets ``BIAS + random() % NOISE``
(``ccd_simulator.cpp:893-895``):
a whole number from 0 to NOISE-1 with equal chance. Its mean is
``(NOISE - 1) / 2`` and its root mean square spread (standard deviation) is
``sqrt((NOISE**2 - 1) / 12)``. There is no photon (shot) noise.

Seeing. The main camera simulator draws a star as a Gaussian of full width at
half maximum equal to ``seeing`` arcseconds (``ccd_simulator.cpp:968``).
The guide camera simulator draws ``exp(-1.4 d^2 / seeing^2)``
(``guide_simulator.cpp:964``), a Gaussian whose full width at half maximum is
``seeing * sqrt(2 ln 2 / 1.4) * 2``, about 1.41 times ``SIM_SEEING``.

Periodic error and drift. Both drivers add the amount in arcseconds to the
right ascension of the drawn field *in degrees of right ascension*, without a
``cos(dec)`` factor (``ccd_simulator.cpp:685`` and
``guide_simulator.cpp:592``), so the star moves ``amount * cos(dec)`` on the
sky. The converters below divide by ``cos(dec)`` so the on-sky motion equals
the measured one.
"""

import math

import numpy as np

__all__ = [
    "GUIDE_DEFAULT_GAIN",
    "GUIDE_GAIN_FACTOR_NOTE",
    "MAIN_GLOW_FACTOR",
    "SIDEREAL_GUIDE_RATE_ARCSEC_PER_S",
    "ccd_flux",
    "exposure_gain_factor",
    "glow_for_guide_camera",
    "glow_for_main_camera",
    "guide_flux",
    "guide_fwhm_to_seeing",
    "noise_limit_for_rms",
    "noise_mean",
    "noise_rms",
    "on_sky_to_ra_coordinate",
    "vignetting_median",
]

#: The main camera simulator multiplies the sky glow by this before it uses
#: it (``ccd_simulator.cpp:827``).
MAIN_GLOW_FACTOR = 1.3
#: The guide camera simulator's gain when nothing sets it: the default of
#: INDI's ``CCD_GAIN`` property (``libs/indibase/indiccd.cpp:216``).
GUIDE_DEFAULT_GAIN = 50.0
GUIDE_GAIN_FACTOR_NOTE = (
    "exposure is multiplied by 1 + sqrt(gain) (ccd_simulator.cpp:551, guide_simulator.cpp:455)"
)
#: The telescope simulator moves its axis at ``rate * 360/86400`` degrees per
#: second, so one unit of ``GUIDE_RATE`` is 15 arcseconds per second
#: (``drivers/telescope/scopesim_helper.cpp:127``), not the sidereal
#: 15.0411 arcseconds per second of the sky.
SIDEREAL_GUIDE_RATE_ARCSEC_PER_S = 15.0
#: The width of a Gaussian in units of its sigma: full width at half maximum.
_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
#: ``exp(-1.4 d^2 / v^2)`` is the vignetting of both drivers: the 2 * 0.7.
_VIGNETTING_EXPONENT = 1.4


def exposure_gain_factor(gain: float) -> float:
    """Give the factor the simulators multiply the exposure time by.

    Parameters
    ----------
    gain : `float`
        The camera's ``CCD_GAIN`` value.

    Returns
    -------
    factor : `float`
        ``1 + sqrt(gain)``.
    """
    return 1.0 + math.sqrt(max(gain, 0.0))


def ccd_flux(
    magnitude: float, maximum_adu: float, limiting_magnitude: float, saturation_magnitude: float
) -> float:
    """Give the main camera simulator's flux in ADU per second.

    Parameters
    ----------
    magnitude : `float`
        The magnitude of the source.
    maximum_adu : `float`
        ``SIM_MAXVAL``.
    limiting_magnitude, saturation_magnitude : `float`
        ``SIM_LIMITINGMAG`` and ``SIM_SATURATION``.

    Returns
    -------
    flux : `float`
        ``maximum_adu ** ((limiting - magnitude) / (limiting - saturation))``.
    """
    return maximum_adu ** ((limiting_magnitude - magnitude) / (limiting_magnitude - saturation_magnitude))


def _guide_constants(
    maximum_adu: float, limiting_magnitude: float, saturation_magnitude: float
) -> tuple[float, float]:
    """Give the guide camera simulator's zero point constants.

    Returns
    -------
    k, z : `float`
        ``K`` and ``Z`` as ``guide_simulator.cpp`` computes them.
    """
    k = (saturation_magnitude - limiting_magnitude) / (-2.5 * math.log(maximum_adu) + 2.5 * math.log(0.5))
    z = saturation_magnitude - k * (-2.5 * math.log(maximum_adu))
    return k, z


def guide_flux(
    magnitude: float, maximum_adu: float, limiting_magnitude: float, saturation_magnitude: float
) -> float:
    """Give the guide camera simulator's flux in ADU per second.

    Parameters
    ----------
    magnitude : `float`
        The magnitude of the source.
    maximum_adu : `float`
        ``SIM_MAXVAL``.
    limiting_magnitude, saturation_magnitude : `float`
        ``SIM_LIMITINGMAG`` and ``SIM_SATURATION``.

    Returns
    -------
    flux : `float`
        ``10 ** ((magnitude - Z) * K / -2.5)``.
    """
    k, z = _guide_constants(maximum_adu, limiting_magnitude, saturation_magnitude)
    return 10.0 ** ((magnitude - z) * k / -2.5)


def vignetting_median(width_px: int, height_px: int, scale_by_shorter_side: bool) -> float:
    """Give the median of the simulators' vignetting factor over a frame.

    Parameters
    ----------
    width_px, height_px : `int`
        The frame size in pixels (pixels are square in the fit's use).
    scale_by_shorter_side : `bool`
        `True` for the main camera simulator, whose vignetting radius is the
        shorter side of the frame; `False` for the guide camera simulator,
        whose radius is the width.

    Returns
    -------
    factor : `float`
        The median of ``exp(-1.4 d^2 / v^2)`` over the frame, with ``d`` the
        distance from the frame centre in pixels and ``v`` the radius in
        pixels. The sky level a median-based measurement reports is the
        centre value times this.
    """
    radius = min(width_px, height_px) if scale_by_shorter_side else width_px
    # A coarse grid is enough for a median of a smooth function.
    columns = np.linspace(-width_px / 2.0, width_px / 2.0, 200)
    rows = np.linspace(-height_px / 2.0, height_px / 2.0, 200)
    distance_squared = columns[None, :] ** 2 + rows[:, None] ** 2
    return float(np.median(np.exp(-_VIGNETTING_EXPONENT * distance_squared / radius**2)))


def _inverse_flux_ccd(
    target_adu_per_s: float, maximum_adu: float, limiting_magnitude: float, saturation_magnitude: float
) -> float:
    """Solve the main camera simulator's flux for the magnitude.

    Returns
    -------
    magnitude : `float`
        The magnitude whose flux is `target_adu_per_s`.
    """
    return limiting_magnitude - (limiting_magnitude - saturation_magnitude) * math.log(
        target_adu_per_s
    ) / math.log(maximum_adu)


def glow_for_main_camera(
    sky_rate_adu_per_s: float,
    gain: float,
    width_px: int,
    height_px: int,
    maximum_adu: float,
    limiting_magnitude: float,
    saturation_magnitude: float,
) -> float:
    """Give the main camera simulator's ``SIM_SKYGLOW`` for a sky rate.

    Parameters
    ----------
    sky_rate_adu_per_s : `float`
        The wanted sky rate in ADU per second per pixel, as a median over the
        frame (the quantity the sky model predicts).
    gain : `float`
        The ``CCD_GAIN`` value the simulator runs at.
    width_px, height_px : `int`
        The frame size.
    maximum_adu, limiting_magnitude, saturation_magnitude : `float`
        ``SIM_MAXVAL``, ``SIM_LIMITINGMAG`` and ``SIM_SATURATION`` as the
        simulator has them.

    Returns
    -------
    glow : `float`
        ``SIM_SKYGLOW`` in magnitudes, so that
        ``flux(1.3 * glow) * (1 + sqrt(gain)) * median vignetting`` equals
        the wanted rate.

    Raises
    ------
    ValueError
        If the wanted rate is not positive.
    """
    if sky_rate_adu_per_s <= 0:
        raise ValueError("The sky rate must be above zero.")
    centre_flux = sky_rate_adu_per_s / (
        exposure_gain_factor(gain) * vignetting_median(width_px, height_px, True)
    )
    magnitude = _inverse_flux_ccd(centre_flux, maximum_adu, limiting_magnitude, saturation_magnitude)
    return magnitude / MAIN_GLOW_FACTOR


def glow_for_guide_camera(
    sky_rate_adu_per_s: float,
    gain: float,
    width_px: int,
    height_px: int,
    maximum_adu: float,
    limiting_magnitude: float,
    saturation_magnitude: float,
) -> float:
    """Give the guide camera simulator's ``SIM_SKYGLOW`` for a sky rate.

    Parameters
    ----------
    sky_rate_adu_per_s, gain : `float`
        As for `glow_for_main_camera`.
    width_px, height_px : `int`
        As for `glow_for_main_camera`.
    maximum_adu, limiting_magnitude, saturation_magnitude : `float`
        As for `glow_for_main_camera`.

    Returns
    -------
    glow : `float`
        ``SIM_SKYGLOW`` in magnitudes, so that the guide simulator's sky
        (no 1.3 factor, vignetting radius the frame width) has the wanted
        median rate.

    Raises
    ------
    ValueError
        If the wanted rate is not positive.
    """
    if sky_rate_adu_per_s <= 0:
        raise ValueError("The sky rate must be above zero.")
    centre_flux = sky_rate_adu_per_s / (
        exposure_gain_factor(gain) * vignetting_median(width_px, height_px, False)
    )
    k, z = _guide_constants(maximum_adu, limiting_magnitude, saturation_magnitude)
    return z + math.log10(centre_flux) * (-2.5) / k


def noise_rms(noise_limit: int) -> float:
    """Give the root mean square of the simulators' noise.

    Parameters
    ----------
    noise_limit : `int`
        ``SIM_NOISE``: the noise is a whole number from 0 to this minus 1.

    Returns
    -------
    rms : `float`
        ``sqrt((N**2 - 1) / 12)``, in ADU.
    """
    return math.sqrt(max(noise_limit**2 - 1, 0) / 12.0)


def noise_mean(noise_limit: int) -> float:
    """Give the mean of the simulators' noise, which sits on top of the bias.

    Parameters
    ----------
    noise_limit : `int`
        ``SIM_NOISE``.

    Returns
    -------
    mean : `float`
        ``(N - 1) / 2``, in ADU (zero for no noise).
    """
    return max(noise_limit - 1, 0) / 2.0


def noise_limit_for_rms(rms_adu: float) -> int:
    """Give the ``SIM_NOISE`` whose spread is closest to a read noise.

    Parameters
    ----------
    rms_adu : `float`
        The wanted read noise (RMS), in ADU.

    Returns
    -------
    noise_limit : `int`
        ``round(sqrt(12 * rms**2 + 1))``, at least 1 (the driver draws no
        noise at all for 0, and the value must be a whole number because it
        is the divisor of an integer remainder).
    """
    return max(1, round(math.sqrt(12.0 * rms_adu**2 + 1.0)))


def guide_fwhm_to_seeing(fwhm_arcsec: float) -> float:
    """Give the guide camera simulator's ``SIM_SEEING`` for a star width.

    Parameters
    ----------
    fwhm_arcsec : `float`
        The wanted full width at half maximum, in arcseconds.

    Returns
    -------
    seeing : `float`
        ``SIM_SEEING``: the star profile ``exp(-1.4 d^2 / seeing^2)`` has
        sigma ``seeing / sqrt(2.8)``.
    """
    return fwhm_arcsec / (_FWHM_PER_SIGMA / math.sqrt(2.0 * _VIGNETTING_EXPONENT))


def on_sky_to_ra_coordinate(amount_arcsec: float, declination_degrees: float) -> float:
    """Convert an on-sky amount to the drivers' right ascension amount.

    Parameters
    ----------
    amount_arcsec : `float`
        An error size on the sky, in arcseconds.
    declination_degrees : `float`
        Where the simulated telescope points.

    Returns
    -------
    amount : `float`
        The number to give the driver, which adds it to the right ascension
        without a ``cos(dec)`` factor: `amount_arcsec` / cos(dec).
    """
    return amount_arcsec / max(math.cos(math.radians(declination_degrees)), 1e-6)
