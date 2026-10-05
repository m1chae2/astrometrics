"""Purpose: Guider/backlash calibration and polar-alignment-assist routines.

Description: The orchestrating routines `Wayfinding_Library_Architecture.md`
§2.5.1a's §6a extension (M7b) adds alongside the existing pure
computations in `guider_calibration_tasks.py` (deriving a
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

from wayfindinglib.analytics.pointing_model import fit_pointing_model
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.session.telemetry import MountPointingModel
from wayfindinglib.tasks.control_tasks.guider_calibration_tasks import compute_guider_calibration


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


def run_guider_calibration(
    steps: GuiderCalibrationSteps,
    calibration_id: str,
    camera_id: str,
    telescope_id: str,
    arcsec_per_pixel: float,
    ra_pulse_duration_sec: float = 3.0,
    dec_pulse_duration_sec: float = 3.0,
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
        Duration of each axis's calibration pulse, default 3.0 sec.

    Returns
    -------
    calibration : `GuiderCalibration`
        The derived camera angle and per-axis rates -- not persisted;
        the caller (`control.guiding.run_calibration`)
        decides whether and how to record it. May raise
        `compute_guider_calibration`'s `ValueError` if either pulse
        produced no measurable star displacement.

    Raises
    ------
    RuntimeError
        If either pulse command reports failure.
    """
    ra_start_xy = steps.measure_guide_star_centroid()
    if not steps.pulse_ra(ra_pulse_duration_sec):
        raise RuntimeError("RA calibration pulse command failed")
    ra_end_xy = steps.measure_guide_star_centroid()

    dec_start_xy = steps.measure_guide_star_centroid()
    if not steps.pulse_dec(dec_pulse_duration_sec):
        raise RuntimeError("Dec calibration pulse command failed")
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
    settle_pulse_sec: float = 1.0,
    reversal_test_pulse_sec: float = 0.05,
    max_test_pulses: int = 40,
    motion_detection_threshold_px: float = 0.5,
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
        Duration of the initial settling pulse, default 1.0 sec.
    reversal_test_pulse_sec : `float`, optional
        Duration of each small reversed-direction test pulse, default
        0.05 sec (50 ms) -- kept short so the measured delay resolves
        finely rather than overshooting past when motion actually
        resumed.
    max_test_pulses : `int`, optional
        Upper bound on test pulses before giving up, default 40 (2
        seconds of probing at the default pulse size) -- bounds a
        stuck/miscalibrated axis to a fixed run time rather than
        probing indefinitely.
    motion_detection_threshold_px : `float`, optional
        Minimum centroid displacement counted as "motion resumed",
        default 0.5 px.

    Returns
    -------
    backlash_estimate_ms : `float`
        The elapsed pulse duration, in milliseconds, before the star's
        position began moving in the reversed direction. Equal to
        ``max_test_pulses * reversal_test_pulse_sec * 1000`` (the full
        probing budget) if motion was never detected -- a real, if
        possibly incomplete, measurement, not a sentinel.
    """
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
    latitude_deg: float = 45.0,
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
    from a `LoggerInterface`: this loop is meant to run before any of
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
    latitude_deg : `float`, optional
        Observer latitude in decimal degrees, defaults to 45.0.

    Returns
    -------
    model : `MountPointingModel`
        The freshly-fit model. `confidence` is ``"insufficient_data"``
        with fewer than 4 attempts -- call again once more solves are
        in, rather than treating a too-small fit as a real answer.
    """
    return fit_pointing_model(attempts, latitude_deg=latitude_deg)
