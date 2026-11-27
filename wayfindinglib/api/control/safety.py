"""Purpose: `control.safety`, weather, the enclosure, and who may act.

Description: Judges weather readings against the saved safety rules,
opens and closes the enclosure, runs the safe-state sequence, and
changes the delegation policy that says which capabilities this app may
command: `set_mode` for all of them at once, `update` for chosen ones.
`get_status` reads the rules, a fresh weather verdict, the enclosure and
its state, the delegation policy and the agreement evidence for one
capability. Enclosure commands need `OBSERVATORY_SAFETY` to be
`AUTHORITATIVE`.
"""

from datetime import datetime

from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import SafetyStatus
from wayfindinglib.models.policy.delegation import DelegationPolicy, DelegationState, ObservatoryCapability
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyRuleSet, SensorReadings
from wayfindinglib.models.session.safe_state import SafeStateOutcome
from wayfindinglib.tasks.control_tasks.capability_promotion import BulkDelegationOutcome
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps

__all__ = ["SafetyControl"]


class SafetyControl(ControlChild):
    """Keep the observatory safe and decide who may command it."""

    def get_status(
        self, include: list[str] | None = None, capability: ObservatoryCapability | None = None
    ) -> SafetyStatus:
        """Read the safety rules, weather verdict, enclosure and policy.

        Owner:
            wayfindinglib.tasks.control_tasks.control_status.read_safety_status

        Parameters
        ----------
        include : `list` [`str`], optional
            Sections to read: ``rule_set`` (saved), ``assessment`` (reads
            the weather sensors and judges them), ``enclosure`` (the saved
            enclosure), ``enclosure_state`` (reads the enclosure),
            ``delegation_policy`` (saved) and ``divergence`` (saved
            agreement evidence for `capability`). Every section but
            ``divergence`` when omitted, plus ``divergence`` when
            `capability` is given.
        capability : `ObservatoryCapability`, optional
            The capability whose agreement evidence to summarize. Used only
            by ``divergence``, which needs it.

        Returns
        -------
        status : `SafetyStatus`
            The sections read. The others stay `None`.

        Notes
        -----
        Raises `InvalidArgumentError` if ``divergence`` is asked for
        without `capability`, or `capability` is given without it.
        """
        from wayfindinglib.tasks.control_tasks.control_status import read_safety_status

        return read_safety_status(self._context, include, capability)

    def set_mode(self, mode: str, note: str = "", shadow_first: bool = False) -> BulkDelegationOutcome:
        """Move every capability toward a mode and save the policy.

        ``"monitor"`` moves every capability to `DELEGATED`: this app then
        watches and computes but commands nothing, and that always fully
        succeeds. ``"controller"`` moves every capability toward
        `AUTHORITATIVE`; a capability that may not move yet (a correction
        not yet `SHADOWED`, or capture before the corrections it depends
        on) is listed in ``rejected``, never skipped silently or forced.

        Owner:
            wayfindinglib.tasks.control_tasks.delegation_changes.set_mode

        Parameters
        ----------
        mode : `str`
            ``"monitor"`` or ``"controller"``.
        note : `str`, optional
            Why, in the operator's words.
        shadow_first : `bool`, optional
            With ``"controller"``, first move each correction capability
            still `DELEGATED` to `SHADOWED`, the step the rules need before
            `AUTHORITATIVE`. The other rules still apply and still reject.

        Returns
        -------
        outcome : `BulkDelegationOutcome`
            Which capabilities reached the mode's state, and why the others
            did not.

        Notes
        -----
        Raises `InvalidArgumentError` for an unknown mode.
        """
        from wayfindinglib.tasks.control_tasks.delegation_changes import set_mode

        return set_mode(self._context, mode, note=note, shadow_first=shadow_first)

    def update(
        self,
        policy: DelegationPolicy,
        changes: dict[ObservatoryCapability, DelegationState],
        note: str = "",
    ) -> DelegationPolicy:
        """Change capabilities' states in the policy, and save it.

        The policy's rules (shadow before authority, the safety exemption,
        capture's dependencies, calibration presence) are checked for each
        change before it is saved.

        Owner:
            wayfindinglib.tasks.control_tasks.delegation_changes.update_policy

        Parameters
        ----------
        policy : `DelegationPolicy`
            The saved policy, from ``get_status``.
        changes : `dict` [`ObservatoryCapability`, `DelegationState`]
            Each capability to change, with its new state, such as
            ``{ObservatoryCapability.AUTOFOCUS: DelegationState.SHADOWED}``.
        note : `str`, optional
            Why, in the operator's words.

        Returns
        -------
        policy : `DelegationPolicy`
            The saved policy.

        Notes
        -----
        Raises `DelegationPolicyValidationError` when a change breaks a
        rule, and `InvalidArgumentError` when `policy` is not the saved one
        or `changes` is empty.
        """
        from wayfindinglib.tasks.control_tasks.delegation_changes import update_policy

        return update_policy(self._context, policy, changes, note=note)

    def _assess(self, readings: SensorReadings, now: datetime | None = None) -> SafetyAssessment:
        """Judge weather readings against the saved safety rules.

        The verdict waits before it turns safe again after an unsafe
        reading. The time of the last unsafe reading is kept between calls
        made through the same `control`.

        Parameters
        ----------
        readings : `SensorReadings`
            The weather readings.
        now : `datetime`, optional
            The time of the readings. Now when omitted.

        Returns
        -------
        assessment : `SafetyAssessment`
            The verdict, the rule that produced it, and how fresh it is.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.assess_safety(self._context, readings, now)

    def _save_rule_set(self, rule_set: SafetyRuleSet) -> None:
        """Save `rule_set` as the active safety rules.

        Parameters
        ----------
        rule_set : `SafetyRuleSet`
            The rules.
        """
        from wayfindinglib.data_access.safety_policy_reader import save_safety_rule_set

        save_safety_rule_set(self._context.butler, rule_set)

    def _execute_safe_state(self, trigger: str, steps: SafeStateSteps) -> SafeStateOutcome:
        """Run the ordered safe-state sequence and record it.

        Parameters
        ----------
        trigger : `str`
            Why the sequence was started.
        steps : `SafeStateSteps`
            The device operations the sequence calls.

        Returns
        -------
        outcome : `SafeStateOutcome`
            What each step did.
        """
        from wayfindinglib.tasks.control_tasks.safe_state import execute

        return execute(trigger, steps)

    def _open_enclosure(self) -> bool:
        """Open the enclosure.

        Returns
        -------
        success : `bool`
            Whether the open command was issued and confirmed.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.open_enclosure(self._context)

    def _close_enclosure(self) -> bool:
        """Close the enclosure, refusing if the mount is not clear of it.

        Returns
        -------
        success : `bool`
            Whether the close command was issued and confirmed.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.close_enclosure(self._context)
