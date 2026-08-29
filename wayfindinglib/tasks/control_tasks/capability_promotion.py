"""Purpose: Capability Promotion Decisions.

Description: Summarizes recorded `DivergenceRecord` evidence against a
phase gate and applies an operator's decision, per
`Wayfinding_Library_Architecture.md` §2.5.2. Building on
`data_access/delegation_policy_reader.py`'s `promote_capability` (which
already re-validates shadow precedence and every snapshot-checkable
policy rule before returning a candidate policy), this module adds the
two pieces that sit above it: turning raw divergence evidence into an
agreement-rate summary an operator can judge a phase gate against, and
recording the validated decision.
"""

from dataclasses import dataclass

from wayfindinglib.data_access.delegation_policy_reader import (
    get_delegation_policy,
    promote_capability,
)
from wayfindinglib.models.policy.delegation import DelegationPolicy, DelegationState, ObservatoryCapability
from wayfindinglib.models.session.divergence import DivergenceRecord


@dataclass(frozen=True)
class DivergenceEvidenceSummary:
    """An agreement-rate summary of divergence evidence for one capability."""

    capability: ObservatoryCapability
    sample_count: int
    within_tolerance_count: int

    @property
    def agreement_rate(self) -> float:
        """The fraction of samples that fell within tolerance.

        `0.0` when `sample_count` is zero -- no evidence is not
        treated as perfect agreement.
        """
        if self.sample_count == 0:
            return 0.0
        return self.within_tolerance_count / self.sample_count

    def meets_phase_gate(self, minimum_sample_count: int, minimum_agreement_rate: float) -> bool:
        """Return whether this evidence clears a phase gate's thresholds.

        A gate requires both enough samples to be statistically
        meaningful and a high enough agreement rate -- a 100%
        agreement rate from 2 samples proves nothing.

        Returns
        -------
        clears_gate : `bool`
            `True` if both the sample count and agreement rate meet
            or exceed the given thresholds.
        """
        return self.sample_count >= minimum_sample_count and self.agreement_rate >= minimum_agreement_rate


def summarize_divergence_evidence(
    capability: ObservatoryCapability, divergence_records: list[DivergenceRecord]
) -> DivergenceEvidenceSummary:
    """Summarize divergence records for one capability into an agreement rate.

    Parameters
    ----------
    capability : `ObservatoryCapability`
        The capability to summarize evidence for.
    divergence_records : `list` [`DivergenceRecord`]
        Every recorded divergence record (e.g. from
        `butler.get_all("divergence_record")`); records for other
        capabilities are ignored.

    Returns
    -------
    summary : `DivergenceEvidenceSummary`
        The sample count and within-tolerance count for `capability`.
    """
    matching = [record for record in divergence_records if record.capability == capability]
    within_tolerance_count = sum(1 for record in matching if record.within_tolerance)
    return DivergenceEvidenceSummary(
        capability=capability, sample_count=len(matching), within_tolerance_count=within_tolerance_count
    )


def apply_promotion_decision(
    butler,  # ruff: ignore[missing-type-function-argument]
    capability: ObservatoryCapability,
    new_state: DelegationState,
    *,
    evidence_note: str = "",
    has_guider_calibration: bool = False,
    has_focus_model: bool = False,
) -> DelegationPolicy:
    """Apply an operator's promotion decision and record the result.

    Re-validates the full set of delegation policy rules (via
    `promote_capability`, which raises `DelegationPolicyValidationError`
    on any violation) before writing anything, so an invalid decision
    -- one that would violate shadow precedence, the safety exemption,
    capture's dependency ordering, or calibration presence -- is
    rejected rather than recorded.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The storage layer to read the current policy from and
        write the new one to.
    capability : `ObservatoryCapability`
        The capability being promoted or demoted.
    new_state : `DelegationState`
        The requested new delegation state.
    evidence_note : `str`, optional
        A human-readable note on what evidence motivated this decision.
    has_guider_calibration, has_focus_model : `bool`, optional
        Passed through to `promote_capability`'s validation.

    Returns
    -------
    policy : `DelegationPolicy`
        The new policy, already recorded.
    """
    current_policy = get_delegation_policy(butler)
    new_policy = promote_capability(
        current_policy,
        capability,
        new_state,
        evidence_note=evidence_note,
        has_guider_calibration=has_guider_calibration,
        has_focus_model=has_focus_model,
    )
    butler.put(new_policy, "delegation_policy", {"id": new_policy.id})
    return new_policy
