"""Purpose: Bounded Device Fault Recovery.

Description: A device reporting `FAULT` opens a `FaultRecord` and
begins recovery governed by `RecoveryPolicy`, per
`Wayfinding_Library_Architecture.md` §2.4.6: return the device to
`STANDBY`, re-enable it, and verify it reports `ENABLED`. Attempts are
bounded by `max_attempts` with an interval growing by
`backoff_multiplier`; each attempt appends a `RecoveryAttempt` with its
outcome. Exhausting the bound escalates to the safe-state sequence.

Recovery is deliberately shallow -- it re-establishes lifecycle state
and nothing more, because software that cannot see the observatory
cannot distinguish a transient disconnection from a mechanical
obstruction (`Wayfinding_Library_Architecture.md` §2.4.6). Every
hardware-facing step and the sleep between attempts are injected
callables, so this module carries no hardware import and is testable
without real timing delays.
"""

from collections.abc import Callable

from wayfindinglib.models.policy.device_state import DeviceSummaryState
from wayfindinglib.models.policy.recovery import FaultRecord, RecoveryAttempt, RecoveryPolicy


def attempt_recovery(
    return_to_standby: Callable[[], None],
    re_enable: Callable[[], None],
    read_state: Callable[[], DeviceSummaryState],
) -> RecoveryAttempt:
    """Run one recovery attempt: return to standby, re-enable, verify.

    Returns
    -------
    attempt : `RecoveryAttempt`
        The resulting device state after the attempt.
    """
    return_to_standby()
    re_enable()
    outcome = read_state()
    return RecoveryAttempt(outcome=outcome)


def recover_fault(
    fault_record_id: str,
    device_id: str,
    faulted_state: DeviceSummaryState,
    fault_detail: str,
    policy: RecoveryPolicy,
    return_to_standby: Callable[[], None],
    re_enable: Callable[[], None],
    read_state: Callable[[], DeviceSummaryState],
    sleep: Callable[[float], None],
    escalate: Callable[[], None] | None = None,
) -> FaultRecord:
    """Run bounded recovery attempts against a faulted device.

    Parameters
    ----------
    fault_record_id : `str`
        Identifier for the resulting `FaultRecord`.
    device_id : `str`
        The faulted device's identifier.
    faulted_state : `DeviceSummaryState`
        The state the device was found in.
    fault_detail : `str`
        Detail describing the fault, e.g. from an alert property.
    policy : `RecoveryPolicy`
        Bounds the attempt count and inter-attempt interval.
    return_to_standby, re_enable, read_state : callable
        The per-attempt hardware-facing operations.
    sleep : callable
        Called with the computed interval before each attempt after
        the first; injected so tests need no real wall-clock delay.
    escalate : callable, optional
        Invoked once if every attempt is exhausted without recovery
        -- the safe-state trigger (§2.5.7). Not called when recovery
        succeeds.

    Returns
    -------
    fault_record : `FaultRecord`
        Every attempt made, and whether recovery succeeded.
    """
    record = FaultRecord(
        id=fault_record_id, device_id=device_id, faulted_state=faulted_state, fault_detail=fault_detail
    )

    for attempt_number in range(1, policy.max_attempts + 1):
        interval = policy.interval_for_attempt(attempt_number)
        if interval > 0.0:
            sleep(interval)

        attempt = attempt_recovery(return_to_standby, re_enable, read_state)
        record.attempts.append(attempt)
        if attempt.outcome == DeviceSummaryState.ENABLED:
            record.recovered = True
            return record

    record.recovered = False
    record.escalated_to_safe_state = True
    if escalate is not None:
        escalate()
    return record
