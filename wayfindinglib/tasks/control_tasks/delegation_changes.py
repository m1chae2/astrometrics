"""Purpose: Change who may command the observatory, for `control.safety`.

Description: The delegation policy says, per capability, whether this app
only watches (`DELEGATED`), computes beside the program in charge
(`SHADOWED`) or commands the hardware (`AUTHORITATIVE`). `set_mode` moves
every capability toward one of two modes at once: ``"monitor"`` (all
`DELEGATED`) or ``"controller"`` (all toward `AUTHORITATIVE`).
`update_policy` changes chosen capabilities. Both keep every rule of the policy
(`capability_promotion`) and save the result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.models.policy.delegation import DelegationState
from wayfindinglib.tasks.control_tasks.capability_promotion import (
    BulkDelegationOutcome,
    apply_promotion_decision,
    set_all_capabilities,
)

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.policy.delegation import DelegationPolicy, ObservatoryCapability

__all__ = ["MODES", "set_mode", "update_policy"]

MODES = {"monitor": DelegationState.DELEGATED, "controller": DelegationState.AUTHORITATIVE}
"""Each mode and the state it moves every capability toward."""


def set_mode(
    context: ControlContext, mode: str, note: str = "", shadow_first: bool = False
) -> BulkDelegationOutcome:
    """Move every capability toward one mode and save the policy.

    ``"monitor"`` always fully succeeds. With ``"controller"``, a
    capability that may not move yet (a correction not yet `SHADOWED`, or
    capture before the corrections it depends on) is listed in
    ``rejected``, never skipped silently or forced.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage and the saved guider and focus models.
    mode : `str`
        ``"monitor"`` or ``"controller"``.
    note : `str`, optional
        Why, in the operator's words.
    shadow_first : `bool`, optional
        With ``"controller"``, first move each correction capability still
        `DELEGATED` to `SHADOWED`, the step the rules need before
        `AUTHORITATIVE`.

    Returns
    -------
    outcome : `BulkDelegationOutcome`
        Every capability's new state, and why any were refused.

    Raises
    ------
    InvalidArgumentError
        If `mode` is not ``"monitor"`` or ``"controller"``.
    """
    if mode not in MODES:
        raise InvalidArgumentError('mode must be "monitor" or "controller".')
    return set_all_capabilities(
        context.butler,
        MODES[mode],
        evidence_note=note,
        has_guider_calibration=context.active_guider_calibration() is not None,
        has_focus_model=context.active_focus_model() is not None,
        shadow_first=shadow_first and mode == "controller",
    )


def update_policy(
    context: ControlContext,
    policy: DelegationPolicy,
    changes: dict[ObservatoryCapability, DelegationState],
    note: str = "",
) -> DelegationPolicy:
    """Change capabilities' states, check the rules and save the policy.

    The changes are applied one at a time, in the order given; each one is
    checked against the rules of the policy before it is saved.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage and the saved guider and focus models.
    policy : `DelegationPolicy`
        The saved policy, from ``control.safety.get_status``.
    changes : `dict` [`ObservatoryCapability`, `DelegationState`]
        Each capability to change, with its new state.
    note : `str`, optional
        Why, in the operator's words.

    Returns
    -------
    policy : `DelegationPolicy`
        The saved policy.

    Raises
    ------
    InvalidArgumentError
        If `policy` is not the saved policy, or `changes` is empty.

    Notes
    -----
    Raises `DelegationPolicyValidationError` when a change breaks a rule of
    the policy; that change and the ones after it are not saved.
    """
    if policy.id != context.delegation_policy().id:
        raise InvalidArgumentError(f"{policy.id!r} is not the saved delegation policy.")
    if not changes:
        raise InvalidArgumentError("changes names no capability.")
    for capability, state in changes.items():
        policy = apply_promotion_decision(
            context.butler,
            capability,
            state,
            evidence_note=note,
            has_guider_calibration=context.active_guider_calibration() is not None,
            has_focus_model=context.active_focus_model() is not None,
        )
    return policy
