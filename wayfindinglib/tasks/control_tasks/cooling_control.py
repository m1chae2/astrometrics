"""Purpose: Camera Thermal Ramp Management.

Description: Ramps sensor temperature toward `CoolingPolicy.target_temp_c`
at no more than the configured rate, and reports settled only once the
reading holds within tolerance, per
`Wayfinding_Library_Architecture.md` §2.5.8. Warm-up is the same ramp
run toward a warmer target (ambient, typically) rather than a distinct
algorithm, and is a step in the safe-state sequence
(`safe_state.py`) -- uncontrolled warming risks condensation on optics
the enclosure has just sealed in.

Every function here is a pure computation over already-read
temperatures; the polling loop that reads the sensor and commands each
intermediate setpoint is a thin orchestration step layered on top, not
part of this module.
"""

from wayfindinglib.models.equipment_and_site.equipment import Camera, CoolingPolicy


def effective_ramp_rate_c_per_min(policy: CoolingPolicy, camera: Camera | None) -> float:
    """Return the ramp rate to use, respecting the sensor's own maximum.

    Returns
    -------
    ramp_c_per_min : `float`
        `policy.ramp_c_per_min`, capped at `camera.max_cooling_ramp_c_per_min`
        when a camera is given.
    """
    if camera is None:
        return policy.ramp_c_per_min
    return min(policy.ramp_c_per_min, camera.max_cooling_ramp_c_per_min)


def compute_ramped_setpoint(
    current_setpoint_c: float, target_temp_c: float, ramp_c_per_min: float, elapsed_sec: float
) -> float:
    """Compute the next intermediate setpoint, bounded by the ramp rate.

    Works symmetrically for cooling and warm-up: the step moves from
    `current_setpoint_c` toward `target_temp_c`, in whichever direction
    that is, clamped so it never overshoots the target and never moves
    faster than `ramp_c_per_min`.

    Returns
    -------
    setpoint_c : `float`
        The next setpoint to command.
    """
    max_step_c = ramp_c_per_min * (elapsed_sec / 60.0)
    remaining_c = target_temp_c - current_setpoint_c
    if abs(remaining_c) <= max_step_c:
        return target_temp_c
    step_c = max_step_c if remaining_c > 0.0 else -max_step_c
    return current_setpoint_c + step_c


def is_settled(current_temp_c: float, target_temp_c: float, settle_tolerance_c: float) -> bool:
    """Return whether the current reading holds within tolerance.

    Returns
    -------
    settled : `bool`
        `True` if `current_temp_c` is within `settle_tolerance_c` of
        `target_temp_c`.
    """
    return abs(current_temp_c - target_temp_c) <= settle_tolerance_c


def has_settle_timed_out(elapsed_sec: float, settle_timeout_sec: int) -> bool:
    """Return whether the settle wait has exceeded its configured bound.

    Returns
    -------
    timed_out : `bool`
        `True` if `elapsed_sec` exceeds `settle_timeout_sec`.
    """
    return elapsed_sec > settle_timeout_sec
