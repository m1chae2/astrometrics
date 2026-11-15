"""Tests for the values of two areas that scale with a measured basis.

The areas are spectroscopy and asteroid detection.

Each converted value is a factor times a basis (trail width, dispersion,
spectral resolution, star width, plate scale or field of view). With no basis
measured, it must resolve to the absolute value it had before the conversion.
When the run records a basis, the value must scale with it. The runner records
the run's typical trail width, dispersion and line spread once, from the
spectra the extraction produced.
"""

import importlib
from types import SimpleNamespace

import pytest

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES
from astrometricslib.models.tuned_parameter import RunBasis, TunedParameter
from astrometricslib.pipelines.spectroscopy.runner import record_measured_spectral_bases

MODULES = (
    "astrometricslib.pipelines.asteroid_detection.pipeline",
    "astrometricslib.pipelines.spectroscopy.pipeline",
    "astrometricslib.pipelines.spectroscopy.runner",
    "astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp",
    "astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale",
    "astrometricslib.pipelines.spectroscopy.pre_processing.assess_raw_frame_quality",
    "astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_trail_deblending",
    "astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_wing_correction",
    "astrometricslib.pipelines.spectroscopy.pre_processing.spectral_frame_check",
    "astrometricslib.pipelines.spectroscopy.pre_processing.spectral_star_registration",
    "astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor",
    "astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point",
    "astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector",
    "astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner",
)


def _scaled_parameters() -> list[TunedParameter]:
    """Collect every declaration in the converted modules that has a basis.

    Returns
    -------
    parameters : `list` [`TunedParameter`]
        The declarations with a basis, in module order.
    """
    found: dict[str, TunedParameter] = {}
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if isinstance(value, TunedParameter) and value.basis:
                found[value.name] = value
    return list(found.values())


PARAMETERS = _scaled_parameters()


def test_the_conversion_covers_every_decided_value() -> None:
    """Twenty-eight values of these areas scale with a basis."""
    assert len(PARAMETERS) == 28


@pytest.mark.parametrize("parameter", PARAMETERS, ids=lambda parameter: parameter.name)
def test_value_is_its_old_default_when_no_basis_is_measured(parameter: TunedParameter) -> None:
    """With no basis and no setup, the value is its old absolute value."""
    with parameter_context():
        assert resolve(parameter).value == parameter.reference_value


@pytest.mark.parametrize("parameter", PARAMETERS, ids=lambda parameter: parameter.name)
def test_value_follows_a_recorded_basis(parameter: TunedParameter) -> None:
    """Twice the reference basis doubles a value (halves an inverse one)."""
    assert parameter.basis is not None and isinstance(parameter.reference_value, (int, float))
    with parameter_context():
        record_basis(parameter.basis, 2.0 * REFERENCE_SETUP_BASES[parameter.basis])
        value = resolve(parameter).value
    expected = (0.5 if parameter.basis_inverse else 2.0) * parameter.reference_value
    if isinstance(parameter.reference_value, int):
        expected = max(1, round(expected))
    assert value == pytest.approx(expected)


def _spectrum(wavelengths: list[float], sigma: float) -> SimpleNamespace:
    """Build a stand-in star with a spectrum of constant trail width.

    Returns
    -------
    star : `SimpleNamespace`
        A star whose ``spectroscopy`` has wavelengths and trail widths.
    """
    return SimpleNamespace(
        spectroscopy=SimpleNamespace(
            wavelengths_angstrom=wavelengths, trail_width_px=[sigma] * len(wavelengths)
        )
    )


def test_runner_records_the_run_trail_width_dispersion_and_resolution() -> None:
    """The median trail FWHM, pixel step and line spread are recorded."""
    wavelengths = [4000.0 + 10.0 * step for step in range(200)]
    stars = [_spectrum(wavelengths, 2.0), _spectrum(wavelengths, 2.0), SimpleNamespace(spectroscopy=None)]
    with parameter_context() as context:
        record_measured_spectral_bases(stars)
    assert context.bases[RunBasis.TRAIL_WIDTH_PX] == pytest.approx(2.355 * 2.0)
    assert context.bases[RunBasis.DISPERSION_ANGSTROM_PER_PX] == pytest.approx(10.0)
    assert context.bases[RunBasis.SPECTRAL_RESOLUTION_ANGSTROM] == pytest.approx(2.355 * 2.0 * 10.0)


def test_runner_records_nothing_without_spectra() -> None:
    """A run with no spectra measures no basis."""
    with parameter_context() as context:
        record_measured_spectral_bases([SimpleNamespace(spectroscopy=None)])
    assert context.bases == {}
