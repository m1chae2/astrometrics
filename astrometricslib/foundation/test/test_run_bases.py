"""Tests for determined-each-run values: factor times a measured basis.

A value with a basis resolves to its factor times the basis the run measured,
or the setup's value of that basis before the run measures it, or its
reference value (with a notice) when neither is known. The setup's plate
scale, image size, field of view and dispersion follow from the optic and the
camera. The model refuses a basis without a reference value or on another
source.
"""

import pytest

from astrometricslib.foundation.observatory_setups import (
    ObservatorySetups,
    OpticConfig,
    SetupConfig,
)
from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import SetupBases, scale_by_basis
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)

SETUP = SetupConfig(name="Mono on Refractor", camera_name="Mono Camera", optic_name="Refractor")


class FakeConfiguration:
    """Give the camera section and setups the bases are worked out from."""

    def get_camera_config(self, camera_name: str) -> dict[str, str]:
        """Return a 3.76 µm, 3008 px square camera with a 200 lines/mm grating.

        Returns
        -------
        section : `dict` [`str`, `str`]
            The camera's settings.
        """
        return {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "3008",
            "sensor_height_px": "3008",
            "grating_lines_per_mm": "200",
            "grating_distance_mm": "16.5",
        }

    def get_observatory_setups(self) -> ObservatorySetups:
        """Return one 405 mm optic and the setup.

        Returns
        -------
        setups : `ObservatorySetups`
            The optic and setup.
        """
        return ObservatorySetups(
            optics=(OpticConfig(name="Refractor", focal_length_mm=405.0),), setups=(SETUP,)
        )


def _with_basis(basis: RunBasis, factor: float, reference: float | int) -> TunedParameter:
    """Build a determined-each-run declaration with a basis.

    Returns
    -------
    parameter : `TunedParameter`
        The declaration.
    """
    return TunedParameter(
        name="test.scaled_value",
        default=factor,
        unit=f"× {basis}",
        description="A test value.",
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.DETERMINED_EACH_RUN,
        basis=basis,
        reference_value=reference,
    )


def test_the_setup_gives_plate_scale_image_size_field_and_dispersion() -> None:
    """The optic and camera give plate scale, image size, field, dispersion."""
    bases = SetupBases(FakeConfiguration(), SETUP)
    assert bases.value(RunBasis.PLATE_SCALE_ARCSEC_PER_PX) == pytest.approx(1.915, abs=0.001)
    assert bases.value(RunBasis.IMAGE_SHORT_SIDE_PX) == pytest.approx(3008.0)
    assert bases.value(RunBasis.FIELD_OF_VIEW_DEG) == pytest.approx(1.600, abs=0.001)
    assert bases.value(RunBasis.DISPERSION_ANGSTROM_PER_PX) == pytest.approx(11.39, abs=0.01)
    assert bases.value(RunBasis.TARGET_SIZE_ARCSEC) is None


def test_a_measured_basis_wins_over_the_setup() -> None:
    """After the run records a star width, the value follows it."""
    parameter = _with_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, 2.0, 3.8)
    with parameter_context() as context:
        context.setup_bases = SetupBases(FakeConfiguration(), SETUP)
        assert resolve(parameter).value == pytest.approx(2.0 * 1.915, abs=0.01)
        record_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, 3.0)
        assert resolve(parameter).value == pytest.approx(6.0)
    assert context.notices == []


def test_with_no_basis_the_reference_value_stands_in_with_a_notice() -> None:
    """A basis nobody knows gives the reference value and records why."""
    parameter = _with_basis(RunBasis.TARGET_SIZE_ARCSEC, 2.0, 60.0)
    with parameter_context() as context:
        resolved = resolve(parameter)
    assert (resolved.value, resolved.origin) == (60.0, "default")
    assert context.notices == [
        "test.scaled_value: no target size is known; using the reference-setup value 60.0"
    ]


def test_a_whole_number_stays_whole_and_a_tuple_scales_per_element() -> None:
    """The result keeps the type of the reference value."""
    box = _with_basis(RunBasis.STAR_WIDTH_PX, 3.33, 10)
    assert scale_by_basis(box, 3.0) == 10
    assert scale_by_basis(box, 0.01) == 1
    widths = TunedParameter(
        name="test.widths",
        default=(0.5, 1.0),
        unit="× FWHM",
        description="Test widths.",
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.DETERMINED_EACH_RUN,
        basis=RunBasis.STAR_WIDTH_PX,
        reference_value=(1.5, 3.0),
    )
    assert scale_by_basis(widths, 4.0) == (2.0, 4.0)


def test_recording_a_basis_outside_a_run_does_nothing() -> None:
    """With no run context there is nothing to record into."""
    record_basis(RunBasis.STAR_WIDTH_PX, 4.0)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reference_value": None}, "needs its reference_value"),
        (
            {"source": ParameterSource.EQUIPMENT_PROFILE, "equipment": frozenset({EquipmentKind.CAMERA})},
            "only a value determined each run has a basis",
        ),
        ({"reference_value": (1.0, 2.0)}, "shape of the default"),
    ],
)
def test_the_model_checks_the_basis_fields(changes: dict, message: str) -> None:
    """A basis needs a reference value, the right source and its shape."""
    fields = {
        "name": "test.checked",
        "default": 2.0,
        "unit": "× FWHM",
        "description": "A test value.",
        "role": ParameterRole.MEASUREMENT,
        "source": ParameterSource.DETERMINED_EACH_RUN,
        "basis": RunBasis.STAR_WIDTH_PX,
        "reference_value": 6.0,
        **changes,
    }
    with pytest.raises(ValueError, match=message):
        TunedParameter(**fields)
