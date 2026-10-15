"""Purpose: Check at run time that Execution stays optional.

Description: Two of the library-wide structural checks in
`Wayfinding_Library_Architecture.md` (Section 7.1, "Library-wide
structural checks"; the tests are listed in
`Wayfinding_Library_Implementation.md` Section 7.1):

- **Execution is optional.** Importing and using the Control and
  Planning sub-APIs loads no Execution module.
- **Safety runs without Execution.** The safety monitor, the safe-state
  sequence, the enclosure interlock and the out-of-process watchdog load
  no Execution module, so the observatory can protect itself when no
  session is running.

Each test runs its code in a fresh Python process
(`wayfindinglib/test/import_probe.py`) and then inspects that process's
`sys.modules`. A check inside the pytest process would depend on test
order, since other tests import Execution on purpose. The
planning-is-hardware-free check lives with the Planning tests in
`wayfindinglib/api/test/test_planning.py`, and the import-linter
contracts in ``pyproject.toml`` check the same rules statically.
"""

from wayfindinglib.test.import_probe import (
    EXECUTION_PACKAGES,
    modules_within,
    wayfindinglib_modules_loaded_by,
)


def test_control_and_planning_work_without_loading_execution() -> None:
    """Using `control` and `planning` loads no Execution module.

    The child builds both sub-APIs over one storage, asks Control for a
    safety assessment and has Planning build an empty session for the
    active rig.
    """
    loaded = wayfindinglib_modules_loaded_by(
        """
        from datetime import UTC, datetime

        from wayfindinglib.api.control import ObservatoryControl
        from wayfindinglib.api.planning import ObservationPlanning
        from wayfindinglib.drivers.butler import DiskButler

        butler = DiskButler(app_config=config)
        control = ObservatoryControl(config=config, butler=butler)
        assessment = control.safety.assess({}, datetime.now(UTC))
        assert not assessment.permits_observing()

        planning = ObservationPlanning(config, butler)
        session = planning.create_plan("empty_session", night_id="2026-08-10")
        assert session.queue == []
        """
    )

    assert {"wayfindinglib.api.control", "wayfindinglib.api.planning"} <= loaded
    assert modules_within(loaded, EXECUTION_PACKAGES) == []


def test_safety_runs_without_loading_execution() -> None:
    """The safety path loads no Execution module.

    The child evaluates an empty set of readings (which must read as not
    safe), checks the enclosure interlock, and lets the watchdog find a
    missing heartbeat and run the whole safe-state sequence with stand-in
    hardware steps.
    """
    loaded = wayfindinglib_modules_loaded_by(
        """
        from datetime import UTC, datetime
        from pathlib import Path

        from wayfindinglib.models.equipment_and_site.enclosure import (
            Enclosure,
            EnclosureState,
            EnclosureType,
        )
        from wayfindinglib.models.policy.safety import SafetyVerdict
        from wayfindinglib.tasks.control_tasks import enclosure_control, safe_state, safety_monitor
        from wayfindinglib.watchdog import heartbeat, watchdog_process

        assessment = safety_monitor.SafetyMonitor().evaluate(None, {}, datetime.now(UTC))
        assert assessment.verdict == SafetyVerdict.UNKNOWN

        assert not enclosure_control.can_leave_park(EnclosureState.UNKNOWN)

        enclosure = Enclosure(
            id="roof",
            enclosure_type=EnclosureType.ROLL_OFF_ROOF,
            park_azimuth_deg=180.0,
            park_altitude_deg=0.0,
        )
        steps = safe_state.SafeStateSteps(
            abandon_exposure=lambda: True,
            stop_guiding=lambda: True,
            park_mount=lambda: True,
            get_mount_position=lambda: (0.0, 180.0),
            close_enclosure=lambda: True,
            warm_sensor=lambda: True,
            close_session=lambda: True,
            enclosure=enclosure,
        )
        missing = Path(config.get_value("Wayfinding Library", "path")) / "no_heartbeat"
        assert heartbeat.read_heartbeat(missing) is None
        outcome = watchdog_process.check_and_escalate(missing, 30, 1000.0, steps)
        assert outcome is not None and outcome.trigger == "watchdog"
        assert outcome.failed_step is None
        """
    )

    assert "wayfindinglib.tasks.control_tasks.safe_state" in loaded
    assert "wayfindinglib.watchdog.watchdog_process" in loaded
    assert modules_within(loaded, EXECUTION_PACKAGES) == []
