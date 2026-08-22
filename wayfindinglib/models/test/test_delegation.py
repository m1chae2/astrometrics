"""Purpose: Unit tests for capability delegation domain models.

Description: Verifies DelegationPolicy.state_for() defaults an
unconfigured capability to DELEGATED, and that is_authoritative()/
is_shadowed() correctly read a configured entry.
"""

from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)


def test_state_for_defaults_to_delegated_when_unconfigured():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an empty policy resolves every capability to DELEGATED."""
    policy = DelegationPolicy(id="policy1")
    assert policy.state_for(ObservatoryCapability.MOUNT_CONTROL) == DelegationState.DELEGATED
    assert policy.is_authoritative(ObservatoryCapability.MOUNT_CONTROL) is False
    assert policy.is_shadowed(ObservatoryCapability.MOUNT_CONTROL) is False


def test_state_for_reads_configured_entry():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a configured entry overrides the DELEGATED default."""
    policy = DelegationPolicy(
        id="policy1",
        capability_delegations=[
            CapabilityDelegation(
                capability=ObservatoryCapability.MOUNT_CONTROL,
                state=DelegationState.AUTHORITATIVE,
            )
        ],
    )
    assert policy.is_authoritative(ObservatoryCapability.MOUNT_CONTROL) is True
    assert policy.is_shadowed(ObservatoryCapability.MOUNT_CONTROL) is False
    # An unconfigured capability still defaults to DELEGATED.
    assert policy.state_for(ObservatoryCapability.AUTOGUIDING) == DelegationState.DELEGATED


def test_is_shadowed_true_for_shadowed_capability():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify is_shadowed() correctly identifies a SHADOWED entry."""
    policy = DelegationPolicy(
        id="policy1",
        capability_delegations=[
            CapabilityDelegation(
                capability=ObservatoryCapability.AUTOGUIDING,
                state=DelegationState.SHADOWED,
            )
        ],
    )
    assert policy.is_shadowed(ObservatoryCapability.AUTOGUIDING) is True
    assert policy.is_authoritative(ObservatoryCapability.AUTOGUIDING) is False


def test_all_six_capabilities_distinct():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify all six delegation-tracked capabilities are distinct."""
    capabilities = {
        ObservatoryCapability.MOUNT_CONTROL,
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        ObservatoryCapability.AUTOGUIDING,
        ObservatoryCapability.AUTOFOCUS,
        ObservatoryCapability.CAPTURE_ORCHESTRATION,
        ObservatoryCapability.OBSERVATORY_SAFETY,
    }
    assert len(capabilities) == 6
