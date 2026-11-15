"""Tests for the links between tuned parameters and the quality models."""

import pytest
from pydantic import ValidationError

from astrometricslib.foundation.parameters import ResolvedValue
from astrometricslib.models.quality.limit import LimitTier, QualityLimit, limit_tier_for
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)


def _spec(**extra: object) -> MetricSpec:
    """Build a minimal metric spec.

    Parameters
    ----------
    **extra : `object`
        Extra fields to set.

    Returns
    -------
    spec : `MetricSpec`
        The spec.
    """
    return MetricSpec(
        name="frame.example",
        definitionVersion=1,
        unit="px",
        checkpoint=Checkpoint.FRAME,
        levels=(SubjectLevel.FRAME,),
        better=Direction.LOWER,
        meaning="Example.",
        estimator="median",
        sampleDefinition="stars",
        band="L",
        computedBy="a.b",
        uncertaintyKind=UncertaintyKind.NONE,
        uncertaintyMethod="none",
        purpose=MetricPurpose.CONTEXT,
        purposeReason="Context.",
        **extra,
    )


def _parameter(**extra: object) -> TunedParameter:
    """Build a hardcoded scalar parameter with a register entry.

    Parameters
    ----------
    **extra : `object`
        Fields that replace the defaults.

    Returns
    -------
    parameter : `TunedParameter`
        The parameter.
    """
    fields: dict[str, object] = {
        "name": "quality.example_limit",
        "default": 2.5,
        "unit": "arcsec",
        "description": "An example limit.",
        "role": ParameterRole.JUDGMENT,
        "source": ParameterSource.HARDCODED,
        "kind": HardcodedKind.METHOD_CHOICE,
        "register_entry": "STK-D12",
    }
    fields.update(extra)
    return TunedParameter(**fields)


def test_defined_by_defaults_to_empty_and_uses_alias() -> None:
    """The field is empty by default and accepts and dumps the alias."""
    assert _spec().defined_by == ()
    spec = _spec(definedBy=("stacking.flat_noise_fraction",))
    assert spec.defined_by == ("stacking.flat_noise_fraction",)
    assert spec.model_dump(by_alias=True)["definedBy"] == ("stacking.flat_noise_fraction",)


def test_defined_by_rejects_bad_name_and_duplicates() -> None:
    """A malformed or repeated name is refused."""
    with pytest.raises(ValidationError):
        _spec(definedBy=("NotDotted",))
    with pytest.raises(ValidationError):
        _spec(definedBy=("a.b", "a.b"))


@pytest.mark.parametrize(
    ("source", "status", "tier"),
    [
        (ParameterSource.EQUIPMENT_PROFILE, None, LimitTier.SENSOR_PROFILE),
        (ParameterSource.TUNED_PARAMETER, None, LimitTier.TUNED_PARAMETER),
        (ParameterSource.PROCESSING_SETTING, None, LimitTier.PROCESSING_SETTING),
        (ParameterSource.DETERMINED_EACH_RUN, None, LimitTier.PHYSICAL_BUDGET),
        (ParameterSource.HARDCODED, "Measured", LimitTier.VALIDATED_DATASET),
        (ParameterSource.HARDCODED, "Partly measured", LimitTier.DESIGN_ESTIMATE),
        (ParameterSource.HARDCODED, "Design estimate", LimitTier.DESIGN_ESTIMATE),
        (ParameterSource.HARDCODED, None, LimitTier.DESIGN_ESTIMATE),
    ],
)
def test_limit_tier_for(source: ParameterSource, status: str | None, tier: LimitTier) -> None:
    """Every source and status row maps to its tier."""
    assert limit_tier_for(source, status) is tier


def test_from_parameter_measured_hardcoded() -> None:
    """A measured hardcoded value becomes a validated-dataset limit."""
    resolved = ResolvedValue(name="quality.example_limit", value=2.5, origin=ParameterSource.HARDCODED)
    limit = QualityLimit.from_parameter(_parameter(), resolved, status="Measured")
    assert limit.value == pytest.approx(2.5)
    assert limit.unit == "arcsec"
    assert limit.tier is LimitTier.VALIDATED_DATASET
    assert limit.register_entry == "STK-D12"
    assert limit.derivation == "An example limit."


def test_from_parameter_default_origin_uses_hardcoded_tier() -> None:
    """A default standing in for a missing value takes the hardcoded tier."""
    parameter = _parameter(
        source=ParameterSource.PROCESSING_SETTING,
        kind=None,
        config_key=("Section", "key"),
    )
    resolved = ResolvedValue(name=parameter.name, value=2.5, origin="default", notice="missing")
    limit = QualityLimit.from_parameter(parameter, resolved, derivation="Because.")
    assert limit.tier is LimitTier.DESIGN_ESTIMATE
    assert limit.derivation == "Because."


def test_from_parameter_validated_needs_register_entry() -> None:
    """The validated-dataset rule still needs a register entry."""
    parameter = _parameter(register_entry=None)
    resolved = ResolvedValue(name=parameter.name, value=2.5, origin=ParameterSource.HARDCODED)
    with pytest.raises(ValidationError):
        QualityLimit.from_parameter(parameter, resolved, status="Measured")


def test_from_parameter_rejects_tuple() -> None:
    """A tuple value cannot be a single limit."""
    parameter = _parameter(default=(1.0, 2.0))
    resolved = ResolvedValue(name=parameter.name, value=(1.0, 2.0), origin=ParameterSource.HARDCODED)
    with pytest.raises(ValueError, match="single number"):
        QualityLimit.from_parameter(parameter, resolved)
