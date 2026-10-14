"""Purpose: `control.safety`, weather, the enclosure, and who may act.

Description: Judges weather readings against the saved safety rules,
opens and closes the enclosure, runs the safe-state sequence, and
changes the delegation policy that says which capabilities this app may
command. `status` reads the rules, a fresh weather verdict, the
enclosure and its state, the delegation policy and the agreement
evidence for one capability. Enclosure commands need
`OBSERVATORY_SAFETY` to be `AUTHORITATIVE`.
"""

import dataclasses
from datetime import datetime

from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import SafetyStatus
from wayfindinglib.models.policy.delegation import DelegationPolicy, DelegationState, ObservatoryCapability
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyRuleSet
from wayfindinglib.models.session.safe_state import SafeStateOutcome
from wayfindinglib.tasks.control_tasks.capability_promotion import BulkDelegationOutcome
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps
from wayfindinglib.tasks.control_tasks.safety_monitor import SensorReadings

__all__ = ["SafetyControl"]

STATUS_SECTIONS = (
    "rule_set",
    "assessment",
    "enclosure",
    "enclosure_state",
    "delegation_policy",
    "divergence",
)
"""Sections `SafetyControl.status` can read."""


class SafetyControl(ControlChild):
    """Keep the observatory safe and decide who may command it."""

    def status(
        self, include: list[str] | None = None, capability: ObservatoryCapability | None = None
    ) -> SafetyStatus:
        """Read the safety rules, weather verdict, enclosure and policy.

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

        Raises
        ------
        InvalidArgumentError
            If ``divergence`` is asked for without `capability`, or
            `capability` is given without ``divergence``.
        """
        from astrometricslib import InvalidArgumentError
        from wayfindinglib.data_access.safety_policy_reader import get_safety_rule_set
        from wayfindinglib.tasks.control_tasks import hardware_operations
        from wayfindinglib.tasks.control_tasks.capability_promotion import summarize_divergence_evidence

        if include is None:
            include = [name for name in STATUS_SECTIONS if name != "divergence" or capability is not None]
        sections = hardware_operations.check_sections(include, STATUS_SECTIONS)
        if ("divergence" in sections) != (capability is not None):
            raise InvalidArgumentError('The "divergence" section and capability go together.')
        context = self._context
        status = SafetyStatus(sections=sections)
        if "rule_set" in sections:
            status.rule_set = get_safety_rule_set(context.butler)
        if "assessment" in sections:
            status.assessment = hardware_operations.refresh_safety_assessment(context)
        if "enclosure" in sections:
            status.enclosure = context.active_enclosure()
        if "enclosure_state" in sections:
            status.enclosure_state = hardware_operations.enclosure_state(context)
        if "delegation_policy" in sections:
            status.delegation_policy = context.delegation_policy()
        if capability is not None:
            summary = summarize_divergence_evidence(capability, context.butler.get_all("divergence_record"))
            status.divergence = {**dataclasses.asdict(summary), "agreement_rate": summary.agreement_rate}
        return status

    def assess(self, readings: SensorReadings, now: datetime | None = None) -> SafetyAssessment:
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

    def save_rule_set(self, rule_set: SafetyRuleSet) -> None:
        """Save `rule_set` as the active safety rules.

        Parameters
        ----------
        rule_set : `SafetyRuleSet`
            The rules.
        """
        from wayfindinglib.data_access.safety_policy_reader import save_safety_rule_set

        save_safety_rule_set(self._context.butler, rule_set)

    def execute_safe_state(self, trigger: str, steps: SafeStateSteps) -> SafeStateOutcome:
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

    def open_enclosure(self) -> bool:
        """Open the enclosure.

        Returns
        -------
        success : `bool`
            Whether the open command was issued and confirmed.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.open_enclosure(self._context)

    def close_enclosure(self) -> bool:
        """Close the enclosure, refusing if the mount is not clear of it.

        Returns
        -------
        success : `bool`
            Whether the close command was issued and confirmed.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.close_enclosure(self._context)

    def apply_promotion_decision(
        self,
        capability: ObservatoryCapability,
        new_state: DelegationState,
        *,
        evidence_note: str = "",
    ) -> DelegationPolicy:
        """Apply an operator's promotion decision and save the policy.

        Parameters
        ----------
        capability : `ObservatoryCapability`
            The capability to change.
        new_state : `DelegationState`
            Its new state.
        evidence_note : `str`, optional
            Why, in the operator's words.

        Returns
        -------
        policy : `DelegationPolicy`
            The saved policy.
        """
        from wayfindinglib.tasks.control_tasks.capability_promotion import apply_promotion_decision

        context = self._context
        return apply_promotion_decision(
            context.butler,
            capability,
            new_state,
            evidence_note=evidence_note,
            has_guider_calibration=context.active_guider_calibration() is not None,
            has_focus_model=context.active_focus_model() is not None,
        )

    def enter_monitoring_mode(self, *, evidence_note: str = "") -> BulkDelegationOutcome:
        """Move every capability to `DELEGATED` ("monitoring mode").

        This app then watches and computes for every capability but
        commands nothing. It always fully succeeds.

        Parameters
        ----------
        evidence_note : `str`, optional
            Why, in the operator's words.

        Returns
        -------
        outcome : `BulkDelegationOutcome`
            Every capability's new state.
        """
        return self._set_all(DelegationState.DELEGATED, evidence_note)

    def enter_controller_mode(self, *, evidence_note: str = "") -> BulkDelegationOutcome:
        """Move every capability toward `AUTHORITATIVE` ("controller mode").

        A capability that may not move yet (a correction not yet
        `SHADOWED`, or capture before the corrections it depends on) is
        listed in `BulkDelegationOutcome.rejected`, never skipped silently
        or forced.

        Parameters
        ----------
        evidence_note : `str`, optional
            Why, in the operator's words.

        Returns
        -------
        outcome : `BulkDelegationOutcome`
            Which capabilities reached `AUTHORITATIVE`, and why the others
            did not.
        """
        return self._set_all(DelegationState.AUTHORITATIVE, evidence_note)

    def _set_all(self, state: DelegationState, evidence_note: str) -> BulkDelegationOutcome:
        """Move every capability toward `state` and save the policy.

        Parameters
        ----------
        state : `DelegationState`
            The state to move toward.
        evidence_note : `str`
            Why, in the operator's words.

        Returns
        -------
        outcome : `BulkDelegationOutcome`
            Every capability's resulting state, and any refusals.
        """
        from wayfindinglib.tasks.control_tasks.capability_promotion import set_all_capabilities

        context = self._context
        return set_all_capabilities(
            context.butler,
            state,
            evidence_note=evidence_note,
            has_guider_calibration=context.active_guider_calibration() is not None,
            has_focus_model=context.active_focus_model() is not None,
        )
