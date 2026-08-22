"""Purpose: Unit tests for check_and_escalate and run_watchdog_loop.

Description: Verifies a fresh heartbeat triggers no action, a stale or
missing heartbeat invokes safe_state.execute with trigger="watchdog",
and the polling loop checks repeatedly and reports each escalation --
using a fake SafeStateSteps bundle rather than real hardware.
"""

import threading
import time

from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps
from wayfindinglib.watchdog.heartbeat import write_heartbeat
from wayfindinglib.watchdog.watchdog_process import check_and_escalate, run_watchdog_loop


def _steps() -> SafeStateSteps:
    return SafeStateSteps(
        abandon_exposure=lambda: True,
        stop_guiding=lambda: True,
        park_mount=lambda: True,
        get_mount_position=lambda: (0.0, 180.0),
        close_enclosure=lambda: True,
        warm_sensor=lambda: True,
        close_session=lambda: True,
        enclosure=Enclosure(
            id="enc-1",
            enclosure_type=EnclosureType.ROLL_OFF_ROOF,
            park_azimuth_deg=180.0,
            park_altitude_deg=0.0,
        ),
    )


def test_fresh_heartbeat_triggers_no_escalation(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a heartbeat within the timeout does not escalate."""
    heartbeat_path = tmp_path / "heartbeat"
    write_heartbeat(heartbeat_path, 1000.0)

    outcome = check_and_escalate(heartbeat_path, watchdog_timeout_sec=60, now=1010.0, steps=_steps())

    assert outcome is None


def test_stale_heartbeat_escalates_with_watchdog_trigger(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a heartbeat past the timeout escalates via safe_state.execute."""
    heartbeat_path = tmp_path / "heartbeat"
    write_heartbeat(heartbeat_path, 1000.0)

    outcome = check_and_escalate(heartbeat_path, watchdog_timeout_sec=60, now=1200.0, steps=_steps())

    assert outcome is not None
    assert outcome.trigger == "watchdog"
    assert outcome.mount_parked is True


def test_missing_heartbeat_escalates(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a heartbeat file that was never written escalates immediately."""
    heartbeat_path = tmp_path / "never-written"

    outcome = check_and_escalate(heartbeat_path, watchdog_timeout_sec=60, now=1000.0, steps=_steps())

    assert outcome is not None
    assert outcome.trigger == "watchdog"


def test_run_watchdog_loop_reports_each_escalation(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the polling loop reports an escalation via on_escalation."""
    heartbeat_path = tmp_path / "heartbeat"
    # Never written -- every poll finds it stale.
    escalations = []
    stop_event = threading.Event()

    def stop_after_one(outcome):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        escalations.append(outcome)
        stop_event.set()

    run_watchdog_loop(
        heartbeat_path,
        watchdog_timeout_sec=60,
        steps_factory=_steps,
        poll_interval_sec=0.0,
        stop_event=stop_event,
        on_escalation=stop_after_one,
    )

    assert len(escalations) == 1
    assert escalations[0].trigger == "watchdog"


def test_run_watchdog_loop_does_not_escalate_when_heartbeat_kept_fresh(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the loop takes no action while the heartbeat stays fresh."""
    heartbeat_path = tmp_path / "heartbeat"
    write_heartbeat(heartbeat_path, time.monotonic())
    escalations = []
    stop_event = threading.Event()
    call_count = {"value": 0}

    def stop_after_three_checks():  # ruff: ignore[missing-return-type-private-function]
        call_count["value"] += 1
        if call_count["value"] >= 3:
            stop_event.set()
        return _steps()

    run_watchdog_loop(
        heartbeat_path,
        watchdog_timeout_sec=3600,
        steps_factory=stop_after_three_checks,
        poll_interval_sec=0.0,
        stop_event=stop_event,
        on_escalation=escalations.append,
    )

    assert escalations == []
    assert call_count["value"] == 3
