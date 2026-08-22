"""Purpose: Queue Advancement — The Nine-Step Execution Cycle.

Description: `advance_session` runs one queue-advancement cycle against
an `ObservationSession`, per `Wayfinding_Library_Architecture.md`
§2.4.3 Table 5: safety gate, readiness check, entry selection,
meridian check, intent computation, disposition, outcome capture,
status transition, checkpoint. Table 5 lists readiness check (step 2)
before entry selection (step 3), but a device's *relevance* is scoped
to the entry that needs it, so this implementation selects the
candidate entry first and then checks readiness against exactly the
devices that entry requires -- the only order in which "no device
required by the entry" (the table's own step-2 wording) is answerable.

**Disposition, not mode** (§2.4.2): for each computed action,
`dispose_action` asks the `DelegationPolicy` for that action's
capability state. `AUTHORITATIVE` issues; `SHADOWED` hands the same
computed intent to `divergence_recording` and issues nothing;
`DELEGATED` computes and issues nothing for that capability. The same
computed intent feeds all three paths, so what a shadowed capability
records is exactly the action an authoritative one would issue.

Every hardware-facing and domain-specific operation -- reading safety
state, checking device readiness, detecting a meridian crossing,
running a flip, computing/issuing/observing an action, capturing
outcomes, checkpointing -- is injected via `SessionRunnerDependencies`,
so this module carries no hardware import and no capture-orchestration
logic of its own; it is the state machine Table 5 specifies, not the
astronomy or hardware behind each step.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from wayfindinglib.models.policy.delegation import DelegationPolicy, DelegationState, ObservatoryCapability
from wayfindinglib.models.policy.device_state import DeviceState, DeviceSummaryState
from wayfindinglib.models.policy.safety import SafetyAssessment
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
)
from wayfindinglib.tasks.execution_tasks.divergence_recording import (
    UNCOMPARABLE_CAPABILITIES,
    record_divergence,
)

_TERMINAL_ENTRY_STATUSES = (QueueEntryStatus.COMPLETED, QueueEntryStatus.FAILED, QueueEntryStatus.SKIPPED)


@dataclass
class ActionDisposition:
    """One computed action awaiting disposition by the delegation policy.

    `compute_intent`/`observe` are only exercised when the capability
    is both `SHADOWED` and comparable (has an independently computed
    counterpart, per `divergence_recording`'s `UNCOMPARABLE_CAPABILITIES`);
    `issue` is only called when `AUTHORITATIVE`.
    """

    capability: ObservatoryCapability
    issue: Callable[[], None]
    compute_intent: Callable[[], float] | None = None
    observe: Callable[[], float] | None = None
    comparison_input_id: str = ""
    tolerance: float = 0.0
    divergence_unit: str = ""


def dispose_action(
    disposition: ActionDisposition,
    delegation_policy: DelegationPolicy,
    observation_session_id: str,
    queued_observation_package_id: str,
    record_id: str,
) -> DivergenceRecord | None:
    """Dispose one computed action per its capability's delegation state.

    Returns
    -------
    record : `DivergenceRecord` or `None`
        A divergence record when the capability is `SHADOWED` and
        comparable; `None` for `AUTHORITATIVE` (issued, not recorded),
        `DELEGATED` (neither issued nor recorded), and `SHADOWED`
        capabilities with no comparable counterpart.
    """
    state = delegation_policy.state_for(disposition.capability)

    if state == DelegationState.AUTHORITATIVE:
        disposition.issue()
        return None

    if state == DelegationState.SHADOWED:
        is_comparable = disposition.capability not in UNCOMPARABLE_CAPABILITIES
        if not is_comparable or disposition.compute_intent is None or disposition.observe is None:
            return None
        intended_value = disposition.compute_intent()
        observed_value = disposition.observe()
        return record_divergence(
            record_id,
            observation_session_id,
            queued_observation_package_id,
            disposition.capability,
            disposition.comparison_input_id,
            intended_value,
            observed_value,
            disposition.tolerance,
            disposition.divergence_unit,
        )

    return None  # DELEGATED: compute and issue nothing for this capability.


@dataclass
class SessionRunnerDependencies:
    """Every hardware-facing and domain-specific operation this cycle needs.

    Injected so the state machine itself stays free of hardware and
    capture-orchestration logic.
    """

    delegation_policy: DelegationPolicy
    get_safety_assessment: Callable[[], SafetyAssessment]
    required_device_states: Callable[[QueuedObservationPackage], list[DeviceState]]
    check_meridian_crossing: Callable[[QueuedObservationPackage, datetime], bool]
    perform_meridian_flip: Callable[[QueuedObservationPackage], MeridianFlipOutcome]
    compute_actions: Callable[[QueuedObservationPackage], list[ActionDisposition]]
    capture_outcome: Callable[[QueuedObservationPackage], tuple[int, str]]
    checkpoint: Callable[[ObservationSession], None]
    now: Callable[[], datetime]
    record_id_factory: Callable[[], str] = lambda: ""


def advance_session(session: ObservationSession, deps: SessionRunnerDependencies) -> ObservationSession:
    """Run one queue-advancement cycle (Table 5) against `session`, in place.

    Parameters
    ----------
    session : `ObservationSession`
        The session to advance. Mutated and returned.
    deps : `SessionRunnerDependencies`
        Every injected operation the cycle needs.

    Returns
    -------
    session : `ObservationSession`
        The same session, advanced by at most one entry and always
        checkpointed before returning.
    """
    safety = deps.get_safety_assessment()
    if not safety.permits_observing():
        if session.status in (SessionStatus.RUNNING, SessionStatus.PLANNED):
            session.status = SessionStatus.SUSPENDED
            session.status_detail = "Safety assessment does not permit observing"
        deps.checkpoint(session)
        return session

    if session.status in (SessionStatus.PLANNED, SessionStatus.SUSPENDED):
        session.status = SessionStatus.RUNNING
        session.status_detail = ""

    now = deps.now()
    entry = next(
        (
            candidate
            for candidate in session.queue
            if candidate.status == QueueEntryStatus.PENDING
            and candidate.computed_start_time is not None
            and candidate.computed_start_time <= now
        ),
        None,
    )
    if entry is None:
        deps.checkpoint(session)
        return session

    required_states = deps.required_device_states(entry)
    if any(state.summary_state == DeviceSummaryState.FAULT for state in required_states):
        entry.status_detail = "A device required by this entry is in the FAULT state"
        deps.checkpoint(session)
        return session

    entry.status = QueueEntryStatus.RUNNING
    entry.actual_start_time = now

    if deps.check_meridian_crossing(entry, now):
        flip_outcome = deps.perform_meridian_flip(entry)
        session.meridian_flips.append(flip_outcome)
        if not flip_outcome.resumed:
            entry.status = QueueEntryStatus.FAILED
            entry.status_detail = flip_outcome.failure_detail or "Meridian flip did not resume"
            entry.actual_end_time = deps.now()
            deps.checkpoint(session)
            return session

    for disposition in deps.compute_actions(entry):
        record = dispose_action(
            disposition, deps.delegation_policy, session.id, entry.id, deps.record_id_factory()
        )
        if record is not None:
            session.divergence_records.append(record)

    frames_captured, status_detail = deps.capture_outcome(entry)
    entry.frames_captured = frames_captured
    entry.status_detail = status_detail
    entry.actual_end_time = deps.now()
    entry.status = QueueEntryStatus.COMPLETED

    if session.queue and all(candidate.status in _TERMINAL_ENTRY_STATUSES for candidate in session.queue):
        session.status = SessionStatus.COMPLETED
        session.closed_at = deps.now()

    deps.checkpoint(session)
    return session


def abort_session(session: ObservationSession, reason: str, now: datetime) -> ObservationSession:
    """Abort a session: skip remaining pending entries, close it out.

    Parameters
    ----------
    session : `ObservationSession`
        The session to abort. Mutated and returned.
    reason : `str`
        Recorded on the session and on every skipped entry.
    now : `datetime`
        Recorded as `closed_at`.

    Returns
    -------
    session : `ObservationSession`
        The same session, with every `PENDING` entry transitioned to
        `SKIPPED`, `status=ABORTED`, and `closed_at` set.
    """
    for entry in session.queue:
        if entry.status == QueueEntryStatus.PENDING:
            entry.status = QueueEntryStatus.SKIPPED
            entry.status_detail = reason

    session.status = SessionStatus.ABORTED
    session.status_detail = reason
    session.closed_at = now
    return session
