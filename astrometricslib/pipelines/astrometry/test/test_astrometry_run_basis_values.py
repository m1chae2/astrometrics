"""Tests that the astrometry values follow their run basis.

Each value declared as a factor times a basis gives back its old absolute
value when the run has measured nothing, and scales with the basis once the
run records one.
"""

import importlib

import pytest

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES
from astrometricslib.models.tuned_parameter import TunedParameter

MODULES = (
    "astrometricslib.pipelines.astrometry.pre_processing.fwhm",
    "astrometricslib.pipelines.astrometry.pre_processing.source_detection",
    "astrometricslib.pipelines.astrometry.processing.star_identifier",
    "astrometricslib.pipelines.astrometry.processing.wcs_refinement",
    "astrometricslib.pipelines.astrometry.utilities.catalog_seeding",
)


def _basis_parameters(module_name: str) -> list[TunedParameter]:
    """Collect the module's declarations that have a basis.

    Parameters
    ----------
    module_name : `str`
        The dotted module name.

    Returns
    -------
    parameters : `list` [`TunedParameter`]
        The declarations with a basis, in the order of the module.
    """
    module = importlib.import_module(module_name)
    return [
        value
        for value in vars(module).values()
        if isinstance(value, TunedParameter) and value.basis is not None
    ]


@pytest.mark.parametrize("module_name", MODULES)
def test_with_no_basis_measured_the_old_values_stand(module_name: str) -> None:
    """With nothing measured, values keep their old defaults."""
    parameters = _basis_parameters(module_name)
    assert parameters
    with parameter_context():
        for parameter in parameters:
            resolved = resolve(parameter).value
            assert resolved == pytest.approx(parameter.reference_value), parameter.name
            assert type(resolved) is type(parameter.reference_value), parameter.name


@pytest.mark.parametrize("module_name", MODULES)
def test_a_recorded_basis_scales_the_values(module_name: str) -> None:
    """Once the run records twice the reference basis, the values double."""
    for parameter in _basis_parameters(module_name):
        assert parameter.basis is not None
        with parameter_context():
            record_basis(parameter.basis, 2.0 * REFERENCE_SETUP_BASES[parameter.basis])
            scaled = resolve(parameter).value
        expected = 2.0 * float(parameter.reference_value)
        if isinstance(parameter.reference_value, int):
            expected = max(1, round(expected))
        assert scaled == pytest.approx(expected), parameter.name
