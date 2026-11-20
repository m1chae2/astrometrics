"""Tests for the `TunedParameter` declaration and its validation rules."""

import pytest
from pydantic import ValidationError

from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    WhenMissing,
)


def _build(**overrides: object) -> TunedParameter:
    """Build a valid hardcoded parameter, with any field replaced.

    Parameters
    ----------
    **overrides : `object`
        Fields to replace.

    Returns
    -------
    parameter : `TunedParameter`
        The parameter.
    """
    fields: dict[str, object] = {
        "name": "stacking.flat_noise_fraction",
        "default": 0.005,
        "unit": "fraction",
        "description": "The share of noise that counts as a problem.",
        "role": ParameterRole.JUDGMENT,
        "source": ParameterSource.HARDCODED,
        "kind": HardcodedKind.METHOD_CHOICE,
    }
    fields.update(overrides)
    return TunedParameter(**fields)  # type: ignore[arg-type]


def test_valid_hardcoded_parameter_builds() -> None:
    """A hardcoded parameter with a kind and no equipment is accepted."""
    parameter = _build()
    assert parameter.when_missing is WhenMissing.FALL_BACK
    assert parameter.equipment == frozenset()


def test_enum_values_are_the_agreed_words() -> None:
    """The string values of the enums match the register wording."""
    assert [role.value for role in ParameterRole] == [
        "measurement",
        "judgment",
        "exclusion criteria",
        "hardware limit",
        "recommendation",
    ]
    assert ParameterSource.DETERMINED_EACH_RUN == "determined each run"
    assert HardcodedKind.PUBLISHED_CONSTANT == "published constant"
    assert EquipmentKind.FILTER_OR_GRATING == "filter or grating"


def test_only_recommendation_is_not_automatic() -> None:
    """Every role except recommendation is automatic."""
    for role in ParameterRole:
        assert role.is_automatic is (role is not ParameterRole.RECOMMENDATION)


BAD_NAMES = ["flat", "Stacking.flat", "stacking.Flat", "1stacking.flat", "stacking..flat"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_bad_names_are_rejected(name: str) -> None:
    """A name must be dotted, lower case, with at least two parts."""
    with pytest.raises(ValidationError):
        _build(name=name)


def test_bad_register_entry_is_rejected() -> None:
    """A register entry must look like ``STK-D12`` or ``AST-045``."""
    assert _build(register_entry="STK-D12").register_entry == "STK-D12"
    with pytest.raises(ValidationError):
        _build(register_entry="stk-12")


def test_empty_unit_and_description_are_rejected() -> None:
    """The unit and the description must not be empty."""
    with pytest.raises(ValidationError):
        _build(unit="")
    with pytest.raises(ValidationError):
        _build(description="")


def test_hardcoded_requires_kind() -> None:
    """Rule 1: a hardcoded value needs a kind."""
    with pytest.raises(ValueError, match="needs a kind"):
        _build(kind=None)


def test_hardcoded_rejects_equipment() -> None:
    """Rule 1: a hardcoded value cannot depend on equipment."""
    with pytest.raises(ValueError, match="cannot depend on equipment"):
        _build(equipment=frozenset({EquipmentKind.CAMERA}))


def test_other_sources_reject_kind() -> None:
    """Rule 2: only a hardcoded value has a kind."""
    with pytest.raises(ValueError, match="only a hardcoded value has a kind"):
        _build(source=ParameterSource.DETERMINED_EACH_RUN, kind=HardcodedKind.RELATIVE)


def test_processing_setting_requires_config_key() -> None:
    """Rule 3: a processing setting needs a configuration key."""
    with pytest.raises(ValueError, match="config_key"):
        _build(source=ParameterSource.PROCESSING_SETTING, kind=None)
    parameter = _build(source=ParameterSource.PROCESSING_SETTING, kind=None, config_key=("Stacking", "noise"))
    assert parameter.config_key == ("Stacking", "noise")


@pytest.mark.parametrize("source", [ParameterSource.EQUIPMENT_PROFILE, ParameterSource.TUNED_PARAMETER])
def test_equipment_and_tuned_sources_require_equipment(source: ParameterSource) -> None:
    """Rule 4: these sources need at least one equipment kind."""
    with pytest.raises(ValueError, match="equipment kind"):
        _build(source=source, kind=None, allowed_range=(0.0, 1.0), minimum_sample=5)


def test_equipment_profile_with_equipment_is_valid() -> None:
    """An equipment profile parameter with equipment is accepted."""
    parameter = _build(
        source=ParameterSource.EQUIPMENT_PROFILE, kind=None, equipment=frozenset({EquipmentKind.MOUNT})
    )
    assert parameter.equipment == frozenset({EquipmentKind.MOUNT})


def _tuned(**overrides: object) -> TunedParameter:
    """Build a valid tuned parameter, with any field replaced.

    Parameters
    ----------
    **overrides : `object`
        Fields to replace.

    Returns
    -------
    parameter : `TunedParameter`
        The parameter.
    """
    fields: dict[str, object] = {
        "source": ParameterSource.TUNED_PARAMETER,
        "kind": None,
        "equipment": frozenset({EquipmentKind.CAMERA}),
        "allowed_range": (0.0, 1.0),
        "minimum_sample": 20,
    }
    fields.update(overrides)
    return _build(**fields)


def test_valid_tuned_parameter_builds() -> None:
    """A tuned parameter with range, sample size and equipment is accepted."""
    assert _tuned().minimum_sample == 20


def test_tuned_requires_range() -> None:
    """Rule 5: a tuned parameter needs an allowed range."""
    with pytest.raises(ValueError, match="allowed_range"):
        _tuned(allowed_range=None)


def test_tuned_range_must_be_ordered() -> None:
    """Rule 5: the low end must be below the high end."""
    with pytest.raises(ValueError, match="low below high"):
        _tuned(allowed_range=(1.0, 1.0))


@pytest.mark.parametrize("sample", [None, 0])
def test_tuned_requires_sample_size(sample: int | None) -> None:
    """Rule 5: the minimum sample must be at least 1."""
    with pytest.raises(ValueError, match="minimum_sample"):
        _tuned(minimum_sample=sample)


def test_tuned_default_must_lie_in_range() -> None:
    """Rule 5: a scalar default must be inside the allowed range."""
    with pytest.raises(ValueError, match="outside allowed_range"):
        _tuned(default=2.0)


def test_hardware_limit_must_refuse() -> None:
    """Rule 6: a hardware limit must refuse when its value is missing."""
    with pytest.raises(ValueError, match="must refuse"):
        _build(role=ParameterRole.HARDWARE_LIMIT)
    parameter = _build(role=ParameterRole.HARDWARE_LIMIT, when_missing=WhenMissing.REFUSE)
    assert parameter.when_missing is WhenMissing.REFUSE


def test_parameter_is_frozen() -> None:
    """A declaration cannot be changed after it is built."""
    parameter = _build()
    with pytest.raises(ValidationError):
        parameter.default = 1.0  # type: ignore[misc]


def test_pending_conversion_is_refused_for_a_hardcoded_value() -> None:
    """A hardcoded value is already final, so it cannot be pending."""
    with pytest.raises(ValueError, match="nothing to convert"):
        TunedParameter(
            name="stacking.example_pending",
            default=1.0,
            unit="px",
            description="An example.",
            role=ParameterRole.MEASUREMENT,
            source=ParameterSource.HARDCODED,
            kind=HardcodedKind.RELATIVE,
            pending_conversion=True,
        )


def test_a_pending_tuned_parameter_needs_no_range_yet() -> None:
    """A value waiting to become tuned is accepted without its range."""
    parameter = TunedParameter(
        name="stacking.example_pending_tuned",
        default=0.8,
        unit="ratio",
        description="An example.",
        role=ParameterRole.EXCLUSION_CRITERIA,
        source=ParameterSource.TUNED_PARAMETER,
        equipment=frozenset({EquipmentKind.MOUNT}),
        pending_conversion=True,
    )
    assert parameter.allowed_range is None


def test_a_tuned_value_may_depend_on_the_site() -> None:
    """A value tuned for the observing site is accepted."""
    parameter = TunedParameter(
        name="spectroscopy.example_site_extinction",
        default=0.2,
        unit="mag per airmass",
        description="An example.",
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.TUNED_PARAMETER,
        equipment=frozenset({EquipmentKind.SITE}),
        pending_conversion=True,
    )
    assert EquipmentKind.SITE in parameter.equipment
