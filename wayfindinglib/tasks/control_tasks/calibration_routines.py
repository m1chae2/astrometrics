"""Purpose: Guider/backlash calibration and polar-alignment-assist routines.

Description: The orchestrating routines of
`Wayfinding_Library_Architecture.md` Section 6.7, alongside the existing
pure computations in `guider_calibration_tasks.py` (deriving a
`GuiderCalibration` from a measured pulse run) and
`pointing_model.py` (fitting `MountPointingModel` from plate solves):

- `run_guider_calibration`: commands the known RA/Dec pulse pair
  `compute_guider_calibration` needs and measures the guide-star
  centroid shift on each axis.
- `run_backlash_calibration`: commands a deliberate Dec-direction
  reversal and measures the response delay directly -- far faster and
  cleaner than waiting to observe an organic reversal during normal
  guiding.
- `run_polar_alignment_assist`: fits `MountPointingModel` from
  whatever plate solves have been collected so far *this session*
  (typically just the first couple, taken deliberately during polar
  alignment) so the ME/MA magnitude can be reported back to the user
  for a real-time adjust-and-recheck loop, before imaging starts.

Every hardware-facing/measurement step is injected as a callable
(`GuiderCalibrationSteps`/`BacklashCalibrationSteps`), the same pattern
`safe_state.py`'s `SafeStateSteps` already uses, so this module carries
no hardware or image-processing import and is exercisable with fakes.
Callers (e.g. `ObservatoryControl`) supply real callables; this module
does not construct a default wiring itself, matching `SafeStateSteps`'s
own precedent -- no production code builds one of those either.

Deliberately not wired here: feeding `run_polar_alignment_assist`'s
result forward beyond this session. Its ME/MA terms describe tonight's
specific polar-alignment setup, not the mount -- see
`pointing_log_ingestion.py`'s module docstring.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from astrometricslib.exceptions import HardwareError
from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.analytics.guide_pulses import GUIDE_RATE_ARCSEC_PER_S
from wayfindinglib.analytics.pointing_model import fit_pointing_model
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.session.telemetry import MountPointingModel
from wayfindinglib.tasks.control_tasks.guider_calibration_tasks import compute_guider_calibration

CALIBRATION_MOVE_GUIDE_PX = TunedParameter(
    name="wayfinding.calibration_move_guide_px",
    default=3.53,
    unit="px",
    description="How far the guide star should move on the guide camera during one calibration pulse.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D145",
)
"""How far the guide star should move on the guide camera during one pulse.

The move must be large against the noise in the star's measured position, so
that the camera angle and the rate of each axis are measured well. The pulse
length follows from it (`calibration_pulse_seconds`): the move, times the guide
plate scale, over the mount's guide rate. The value was fitted so that the
reference setup keeps the 3.0 s pulse the library used before. That setup is
the Apertura 32 mm guide scope (focal length 121.05 mm) with the ZWO ASI120MC-S
guide camera (3.75 micrometer pixels), which has a plate scale of 6.39
arcsec/px, on a mount that guides at 7.52 arcsec/s: 3.0 s * 7.52 arcsec/s /
6.39 arcsec/px = 3.53 px. Design estimate, register row WAY-D145; the move has
not been checked against the position noise of the guide camera.
"""

BACKLASH_SETTLE_PULSE_SECONDS = TunedParameter(
    name="wayfinding.backlash_settle_pulse_seconds",
    default=1.0,
    unit="s",
    description="Length of the first Dec pulse that sets a direction of travel before the backlash probe.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D146",
)
"""Length of the pulse that sets a known direction of travel before the probe.

A method choice: one second is long enough to take up the slack in the gears in
one direction. Design estimate, register row WAY-D146.
"""

BACKLASH_REVERSAL_TEST_PULSE_SECONDS = TunedParameter(
    name="wayfinding.backlash_reversal_test_pulse_seconds",
    default=0.05,
    unit="s",
    description="Length of each small reversed Dec pulse of the backlash probe.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D146",
)
"""Length of each small reversed Dec pulse of the backlash probe.

A method choice. It sets the resolution of the measured delay: 50 ms, so the
delay is found to the nearest 50 ms. Design estimate, register row WAY-D146.
"""

BACKLASH_MAX_TEST_PULSES = TunedParameter(
    name="wayfinding.backlash_max_test_pulses",
    default=40,
    unit="pulses",
    description="Most small reversed pulses the backlash probe sends before it gives up.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D146",
)
"""Most small reversed pulses the backlash probe sends before it gives up.

A method choice: 40 pulses of 50 ms make a 2 s probing budget, which bounds a
stuck axis to a fixed run time. Design estimate, register row WAY-D146.
"""

BACKLASH_MOTION_THRESHOLD_PX = TunedParameter(
    name="wayfinding.backlash_motion_threshold_px",
    default=0.5,
    unit="px",
    description="Star movement between two guide frames that counts as the Dec axis moving again.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D146",
)
"""Star movement between two guide frames that counts as the axis moving.

It must be several times the noise in the guide star's measured position, or
noise alone would end the probe early. Half a pixel is that for a guide star of
normal brightness. Design estimate, register row WAY-D146; not yet checked
against the measured position noise of the guide camera.
"""


@dataclass
class GuiderCalibrationSteps:
    """The hardware-facing operations `run_guider_calibration` sequences.

    Every callable returning a centroid position returns it as
    ``(x_px, y_px)``.
    """

    pulse_ra: Callable[[float], bool]
    """Command an RA-axis calibration pulse of the given duration (sec)."""

    pulse_dec: Callable[[float], bool]
    """Command a Dec-axis calibration pulse of the given duration (sec)."""

    measure_guide_star_centroid: Callable[[], tuple[float, float]]
    """Capture a guide frame and return the guide star's centroid."""


@dataclass
class BacklashCalibrationSteps:
    """The hardware-facing operations `run_backlash_calibration` sequences."""

    pulse_dec: Callable[[str, float], bool]
    """Command a Dec-axis pulse: `(direction, duration_sec) -> success`.

    `direction` is caller-defined (e.g. ``"north"``/``"south"``) --
    this module never inspects it, only passes it straight to the
    callable, so it stays consistent with whatever the real
    `pulse_guide` direction convention is.
    """

    measure_guide_star_centroid: Callable[[], tuple[float, float]]
    """Capture a guide frame and return the guide star's centroid."""


def calibration_pulse_seconds(arcsec_per_pixel: float, guide_rate_arcsec_per_s: float | None = None) -> float:
    """Work out the calibration pulse that moves the guide star far enough.

    Parameters
    ----------
    arcsec_per_pixel : `float`
        The guide camera's plate scale.
    guide_rate_arcsec_per_s : `float`, optional
        How fast the mount moves the sky during a guide pulse, in
        arcseconds per second. `None` uses the equipment profile's value
        (`GUIDE_RATE_ARCSEC_PER_S`).

    Returns
    -------
    pulse_seconds : `float`
        The pulse length, in seconds, that moves the star by
        `CALIBRATION_MOVE_GUIDE_PX` pixels.
    """
    if guide_rate_arcsec_per_s is None:
        guide_rate_arcsec_per_s = resolve_parameter(GUIDE_RATE_ARCSEC_PER_S).value
    move_px = resolve_parameter(CALIBRATION_MOVE_GUIDE_PX).value
    return move_px * arcsec_per_pixel / guide_rate_arcsec_per_s


def run_guider_calibration(
    steps: GuiderCalibrationSteps,
    calibration_id: str,
    camera_id: str,
    telescope_id: str,
    arcsec_per_pixel: float,
    ra_pulse_duration_sec: float | None = None,
    dec_pulse_duration_sec: float | None = None,
) -> GuiderCalibration:
    """Command a known RA pulse, then a Dec pulse, deriving a calibration.

    Parameters
    ----------
    steps : `GuiderCalibrationSteps`
        The injected pulse/centroid-measurement operations.
    calibration_id : `str`
        Identifier for the resulting calibration record.
    camera_id, telescope_id : `str`
        The camera/telescope pairing this calibration applies to.
    arcsec_per_pixel : `float`
        The pairing's known plate scale.
    ra_pulse_duration_sec, dec_pulse_duration_sec : `float`, optional
        Duration of each axis's calibration pulse. `None` works it out
        from the plate scale and the mount's guide rate
        (`calibration_pulse_seconds`).

    Returns
    -------
    calibration : `GuiderCalibration`
        The derived camera angle and per-axis rates -- not persisted;
        the caller (`control.guiding._run_calibration`)
        decides whether and how to record it. May raise
        `compute_guider_calibration`'s `ValueError` if either pulse
        produced no measurable star displacement.

    Raises
    ------
    HardwareError
        If either pulse command reports failure.
    """
    if ra_pulse_duration_sec is None:
        ra_pulse_duration_sec = calibration_pulse_seconds(arcsec_per_pixel)
    if dec_pulse_duration_sec is None:
        dec_pulse_duration_sec = calibration_pulse_seconds(arcsec_per_pixel)
    ra_start_xy = steps.measure_guide_star_centroid()
    if not steps.pulse_ra(ra_pulse_duration_sec):
        raise HardwareError("RA calibration pulse command failed")
    ra_end_xy = steps.measure_guide_star_centroid()

    dec_start_xy = steps.measure_guide_star_centroid()
    if not steps.pulse_dec(dec_pulse_duration_sec):
        raise HardwareError("Dec calibration pulse command failed")
    dec_end_xy = steps.measure_guide_star_centroid()

    return compute_guider_calibration(
        calibration_id,
        camera_id,
        telescope_id,
        arcsec_per_pixel,
        ra_pulse_duration_sec,
        ra_start_xy,
        ra_end_xy,
        dec_pulse_duration_sec,
        dec_start_xy,
        dec_end_xy,
    )


def run_backlash_calibration(
    steps: BacklashCalibrationSteps,
    settle_direction: str = "north",
    reversed_direction: str = "south",
    settle_pulse_sec: float | None = None,
    reversal_test_pulse_sec: float | None = None,
    max_test_pulses: int | None = None,
    motion_detection_threshold_px: float | None = None,
) -> float:
    """Measure Dec backlash by reversing direction and probing in small steps.

    First pulses `settle_direction` once to establish a known direction
    of travel, then repeatedly pulses `reversed_direction` in small
    steps until the guide star's measured centroid has actually moved
    `motion_detection_threshold_px` in response -- the elapsed pulse
    time before that is the mechanical slack a real guiding correction
    would otherwise have to fight through unmeasured, every time the
    Dec axis reverses.

    Parameters
    ----------
    steps : `BacklashCalibrationSteps`
        The injected pulse/centroid-measurement operations.
    settle_direction, reversed_direction : `str`, optional
        The initial and reversed Dec directions, defaults ``"north"``/
        ``"south"`` -- passed straight through to `steps.pulse_dec`.
    settle_pulse_sec : `float`, optional
        Duration of the initial settling pulse. `None` uses
        `BACKLASH_SETTLE_PULSE_SECONDS` (1.0 s).
    reversal_test_pulse_sec : `float`, optional
        Duration of each small reversed-direction test pulse. `None` uses
        `BACKLASH_REVERSAL_TEST_PULSE_SECONDS` (0.05 s) -- kept short so
        the measured delay resolves finely rather than overshooting past
        when motion actually resumed.
    max_test_pulses : `int`, optional
        Upper bound on test pulses before giving up. `None` uses
        `BACKLASH_MAX_TEST_PULSES` (40, or 2 seconds of probing at the
        default pulse size) -- bounds a
        stuck/miscalibrated axis to a fixed run time rather than
        probing indefinitely.
    motion_detection_threshold_px : `float`, optional
        Minimum centroid displacement counted as "motion resumed". `None`
        uses `BACKLASH_MOTION_THRESHOLD_PX` (0.5 px).

    Returns
    -------
    backlash_estimate_ms : `float`
        The elapsed pulse duration, in milliseconds, before the star's
        position began moving in the reversed direction. Equal to
        ``max_test_pulses * reversal_test_pulse_sec * 1000`` (the full
        probing budget) if motion was never detected -- a real, if
        possibly incomplete, measurement, not a sentinel.
    """
    if settle_pulse_sec is None:
        settle_pulse_sec = resolve_parameter(BACKLASH_SETTLE_PULSE_SECONDS).value
    if reversal_test_pulse_sec is None:
        reversal_test_pulse_sec = resolve_parameter(BACKLASH_REVERSAL_TEST_PULSE_SECONDS).value
    if max_test_pulses is None:
        max_test_pulses = int(resolve_parameter(BACKLASH_MAX_TEST_PULSES).value)
    if motion_detection_threshold_px is None:
        motion_detection_threshold_px = resolve_parameter(BACKLASH_MOTION_THRESHOLD_PX).value
    steps.pulse_dec(settle_direction, settle_pulse_sec)
    previous_xy = steps.measure_guide_star_centroid()

    elapsed_ms = 0.0
    reversal_test_pulse_ms = reversal_test_pulse_sec * 1000.0
    for _ in range(max_test_pulses):
        steps.pulse_dec(reversed_direction, reversal_test_pulse_sec)
        elapsed_ms += reversal_test_pulse_ms
        current_xy = steps.measure_guide_star_centroid()
        displacement_px = math.hypot(current_xy[0] - previous_xy[0], current_xy[1] - previous_xy[1])
        if displacement_px >= motion_detection_threshold_px:
            return elapsed_ms
        previous_xy = current_xy

    return elapsed_ms


def run_polar_alignment_assist(
    attempts: list[dict[str, Any]],
    latitude_deg: float,
    longitude_deg: float,
) -> MountPointingModel:
    """Fit ME/MA from this session's plate solves for real-time adjustment.

    Meant to be called repeatedly as a live feedback loop during
    tonight's own polar-alignment routine: fit from the first couple of
    deliberate plate solves, report `model.me_arcsec`/`model.ma_arcsec`'s
    magnitude to the user, let them adjust the mount's altitude/azimuth
    adjusters, take another plate solve, and call again -- watching the
    magnitude trend toward zero confirms the adjustment direction was
    correct. Distinct from `pointing_log_ingestion.compute_pointing_model`
    in taking `attempts` directly rather than reading recorded history
    from a `ControlRecordStore`: this loop is meant to run before any of
    these solves are necessarily worth recording as real alignment
    attempts.

    This function deliberately does not translate `me_arcsec`/
    `ma_arcsec`'s sign into a physical "raise/lower"/"east/west"
    adjuster instruction -- that mapping depends on mount-specific and
    hemisphere-specific conventions this codebase has not established
    anywhere else, and guessing wrong would mislead a user adjusting
    real hardware. The caller (UI) is responsible for that translation
    using its own verified convention.

    Parameters
    ----------
    attempts : `list` [`dict` [`str`, `Any`]]
        This session's plate-solve records so far -- see
        `fit_pointing_model` for the expected shape.
    latitude_deg : `float`
        Observer latitude in decimal degrees. Required, so no stand-in
        site is assumed.
    longitude_deg : `float`
        Observer longitude in decimal degrees, east positive. Required: it
        sets the true hour angle of each solve.

    Returns
    -------
    model : `MountPointingModel`
        The freshly-fit model. `confidence` is ``"insufficient_data"``
        with fewer than 4 timed attempts, or when the attempts are too
        close together on the sky to separate the terms -- call again once
        more solves are in, rather than treating a weak fit as a real
        answer.
    """
    return fit_pointing_model(attempts, latitude_deg=latitude_deg, longitude_deg=longitude_deg)
