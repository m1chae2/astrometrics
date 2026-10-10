"""Purpose: Test that dereddening fixes a classification that dust broke.

Description: Interstellar dust tilts a spectrum toward the red, and the
template classifier reads the tilt as a cooler star. These tests start from a
bundled Pickles template of known type, blur it to the instrument's stored
line spread, redden it with the Cardelli, Clayton and Mathis law, and classify
it with and without the reddening removed. The truth is the template's own
type, so no catalog value is involved.

They also check the result `analyze_spectrum` records when it is given a
catalog E(B-V): the E(B-V) and its source, both best types, the signed type
shift and the note, and that a star with no E(B-V) is classified unchanged.
"""

import numpy as np
import pytest

from astrometricslib.drivers.interfaces.reddening_driver import ReddeningEstimate
from astrometricslib.models.stellar_source import ladder_position, ladder_steps_between
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    ResolutionProfile,
    blur_to_resolution_profile,
    load_line_spread_profile,
)
from astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction import (
    deredden_spectrum,
    redden_spectrum,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    _get_reference_templates,
    classify_spectral_type,
)
from astrometricslib.pipelines.spectroscopy.processing.spectrum_analysis import analyze_spectrum

CAMERA_NAME = "ZWO ASI 533MM Pro"

# The range the instrument response is valid over (see
# `instrument_response.DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM`).
RESPONSE_RANGE_ANGSTROM = (4200.0, 8000.0)

# The colour excess the task's reference case uses.
EBV = 0.3


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


def blurred_template(spectral_type: str, profile: ResolutionProfile) -> tuple[np.ndarray, np.ndarray]:
    """Give a bundled template blurred to the instrument.

    Parameters
    ----------
    spectral_type : `str`
        The template's label, such as ``"G0V"``.
    profile : `ResolutionProfile`
        The instrument's blur along the spectrum.

    Returns
    -------
    wavelength_angstrom, flux : `tuple` [`np.ndarray`, `np.ndarray`]
        The template inside `RESPONSE_RANGE_ANGSTROM`.
    """
    wavelength, flux = _get_reference_templates()[spectral_type]
    blurred = blur_to_resolution_profile(wavelength, flux, profile)
    inside = (wavelength >= RESPONSE_RANGE_ANGSTROM[0]) & (wavelength <= RESPONSE_RANGE_ANGSTROM[1])
    return wavelength[inside], blurred[inside]


def best_type(wavelength: np.ndarray, spectrum: np.ndarray, profile: ResolutionProfile) -> str:
    """Classify a spectrum with the stored line spread.

    Parameters
    ----------
    wavelength : `np.ndarray`
        Wavelengths in Angstroms.
    spectrum : `np.ndarray`
        The response-corrected spectrum.
    profile : `ResolutionProfile`
        The instrument's blur along the spectrum.

    Returns
    -------
    spectral_type : `str`
        The best template type.
    """
    result = classify_spectral_type(
        wavelength, spectrum, FALLBACK_RESOLUTION_ELEMENT_ANGSTROM, resolution_profile=profile
    )
    return str(result["spectral_type"])


def test_a_reddened_g0v_is_classified_several_subtypes_too_late_without_dereddening(
    line_spread: ResolutionProfile,
) -> None:
    """Verify E(B-V) = 0.3 makes a G0V template read as a much cooler type.

    Measured on the bundled templates: K2V, 12 subtypes too late.
    """
    wavelength, flux = blurred_template("G0V", line_spread)
    reddened = redden_spectrum(wavelength, flux, EBV)

    observed = classify_spectral_type(
        wavelength, reddened, FALLBACK_RESOLUTION_ELEMENT_ANGSTROM, resolution_profile=line_spread
    )

    steps_too_late = ladder_steps_between("G0V", str(observed["spectral_type"]))
    assert steps_too_late is not None
    assert steps_too_late >= 3


def test_dereddening_with_the_known_colour_excess_recovers_g0v(line_spread: ResolutionProfile) -> None:
    """Verify the type is within one subtype of G0V once E(B-V) is removed."""
    wavelength, flux = blurred_template("G0V", line_spread)
    reddened = redden_spectrum(wavelength, flux, EBV)

    dereddened = classify_spectral_type(
        wavelength,
        deredden_spectrum(wavelength, reddened, EBV),
        FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
        resolution_profile=line_spread,
    )

    steps = ladder_steps_between("G0V", str(dereddened["spectral_type"]))
    assert steps is not None
    assert abs(steps) <= 1


@pytest.mark.parametrize("true_type", ["B8V", "A0V", "F5V", "K0V", "M0V"])
def test_dereddening_never_makes_a_reddened_template_worse(
    true_type: str, line_spread: ResolutionProfile
) -> None:
    """Verify the dereddened type is no further off than the observed one."""
    wavelength, flux = blurred_template(true_type, line_spread)
    reddened = redden_spectrum(wavelength, flux, EBV)
    truth = ladder_position(true_type)
    assert truth is not None

    def distance(spectrum: np.ndarray) -> float:
        """Give how far a spectrum's best type is from the truth.

        Returns
        -------
        steps : `float`
            The distance in subtypes.
        """
        position = ladder_position(best_type(wavelength, spectrum, line_spread))
        assert position is not None
        return abs(position - truth)

    assert distance(deredden_spectrum(wavelength, reddened, EBV)) <= distance(reddened)


def test_a_modest_error_in_the_colour_excess_still_helps(line_spread: ResolutionProfile) -> None:
    """Verify using E(B-V) = 0.25 for a star with 0.30 still beats using none.

    Catalog values are estimates, so the correction has to help when it is a
    little wrong.
    """
    wavelength, flux = blurred_template("G0V", line_spread)
    reddened = redden_spectrum(wavelength, flux, EBV)
    partly_removed = deredden_spectrum(wavelength, reddened, 0.25)

    uncorrected = abs(ladder_steps_between("G0V", best_type(wavelength, reddened, line_spread)) or 0.0)
    partly_corrected = abs(
        ladder_steps_between("G0V", best_type(wavelength, partly_removed, line_spread)) or 0.0
    )
    assert partly_corrected < uncorrected


def analyze(
    wavelength: np.ndarray, flux: np.ndarray, profile: ResolutionProfile, reddening: ReddeningEstimate | None
) -> object:
    """Run `analyze_spectrum` on a response-corrected test spectrum.

    Parameters
    ----------
    wavelength : `np.ndarray`
        Wavelengths in Angstroms.
    flux : `np.ndarray`
        The response-corrected spectrum.
    profile : `ResolutionProfile`
        The instrument's blur along the spectrum.
    reddening : `ReddeningEstimate`, optional
        The catalog colour excess to pass.

    Returns
    -------
    analysis : `SpectrumAnalysis`
        The analysis.
    """
    return analyze_spectrum(
        wavelength,
        flux,
        flux,
        resolution_profile=profile,
        reddening=reddening,
    )


def test_analyze_spectrum_reports_the_dereddened_type_and_records_both(
    line_spread: ResolutionProfile,
) -> None:
    """Verify the reported type is the dereddened one; the record has both."""
    wavelength, flux = blurred_template("G0V", line_spread)
    reddened = redden_spectrum(wavelength, flux, EBV)
    estimate = ReddeningEstimate(ebv=EBV, source="Gaia DR3 GSP-Phot E(BP-RP) / 1.339", gaia_source_id=42)

    analysis = analyze(wavelength, reddened, line_spread, estimate)

    assert analysis.classification["spectral_type"] == "G0V"
    record = analysis.reddening
    assert record is not None
    assert record.ebv == pytest.approx(EBV)
    assert record.ebv_source == "Gaia DR3 GSP-Phot E(BP-RP) / 1.339"
    assert record.gaia_source_id == "42"
    assert record.dereddened_best_type == "G0V"
    assert record.observed_best_type != "G0V"
    # Dereddening gave a hotter type, so the signed shift is negative.
    assert record.type_shift_steps is not None
    assert record.type_shift_steps <= -3
    assert record.type_shift_steps == pytest.approx(
        ladder_steps_between(record.observed_best_type, record.dereddened_best_type)
    )
    note = str(analysis.classification["reason"])
    assert "dereddened with E(B-V) = 0.30" in note
    assert "Gaia DR3 GSP-Phot" in note


def test_analyze_spectrum_without_a_colour_excess_classifies_the_observed_spectrum(
    line_spread: ResolutionProfile,
) -> None:
    """Verify no E(B-V) leaves the classification as it was."""
    wavelength, flux = blurred_template("G0V", line_spread)
    reddened = redden_spectrum(wavelength, flux, EBV)

    analysis = analyze(wavelength, reddened, line_spread, None)

    assert analysis.reddening is None
    assert analysis.classification["spectral_type"] != "G0V"
    assert "dereddened" not in str(analysis.classification["reason"] or "")


def test_a_zero_colour_excess_leaves_the_type_unchanged(line_spread: ResolutionProfile) -> None:
    """Verify E(B-V) = 0 gives the same type before and after."""
    wavelength, flux = blurred_template("A0V", line_spread)

    analysis = analyze(wavelength, flux, line_spread, ReddeningEstimate(ebv=0.0, source="test"))

    assert analysis.reddening is not None
    assert analysis.reddening.type_shift_steps == pytest.approx(0.0)
    assert analysis.reddening.observed_best_type == analysis.reddening.dereddened_best_type == "A0V"


def test_the_dereddened_classification_carries_a_chi_square_from_scaled_errors(
    line_spread: ResolutionProfile,
) -> None:
    """Verify the reported (dereddened) type keeps its reduced chi-square.

    Dereddening multiplies each sample by a fixed factor, so the sample
    errors are scaled by the same factor before the classifier uses them.
    With 1% errors on a noiseless reddened G0V, the dereddened fit must give
    a finite chi-square. Without errors it would have none.
    """
    wavelength, flux = blurred_template("G0V", line_spread)
    reddened = redden_spectrum(wavelength, flux, EBV)
    errors = 0.01 * np.abs(reddened)

    analysis = analyze_spectrum(
        wavelength,
        reddened,
        reddened,
        resolution_profile=line_spread,
        reddening=ReddeningEstimate(ebv=EBV, source="test", gaia_source_id=1),
        intensity_errors=errors,
        response_corrected_intensity_errors=errors,
    )

    chi_square = analysis.classification.get("reduced_chi_square")
    assert analysis.reddening is not None
    assert analysis.reddening.dereddened_best_type
    assert isinstance(chi_square, float)
    assert np.isfinite(chi_square)
