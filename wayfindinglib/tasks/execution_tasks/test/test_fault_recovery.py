"""Purpose: Unit tests for recover_fault.

Description: Verifies recovery succeeds and stops on the first ENABLED
outcome, attempts are bounded by max_attempts, the backoff interval is
applied between attempts, and exhaustion escalates to safe state --
the cases `Wayfinding_Library_Architecture.md` §2.4.11 calls out.
"""

from wayfindinglib.models.policy.device_state import DeviceSummaryState
from wayfindinglib.models.policy.recovery import RecoveryPolicy
from wayfindinglib.tasks.execution_tasks.fault_recovery import recover_fault


def test_recovery_succeeds_on_first_enabled_outcome():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify recovery stops as soon as the device reports ENABLED."""
    call_count = {"value": 0}

    def read_state():  # ruff: ignore[missing-return-type-private-function]
        call_count["value"] += 1
        return DeviceSummaryState.ENABLED

    record = recover_fault(
        "fault-1",
        "mount-1",
        DeviceSummaryState.FAULT,
        "connection lost",
        RecoveryPolicy(max_attempts=3),
        return_to_standby=lambda: None,
        re_enable=lambda: None,
        read_state=read_state,
        sleep=lambda seconds: None,
    )

    assert record.recovered is True
    assert record.escalated_to_safe_state is False
    assert len(record.attempts) == 1
    assert call_count["value"] == 1


def test_recovery_exhausts_attempts_and_escalates():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify exhausted attempts set escalated_to_safe_state and escalate."""
    escalate_calls = []

    record = recover_fault(
        "fault-2",
        "mount-1",
        DeviceSummaryState.FAULT,
        "connection lost",
        RecoveryPolicy(max_attempts=3),
        return_to_standby=lambda: None,
        re_enable=lambda: None,
        read_state=lambda: DeviceSummaryState.FAULT,
        sleep=lambda seconds: None,
        escalate=lambda: escalate_calls.append(True),
    )

    assert record.recovered is False
    assert record.escalated_to_safe_state is True
    assert len(record.attempts) == 3
    assert escalate_calls == [True]


def test_recovery_without_escalate_callable_does_not_raise():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify escalation with no injected escalate callable does not raise."""
    record = recover_fault(
        "fault-3",
        "mount-1",
        DeviceSummaryState.FAULT,
        "connection lost",
        RecoveryPolicy(max_attempts=1),
        return_to_standby=lambda: None,
        re_enable=lambda: None,
        read_state=lambda: DeviceSummaryState.FAULT,
        sleep=lambda seconds: None,
    )
    assert record.escalated_to_safe_state is True


def test_backoff_interval_applied_between_attempts():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the sleep interval grows by backoff_multiplier."""
    slept_intervals = []

    recover_fault(
        "fault-4",
        "mount-1",
        DeviceSummaryState.FAULT,
        "connection lost",
        RecoveryPolicy(max_attempts=3, initial_interval_sec=10, backoff_multiplier=2.0),
        return_to_standby=lambda: None,
        re_enable=lambda: None,
        read_state=lambda: DeviceSummaryState.FAULT,
        sleep=lambda seconds: slept_intervals.append(seconds),
    )

    # Attempt 1 has no wait; attempt 2 waits 10s; attempt 3 waits 20s.
    assert slept_intervals == [10.0, 20.0]


def test_attempt_records_every_outcome_in_order():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify each attempt's outcome is recorded in the order it occurred."""
    outcomes = iter([DeviceSummaryState.FAULT, DeviceSummaryState.STANDBY, DeviceSummaryState.ENABLED])

    record = recover_fault(
        "fault-5",
        "mount-1",
        DeviceSummaryState.FAULT,
        "connection lost",
        RecoveryPolicy(max_attempts=5),
        return_to_standby=lambda: None,
        re_enable=lambda: None,
        read_state=lambda: next(outcomes),
        sleep=lambda seconds: None,
    )

    assert [attempt.outcome for attempt in record.attempts] == [
        DeviceSummaryState.FAULT,
        DeviceSummaryState.STANDBY,
        DeviceSummaryState.ENABLED,
    ]
    assert record.recovered is True
