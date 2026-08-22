"""Purpose: Unit tests for fault recovery domain models.

Description: Verifies RecoveryPolicy's exponential backoff interval
computation and FaultRecord's default unrecovered/unescalated state.
"""

import pytest

from wayfindinglib.models.policy.device_state import DeviceSummaryState
from wayfindinglib.models.policy.recovery import FaultRecord, RecoveryAttempt, RecoveryPolicy


def test_interval_for_first_attempt_is_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the first recovery attempt has no preceding wait."""
    policy = RecoveryPolicy()
    assert policy.interval_for_attempt(1) == pytest.approx(0.0)


def test_interval_backs_off_exponentially():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify each subsequent attempt grows by the backoff multiplier."""
    policy = RecoveryPolicy(initial_interval_sec=30, backoff_multiplier=2.0)
    assert policy.interval_for_attempt(2) == pytest.approx(30.0)
    assert policy.interval_for_attempt(3) == pytest.approx(60.0)
    assert policy.interval_for_attempt(4) == pytest.approx(120.0)


def test_fault_record_defaults_unrecovered_and_unescalated():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a fresh FaultRecord defaults to unrecovered, unescalated."""
    record = FaultRecord(
        id="fr1", device_id="mount1", faulted_state=DeviceSummaryState.FAULT, fault_detail="ALERT property"
    )
    assert record.recovered is False
    assert record.escalated_to_safe_state is False
    assert record.attempts == []


def test_fault_record_accumulates_attempts():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify recovery attempts append to the record's attempts list."""
    record = FaultRecord(id="fr1", device_id="mount1", faulted_state=DeviceSummaryState.FAULT)
    record.attempts.append(RecoveryAttempt(outcome=DeviceSummaryState.STANDBY, detail="first try"))
    record.attempts.append(RecoveryAttempt(outcome=DeviceSummaryState.ENABLED, detail="recovered"))
    assert len(record.attempts) == 2
