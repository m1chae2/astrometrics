"""Purpose: Unit tests for capability_promotion.

Description: Verifies divergence evidence summarizes to an agreement
rate a phase gate can be judged against, and that a promotion decision
re-validates policy rules before persisting -- an invalid decision
must not reach the butler at all.
"""

import pytest

from wayfindinglib.data_access.delegation_policy_reader import DelegationPolicyValidationError
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.policy.delegation import DelegationState, ObservatoryCapability
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.tasks.control_tasks.capability_promotion import (
    apply_promotion_decision,
    summarize_divergence_evidence,
)


@pytest.fixture
def isolated_butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by a fully isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        A fresh, isolated butler instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _divergence_record(capability, within_tolerance, comparison_input_id="frame-1") -> DivergenceRecord:  # ruff: ignore[missing-type-function-argument]
    return DivergenceRecord(
        id=f"div-{comparison_input_id}",
        observation_session_id="session-1",
        capability=capability,
        comparison_input_id=comparison_input_id,
        intended_value=1.0,
        observed_value=1.0 if within_tolerance else 5.0,
        divergence_magnitude=0.0 if within_tolerance else 4.0,
        divergence_unit="arcsec",
        tolerance=1.0,
        within_tolerance=within_tolerance,
    )


def test_summarize_divergence_evidence_computes_agreement_rate():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the summary computes sample count and agreement rate."""
    records = [
        _divergence_record(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, True, "f1"),
        _divergence_record(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, True, "f2"),
        _divergence_record(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, False, "f3"),
        _divergence_record(ObservatoryCapability.AUTOGUIDING, True, "f4"),  # different capability
    ]
    summary = summarize_divergence_evidence(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, records)
    assert summary.sample_count == 3
    assert summary.within_tolerance_count == 2
    assert summary.agreement_rate == pytest.approx(2.0 / 3.0)


def test_summarize_divergence_evidence_zero_samples_is_not_perfect_agreement():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no evidence yields a 0.0 agreement rate, not a divide-by-zero."""
    summary = summarize_divergence_evidence(ObservatoryCapability.AUTOFOCUS, [])
    assert summary.sample_count == 0
    assert summary.agreement_rate == pytest.approx(0.0)
    assert summary.meets_phase_gate(minimum_sample_count=1, minimum_agreement_rate=0.9) is False


def test_meets_phase_gate_requires_both_sample_count_and_agreement_rate():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a high agreement rate from too few samples fails the gate."""
    records = [_divergence_record(ObservatoryCapability.AUTOFOCUS, True, "f1")]
    summary = summarize_divergence_evidence(ObservatoryCapability.AUTOFOCUS, records)
    assert summary.agreement_rate == pytest.approx(1.0)
    assert summary.meets_phase_gate(minimum_sample_count=20, minimum_agreement_rate=0.9) is False


def test_apply_promotion_decision_persists_valid_transition(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a valid promotion is written to the butler and returned."""
    policy = apply_promotion_decision(
        isolated_butler,
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        DelegationState.SHADOWED,
        evidence_note="starting shadow validation",
    )
    assert policy.state_for(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT) == DelegationState.SHADOWED

    reloaded = isolated_butler.get("delegation_policy", {"id": policy.id})
    assert reloaded.state_for(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT) == DelegationState.SHADOWED


def test_apply_promotion_decision_rejects_invalid_transition_without_persisting(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify skipping SHADOWED raises and writes nothing to the butler."""
    with pytest.raises(DelegationPolicyValidationError):
        apply_promotion_decision(
            isolated_butler,
            ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
            DelegationState.AUTHORITATIVE,
        )

    reloaded = isolated_butler.get("delegation_policy", {"id": "default"})
    assert reloaded is None


def test_apply_promotion_decision_rejects_safety_shadowed_without_persisting(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify OBSERVATORY_SAFETY may never be SHADOWED, nothing written."""
    with pytest.raises(DelegationPolicyValidationError):
        apply_promotion_decision(
            isolated_butler,
            ObservatoryCapability.OBSERVATORY_SAFETY,
            DelegationState.SHADOWED,
        )

    reloaded = isolated_butler.get("delegation_policy", {"id": "default"})
    assert reloaded is None
