"""Purpose: Unit tests for advance_session, dispose_action, and abort_session.

Description: Verifies an entry advances through its full status
progression recording actual times/frames, an unsafe verdict suspends
the session issuing no command and a subsequent safe verdict resumes
it, a required device in FAULT blocks advancement, an unrecovered
meridian flip fails the entry without disposing actions, a SHADOWED
capability records divergence and issues zero calls, an AUTHORITATIVE
capability issues and records nothing, a DELEGATED capability does
neither, AUTHORITATIVE issues exactly what a SHADOWED run would have
recorded for the same input, and abort_session skips remaining pending
entries -- the cases `Wayfinding_Library_Architecture.md` §2.4.11
calls out.
"""

from datetime import UTC, datetime, timedelta

import pytest

from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)
from wayfindinglib.models.policy.device_state import DeviceRole, DeviceState, DeviceSummaryState
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyVerdict
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
    StartTimeMode,
)
from wayfindinglib.tasks.execution_tasks.session_runner import (
    ActionDisposition,
    SessionRunnerDependencies,
    abort_session,
    advance_session,
    dispose_action,
)

_NOW = datetime(2026, 8, 5, 4, 0, 0, tzinfo=UTC)


def _safe_assessment() -> SafetyAssessment:
    return SafetyAssessment(verdict=SafetyVerdict.SAFE)


def _unsafe_assessment() -> SafetyAssessment:
    return SafetyAssessment(verdict=SafetyVerdict.UNSAFE)


def _entry(
    entry_id="entry-1",  # ruff: ignore[missing-type-function-argument]
    status=QueueEntryStatus.PENDING,  # ruff: ignore[missing-type-function-argument]
    computed_start_time=_NOW,  # ruff: ignore[missing-type-function-argument]
) -> QueuedObservationPackage:
    return QueuedObservationPackage(
        id=entry_id,
        observation_package_id="pkg-1",
        target_id="M 81",
        start_time_mode=StartTimeMode.SOONEST,
        computed_start_time=computed_start_time,
        status=status,
    )


def _session(entries=None) -> ObservationSession:  # ruff: ignore[missing-type-function-argument]
    return ObservationSession(
        id="session-1",
        night_date=_NOW.date(),
        status=SessionStatus.RUNNING,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id="cam-1",
        queue=entries if entries is not None else [_entry()],
    )


def _policy(**capability_states: DelegationState) -> DelegationPolicy:
    return DelegationPolicy(
        id="policy-1",
        capability_delegations=[
            CapabilityDelegation(capability=capability, state=state)
            for capability, state in capability_states.items()
        ],
    )


def _deps(**overrides) -> SessionRunnerDependencies:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "delegation_policy": _policy(),
        "get_safety_assessment": _safe_assessment,
        "required_device_states": lambda entry: [],
        "check_meridian_crossing": lambda entry, now: False,
        "perform_meridian_flip": lambda entry: None,
        "compute_actions": lambda entry: [],
        "capture_outcome": lambda entry: (10, ""),
        "checkpoint": lambda session: None,
        "now": lambda: _NOW,
        "record_id_factory": lambda: "div-1",
    }
    defaults.update(overrides)
    return SessionRunnerDependencies(**defaults)


def test_full_entry_progression_records_actual_times_and_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a due entry advances RUNNING -> COMPLETED, recording actuals."""
    session = _session()
    checkpoints = []
    result = advance_session(session, _deps(checkpoint=checkpoints.append))

    entry = result.queue[0]
    assert entry.status == QueueEntryStatus.COMPLETED
    assert entry.actual_start_time == _NOW
    assert entry.actual_end_time == _NOW
    assert entry.frames_captured == 10
    assert result.status == SessionStatus.COMPLETED
    assert result.closed_at == _NOW
    assert len(checkpoints) == 1


def test_no_due_entry_leaves_queue_unchanged_but_checkpoints():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a session with no due entry is checkpointed, not advanced."""
    future_entry = _entry(computed_start_time=_NOW + timedelta(hours=1))
    session = _session(entries=[future_entry])
    checkpoints = []

    result = advance_session(session, _deps(checkpoint=checkpoints.append))

    assert result.queue[0].status == QueueEntryStatus.PENDING
    assert len(checkpoints) == 1


def test_unsafe_verdict_suspends_session_and_advances_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unsafe verdict suspends the session and issues no command."""
    session = _session()
    issue_calls = []
    deps = _deps(
        get_safety_assessment=_unsafe_assessment,
        compute_actions=lambda entry: [
            ActionDisposition(ObservatoryCapability.MOUNT_CONTROL, issue=lambda: issue_calls.append(True))
        ],
    )

    result = advance_session(session, deps)

    assert result.status == SessionStatus.SUSPENDED
    assert result.queue[0].status == QueueEntryStatus.PENDING
    assert issue_calls == []


def test_safe_verdict_resumes_a_suspended_session():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a subsequent safe verdict resumes a suspended session."""
    session = _session()
    session.status = SessionStatus.SUSPENDED

    result = advance_session(session, _deps())

    assert result.status == SessionStatus.COMPLETED  # resumed, then completed its only entry
    assert result.queue[0].status == QueueEntryStatus.COMPLETED


def test_device_fault_blocks_entry_advancement():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a required device in FAULT blocks the entry from advancing."""
    session = _session()
    faulted = DeviceState(
        device_id="mount-1", device_role=DeviceRole.MOUNT, summary_state=DeviceSummaryState.FAULT
    )
    deps = _deps(required_device_states=lambda entry: [faulted])

    result = advance_session(session, deps)

    assert result.queue[0].status == QueueEntryStatus.PENDING
    assert "FAULT" in result.queue[0].status_detail


def test_meridian_flip_failure_fails_entry_without_disposing_actions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unrecovered flip fails the entry and skips disposition."""
    from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome

    session = _session()
    issue_calls = []
    failed_flip = MeridianFlipOutcome(
        id="flip-1",
        queued_observation_package_id="entry-1",
        hour_angle_at_trigger_deg=1.0,
        resumed=False,
        failure_detail="flip did not resume",
    )
    deps = _deps(
        check_meridian_crossing=lambda entry, now: True,
        perform_meridian_flip=lambda entry: failed_flip,
        compute_actions=lambda entry: [
            ActionDisposition(ObservatoryCapability.MOUNT_CONTROL, issue=lambda: issue_calls.append(True))
        ],
    )

    result = advance_session(session, deps)

    assert result.queue[0].status == QueueEntryStatus.FAILED
    assert result.queue[0].status_detail == "flip did not resume"
    assert len(result.meridian_flips) == 1
    assert issue_calls == []


def test_shadowed_capability_records_divergence_and_issues_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify SHADOWED produces a divergence record and issues zero calls."""
    session = _session()
    issue_calls = []
    disposition = ActionDisposition(
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        issue=lambda: issue_calls.append(True),
        compute_intent=lambda: 10.0,
        observe=lambda: 9.7,
        comparison_input_id="frame-1",
        tolerance=1.0,
        divergence_unit="arcsec",
    )
    deps = _deps(
        delegation_policy=_policy(PLATE_SOLVE_ALIGNMENT=DelegationState.SHADOWED),
        compute_actions=lambda entry: [disposition],
    )

    result = advance_session(session, deps)

    assert issue_calls == []
    assert len(result.divergence_records) == 1
    record = result.divergence_records[0]
    assert record.intended_value == pytest.approx(10.0)
    assert record.observed_value == pytest.approx(9.7)
    assert record.within_tolerance is True


def test_authoritative_capability_issues_and_records_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify AUTHORITATIVE issues the action and records no divergence."""
    session = _session()
    issue_calls = []
    disposition = ActionDisposition(
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        issue=lambda: issue_calls.append(True),
        compute_intent=lambda: 10.0,
        observe=lambda: 9.7,
        comparison_input_id="frame-1",
        tolerance=1.0,
        divergence_unit="arcsec",
    )
    deps = _deps(
        delegation_policy=_policy(PLATE_SOLVE_ALIGNMENT=DelegationState.AUTHORITATIVE),
        compute_actions=lambda entry: [disposition],
    )

    result = advance_session(session, deps)

    assert issue_calls == [True]
    assert result.divergence_records == []


def test_delegated_capability_neither_issues_nor_records():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a DELEGATED capability issues nothing and records nothing."""
    session = _session()
    issue_calls = []
    disposition = ActionDisposition(
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        issue=lambda: issue_calls.append(True),
        compute_intent=lambda: 10.0,
        observe=lambda: 9.7,
        comparison_input_id="frame-1",
        tolerance=1.0,
        divergence_unit="arcsec",
    )
    deps = _deps(compute_actions=lambda entry: [disposition])  # default policy: DELEGATED

    result = advance_session(session, deps)

    assert issue_calls == []
    assert result.divergence_records == []


def test_authoritative_issues_exactly_what_shadowed_would_have_recorded():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify AUTHORITATIVE issues the same intent a SHADOWED run recorded."""
    shadowed_intent_seen = []
    authoritative_intent_seen = []

    def make_disposition(sink):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return ActionDisposition(
            ObservatoryCapability.AUTOFOCUS,
            issue=lambda: None,
            compute_intent=lambda: 5000.0,
            observe=lambda: sink.append(5000.0) or 5000.0,
            comparison_input_id="sweep-1",
            tolerance=50.0,
            divergence_unit="steps",
        )

    shadowed_policy = _policy(AUTOFOCUS=DelegationState.SHADOWED)
    shadowed_record = dispose_action(
        make_disposition(shadowed_intent_seen), shadowed_policy, "session-1", "entry-1", "div-1"
    )

    authoritative_policy = _policy(AUTOFOCUS=DelegationState.AUTHORITATIVE)
    issued = []
    authoritative_disposition = ActionDisposition(
        ObservatoryCapability.AUTOFOCUS,
        issue=lambda: authoritative_intent_seen.append(5000.0) or issued.append(5000.0),
        compute_intent=lambda: 5000.0,
    )
    dispose_action(authoritative_disposition, authoritative_policy, "session-1", "entry-1", "div-2")

    assert shadowed_record.intended_value == pytest.approx(5000.0)
    assert issued == [5000.0]
    assert shadowed_record.intended_value == pytest.approx(issued[0])


def test_abort_session_skips_pending_entries_and_closes():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify abort_session transitions PENDING entries to SKIPPED, closes."""
    running_entry = _entry("entry-1", status=QueueEntryStatus.RUNNING)
    pending_entry = _entry("entry-2", status=QueueEntryStatus.PENDING)
    completed_entry = _entry("entry-3", status=QueueEntryStatus.COMPLETED)
    session = _session(entries=[running_entry, pending_entry, completed_entry])

    result = abort_session(session, "operator abort", _NOW)

    assert result.status == SessionStatus.ABORTED
    assert result.closed_at == _NOW
    assert result.queue[0].status == QueueEntryStatus.RUNNING  # not touched, wasn't PENDING
    assert result.queue[1].status == QueueEntryStatus.SKIPPED
    assert result.queue[1].status_detail == "operator abort"
    assert result.queue[2].status == QueueEntryStatus.COMPLETED  # untouched
