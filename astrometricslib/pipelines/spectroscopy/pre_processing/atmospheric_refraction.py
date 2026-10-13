"""Computes how much the air bends starlight toward the zenith.

Air bends a ray of starlight so that the star looks higher in the sky than it
is. The zenith is the point straight overhead. Blue light bends more than red
light, so a star's blue image sits slightly closer to the zenith than its red
image. This is atmospheric differential refraction (DAR, "differential"
because only the difference between wavelengths matters here). The size of
the effect grows with the tangent of the zenith distance (the angle between
the star and the zenith).

This module gives the refraction at one wavelength. It has three parts:

1. The refractive index of air, `n`, from the Ciddor (1996) equations. The
   index depends on wavelength, air pressure, air temperature and humidity.
   The refraction is proportional to `n - 1`, which is about 0.000279 at
   5000 A at sea level.
2. The refraction `R = (n - 1) * tan(z)`, where `z` is the zenith distance
   of the star. This is the plane-parallel model. It treats the atmosphere
   as flat layers. Near the horizon the curvature of the Earth matters, so
   the model is refused below an altitude of 20 degrees
   (`MINIMUM_ALTITUDE_DEGREES`).
3. The conditions of the air (`AtmosphericConditions`). They come from the
   config when it gives them. Otherwise they come from the standard
   atmosphere scaled to the site's elevation, and the result records which.

Sources
-------
* Ciddor, P. E. 1996, Applied Optics 35, 1566, "Refractive index of air: new
  equations for the visible and near infrared". The dispersion formulas,
  the water vapor formula, the compressibility `Z` and the density ratios
  below are the ones in that paper. The paper states the equations for a
  wavelength in vacuum from 0.3 to 1.69 micrometers.
* Filippenko, A. V. 1982, PASP 94, 715, "The Importance of Atmospheric
  Differential Refraction in Spectrophotometry". It gives the refraction
  `R = (n - 1) tan(z)` and tabulates the refraction relative to 5000 A. The
  test of this module uses the value quoted from it by Massey and Hanson
  (arXiv:1010.5270): 0.71 arcsec between 5000 A and 4000 A at airmass 1.5,
  for a pressure of 600 mm Hg, 7 degrees Celsius and a water vapor pressure
  of 8 mm Hg.
* U.S. Standard Atmosphere 1976 (the troposphere formulas for the standard
  pressure and temperature at a given height).
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

# A single number or an array of them.
Numeric = float | np.ndarray

# The section of the config that holds the site, and the three optional keys
# this module reads from it next to ``latitude``, ``longitude`` and
# ``elevation``.
LOCATION_SECTION = "Observatory.Location"
PRESSURE_KEY = "pressure_hpa"
TEMPERATURE_KEY = "temperature_c"
HUMIDITY_KEY = "relative_humidity_percent"

# The lowest altitude, in degrees above the horizon, at which the
# plane-parallel model is used. Below it the pipeline records that DAR was
# not computed. This is a design choice (the common rule of thumb for a flat
# atmosphere), not a measurement.
MINIMUM_ALTITUDE_DEGREES = 20.0

# The wavelength range of the Ciddor equations, in Angstroms.
MINIMUM_WAVELENGTH_ANGSTROM = 3000.0
MAXIMUM_WAVELENGTH_ANGSTROM = 16900.0

# The carbon dioxide content Ciddor's dispersion formula is written for, in
# parts per million. A change of 100 ppm changes `n - 1` by 0.005 percent of
# itself (the 0.534e-6 per ppm term of the paper).
CARBON_DIOXIDE_PPM = 450.0

# Radians to arcseconds.
_ARCSEC_PER_RADIAN = 206264.80624709636

# Constants of the Ciddor (1996) equations.
_STANDARD_PRESSURE_PA = 101325.0
_STANDARD_TEMPERATURE_K = 288.15
_WATER_REFERENCE_PRESSURE_PA = 1333.0
_WATER_REFERENCE_TEMPERATURE_K = 293.15
_GAS_CONSTANT = 8.314510
_WATER_MOLAR_MASS = 0.018015
_COMPRESSIBILITY_A = (1.58123e-6, -2.9331e-8, 1.1043e-10)
_COMPRESSIBILITY_B = (5.707e-6, -2.051e-8)
_COMPRESSIBILITY_C = (1.9898e-4, -2.376e-6)
_COMPRESSIBILITY_D = 1.83e-11
_COMPRESSIBILITY_E = -0.765e-8
_SATURATION_A = 1.2378847e-5
_SATURATION_B = -1.9121316e-2
_SATURATION_C = 33.93711047
_SATURATION_D = -6.3431645e3
_ENHANCEMENT = (1.00062, 3.14e-8, 5.6e-7)

# The sources recorded with the conditions of the air.
SOURCE_CONFIG = "config"
SOURCE_STANDARD_ATMOSPHERE = "standard atmosphere scaled to the site elevation"


@dataclass(frozen=True)
class AtmosphericConditions:
    """The state of the air at the telescope.

    Attributes
    ----------
    pressure_hpa : `float`
        Air pressure at the telescope, in hectopascals (1 hPa is 1 mbar).
    temperature_c : `float`
        Air temperature, in degrees Celsius.
    relative_humidity_percent : `float`
        Relative humidity, from 0 to 100. This is the water vapor pressure
        as a percentage of the saturation pressure at this temperature.
    source : `str`
        Where the three numbers came from, for the record. It is
        `SOURCE_CONFIG`, `SOURCE_STANDARD_ATMOSPHERE`, or a sentence that
        names which numbers came from the config.
    """

    pressure_hpa: float
    temperature_c: float
    relative_humidity_percent: float
    source: str


def standard_atmosphere(elevation_m: float) -> AtmosphericConditions:
    """Give the standard atmosphere at a height above sea level.

    The pressure and temperature follow the troposphere of the U.S.
    Standard Atmosphere 1976: 1013.25 hPa and 15 degrees Celsius at sea
    level, with the temperature falling 6.5 degrees per kilometer. The
    standard atmosphere is dry, so the humidity is 0. Moist air changes the
    refraction by less than 1 percent of itself.

    Parameters
    ----------
    elevation_m : `float`
        The height above sea level, in meters.

    Returns
    -------
    conditions : `AtmosphericConditions`
        The pressure, temperature and humidity at that height.
    """
    pressure_pa = _STANDARD_PRESSURE_PA * (1.0 - 2.25577e-5 * elevation_m) ** 5.25588
    temperature_k = _STANDARD_TEMPERATURE_K - 0.0065 * elevation_m
    return AtmosphericConditions(
        pressure_hpa=float(pressure_pa / 100.0),
        temperature_c=float(temperature_k - 273.15),
        relative_humidity_percent=0.0,
        source=SOURCE_STANDARD_ATMOSPHERE,
    )


def _optional_config_number(app_config: Any, key: str) -> float | None:
    """Read one number from the site section of the config.

    Parameters
    ----------
    app_config : `Any`
        The loaded config, offering ``get_value(section, key, fallback)``.
    key : `str`
        The key to read in the ``Observatory.Location`` section.

    Returns
    -------
    value : `float` or `None`
        The number, or `None` when the key is missing or is not a finite
        number.
    """
    raw = app_config.get_value(LOCATION_SECTION, key, None)
    if raw is None:
        return None
    try:
        value = float(raw)
    except TypeError, ValueError:
        return None
    return value if math.isfinite(value) else None


def load_atmospheric_conditions(app_config: Any, elevation_m: float) -> AtmosphericConditions:
    """Read the air's conditions from the config, with the standard fallback.

    The config may give ``pressure_hpa``, ``temperature_c`` and
    ``relative_humidity_percent`` in the ``[Observatory.Location]`` section.
    Each one that is missing, not a number, or out of range (a pressure of
    zero or less, a humidity outside 0 to 100) comes from the standard
    atmosphere at `elevation_m` instead.

    Parameters
    ----------
    app_config : `Any`
        The loaded config, offering ``get_value(section, key, fallback)``.
    elevation_m : `float`
        The site's height above sea level, in meters. It sets the standard
        pressure and temperature.

    Returns
    -------
    conditions : `AtmosphericConditions`
        The conditions. Its ``source`` is `SOURCE_CONFIG` when the config
        gave all three numbers, `SOURCE_STANDARD_ATMOSPHERE` when it gave
        none, and a sentence naming both groups otherwise.
    """
    fallback = standard_atmosphere(elevation_m)
    pressure = _optional_config_number(app_config, PRESSURE_KEY)
    temperature = _optional_config_number(app_config, TEMPERATURE_KEY)
    humidity = _optional_config_number(app_config, HUMIDITY_KEY)
    if pressure is not None and pressure <= 0.0:
        pressure = None
    if humidity is not None and not 0.0 <= humidity <= 100.0:
        humidity = None
    from_config = [
        key
        for key, value in ((PRESSURE_KEY, pressure), (TEMPERATURE_KEY, temperature), (HUMIDITY_KEY, humidity))
        if value is not None
    ]
    from_standard = [key for key in (PRESSURE_KEY, TEMPERATURE_KEY, HUMIDITY_KEY) if key not in from_config]
    if not from_config:
        return fallback
    source = (
        SOURCE_CONFIG
        if not from_standard
        else f"config for {', '.join(from_config)}; standard atmosphere for {', '.join(from_standard)}"
    )
    return AtmosphericConditions(
        pressure_hpa=pressure if pressure is not None else fallback.pressure_hpa,
        temperature_c=temperature if temperature is not None else fallback.temperature_c,
        relative_humidity_percent=humidity if humidity is not None else fallback.relative_humidity_percent,
        source=source,
    )


def _compressibility(temperature_k: float, pressure_pa: float, water_mole_fraction: float) -> float:
    """Give the compressibility factor `Z` of moist air.

    `Z` measures how far the air departs from an ideal gas. It is within a
    fraction of a percent of 1. This is equation (12) of Ciddor (1996).

    Parameters
    ----------
    temperature_k : `float`
        The air temperature, in kelvin.
    pressure_pa : `float`
        The air pressure, in pascals.
    water_mole_fraction : `float`
        The fraction of the air's molecules that are water.

    Returns
    -------
    z : `float`
        The compressibility factor.
    """
    celsius = temperature_k - 273.15
    ratio = pressure_pa / temperature_k
    a0, a1, a2 = _COMPRESSIBILITY_A
    b0, b1 = _COMPRESSIBILITY_B
    c0, c1 = _COMPRESSIBILITY_C
    x = water_mole_fraction
    return (
        1.0
        - ratio * (a0 + a1 * celsius + a2 * celsius**2 + (b0 + b1 * celsius) * x + (c0 + c1 * celsius) * x**2)
        + ratio**2 * (_COMPRESSIBILITY_D + _COMPRESSIBILITY_E * x**2)
    )


def _water_mole_fraction(temperature_c: float, pressure_pa: float, relative_humidity_percent: float) -> float:
    """Give the fraction of the air's molecules that are water.

    It uses the saturation vapor pressure of Giacomo (1982) and Davis
    (1992) and the enhancement factor of Ciddor (1996).

    Parameters
    ----------
    temperature_c : `float`
        The air temperature, in degrees Celsius.
    pressure_pa : `float`
        The air pressure, in pascals.
    relative_humidity_percent : `float`
        The relative humidity, from 0 to 100.

    Returns
    -------
    fraction : `float`
        The water mole fraction, 0 for dry air.
    """
    temperature_k = temperature_c + 273.15
    saturation_pa = math.exp(
        _SATURATION_A * temperature_k**2
        + _SATURATION_B * temperature_k
        + _SATURATION_C
        + _SATURATION_D / temperature_k
    )
    alpha, beta, gamma = _ENHANCEMENT
    enhancement = alpha + beta * pressure_pa + gamma * temperature_c**2
    return enhancement * (relative_humidity_percent / 100.0) * saturation_pa / pressure_pa


def air_refractive_index(wavelength_angstrom: Numeric, conditions: AtmosphericConditions) -> Numeric:
    """Give the refractive index of air at a wavelength.

    This is the Ciddor (1996) calculation. It computes the index of
    standard dry air and of water vapor at their reference conditions from
    dispersion formulas, then scales each by its density in the actual air.

    Parameters
    ----------
    wavelength_angstrom : `float` or `numpy.ndarray`
        The wavelength, in Angstroms. It must lie within
        `MINIMUM_WAVELENGTH_ANGSTROM` and `MAXIMUM_WAVELENGTH_ANGSTROM`.
    conditions : `AtmosphericConditions`
        The pressure, temperature and humidity of the air.

    Returns
    -------
    index : `float` or `numpy.ndarray`
        The refractive index `n`, about 1.00028 at sea level in visible
        light. It has the shape of `wavelength_angstrom`.

    Raises
    ------
    ValueError
        A wavelength is outside the range of the equations, or the
        conditions are not physical.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    if np.any(wavelength < MINIMUM_WAVELENGTH_ANGSTROM) or np.any(wavelength > MAXIMUM_WAVELENGTH_ANGSTROM):
        raise ValueError(
            f"Wavelength outside {MINIMUM_WAVELENGTH_ANGSTROM:.0f} to "
            f"{MAXIMUM_WAVELENGTH_ANGSTROM:.0f} A, the range of the air index equations."
        )
    pressure_pa = conditions.pressure_hpa * 100.0
    temperature_k = conditions.temperature_c + 273.15
    if pressure_pa <= 0.0 or temperature_k <= 0.0:
        raise ValueError("The air pressure and temperature must be above zero.")

    # sigma is the wavenumber in inverse micrometers (vacuum wavelength).
    sigma_squared = (1.0e4 / wavelength) ** 2

    # Index minus one of standard dry air (15 C, 101325 Pa, 450 ppm CO2),
    # eq. (1) and (2) of the paper, and of water vapor at 20 C and 1333 Pa,
    # eq. (3).
    dry_standard = 1.0e-8 * (5792105.0 / (238.0185 - sigma_squared) + 167917.0 / (57.362 - sigma_squared))
    dry_standard *= 1.0 + 0.534e-6 * (CARBON_DIOXIDE_PPM - 450.0)
    vapor_standard = 1.022e-8 * (
        295.235 + 2.6422 * sigma_squared - 0.03238 * sigma_squared**2 + 0.004028 * sigma_squared**3
    )

    # Density of dry air and of water vapor at the reference conditions,
    # and in the actual air (eq. 4 and 5 with the compressibility above).
    dry_molar_mass = 0.0289635 + 1.2011e-8 * (CARBON_DIOXIDE_PPM - 400.0)
    standard_dry_density = (
        _STANDARD_PRESSURE_PA
        * dry_molar_mass
        / (
            _compressibility(_STANDARD_TEMPERATURE_K, _STANDARD_PRESSURE_PA, 0.0)
            * _GAS_CONSTANT
            * _STANDARD_TEMPERATURE_K
        )
    )
    standard_vapor_density = (
        _WATER_REFERENCE_PRESSURE_PA
        * _WATER_MOLAR_MASS
        / (
            _compressibility(_WATER_REFERENCE_TEMPERATURE_K, _WATER_REFERENCE_PRESSURE_PA, 1.0)
            * _GAS_CONSTANT
            * _WATER_REFERENCE_TEMPERATURE_K
        )
    )
    water_fraction = _water_mole_fraction(
        conditions.temperature_c, pressure_pa, conditions.relative_humidity_percent
    )
    compressibility = _compressibility(temperature_k, pressure_pa, water_fraction)
    common = pressure_pa / (compressibility * _GAS_CONSTANT * temperature_k)
    dry_density = common * dry_molar_mass * (1.0 - water_fraction)
    vapor_density = common * _WATER_MOLAR_MASS * water_fraction

    index = (
        1.0
        + (dry_density / standard_dry_density) * dry_standard
        + (vapor_density / standard_vapor_density) * vapor_standard
    )
    return float(index) if np.ndim(index) == 0 else index


def refraction_arcsec(
    wavelength_angstrom: Numeric, zenith_distance_degrees: float, conditions: AtmosphericConditions
) -> Numeric:
    """Give the refraction of starlight toward the zenith at a wavelength.

    It is `(n - 1) * tan(z)` in the plane-parallel model, where `n` is the
    index of air at the telescope and `z` is the apparent zenith distance
    (the zenith distance of the star as the telescope sees it).

    Parameters
    ----------
    wavelength_angstrom : `float` or `numpy.ndarray`
        The wavelength, in Angstroms.
    zenith_distance_degrees : `float`
        The zenith distance, in degrees. It must be below
        ``90 - MINIMUM_ALTITUDE_DEGREES``.
    conditions : `AtmosphericConditions`
        The pressure, temperature and humidity of the air.

    Returns
    -------
    refraction : `float` or `numpy.ndarray`
        How far the star's image at this wavelength sits closer to the
        zenith than the star's true position, in arcseconds.

    Raises
    ------
    ValueError
        The star is lower than `MINIMUM_ALTITUDE_DEGREES` (or below the
        zenith, with a negative distance), so the model is refused.
    """
    if not 0.0 <= zenith_distance_degrees <= 90.0 - MINIMUM_ALTITUDE_DEGREES:
        raise ValueError(
            f"Zenith distance {zenith_distance_degrees:.2f} degrees is outside 0 to "
            f"{90.0 - MINIMUM_ALTITUDE_DEGREES:.0f}; the plane-parallel model is refused "
            f"below an altitude of {MINIMUM_ALTITUDE_DEGREES:.0f} degrees."
        )
    index = air_refractive_index(wavelength_angstrom, conditions)
    return (index - 1.0) * math.tan(math.radians(zenith_distance_degrees)) * _ARCSEC_PER_RADIAN


def differential_refraction_arcsec(
    wavelength_angstrom: Numeric,
    reference_wavelength_angstrom: float,
    zenith_distance_degrees: float,
    conditions: AtmosphericConditions,
) -> Numeric:
    """Give the refraction at a wavelength relative to a reference wavelength.

    Parameters
    ----------
    wavelength_angstrom : `float` or `numpy.ndarray`
        The wavelength, in Angstroms.
    reference_wavelength_angstrom : `float`
        The wavelength to measure from, in Angstroms.
    zenith_distance_degrees : `float`
        The apparent zenith distance, in degrees.
    conditions : `AtmosphericConditions`
        The pressure, temperature and humidity of the air.

    Returns
    -------
    difference : `float` or `numpy.ndarray`
        `R(wavelength) - R(reference)`, in arcseconds. It is positive for a
        wavelength bluer than the reference: that light sits closer to the
        zenith.
    """
    return refraction_arcsec(wavelength_angstrom, zenith_distance_degrees, conditions) - refraction_arcsec(
        reference_wavelength_angstrom, zenith_distance_degrees, conditions
    )
