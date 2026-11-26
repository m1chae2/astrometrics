"""Tests for the post-processing values that follow a basis measured each run.

The stack measurements (noise apertures, flat match, half pattern, shifts,
sky level, comparison, star tone and the white point of stars like the
Sun) have sizes written as a factor times a basis (the star width, the
plate scale or the image size). With no basis measured they must give their
old absolute values, and once the run records a basis they must scale with
it.
"""

import importlib

import pytest

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import TunedParameter

MODULE_NAMES = [
    "astrometricslib.pipelines.stacking.odd_even_bias",
    "astrometricslib.pipelines.stacking.post_processing.aperture_placement",
    "astrometricslib.pipelines.stacking.post_processing.depth_limits",
    "astrometricslib.pipelines.stacking.post_processing.half_pattern",
    "astrometricslib.pipelines.stacking.post_processing.registration_shifts",
    "astrometricslib.pipelines.stacking.post_processing.sky_level",
    "astrometricslib.pipelines.stacking.post_processing.stack_comparison",
    "astrometricslib.pipelines.stacking.post_processing.stack_image",
    "astrometricslib.pipelines.stacking.post_processing.stack_noise",
    "astrometricslib.pipelines.stacking.post_processing.star_tone",
    "astrometricslib.pipelines.stacking.post_processing.sun_like_balance",
]


def _converted_parameters() -> list[TunedParameter]:
    """Collect every parameter of the listed modules that has a basis.

    Returns
    -------
    parameters : `list` [`TunedParameter`]
        The declarations with a basis.
    """
    found = []
    for module_name in MODULE_NAMES:
        module = importlib.import_module(module_name)
        found += [
            value
            for value in vars(module).values()
            if isinstance(value, TunedParameter) and value.basis is not None
        ]
    return found


PARAMETERS = _converted_parameters()


def _ids(parameter: TunedParameter) -> str:
    """Name a parameter for the test report.

    Returns
    -------
    name : `str`
        The parameter's registered name.
    """
    return parameter.name


def test_the_modules_have_converted_values() -> None:
    """The collection finds the converted values, so the tests below run."""
    assert len(PARAMETERS) >= 20


@pytest.mark.parametrize("parameter", PARAMETERS, ids=_ids)
def test_with_no_basis_measured_the_old_value_stands(parameter: TunedParameter) -> None:
    """With no basis measured, a converted value gives its old value."""
    with parameter_context():
        resolved = resolve(parameter).value
    reference = parameter.reference_value
    if isinstance(reference, tuple):
        assert resolved == pytest.approx(reference)
    elif isinstance(reference, int):
        assert resolved == reference
    else:
        assert resolved == pytest.approx(reference)


@pytest.mark.parametrize("parameter", PARAMETERS, ids=_ids)
def test_a_recorded_basis_scales_the_value(parameter: TunedParameter) -> None:
    """Doubling the basis doubles the value (halves an inverse one)."""
    from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES

    basis = parameter.basis
    assert basis is not None
    with parameter_context():
        record_basis(basis, 2.0 * REFERENCE_SETUP_BASES[basis])
        resolved = resolve(parameter).value
    reference = parameter.reference_value
    expected_ratio = 0.5 if parameter.basis_inverse else 2.0
    values = resolved if isinstance(resolved, tuple) else (resolved,)
    references = reference if isinstance(reference, tuple) else (reference,)
    for value, old in zip(values, references, strict=True):
        assert value == pytest.approx(expected_ratio * old, abs=0.5 if isinstance(old, int) else 1e-9)
