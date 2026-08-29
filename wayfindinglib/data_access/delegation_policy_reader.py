"""Purpose: Delegation Policy Resolution.

Description: Reads the recorded `DelegationPolicy` -- which system
performs each hardware-facing capability -- defaulting every capability
to `DELEGATED` when unconfigured, the safe default since `DELEGATED`
issues no command this system did not previously issue
(`Wayfinding_Library_Architecture.md` §2.2.2).

Validity rules (shadow precedence, the `OBSERVATORY_SAFETY` shadow
exemption, capture orchestration's dependency ordering, and calibration
presence) are enforced here, not by the schema itself -- `DelegationPolicy`
the model only defines the shape.

Shadow precedence is enforceable only at the moment of *transition*,
not by inspecting a policy snapshot in isolation: "was this capability
ever shadowed" is a question about history, and a `DelegationPolicy`
carries only each capability's current entry. `validate_delegation_policy`
therefore checks the rules a flat snapshot *can* prove (the safety
exemption, capture's dependency ordering, calibration presence);
`promote_capability` checks shadow precedence by comparing the prior
state it is passed against the requested new state, which is the one
place both are actually available together.
"""

from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)

_DEFAULT_POLICY_ID = "default"

_CORRECTION_CAPABILITIES = (
    ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
    ObservatoryCapability.AUTOGUIDING,
    ObservatoryCapability.AUTOFOCUS,
)
"""Capabilities that compute a correction and must therefore pass through
SHADOWED before reaching AUTHORITATIVE (Design Invariant "Computed
Corrections Pass Through Shadow",
`Wayfinding_Library_Architecture.md` §2.1.2)."""


class DelegationPolicyValidationError(ValueError):
    """Raised when a delegation policy violates one of its validity rules."""


def get_delegation_policy(butler) -> DelegationPolicy:  # ruff: ignore[missing-type-function-argument]
    """Return the recorded `DelegationPolicy`, or an all-`DELEGATED` default.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The storage layer to read from.

    Returns
    -------
    policy : `DelegationPolicy`
        The recorded policy, or an empty one -- which resolves every
        capability to `DELEGATED` via `DelegationPolicy.state_for`'s own
        default -- when none has been configured yet.
    """
    policy = butler.get("delegation_policy", {"id": _DEFAULT_POLICY_ID})
    if policy is not None:
        return policy
    return DelegationPolicy(id=_DEFAULT_POLICY_ID)


def validate_delegation_policy(
    policy: DelegationPolicy,
    *,
    has_guider_calibration: bool,
    has_focus_model: bool,
) -> None:
    """Enforce the delegation policy's snapshot-checkable validity rules.

    Parameters
    ----------
    policy : `DelegationPolicy`
        The candidate policy to validate.
    has_guider_calibration : `bool`
        Whether a `GuiderCalibration` exists for the active equipment
        pairing -- required before `AUTOGUIDING` may leave `DELEGATED`.
    has_focus_model : `bool`
        Whether a `FocusModel` exists for the active equipment pairing
        -- required before `AUTOFOCUS` may leave `DELEGATED`.

    Raises
    ------
    DelegationPolicyValidationError
        Raised if any of the three rules below is violated
        (`Wayfinding_Library_Architecture.md` §2.2.2). Shadow
        precedence (a fourth rule) is not checkable from a snapshot and
        is instead enforced by `promote_capability`.

        1. `OBSERVATORY_SAFETY` may never be `SHADOWED`.
        2. `CAPTURE_ORCHESTRATION` may not leave `DELEGATED` while any
           of alignment, guiding, or focus is not `AUTHORITATIVE`.
        3. `AUTOGUIDING` requires a `GuiderCalibration`, and
           `AUTOFOCUS` a `FocusModel`, before either may leave
           `DELEGATED`.
    """
    state_for = {entry.capability: entry.state for entry in policy.capability_delegations}

    if state_for.get(ObservatoryCapability.OBSERVATORY_SAFETY) == DelegationState.SHADOWED:
        raise DelegationPolicyValidationError("OBSERVATORY_SAFETY may never be SHADOWED")

    capture_state = state_for.get(ObservatoryCapability.CAPTURE_ORCHESTRATION, DelegationState.DELEGATED)
    if capture_state != DelegationState.DELEGATED:
        for capability in _CORRECTION_CAPABILITIES:
            if state_for.get(capability) != DelegationState.AUTHORITATIVE:
                raise DelegationPolicyValidationError(
                    f"CAPTURE_ORCHESTRATION may not leave DELEGATED while {capability} is not AUTHORITATIVE"
                )

    guiding_state = state_for.get(ObservatoryCapability.AUTOGUIDING, DelegationState.DELEGATED)
    if guiding_state != DelegationState.DELEGATED and not has_guider_calibration:
        raise DelegationPolicyValidationError(
            "AUTOGUIDING may not leave DELEGATED without a GuiderCalibration"
        )

    focus_state = state_for.get(ObservatoryCapability.AUTOFOCUS, DelegationState.DELEGATED)
    if focus_state != DelegationState.DELEGATED and not has_focus_model:
        raise DelegationPolicyValidationError("AUTOFOCUS may not leave DELEGATED without a FocusModel")


def promote_capability(
    policy: DelegationPolicy,
    capability: ObservatoryCapability,
    new_state: DelegationState,
    *,
    evidence_note: str = "",
    has_guider_calibration: bool = False,
    has_focus_model: bool = False,
) -> DelegationPolicy:
    """Return a new `DelegationPolicy` with `capability` moved to `new_state`.

    Checks shadow precedence against `policy`'s *prior* state for
    `capability` -- the one place the old and new states are both
    available together -- then re-validates the full resulting policy
    against every snapshot-checkable rule. A promotion that would
    violate any rule is rejected rather than applied.

    Returns
    -------
    policy : `DelegationPolicy`
        The resulting policy with `capability` moved to `new_state`.

    Raises
    ------
    DelegationPolicyValidationError
        Raised if the transition or the resulting policy would violate
        a validity rule.
    """
    prior_state = policy.state_for(capability)
    if (
        capability in _CORRECTION_CAPABILITIES
        and new_state == DelegationState.AUTHORITATIVE
        and prior_state != DelegationState.SHADOWED
    ):
        raise DelegationPolicyValidationError(
            f"{capability} may not become AUTHORITATIVE except from SHADOWED (current state is {prior_state})"
        )

    remaining = [e for e in policy.capability_delegations if e.capability != capability]
    new_entry = CapabilityDelegation(
        capability=capability, state=new_state, promotion_evidence_note=evidence_note
    )
    candidate = DelegationPolicy(id=policy.id, capability_delegations=[*remaining, new_entry])
    validate_delegation_policy(
        candidate, has_guider_calibration=has_guider_calibration, has_focus_model=has_focus_model
    )
    return candidate
