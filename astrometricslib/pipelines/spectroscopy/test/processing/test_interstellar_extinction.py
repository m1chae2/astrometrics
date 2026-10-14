"""Purpose: Tests for the Cardelli, Clayton and Mathis reddening law.

Description: The law turns a colour excess E(B-V) into a dimming at each
wavelength. These tests check it against values that follow from the
published definition: the dimming is A(V) at 5500 A, the blue-to-visual ratio
near B is about 1.32, the three pieces of the law join without a jump, and
reddening followed by dereddening returns the original spectrum.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction import (
    STANDARD_R_V,
    ccm89_extinction_ratio,
    deredden_spectrum,
    extinction_magnitudes,
    redden_spectrum,
)


def test_the_ratio_is_one_in_the_v_band() -> None:
    """Verify the dimming equals A(V) at 5500 A, where x = 1.82 and y = 0."""
    ratio = ccm89_extinction_ratio(np.array([5500.0]))
    assert ratio[0] == pytest.approx(1.0, abs=0.005)


def test_the_blue_is_dimmed_more_than_the_red() -> None:
    """Verify the dimming falls steadily from 3500 A to 10000 A."""
    wavelengths = np.linspace(3500.0, 10000.0, 200)
    ratio = ccm89_extinction_ratio(wavelengths)
    assert np.all(np.diff(ratio) < 0)


def test_the_ratio_in_the_b_band_matches_the_known_value() -> None:
    """Verify A(4400 A) / A(V) is about 1.32, the value for R_V = 3.1."""
    ratio = ccm89_extinction_ratio(np.array([4400.0]), STANDARD_R_V)
    assert ratio[0] == pytest.approx(1.32, abs=0.02)


def test_a_larger_r_v_dims_the_blue_relative_to_v_less() -> None:
    """Verify grayer dust (larger R_V) has a smaller blue-to-visual ratio."""
    blue = np.array([4400.0])
    assert ccm89_extinction_ratio(blue, 5.0)[0] < ccm89_extinction_ratio(blue, 3.1)[0]


@pytest.mark.parametrize("join_wavelength", [3030.3, 9090.9])
def test_the_pieces_of_the_law_join_without_a_jump(join_wavelength: float) -> None:
    """Verify the ratio changes by under 1% across the piece boundaries.

    The boundaries are x = 3.3 (3030.3 A) and x = 1.1 (9090.9 A).
    """
    below = ccm89_extinction_ratio(np.array([join_wavelength - 0.5]))[0]
    above = ccm89_extinction_ratio(np.array([join_wavelength + 0.5]))[0]
    assert abs(above - below) / below < 0.01


def test_dimming_in_magnitudes_scales_with_the_colour_excess() -> None:
    """Verify A(V) = R_V * E(B-V) and the dimming doubles with E(B-V)."""
    v_band = np.array([5500.0])
    assert extinction_magnitudes(v_band, 0.3)[0] == pytest.approx(0.93, abs=0.01)
    assert extinction_magnitudes(v_band, 0.6)[0] == pytest.approx(2 * extinction_magnitudes(v_band, 0.3)[0])


def test_reddening_then_dereddening_returns_the_original_spectrum() -> None:
    """Verify the two functions are inverses."""
    wavelengths = np.linspace(4200.0, 8000.0, 300)
    flux = 1.0 + 0.3 * np.sin(wavelengths / 400.0)
    restored = deredden_spectrum(wavelengths, redden_spectrum(wavelengths, flux, 0.4), 0.4)
    assert np.allclose(restored, flux)


def test_reddening_makes_the_blue_fainter_than_the_red() -> None:
    """Verify a flat spectrum, once reddened, falls toward the blue."""
    wavelengths = np.array([4400.0, 7000.0])
    reddened = redden_spectrum(wavelengths, np.ones(2), 0.3)
    assert reddened[0] < reddened[1] < 1.0


def test_zero_colour_excess_changes_nothing() -> None:
    """Verify E(B-V) = 0 returns the spectrum unchanged."""
    wavelengths = np.linspace(4200.0, 8000.0, 50)
    flux = np.linspace(1.0, 2.0, 50)
    assert np.array_equal(deredden_spectrum(wavelengths, flux, 0.0), flux)


@pytest.mark.parametrize("wavelength", [1000.0, 40000.0])
def test_a_wavelength_outside_the_law_is_refused(wavelength: float) -> None:
    """Verify the law will not go beyond 1250 A to 3.33 micrometres."""
    with pytest.raises(ValueError, match="covers"):
        ccm89_extinction_ratio(np.array([wavelength]))


def test_a_non_positive_r_v_is_refused() -> None:
    """Verify R_V must be positive."""
    with pytest.raises(ValueError, match="R_V"):
        ccm89_extinction_ratio(np.array([5500.0]), 0.0)
