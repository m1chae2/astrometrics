"""Purpose: Capability Delegation Domain Models.

Description: Which system performs one hardware-facing capability, and
since when. Foundation state because every function reads it and none
writes it during operation -- placing it here is what allows a
capability's state to change without any function's structure changing
(`Wayfinding_Library_Architecture.md` §2.1.2, §2.2.2, Design Invariant 5).

Validity rules (shadow precedence, the safety exemption, capture
orchestration's dependency ordering, and calibration presence) are
enforced by the delegation policy reader at load time
(`Wayfinding_Library_Architecture.md` §2.2.2), not by this schema --
this module defines only the shape.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ObservatoryCapability(StrEnum):
    """One hardware-facing capability whose delegation state is tracked.

    Corresponds one-to-one with the functional modules of the incumbent
    software this architecture is designed to replace
    (`Wayfinding_Library_Architecture.md` §2.1.2).
    """

    MOUNT_CONTROL = "MOUNT_CONTROL"
    PLATE_SOLVE_ALIGNMENT = "PLATE_SOLVE_ALIGNMENT"
    AUTOGUIDING = "AUTOGUIDING"
    AUTOFOCUS = "AUTOFOCUS"
    CAPTURE_ORCHESTRATION = "CAPTURE_ORCHESTRATION"
    OBSERVATORY_SAFETY = "OBSERVATORY_SAFETY"


class DelegationState(StrEnum):
    """Which system currently performs a capability.

    `SHADOWED` is not a valid state for `OBSERVATORY_SAFETY`
    (`Wayfinding_Library_Architecture.md` §2.1.2, "Safety Is Never
    Shadowed") -- enforced by the delegation policy reader, not by this
    enum, since the restriction is per-capability rather than global.
    """

    DELEGATED = "DELEGATED"
    SHADOWED = "SHADOWED"
    AUTHORITATIVE = "AUTHORITATIVE"


class CapabilityDelegation(BaseModel):
    """The delegation state of one capability, and since when."""

    model_config = ConfigDict(populate_by_name=True)

    capability: ObservatoryCapability
    state: DelegationState
    effective_since: datetime = Field(default_factory=lambda: datetime.now(UTC))
    promotion_evidence_note: str = Field(default="")


class DelegationPolicy(BaseModel):
    """The delegation state of every capability -- the observatory phase."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    capability_delegations: list[CapabilityDelegation] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def state_for(self, capability: ObservatoryCapability) -> DelegationState:
        """Return the delegation state for `capability`.

        Defaults to `DELEGATED` when no entry is configured -- the safe
        default, since `DELEGATED` issues no command this system did
        not previously issue
        (`Wayfinding_Library_Architecture.md` §2.2.2).

        Returns
        -------
        state : `DelegationState`
            The current delegation state for `capability`.
        """
        for entry in self.capability_delegations:
            if entry.capability == capability:
                return entry.state
        return DelegationState.DELEGATED

    def is_authoritative(self, capability: ObservatoryCapability) -> bool:
        """Return whether `capability` is currently authoritative.

        Returns
        -------
        is_authoritative : `bool`
            Whether `capability`'s state is `AUTHORITATIVE`.
        """
        return self.state_for(capability) == DelegationState.AUTHORITATIVE

    def is_shadowed(self, capability: ObservatoryCapability) -> bool:
        """Return whether `capability` is currently shadowed.

        Returns
        -------
        is_shadowed : `bool`
            Whether `capability`'s state is `SHADOWED`.
        """
        return self.state_for(capability) == DelegationState.SHADOWED
