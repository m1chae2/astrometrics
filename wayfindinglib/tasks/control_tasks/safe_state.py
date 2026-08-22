"""Purpose: Safe-State Sequencing.

Description: `execute(trigger, steps)` runs the six ordered steps of
`Wayfinding_Library_Architecture.md` Table 6 -- abandon exposure, stop
guiding, park mount, close enclosure, warm sensor, close session --
recording each into a `SafeStateOutcome`, per
`Wayfinding_Library_Architecture.md` §2.5.7.

Each step is attempted even if an earlier one failed: a mount that
will not park must not prevent the attempt to warm the sensor.
Enclosure closure is the one exception -- it is *skipped* rather than
attempted when the mount did not reach a clearance position, since
forcing it is the damage case `enclosure_control.py` exists to
prevent. `failed_step` records the first failure, so a partially
completed safe state is diagnosable rather than an unknown condition.

> [!WARNING]
> A mount that cannot park leaves an enclosure that cannot safely
> close, and no software arrangement resolves this: the failure is
> mechanical and the software has already lost the ability to move
> the obstruction. Unattended operation requires a hardware interlock
> independent of this system -- outside this library's scope.

Every hardware-facing step is injected as a callable (`SafeStateSteps`)
rather than imported, so this module carries no hardware import and is
exercisable with no Execution package present (§2.5.9, "Safety Runs
Without Execution").
"""

from collections.abc import Callable
from dataclasses import dataclass

from wayfindinglib.models.equipment_and_site.enclosure import Enclosure
from wayfindinglib.models.session.safe_state import SafeStateOutcome
from wayfindinglib.tasks.control_tasks.enclosure_control import mount_within_clearance


@dataclass
class SafeStateSteps:
    """The hardware-facing operations `execute` sequences.

    Injected so this module carries no hardware import.

    Every callable returns `True` on success, `False` on a handled
    failure; an exception is also treated as a failure so one
    misbehaving step cannot abort the sequence.
    """

    abandon_exposure: Callable[[], bool]
    stop_guiding: Callable[[], bool]
    park_mount: Callable[[], bool]
    get_mount_position: Callable[[], tuple[float, float]]
    close_enclosure: Callable[[], bool]
    warm_sensor: Callable[[], bool]
    close_session: Callable[[], bool]
    enclosure: Enclosure


def _attempt(action: Callable[[], bool]) -> bool:
    """Run one step, treating any raised exception as a failure.

    Returns
    -------
    succeeded : `bool`
        The step's own return value, or `False` if it raised.
    """
    try:
        return bool(action())
    except Exception:
        return False


def execute(trigger: str, steps: SafeStateSteps) -> SafeStateOutcome:
    """Run the six ordered safe-state steps and record the outcome.

    Parameters
    ----------
    trigger : `str`
        Why safe state was entered, e.g. ``"unsafe_verdict"``,
        ``"recovery_exhausted"``, ``"watchdog"``.
    steps : `SafeStateSteps`
        The injected hardware-facing operations to sequence.

    Returns
    -------
    outcome : `SafeStateOutcome`
        Per-step success and the first step name that failed, if any.
    """
    first_failure: str | None = None

    def record(step_name: str, succeeded: bool) -> bool:
        nonlocal first_failure
        if not succeeded and first_failure is None:
            first_failure = step_name
        return succeeded

    exposure_abandoned = record("exposure", _attempt(steps.abandon_exposure))
    guiding_stopped = record("guiding", _attempt(steps.stop_guiding))
    mount_parked = record("mount", _attempt(steps.park_mount))

    enclosure_closed = False
    if mount_parked:
        altitude_deg, azimuth_deg = steps.get_mount_position()
        if mount_within_clearance(steps.enclosure, altitude_deg, azimuth_deg):
            enclosure_closed = record("enclosure", _attempt(steps.close_enclosure))
        else:
            # Mount reports parked but outside the clearance envelope --
            # closure is deliberately skipped (forcing it is the damage
            # case), but this mismatch is itself a real fault to surface.
            record("enclosure", False)

    sensor_warmed = record("sensor", _attempt(steps.warm_sensor))
    session_closed = record("session", _attempt(steps.close_session))

    return SafeStateOutcome(
        trigger=trigger,
        exposure_abandoned=exposure_abandoned,
        guiding_stopped=guiding_stopped,
        mount_parked=mount_parked,
        enclosure_closed=enclosure_closed,
        sensor_warmed=sensor_warmed,
        session_closed=session_closed,
        failed_step=first_failure,
    )
