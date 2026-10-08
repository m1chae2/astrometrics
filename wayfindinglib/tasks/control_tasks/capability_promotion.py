"""Purpose: Capability Promotion Decisions.

Description: Summarizes recorded `DivergenceRecord` evidence against a
phase gate and applies an operator's decision, per
`Wayfinding_Library_Architecture.md`. Building on
`data_access/delegation_policy_reader.py`'s `promote_capability` (which
already re-validates shadow precedence and every snapshot-checkable
policy rule before returning a candidate policy), this module adds the
two pieces that sit above it: turning raw divergence evidence into an
agreement-rate summary an operator can judge a phase gate against, and
recording the validated decision.
"""

from dataclasses import dataclass, field

from wayfindinglib.data_access.delegation_policy_reader import (
    DelegationPolicyValidationError,
    get_delegation_policy,
    promote_capability,
)
from wayfindinglib.models.policy.delegation import DelegationPolicy, DelegationState, ObservatoryCapability
from wayfindinglib.models.session.divergence import DivergenceRecord

_AUTHORITATIVE_ORDER = (
    ObservatoryCapability.OBSERVATORY_SAFETY,
    ObservatoryCapability.MOUNT_CONTROL,
    ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
    ObservatoryCapability.AUTOGUIDING,
    ObservatoryCapability.AUTOFOCUS,
    ObservatoryCapability.CAPTURE_ORCHESTRATION,
)
"""Order to promote toward AUTHORITATIVE ("controller mode") in: safety
and mount first (no dependency), then the three correction capabilities
(each only succeeds if already SHADOWED -- `promote_capability`'s own
shadow-precedence check), then capture last (only succeeds once the
three corrections above it just became AUTHORITATIVE -- the dependency
rule `validate_delegation_policy` enforces)."""

_DELEGATED_ORDER = tuple(reversed(_AUTHORITATIVE_ORDER))
"""Order to demote toward DELEGATED ("monitoring mode") in -- the exact
reverse, since DELEGATED has no dependency ordering of its own and
always succeeds, but demoting capture before the capabilities it
depends on keeps the policy in a valid state at every intermediate
step rather than only at the end."""


@dataclass(frozen=True)
class BulkDelegationOutcome:
    """The result of a `set_all_capabilities` bulk delegation-state change.

    A capability that could not legally reach `target_state` is
    recorded in `rejected` with the validation error explaining why,
    never silently skipped or forced -- see
    `Wayfinding_Library_Architecture.md`'s `set_all_capabilities`.
    """

    applied: dict[ObservatoryCapability, DelegationState] = field(default_factory=dict)
    rejected: dict[ObservatoryCapability, str] = field(default_factory=dict)


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


def set_all_capabilities(
    butler,  # ruff: ignore[missing-type-function-argument]
    target_state: DelegationState,
    *,
    evidence_note: str = "",
    has_guider_calibration: bool = False,
    has_focus_model: bool = False,
) -> BulkDelegationOutcome:
    """Move every capability toward `target_state`, one at a time.

    A convenience layered on top of the existing per-capability
    `apply_promotion_decision`, mirroring how N.I.N.A. bundles
    per-device driver choices into one profile switch -- this does
    **not** bypass shadow precedence, the safety shadow exemption,
    capture's dependency ordering, or calibration presence. Each
    capability is promoted individually, in the order `target_state`
    requires (see `_AUTHORITATIVE_ORDER`/`_DELEGATED_ORDER`), and a
    capability that cannot legally reach `target_state` yet -- e.g. a
    correction capability not already `SHADOWED`, when moving toward
    `AUTHORITATIVE` -- is recorded in `rejected` rather than silently
    skipped or forced.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The storage layer to read the current policy from and write
        each successful transition to.
    target_state : `DelegationState`
        The state to move every capability toward. `AUTHORITATIVE`
        ("controller mode") and `DELEGATED` ("monitoring mode") are the
        two states this is meant for; any other value is promoted in
        the same reverse order `DELEGATED` uses.
    evidence_note : `str`, optional
        A human-readable note recorded on every successful transition.
    has_guider_calibration, has_focus_model : `bool`, optional
        Passed through to each capability's validation.

    Returns
    -------
    outcome : `BulkDelegationOutcome`
        Which capabilities reached `target_state`, and why any that
        didn't were rejected.
    """
    order = _AUTHORITATIVE_ORDER if target_state == DelegationState.AUTHORITATIVE else _DELEGATED_ORDER

    applied: dict[ObservatoryCapability, DelegationState] = {}
    rejected: dict[ObservatoryCapability, str] = {}
    for capability in order:
        try:
            policy = apply_promotion_decision(
                butler,
                capability,
                target_state,
                evidence_note=evidence_note,
                has_guider_calibration=has_guider_calibration,
                has_focus_model=has_focus_model,
            )
            applied[capability] = policy.state_for(capability)
        except DelegationPolicyValidationError as validation_error:
            rejected[capability] = str(validation_error)

    return BulkDelegationOutcome(applied=applied, rejected=rejected)
