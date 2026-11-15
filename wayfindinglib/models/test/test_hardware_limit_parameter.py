"""Tests for `HardwareLimitParameter`."""

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from astrometricslib.exceptions import ParameterUnavailableError
from astrometricslib.parameters import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    WhenMissing,
    resolve_parameter,
)
from wayfindinglib.models.policy.delegation import ObservatoryCapability
from wayfindinglib.models.policy.hardware_limit_parameter import HardwareLimitParameter


def _limit(**overrides: object) -> HardwareLimitParameter:
    """Build a valid hardware limit, with fields replaced.

    Parameters
    ----------
    **overrides : `object`
        Fields to replace.

    Returns
    -------
    parameter : `HardwareLimitParameter`
        The parameter.
    """
    fields: dict[str, object] = {
        "name": "mount.maximum_slew_rate",
        "default": 3.0,
        "unit": "deg/s",
        "description": "The fastest slew the mount may be asked for.",
        "role": ParameterRole.HARDWARE_LIMIT,
        "source": ParameterSource.EQUIPMENT_PROFILE,
        "equipment": frozenset({EquipmentKind.MOUNT}),
        "when_missing": WhenMissing.REFUSE,
        "capability": ObservatoryCapability.MOUNT_CONTROL,
    }
    fields.update(overrides)
    return HardwareLimitParameter(**fields)  # type: ignore[arg-type]


class _NoEquipmentValue:
    """An equipment provider that has no value."""

    def equipment_value(self, parameter: TunedParameter) -> None:
        """Return no value for any parameter."""
        return None


def test_valid_hardware_limit() -> None:
    """A hardware limit with the right role and refusal is accepted."""
    parameter = _limit()
    assert parameter.capability is ObservatoryCapability.MOUNT_CONTROL


def test_wrong_role_is_refused() -> None:
    """A role other than hardware limit is refused."""
    with pytest.raises(ValidationError, match="hardware limit"):
        _limit(role=ParameterRole.JUDGMENT)


def test_fall_back_is_refused() -> None:
    """A hardware limit that falls back to a default is refused."""
    with pytest.raises(ValidationError, match="refuse"):
        _limit(when_missing=WhenMissing.FALL_BACK)


def test_missing_equipment_value_raises_public_error() -> None:
    """A missing equipment value raises the public unavailable error."""
    with pytest.raises(ParameterUnavailableError):
        resolve_parameter(_limit(), equipment=_NoEquipmentValue())


def test_subclass_only_adds_fields() -> None:
    """The subclass keeps every base field and defines only validators."""
    base_fields = TunedParameter.model_fields
    sub_fields = HardwareLimitParameter.model_fields
    for name, base_field in base_fields.items():
        assert name in sub_fields
        assert sub_fields[name].annotation == base_field.annotation
    assert set(sub_fields) - set(base_fields) == {"capability"}

    own_members: dict[str, Any] = {
        name: member
        for name, member in vars(HardwareLimitParameter).items()
        if not name.startswith("__")
        and (inspect.isfunction(member) or isinstance(member, (classmethod, staticmethod, property)))
    }
    decorators = HardwareLimitParameter.__pydantic_decorators__
    validator_names = set(decorators.model_validators) | set(decorators.field_validators)
    assert set(own_members) <= validator_names
