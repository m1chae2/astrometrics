"""Purpose: Test the line-index estimate of a star's spectral type.

Description: The estimate measures the depth of H-beta, H-alpha, Mg b, Na D
and two titanium oxide bands against the continuum beside each one, then picks
the bundled main-sequence template with the closest indices. The tests use the
bundled Pickles templates blurred to the instrument's stored line spread, so
the true type of every test spectrum is known.

Measured accuracy (ZWO ASI 533MM Pro line-spread profile, 34 templates):

* Each template given as input, with the template itself among the references:
  34 of 34 exact (100%). This only shows that the indices tell the templates
  apart, because the right answer is in the table.
* Each template given as input with itself removed from the references
  (leave one out), so the answer is the nearest other template: 17 of 34
  (50%) within two subtypes, 24 of 34 (71%) within five, and the largest miss
  is 19 subtypes (B3 read as F2). The bundled ladder has gaps of three to five
  subtypes in places (O5 to O9, B3 to B8, A7 to F0), so no method could be
  within two subtypes of every left-out template.
* Each template with 0.5% random noise on every 10 A sample: 99% within two
  subtypes. With 1% noise, 85%. With 2% noise, 72%.
"""

import numpy as np
import pytest

from astrometricslib.models.stellar_source import DIFFERS_FROM_CATALOG_SUBTYPES, ladder_steps_between
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    ResolutionProfile,
    load_line_spread_profile,
)
from astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction import redden_spectrum
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    REFERENCE_SPECTRAL_TYPES,
    blurred_reference_spectrum,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_line_indices import (
    LINE_INDICES,
    classify_by_line_indices,
    measure_line_indices,
    reference_index_table,
)

CAMERA_NAME = "ZWO ASI 533MM Pro"
RESPONSE_RANGE_ANGSTROM = (4200.0, 8000.0)

# The measured shares reported in the module docstring, with a little room so
# a change in the templates or the windows shows up as a failure, not a drift.
MINIMUM_SHARE_WITHIN_TWO_SUBTYPES = 0.80


@pytest.fixture(scope="module")
def line_spread() -> ResolutionProfile:
    """Load the stored line-spread profile of the test camera.

    Returns
    -------
    profile : `ResolutionProfile`
        The instrument's blur along the spectrum.
    """
    profile = load_line_spread_profile(CAMERA_NAME)
    assert profile is not None
    return profile


def instrument_spectrum(spectral_type: str, profile: ResolutionProfile) -> tuple[np.ndarray, np.ndarray]:
    """Give a template blurred to the instrument, over the response range.

    Parameters
    ----------
    spectral_type : `str`
        The template's label.
    profile : `ResolutionProfile`
        The instrument's blur along the spectrum.

    Returns
    -------
    wavelength_angstrom, flux : `tuple` [`np.ndarray`, `np.ndarray`]
        The blurred template inside `RESPONSE_RANGE_ANGSTROM`.
    """
    wavelength, flux = blurred_reference_spectrum(
        spectral_type, FALLBACK_RESOLUTION_ELEMENT_ANGSTROM, profile
    )
    inside = (wavelength >= RESPONSE_RANGE_ANGSTROM[0]) & (wavelength <= RESPONSE_RANGE_ANGSTROM[1])
    return wavelength[inside], flux[inside]


def ladder_error(true_type: str, found_type: str) -> float:
    """Give the distance between two types on the ladder, in subtypes.

    Returns
    -------
    steps : `float`
        The absolute distance.
    """
    steps = ladder_steps_between(true_type, found_type)
    assert steps is not None
    return abs(steps)


def index_table_by_type(profile: ResolutionProfile) -> dict[str, dict[str, float]]:
    """Read the reference index table as a dictionary.

    Parameters
    ----------
    profile : `ResolutionProfile`
        The instrument's blur along the spectrum.

    Returns
    -------
    table : `dict` [`str`, `dict` [`str`, `float`]]
        Template label mapped to its index values by index name.
    """
    types, table = reference_index_table(FALLBACK_RESOLUTION_ELEMENT_ANGSTROM, profile)
    names = [line.name for line in LINE_INDICES]
    return {label: dict(zip(names, row, strict=True)) for label, row in zip(types, table, strict=True)}


# ------------------------------------------------------------- the indices


def test_the_indices_and_their_windows_are_the_documented_ones() -> None:
    """Verify the six indices sit at the wavelengths the module documents."""
    windows = {line.name: (line.center_angstrom, line.half_width_angstrom) for line in LINE_INDICES}
    assert windows == {
        "h_beta": (4861.0, 25.0),
        "h_alpha": (6563.0, 25.0),
        "mg_b": (5175.0, 20.0),
        "na_d": (5893.0, 20.0),
        "tio_6200": (6200.0, 50.0),
        "tio_7100": (7100.0, 50.0),
    }


def test_the_balmer_lines_are_strongest_at_a0_and_fall_to_both_sides(
    line_spread: ResolutionProfile,
) -> None:
    """Verify H-beta peaks in the A stars and is weaker in B and in K stars."""
    table = index_table_by_type(line_spread)
    a_stars = [table[label]["h_beta"] for label in ("A0V", "A2V", "A3V", "A5V")]
    assert min(a_stars) > 2.0 * max(table["O9V"]["h_beta"], table["K2V"]["h_beta"], table["M0V"]["h_beta"])
    assert table["A0V"]["h_beta"] > table["B3V"]["h_beta"] > table["O5V"]["h_beta"]
    assert table["A0V"]["h_beta"] > table["F5V"]["h_beta"] > table["G5V"]["h_beta"]


def test_mg_b_and_na_d_rise_through_the_cool_stars(line_spread: ResolutionProfile) -> None:
    """Verify Mg b is stronger in K3 than in G0, and Na D grows into M."""
    table = index_table_by_type(line_spread)
    assert table["K3V"]["mg_b"] > table["G0V"]["mg_b"] > table["B3V"]["mg_b"]
    assert table["M3V"]["na_d"] > table["K5V"]["na_d"] > table["G0V"]["na_d"]


def test_the_titanium_oxide_bands_appear_in_m_stars(line_spread: ResolutionProfile) -> None:
    """Verify both TiO bands are deep in M4V and shallow in G2V and A0V."""
    table = index_table_by_type(line_spread)
    for band in ("tio_6200", "tio_7100"):
        assert table["M4V"][band] > 0.04
        assert table["M4V"][band] > 3.0 * max(abs(table["G2V"][band]), abs(table["A0V"][band]))


def test_the_indices_barely_change_when_the_spectrum_is_reddened(line_spread: ResolutionProfile) -> None:
    """Verify E(B-V) = 0.3 moves every index of a G0V by under 0.01.

    This is the reason to use the indices as a cross-check: the continuum next
    to each line absorbs the tilt that reddening adds.
    """
    wavelength, flux = instrument_spectrum("G0V", line_spread)
    plain = measure_line_indices(wavelength, flux, resolution_profile=line_spread)
    reddened = measure_line_indices(
        wavelength, redden_spectrum(wavelength, flux, 0.3), resolution_profile=line_spread
    )
    for name, value in plain.items():
        assert value is not None
        assert reddened[name] is not None
        assert abs(reddened[name] - value) < 0.01, name


def test_an_index_whose_core_overlaps_an_excluded_window_is_not_measured(
    line_spread: ResolutionProfile,
) -> None:
    """Verify an emission window over H-alpha removes only that index."""
    wavelength, flux = instrument_spectrum("A0V", line_spread)

    indices = measure_line_indices(
        wavelength, flux, resolution_profile=line_spread, excluded_windows_angstrom=[(6450.0, 6680.0)]
    )

    assert indices["h_alpha"] is None
    assert indices["h_beta"] is not None
    assert indices["mg_b"] is not None


def test_the_atmospheric_bands_are_left_out_of_the_measurement(line_spread: ResolutionProfile) -> None:
    """Verify a dip in the oxygen A band leaves the TiO 7100 index alone."""
    wavelength, flux = instrument_spectrum("K5V", line_spread)
    spoiled = flux.copy()
    spoiled[(wavelength > 7500.0) & (wavelength < 7700.0)] *= 0.5

    plain = measure_line_indices(wavelength, flux, resolution_profile=line_spread)
    with_dip = measure_line_indices(wavelength, spoiled, resolution_profile=line_spread)

    assert with_dip["tio_7100"] == pytest.approx(plain["tio_7100"], abs=1e-9)


# ------------------------------------------------ recovering a known type


def test_each_template_is_recovered_within_two_subtypes(line_spread: ResolutionProfile) -> None:
    """Verify the index classifier names each unreddened template's own type.

    Measured: 34 of 34 exact. The assertion is the required 80% within two
    subtypes.
    """
    confusion: dict[str, str] = {}
    for true_type in REFERENCE_SPECTRAL_TYPES:
        wavelength, flux = instrument_spectrum(true_type, line_spread)
        result = classify_by_line_indices(wavelength, flux, resolution_profile=line_spread)
        assert result is not None
        confusion[true_type] = result.best_type

    errors = np.array([ladder_error(true_type, found) for true_type, found in confusion.items()])
    assert (errors <= 2).mean() >= MINIMUM_SHARE_WITHIN_TWO_SUBTYPES
    assert (errors == 0).all()


def test_a_template_left_out_of_the_references_lands_near_its_neighbours(
    line_spread: ResolutionProfile,
) -> None:
    """Verify the leave-one-out estimate is rough but never wildly wrong.

    The template under test is removed from the references, so the answer is
    its nearest other template. Measured: 50% within two subtypes, 71%
    within five, largest miss 19 subtypes. The assertions leave room under
    those numbers and require that no miss reaches the limit at which the
    checkpoint flags a disagreement.
    """
    errors = []
    for true_type in REFERENCE_SPECTRAL_TYPES:
        wavelength, flux = instrument_spectrum(true_type, line_spread)
        others = [label for label in REFERENCE_SPECTRAL_TYPES if label != true_type]
        result = classify_by_line_indices(
            wavelength, flux, resolution_profile=line_spread, reference_types=others
        )
        assert result is not None
        assert result.best_type != true_type
        errors.append(ladder_error(true_type, result.best_type))

    errors_array = np.array(errors)
    assert (errors_array <= 2).mean() >= 0.45
    assert (errors_array <= 5).mean() >= 0.65
    assert errors_array.max() <= DIFFERS_FROM_CATALOG_SUBTYPES


def test_light_noise_does_not_move_the_type_far(line_spread: ResolutionProfile) -> None:
    """Verify 0.5% noise keeps 95% of templates within two subtypes.

    The noise is random and applies to every 10 A sample.

    Measured: 99% over 5 noise draws of all 34 templates.
    """
    generator = np.random.default_rng(1)
    errors = []
    for _draw in range(5):
        for true_type in REFERENCE_SPECTRAL_TYPES:
            wavelength, flux = instrument_spectrum(true_type, line_spread)
            wavelength, flux = wavelength[::2], flux[::2]
            noisy = flux * (1.0 + 0.005 * generator.standard_normal(flux.size))
            result = classify_by_line_indices(wavelength, noisy, resolution_profile=line_spread)
            assert result is not None
            errors.append(ladder_error(true_type, result.best_type))
    assert (np.array(errors) <= 2).mean() >= 0.95


def test_the_result_records_the_distance_from_the_template_fit_type(line_spread: ResolutionProfile) -> None:
    """Verify the result names both types and the steps between them."""
    wavelength, flux = instrument_spectrum("K3V", line_spread)

    result = classify_by_line_indices(
        wavelength, flux, resolution_profile=line_spread, template_fit_type="G5V"
    )

    assert result is not None
    assert result.best_type == "K3V"
    assert result.template_fit_type == "G5V"
    assert result.steps_from_template_fit == pytest.approx(8.0)
    assert result.distance == pytest.approx(0.0, abs=1e-9)
    assert set(result.indices) == {line.name for line in LINE_INDICES}


def test_a_spectrum_with_too_few_measurable_indices_is_not_classified(
    line_spread: ResolutionProfile,
) -> None:
    """Verify a spectrum that covers only the blue gives no index type."""
    wavelength, flux = instrument_spectrum("G2V", line_spread)
    blue = wavelength < 5300.0

    result = classify_by_line_indices(wavelength[blue], flux[blue], resolution_profile=line_spread)

    assert result is None
