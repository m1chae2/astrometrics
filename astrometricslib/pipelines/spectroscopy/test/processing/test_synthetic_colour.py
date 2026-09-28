"""Purpose: Unit tests for measuring a spectrum's synthetic B-V colour.

Description: A spectrum's colour, read from two wavelength windows and
calibrated on the dwarf references, should recover each reference's own
known colour, stay unset when a window is missing or averages to zero,
and saturate sensibly for a star redder than anything calibrated.

The comparison of this measured colour against a star's catalog B-V
(`colour_disagreement_note`) moved to
`test/post_processing/test_compare_to_catalog.py`, and the
`analyze_spectrum` wiring tests moved to
`test/test_synthetic_colour.py` (a cross-cutting test, since they
exercise pre-processing, processing and post-processing together).
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog import (
    MAXIMUM_CALIBRATED_B_MINUS_V,
    colour_disagreement_note,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates
from astrometricslib.pipelines.spectroscopy.processing.synthetic_colour import (
    DWARF_B_MINUS_V,
    synthetic_b_minus_v,
    window_colour_index,
)


def _blurred(spectral_type: str) -> tuple[np.ndarray, np.ndarray]:
    """Give a reference spectrum blurred to the instrument's resolution.

    Returns
    -------
    wavelength_angstrom, flux : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The reference wavelengths and blurred flux.
    """
    wavelength, flux = _get_reference_templates()[spectral_type]
    return wavelength, gaussian_filter1d(flux, 45.0 / 2.355 / 5.0)


def test_the_calibration_recovers_the_reference_colours() -> None:
    """Each dwarf reference reads close to the B-V it was calibrated on."""
    errors = []
    for reference_type, b_minus_v in DWARF_B_MINUS_V.items():
        recovered = synthetic_b_minus_v(*_get_reference_templates()[reference_type])
        assert recovered is not None, reference_type
        errors.append(recovered - b_minus_v)

    assert np.sqrt(np.mean(np.square(errors))) < 0.06
    assert max(abs(error) for error in errors) < 0.15


@pytest.mark.parametrize(("reference_type", "expected"), [("A0V", 0.0), ("G2V", 0.63), ("K5V", 1.15)])
def test_blurred_references_keep_their_colour(reference_type: str, expected: float) -> None:
    """Blurring to the instrument's resolution barely moves the colour."""
    assert synthetic_b_minus_v(*_blurred(reference_type)) == pytest.approx(expected, abs=0.1)


def test_a_spectrum_that_misses_a_window_has_no_colour() -> None:
    """Without the blue window there is nothing to compare."""
    wavelength, flux = _blurred("G2V")
    red_only = wavelength >= 5000.0

    assert window_colour_index(wavelength[red_only], flux[red_only]) is None
    assert synthetic_b_minus_v(wavelength[red_only], flux[red_only]) is None


def test_a_spectrum_redder_than_any_reference_reads_as_the_reddest() -> None:
    """A late M star reads as a lower limit and still shows a difference."""
    colour = synthetic_b_minus_v(*_get_reference_templates()["M6V"])

    assert colour is not None
    assert 1.5 < colour <= MAXIMUM_CALIBRATED_B_MINUS_V
    assert colour_disagreement_note(0.0, colour) != ""


def test_a_non_positive_window_has_no_colour() -> None:
    """A window that averages to zero gives no magnitude difference."""
    wavelength, flux = _blurred("G2V")

    assert window_colour_index(wavelength, np.zeros_like(flux)) is None
