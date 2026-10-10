"""Purpose: Unit tests for the refraction of starlight by the air.

Description: Checks the refractive index of air against the Ciddor (1996)
reference value and the older Edlen (1966) formula, the differential
refraction against the value Filippenko (1982) published (as quoted by
Massey and Hanson), the plane-parallel refusal below 20 degrees altitude, and
the way the air's conditions come from the config or from the standard
atmosphere at the site's elevation.
"""

import math

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_refraction import (
    MINIMUM_ALTITUDE_DEGREES,
    SOURCE_CONFIG,
    SOURCE_STANDARD_ATMOSPHERE,
    AtmosphericConditions,
    air_refractive_index,
    differential_refraction_arcsec,
    load_atmospheric_conditions,
    refraction_arcsec,
    standard_atmosphere,
)

# Filippenko (1982) assumed 600 mm Hg, 7 degrees Celsius and a water vapor
# pressure of 8 mm Hg, which is saturated air at that temperature. Massey and
# Hanson (arXiv:1010.5270) quote 0.71 arcsec between 5000 A and 4000 A at
# airmass 1.5 from that paper.
MM_HG_TO_HPA = 1.33322387415
FILIPPENKO_CONDITIONS = AtmosphericConditions(
    pressure_hpa=600.0 * MM_HG_TO_HPA,
    temperature_c=7.0,
    relative_humidity_percent=100.0,
    source="Filippenko (1982)",
)
FILIPPENKO_AIRMASS = 1.5
FILIPPENKO_BLUE_MINUS_5000_ARCSEC = 0.71
FILIPPENKO_TOLERANCE_ARCSEC = 0.05

SEA_LEVEL = standard_atmosphere(0.0)


class _FakeConfig:
    """A config that returns values from a dictionary."""

    def __init__(self, values: dict[str, object]) -> None:
        """Keep the values to hand out.

        Parameters
        ----------
        values : `dict`
            The values, by key within the ``Observatory.Location`` section.
        """
        self.values = values

    def get_value(self, section: str, key: str, fallback: object = None) -> object:
        """Give the value for a key, or the fallback.

        Parameters
        ----------
        section : `str`
            The config section (ignored).
        key : `str`
            The key to read.
        fallback : `object`, optional
            What to return when the key is missing.

        Returns
        -------
        value : `object`
            The stored value, or `fallback`.
        """
        return self.values.get(key, fallback)


def _zenith_distance_for_airmass(airmass: float) -> float:
    """Give the zenith distance whose secant is an airmass.

    Parameters
    ----------
    airmass : `float`
        The plane-parallel airmass, `1 / cos(z)`.

    Returns
    -------
    zenith_distance : `float`
        The zenith distance, in degrees.
    """
    return math.degrees(math.acos(1.0 / airmass))


def test_the_index_of_dry_air_matches_ciddors_value_at_633_nm() -> None:
    """Dry air at 20 C and 101325 Pa gives n - 1 = 2.71799e-4 at 633 nm."""
    conditions = AtmosphericConditions(1013.25, 20.0, 0.0, "test")

    assert air_refractive_index(6328.0, conditions) - 1.0 == pytest.approx(2.71799e-4, abs=1.0e-8)


def test_the_index_agrees_with_the_edlen_1966_formula_for_standard_air() -> None:
    """Ciddor and Edlen (1966) agree to 2e-8 in n across the visible."""
    wavelength = np.array([4000.0, 5000.0, 6000.0, 8000.0])
    sigma_squared = (1.0e4 / wavelength) ** 2
    # Edlen (1966): dry air at 15 C and 101325 Pa, 300 ppm of carbon dioxide.
    edlen_minus_one = (
        8342.13 + 2406030.0 / (130.0 - sigma_squared) + 15997.0 / (38.9 - sigma_squared)
    ) * 1e-8

    ours_minus_one = air_refractive_index(wavelength, AtmosphericConditions(1013.25, 15.0, 0.0, "test")) - 1.0

    assert ours_minus_one == pytest.approx(edlen_minus_one, abs=2.0e-8)


def test_blue_light_has_a_higher_index_than_red_light() -> None:
    """The index falls with wavelength, so blue light bends more."""
    index = air_refractive_index(np.array([4000.0, 5000.0, 7000.0, 9000.0]), SEA_LEVEL)

    assert np.all(np.diff(index) < 0.0)


def test_thinner_and_warmer_air_bends_light_less() -> None:
    """Lower pressure and higher temperature both lower n - 1."""
    base = air_refractive_index(5000.0, SEA_LEVEL) - 1.0
    thin = air_refractive_index(5000.0, AtmosphericConditions(800.0, 15.0, 0.0, "t")) - 1.0
    warm = air_refractive_index(5000.0, AtmosphericConditions(1013.25, 35.0, 0.0, "t")) - 1.0

    assert thin < base
    assert warm < base


def test_humid_air_changes_the_blue_to_red_refraction_by_under_one_percent() -> None:
    """Water vapor is a small effect on the difference between colors."""
    zenith_distance = _zenith_distance_for_airmass(1.5)
    dry = AtmosphericConditions(1013.25, 15.0, 0.0, "t")
    humid = AtmosphericConditions(1013.25, 15.0, 80.0, "t")

    dry_difference = differential_refraction_arcsec(4200.0, 8000.0, zenith_distance, dry)
    humid_difference = differential_refraction_arcsec(4200.0, 8000.0, zenith_distance, humid)

    assert humid_difference == pytest.approx(dry_difference, rel=0.01)


def test_differential_refraction_matches_filippenko_1982_at_airmass_1_5() -> None:
    """The 4000 A image sits 0.71 arcsec above the 5000 A image."""
    difference = differential_refraction_arcsec(
        4000.0, 5000.0, _zenith_distance_for_airmass(FILIPPENKO_AIRMASS), FILIPPENKO_CONDITIONS
    )

    assert difference == pytest.approx(FILIPPENKO_BLUE_MINUS_5000_ARCSEC, abs=FILIPPENKO_TOLERANCE_ARCSEC)


def test_refraction_between_4200_and_8000_angstrom_at_sea_level_matches_the_design_figures() -> None:
    """The spread is about 1.6 arcsec at airmass 1.5 and 2.5 arcsec at 2."""
    for airmass, expected in ((1.5, 1.6), (2.0, 2.5)):
        zenith_distance = _zenith_distance_for_airmass(airmass)
        spread = differential_refraction_arcsec(4200.0, 8000.0, zenith_distance, SEA_LEVEL)

        assert spread == pytest.approx(expected, abs=0.1)


def test_refraction_grows_with_the_tangent_of_the_zenith_distance() -> None:
    """Doubling tan(z) doubles the refraction."""
    low = refraction_arcsec(5000.0, 30.0, SEA_LEVEL)
    high = refraction_arcsec(5000.0, math.degrees(math.atan(2.0 * math.tan(math.radians(30.0)))), SEA_LEVEL)

    assert high == pytest.approx(2.0 * low, rel=1e-9)


def test_a_target_below_the_minimum_altitude_is_refused() -> None:
    """The plane-parallel model raises below 20 degrees altitude."""
    limit = 90.0 - MINIMUM_ALTITUDE_DEGREES

    assert refraction_arcsec(5000.0, limit, SEA_LEVEL) > 0.0
    with pytest.raises(ValueError, match="plane-parallel"):
        refraction_arcsec(5000.0, limit + 0.1, SEA_LEVEL)
    with pytest.raises(ValueError, match="plane-parallel"):
        refraction_arcsec(5000.0, -1.0, SEA_LEVEL)


def test_a_wavelength_outside_the_equations_is_refused() -> None:
    """The Ciddor equations stop at 0.3 and 1.69 micrometers."""
    with pytest.raises(ValueError, match="range"):
        air_refractive_index(2000.0, SEA_LEVEL)
    with pytest.raises(ValueError, match="range"):
        air_refractive_index(20000.0, SEA_LEVEL)


def test_the_standard_atmosphere_scales_with_the_elevation() -> None:
    """Sea level is 1013.25 hPa and 15 C; 1500 m is thinner and colder."""
    high = standard_atmosphere(1500.0)

    assert SEA_LEVEL.pressure_hpa == pytest.approx(1013.25, abs=0.01)
    assert SEA_LEVEL.temperature_c == pytest.approx(15.0)
    assert high.pressure_hpa == pytest.approx(845.6, abs=0.5)
    assert high.temperature_c == pytest.approx(5.25)
    assert high.source == SOURCE_STANDARD_ATMOSPHERE


def test_the_conditions_come_from_the_config_when_it_gives_all_three() -> None:
    """The three config keys set the conditions and the source says so."""
    config = _FakeConfig({"pressure_hpa": "830", "temperature_c": "-3.5", "relative_humidity_percent": "40"})

    conditions = load_atmospheric_conditions(config, 1500.0)

    assert (conditions.pressure_hpa, conditions.temperature_c, conditions.relative_humidity_percent) == (
        830.0,
        -3.5,
        40.0,
    )
    assert conditions.source == SOURCE_CONFIG


def test_missing_config_values_fall_back_to_the_standard_atmosphere() -> None:
    """A config with none of the keys gives the standard atmosphere."""
    conditions = load_atmospheric_conditions(_FakeConfig({}), 1500.0)

    assert conditions == standard_atmosphere(1500.0)


def test_a_partly_filled_config_mixes_both_and_names_each_group() -> None:
    """A config with only the pressure keeps it and takes the rest."""
    conditions = load_atmospheric_conditions(_FakeConfig({"pressure_hpa": "830"}), 1500.0)

    assert conditions.pressure_hpa == pytest.approx(830.0)
    assert conditions.temperature_c == pytest.approx(5.25)
    assert "config for pressure_hpa" in conditions.source
    assert "standard atmosphere for temperature_c, relative_humidity_percent" in conditions.source


@pytest.mark.parametrize(
    "values",
    [
        {"pressure_hpa": "0"},
        {"pressure_hpa": "abc"},
        {"relative_humidity_percent": "140"},
        {"temperature_c": "nan"},
    ],
)
def test_unusable_config_values_are_ignored(values: dict[str, object]) -> None:
    """A zero pressure, text, a humidity over 100 or NaN falls back."""
    assert load_atmospheric_conditions(_FakeConfig(values), 0.0) == standard_atmosphere(0.0)
