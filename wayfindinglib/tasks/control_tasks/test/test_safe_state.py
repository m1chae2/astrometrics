"""Purpose: Unit tests for safe_state.execute.

Description: Verifies the six steps run in order even when earlier
ones fail, enclosure closure is skipped (not attempted) when the mount
did not park, and `failed_step` records the first failure -- the cases
`Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps, execute


def _enclosure(**overrides) -> Enclosure:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "id": "enc-1",
        "enclosure_type": EnclosureType.ROLL_OFF_ROOF,
        "park_azimuth_deg": 180.0,
        "park_altitude_deg": 0.0,
        "clearance_tolerance_deg": 2.0,
        "motion_timeout_sec": 120,
    }
    defaults.update(overrides)
    return Enclosure(**defaults)


def _steps(**overrides) -> SafeStateSteps:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "abandon_exposure": lambda: True,
        "stop_guiding": lambda: True,
        "park_mount": lambda: True,
        "get_mount_position": lambda: (0.0, 180.0),
        "close_enclosure": lambda: True,
        "warm_sensor": lambda: True,
        "close_session": lambda: True,
        "enclosure": _enclosure(),
    }
    defaults.update(overrides)
    return SafeStateSteps(**defaults)


def test_all_steps_succeed_records_no_failure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a fully successful run records every step and no failure."""
    outcome = execute("unsafe_verdict", _steps())
    assert outcome.exposure_abandoned is True
    assert outcome.guiding_stopped is True
    assert outcome.mount_parked is True
    assert outcome.enclosure_closed is True
    assert outcome.sensor_warmed is True
    assert outcome.session_closed is True
    assert outcome.failed_step is None
    assert outcome.trigger == "unsafe_verdict"


def test_park_failure_skips_enclosure_close_but_continues():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed park skips enclosure close but continues other steps."""
    calls = []
    outcome = execute(
        "recovery_exhausted",
        _steps(
            park_mount=lambda: False,
            close_enclosure=lambda: calls.append("closed") or True,
            warm_sensor=lambda: calls.append("warmed") or True,
            close_session=lambda: calls.append("closed_session") or True,
        ),
    )
    assert outcome.mount_parked is False
    assert outcome.enclosure_closed is False
    assert "closed" not in calls
    assert outcome.sensor_warmed is True
    assert outcome.session_closed is True
    assert outcome.failed_step == "mount"


def test_first_failure_is_recorded_not_last():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify failed_step names the first failure, not a later one."""
    outcome = execute(
        "watchdog",
        _steps(stop_guiding=lambda: False, warm_sensor=lambda: False),
    )
    assert outcome.failed_step == "guiding"


def test_exception_in_a_step_is_treated_as_failure_and_does_not_abort():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a step that raises is a failure and later steps still run."""

    def failing_park():  # ruff: ignore[missing-return-type-private-function]
        raise RuntimeError("mount not responding")

    outcome = execute("watchdog", _steps(park_mount=failing_park))
    assert outcome.mount_parked is False
    assert outcome.failed_step == "mount"
    assert outcome.sensor_warmed is True
    assert outcome.session_closed is True


def test_mount_outside_clearance_after_park_skips_enclosure_and_flags_it():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a parked-but-outside-clearance report skips closure."""
    outcome = execute(
        "unsafe_verdict",
        _steps(get_mount_position=lambda: (45.0, 45.0)),
    )
    assert outcome.mount_parked is True
    assert outcome.enclosure_closed is False
    assert outcome.failed_step == "enclosure"


def test_steps_run_in_documented_order():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the six steps are attempted in the documented order."""
    call_order = []

    def tracker(name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        def _call():  # ruff: ignore[missing-return-type-private-function]
            call_order.append(name)
            return True

        return _call

    execute(
        "unsafe_verdict",
        _steps(
            abandon_exposure=tracker("exposure"),
            stop_guiding=tracker("guiding"),
            park_mount=tracker("mount"),
            close_enclosure=tracker("enclosure"),
            warm_sensor=tracker("sensor"),
            close_session=tracker("session"),
        ),
    )
    assert call_order == ["exposure", "guiding", "mount", "enclosure", "sensor", "session"]
