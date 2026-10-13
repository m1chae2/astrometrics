"""Purpose: Tests for the reduced chi-square stored next to the RMS.

Description: The classifier ranks reference spectra by relative RMS. When the
spectrum carries per-sample errors, each reference also gets a reduced
chi-square, the sum of the squared residuals divided by the squared errors,
over the number of samples minus one fitted scale. These tests build a
spectrum from a reference plus noise of a known size and check that the true
reference scores near 1, that a neighbouring reference scores higher, that
wrong errors scale the number as the square, and that the errors never change
the ranking, the decision or any other field.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    _get_blurred_templates,
    classify_spectral_type,
    unclassified_result,
)

RESOLUTION_ANGSTROM = 45.0
NOISE_FRACTION = 0.03


def _noisy_observation(seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build an A0V spectrum with noise of a known size.

    Parameters
    ----------
    seed : `int`, optional
        Seed of the noise.

    Returns
    -------
    wavelength, intensity, errors : `tuple` of `numpy.ndarray`
        An 11 A grid from 4200 to 8000 A, the blurred A0V reference scaled to
        1000 plus Gaussian noise of 3% of the brightness, and that 3% as the
        reported 1-sigma error.
    """
    template_wavelength, template_flux = _get_blurred_templates(RESOLUTION_ANGSTROM)["A0V"]
    wavelength = np.arange(4200.0, 8000.0, 11.0)
    truth = (
        1000.0
        * np.interp(wavelength, template_wavelength, template_flux)
        / np.interp(5556.0, template_wavelength, template_flux)
    )
    errors = NOISE_FRACTION * truth
    rng = np.random.default_rng(seed)
    return wavelength, truth + rng.normal(size=wavelength.size) * errors, errors


def test_the_true_reference_fits_within_the_noise() -> None:
    """Reduced chi-square of the matching reference is near 1."""
    wavelength, intensity, errors = _noisy_observation()

    result = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM, intensity_errors=errors)

    assert result["spectral_type"] == "A0V"
    assert result["reduced_chi_square"] == pytest.approx(1.0, abs=0.25)


def test_a_neighbouring_reference_scores_a_larger_chi_square() -> None:
    """The second-best reference fits worse than the best."""
    wavelength, intensity, errors = _noisy_observation()

    result = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM, intensity_errors=errors)

    best = result["reduced_chi_square"]
    second = result["second_best_reduced_chi_square"]
    assert isinstance(best, float) and isinstance(second, float)
    assert second > 1.3 * best
    ranked = result["ranked_types"]
    assert ranked[0]["reduced_chi_square"] == best
    assert ranked[1]["reduced_chi_square"] == second
    assert all(entry["reduced_chi_square"] is not None for entry in ranked)


def test_errors_that_are_twice_too_large_divide_the_chi_square_by_four() -> None:
    """Chi-square scales as one over the error squared."""
    wavelength, intensity, errors = _noisy_observation()

    right = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM, intensity_errors=errors)
    doubled = classify_spectral_type(
        wavelength, intensity, RESOLUTION_ANGSTROM, intensity_errors=2.0 * errors
    )

    assert doubled["reduced_chi_square"] == pytest.approx(right["reduced_chi_square"] / 4.0, rel=1e-6)


def test_the_errors_never_change_the_rms_decision() -> None:
    """The type, the RMS, the gaps and the ranking are the same with errors."""
    wavelength, intensity, errors = _noisy_observation(seed=4)

    plain = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM)
    scored = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM, intensity_errors=errors)
    skewed = classify_spectral_type(
        wavelength,
        intensity,
        RESOLUTION_ANGSTROM,
        intensity_errors=errors * np.linspace(0.1, 10.0, errors.size),
    )

    chi_keys = {"reduced_chi_square", "second_best_reduced_chi_square"}
    for other in (scored, skewed):
        for key, value in plain.items():
            if key in chi_keys or key == "ranked_types":
                continue
            assert other[key] == value, key
        assert [entry["spectral_type"] for entry in other["ranked_types"]] == [
            entry["spectral_type"] for entry in plain["ranked_types"]
        ]
        assert [entry["rms"] for entry in other["ranked_types"]] == [
            entry["rms"] for entry in plain["ranked_types"]
        ]


def test_without_errors_the_chi_square_is_empty() -> None:
    """No errors, no chi-square, and every ranked entry says so."""
    wavelength, intensity, _ = _noisy_observation()

    result = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM)

    assert result["reduced_chi_square"] is None
    assert result["second_best_reduced_chi_square"] is None
    assert all(entry["reduced_chi_square"] is None for entry in result["ranked_types"])


def test_samples_without_an_error_are_left_out_of_the_chi_square() -> None:
    """NaN and zero errors drop their samples and keep the rest."""
    wavelength, intensity, errors = _noisy_observation()
    damaged = errors.copy()
    damaged[::3] = np.nan
    damaged[1::7] = 0.0

    result = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM, intensity_errors=damaged)

    assert result["spectral_type"] == "A0V"
    assert result["reduced_chi_square"] == pytest.approx(1.0, abs=0.35)


def test_too_few_samples_with_errors_give_no_chi_square() -> None:
    """With fewer than twenty usable errors the chi-square is `None`."""
    wavelength, intensity, errors = _noisy_observation()
    sparse = np.full(errors.shape, np.nan)
    sparse[:10] = errors[:10]

    result = classify_spectral_type(wavelength, intensity, RESOLUTION_ANGSTROM, intensity_errors=sparse)

    assert result["spectral_type"] == "A0V"
    assert result["reduced_chi_square"] is None


def test_excluded_windows_are_left_out_of_the_chi_square_too() -> None:
    """A window left out of the RMS is left out of the chi-square."""
    wavelength, intensity, errors = _noisy_observation()
    spoiled = intensity.copy()
    window = (wavelength > 6500.0) & (wavelength < 6700.0)
    spoiled[window] *= 0.5

    bad = classify_spectral_type(wavelength, spoiled, RESOLUTION_ANGSTROM, intensity_errors=errors)
    excluded = classify_spectral_type(
        wavelength,
        spoiled,
        RESOLUTION_ANGSTROM,
        excluded_windows_angstrom=[(6500.0, 6700.0)],
        intensity_errors=errors,
    )

    assert excluded["reduced_chi_square"] == pytest.approx(1.0, abs=0.35)
    assert bad["reduced_chi_square"] > 3.0 * excluded["reduced_chi_square"]


def test_an_unclassified_result_carries_empty_chi_square_fields() -> None:
    """The Unknown result has the same keys with `None` values."""
    result = unclassified_result("test")

    assert result["reduced_chi_square"] is None
    assert result["second_best_reduced_chi_square"] is None
