"""Purpose: Tests for the efficiency, loss account and capture summary models.

Description: Checks the factor, result and loss account validators, the
rule that a factor is above 0 and at most 1, the rule that efficiency and
its effective time appear together, and JSON round trips.
"""

import pytest
from pydantic import ValidationError

from astrometricslib import CaptureSetting
from wayfindinglib.models.quality.capture_summary import CaptureSettingSummary
from wayfindinglib.models.quality.efficiency import (
    EfficiencyFactor,
    EfficiencyReference,
    EfficiencyReferenceKind,
    EfficiencyResult,
    LossAccount,
    LossShare,
    NoiseRegime,
    ScienceGoal,
)


def _reference() -> EfficiencyReference:
    """Build a physical reference with one factor left out.

    Returns
    -------
    reference : `EfficiencyReference`
        A valid reference.
    """
    return EfficiencyReference(
        name="physical",
        version=1,
        kind=EfficiencyReferenceKind.PHYSICAL,
        factors={"star_width_arcsec": 1.6},
        not_included={"transparency": "No zero point that is not exploratory exists yet."},
        register_entry="WAY-D01",
    )


def test_a_factor_is_above_zero_and_at_most_one() -> None:
    """A factor of 0 or above 1 is refused; 1 is allowed with a cap reason."""
    kwargs = {"cause": "seeing", "included": True, "recoverable_with_time": True, "model_derived": False}
    assert EfficiencyFactor(
        value=1.0, capped_reason="Better than the reference.", **kwargs
    ).value == pytest.approx(1.0)
    for bad in (0.0, 1.01, -0.5):
        with pytest.raises(ValidationError):
            EfficiencyFactor(value=bad, **kwargs)


def test_included_factor_needs_a_value_and_excluded_factor_a_reason() -> None:
    """An included factor has a value; a factor left out says why."""
    kwargs = {"cause": "seeing", "recoverable_with_time": False, "model_derived": False}
    with pytest.raises(ValidationError, match="must have a value"):
        EfficiencyFactor(included=True, **kwargs)
    with pytest.raises(ValidationError, match="not_included_reason"):
        EfficiencyFactor(included=False, **kwargs)
    factor = EfficiencyFactor(included=False, not_included_reason="Seeing is not a time loss here.", **kwargs)
    assert factor.value is None


def test_result_effective_time_follows_eta() -> None:
    """Eta and the efficiency effective time are set together or not at all."""
    base = {
        "goal": ScienceGoal.FAINT_POINT_SOURCE,
        "regime": NoiseRegime.SKY_LIMITED,
        "reference": _reference(),
        "captured_seconds": 3600.0,
    }
    result = EfficiencyResult(eta=0.5, efficiency_effective_seconds=1800.0, **base)
    dumped = result.model_dump(by_alias=True, mode="json")
    assert dumped["efficiencyEffectiveSeconds"] == pytest.approx(1800.0)
    assert EfficiencyResult.model_validate(dumped) == result
    assert EfficiencyResult(**base).eta is None
    with pytest.raises(ValidationError, match="both"):
        EfficiencyResult(eta=0.5, **base)
    with pytest.raises(ValidationError):
        EfficiencyResult(eta=1.5, efficiency_effective_seconds=1.0, **base)


def test_noise_regime_has_the_four_documented_values() -> None:
    """The regimes are sky, read noise, source and unknown."""
    assert {member.value for member in NoiseRegime} == {
        "sky_limited",
        "read_noise_limited",
        "source_limited",
        "unknown",
    }


def test_loss_account_needs_reasons_and_round_trips() -> None:
    """The account keeps the unknown part and transparency honest."""
    account = LossAccount(
        goal=ScienceGoal.FAINT_POINT_SOURCE,
        reference_name="physical",
        set_aside_seconds=300.0,
        set_aside=[LossShare(cause="cloud", seconds=300.0, recoverable_with_time=True, model_derived=False)],
        kept_frame_deficit=[
            LossShare(cause="seeing", seconds=120.0, recoverable_with_time=True, model_derived=False)
        ],
        cause_unknown_seconds=None,
        cause_unknown_reason="Efficiency is not measured independently of the causes.",
        transparency_included=False,
        transparency_reason="No zero point that is not exploratory exists yet.",
    )
    dumped = account.model_dump(by_alias=True, mode="json")
    assert dumped["causeUnknownSeconds"] is None
    assert LossAccount.model_validate(dumped) == account
    with pytest.raises(ValidationError):
        LossAccount.model_validate({**dumped, "causeUnknownReason": ""})


def test_capture_setting_summary_round_trips() -> None:
    """The summary dumps with aliases and loads back equal."""
    summary = CaptureSettingSummary(
        capture_setting=CaptureSetting(camera="ASI533MM", exposure_seconds=60.0),
        frames_taken=40,
        nights=2,
        frames_set_aside=3,
        captured_seconds=2400.0,
        weight_effective_seconds=2100.0,
    )
    dumped = summary.model_dump(by_alias=True, mode="json")
    assert dumped["framesSetAside"] == 3
    assert dumped["efficiencyEffectiveSeconds"] is None
    assert CaptureSettingSummary.model_validate(dumped) == summary
    with pytest.raises(ValidationError):
        CaptureSettingSummary.model_validate({**dumped, "framesTaken": -1})
