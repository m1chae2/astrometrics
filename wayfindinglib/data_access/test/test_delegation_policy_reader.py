"""Purpose: Unit tests for delegation policy resolution and validation.

Description: Verifies get_delegation_policy() defaults to all-DELEGATED
when unconfigured, validate_delegation_policy() rejects each of its
three snapshot-checkable rules, and promote_capability() enforces
shadow precedence at the point of transition plus re-validates the
resulting policy.
"""

import pytest

from wayfindinglib.data_access.delegation_policy_reader import (
    DelegationPolicyValidationError,
    get_delegation_policy,
    promote_capability,
    validate_delegation_policy,
)
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)


@pytest.fixture
def isolated_butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by a fully isolated temporary database.

    Overrides `_find_config_file` directly via `monkeypatch.setattr`
    rather than the `ASTROMETRICS_CONFIG_PATH` env var, which
    `astrometricslib/conftest.py`'s session-scoped class-level override
    makes silently ineffective when this session also collects
    `astrometricslib/`.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _policy(*entries: CapabilityDelegation) -> DelegationPolicy:
    return DelegationPolicy(id="default", capability_delegations=list(entries))


def test_get_delegation_policy_defaults_all_delegated_when_unconfigured(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no persisted policy resolves to an all-DELEGATED policy."""
    policy = get_delegation_policy(isolated_butler)
    assert policy.state_for(ObservatoryCapability.MOUNT_CONTROL) == DelegationState.DELEGATED
    assert policy.capability_delegations == []


def test_get_delegation_policy_reads_persisted_value(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a persisted policy is returned as-is."""
    persisted = _policy(
        CapabilityDelegation(
            capability=ObservatoryCapability.MOUNT_CONTROL, state=DelegationState.AUTHORITATIVE
        )
    )
    isolated_butler.put(persisted, "delegation_policy", {"id": "default"})
    policy = get_delegation_policy(isolated_butler)
    assert policy.is_authoritative(ObservatoryCapability.MOUNT_CONTROL) is True


def test_validate_rejects_shadowed_safety():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify OBSERVATORY_SAFETY may never be SHADOWED."""
    policy = _policy(
        CapabilityDelegation(
            capability=ObservatoryCapability.OBSERVATORY_SAFETY, state=DelegationState.SHADOWED
        )
    )
    with pytest.raises(DelegationPolicyValidationError, match="OBSERVATORY_SAFETY"):
        validate_delegation_policy(policy, has_guider_calibration=True, has_focus_model=True)


def test_validate_rejects_capture_orchestration_without_dependencies_authoritative():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify CAPTURE_ORCHESTRATION needs alignment/guiding/focus ready."""
    policy = _policy(
        CapabilityDelegation(
            capability=ObservatoryCapability.CAPTURE_ORCHESTRATION, state=DelegationState.AUTHORITATIVE
        )
    )
    with pytest.raises(DelegationPolicyValidationError, match="CAPTURE_ORCHESTRATION"):
        validate_delegation_policy(policy, has_guider_calibration=True, has_focus_model=True)


def test_validate_accepts_capture_orchestration_when_dependencies_authoritative():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify CAPTURE_ORCHESTRATION passes once dependencies are ready."""
    policy = _policy(
        CapabilityDelegation(
            capability=ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, state=DelegationState.AUTHORITATIVE
        ),
        CapabilityDelegation(
            capability=ObservatoryCapability.AUTOGUIDING, state=DelegationState.AUTHORITATIVE
        ),
        CapabilityDelegation(capability=ObservatoryCapability.AUTOFOCUS, state=DelegationState.AUTHORITATIVE),
        CapabilityDelegation(
            capability=ObservatoryCapability.CAPTURE_ORCHESTRATION, state=DelegationState.AUTHORITATIVE
        ),
    )
    validate_delegation_policy(policy, has_guider_calibration=True, has_focus_model=True)  # does not raise


def test_validate_rejects_autoguiding_without_calibration():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify AUTOGUIDING needs a GuiderCalibration to leave DELEGATED."""
    policy = _policy(
        CapabilityDelegation(capability=ObservatoryCapability.AUTOGUIDING, state=DelegationState.SHADOWED)
    )
    with pytest.raises(DelegationPolicyValidationError, match="AUTOGUIDING"):
        validate_delegation_policy(policy, has_guider_calibration=False, has_focus_model=True)


def test_validate_rejects_autofocus_without_focus_model():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify AUTOFOCUS cannot leave DELEGATED without a FocusModel."""
    policy = _policy(
        CapabilityDelegation(capability=ObservatoryCapability.AUTOFOCUS, state=DelegationState.SHADOWED)
    )
    with pytest.raises(DelegationPolicyValidationError, match="AUTOFOCUS"):
        validate_delegation_policy(policy, has_guider_calibration=True, has_focus_model=False)


def test_promote_rejects_authoritative_without_prior_shadow():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a capability cannot jump DELEGATED to AUTHORITATIVE directly."""
    policy = _policy()  # AUTOGUIDING defaults to DELEGATED
    with pytest.raises(DelegationPolicyValidationError, match="SHADOWED"):
        promote_capability(
            policy,
            ObservatoryCapability.AUTOGUIDING,
            DelegationState.AUTHORITATIVE,
            has_guider_calibration=True,
        )


def test_promote_allows_delegated_to_shadowed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify DELEGATED -> SHADOWED is always allowed for a correction."""
    policy = _policy()
    promoted = promote_capability(
        policy, ObservatoryCapability.AUTOGUIDING, DelegationState.SHADOWED, has_guider_calibration=True
    )
    assert promoted.is_shadowed(ObservatoryCapability.AUTOGUIDING) is True


def test_promote_allows_shadowed_to_authoritative():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify SHADOWED -> AUTHORITATIVE satisfies shadow precedence."""
    policy = _policy(
        CapabilityDelegation(capability=ObservatoryCapability.AUTOGUIDING, state=DelegationState.SHADOWED)
    )
    promoted = promote_capability(
        policy, ObservatoryCapability.AUTOGUIDING, DelegationState.AUTHORITATIVE, has_guider_calibration=True
    )
    assert promoted.is_authoritative(ObservatoryCapability.AUTOGUIDING) is True


def test_promote_mount_control_exempt_from_shadow_precedence():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify MOUNT_CONTROL goes DELEGATED -> AUTHORITATIVE directly."""
    policy = _policy()
    promoted = promote_capability(policy, ObservatoryCapability.MOUNT_CONTROL, DelegationState.AUTHORITATIVE)
    assert promoted.is_authoritative(ObservatoryCapability.MOUNT_CONTROL) is True


def test_promote_preserves_other_capabilities_unchanged():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify promoting one capability does not disturb another's state."""
    policy = _policy(
        CapabilityDelegation(
            capability=ObservatoryCapability.MOUNT_CONTROL, state=DelegationState.AUTHORITATIVE
        )
    )
    promoted = promote_capability(
        policy, ObservatoryCapability.AUTOGUIDING, DelegationState.SHADOWED, has_guider_calibration=True
    )
    assert promoted.is_authoritative(ObservatoryCapability.MOUNT_CONTROL) is True
    assert promoted.is_shadowed(ObservatoryCapability.AUTOGUIDING) is True


def test_promote_rejects_when_resulting_policy_violates_snapshot_rule():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify promote_capability re-validates the resulting policy.

    Promoting AUTOGUIDING to SHADOWED without a calibration should fail
    even though the shadow-precedence check itself would pass.
    """
    policy = _policy()
    with pytest.raises(DelegationPolicyValidationError, match="AUTOGUIDING"):
        promote_capability(
            policy, ObservatoryCapability.AUTOGUIDING, DelegationState.SHADOWED, has_guider_calibration=False
        )
