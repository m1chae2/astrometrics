"""Purpose: Run a capture with the main camera: filter, exposures, dithering.

Description: `capture_frames` is the work behind
`control.imaging.capture`, and `check_capture` the check behind
`control.imaging.check_capture`. The check runs first, before any device
is touched, so a refused capture (a bad argument, or monitor mode) fails
at once and a caller that runs the capture on its own thread can check
first. It turns the filter wheel once (a run
uses one filter), then takes the exposures one after another. It waits
for each exposure plus a short readout time, because the camera driver
only starts an exposure and does not say when it ends.

Dithering means shifting the pointing slightly between exposures, so a
fixed defect of the sensor lands on a different part of the sky in each
frame and stacking removes it. A dither here is one guide pulse on each
axis, in a pattern that walks around the starting point. The pulse
length comes from the dither size in main-camera pixels, the main
camera's plate scale, and the guide rate.

When the call runs as a background job, each frame moves the job's
progress bar.

A run can be stopped from another thread (`control.imaging.stop`,
or a slew or park). The run checks its stop flag before each frame and
during the wait for each exposure. It then ends cleanly with the frames
it has, and the exposure in progress is aborted and not counted.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from astrometricslib.exceptions import ConfigurationError, HardwareError, InvalidArgumentError
from astrometricslib.jobs import get_current_job, registered_job
from wayfindinglib.analytics.guide_pulses import arcsec_to_pulse_ms
from wayfindinglib.models.planning.observation_package import DitherConfig
from wayfindinglib.models.policy.delegation import ObservatoryCapability
from wayfindinglib.models.session.capture_result import CaptureResult
from wayfindinglib.tasks.control_tasks import hardware_operations

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = ["READOUT_SECONDS", "capture_frames", "check_capture", "dither_pulses_ms", "resolve_dither"]

logger = logging.getLogger(__name__)

READOUT_SECONDS = 0.5
"""Time allowed after each exposure for the camera to read the sensor out."""

DITHER_PATTERN = (("north", "west"), ("south", "east"), ("south", "west"), ("north", "east"))
"""The declination and right ascension directions of successive dithers.
The pattern returns to the start after four dithers, so the pointing
never wanders far."""


def resolve_dither(dither: bool | DitherConfig | dict) -> DitherConfig:
    """Turn the `dither` argument into a `DitherConfig`.

    Parameters
    ----------
    dither : `bool`, `DitherConfig` or `dict`
        `True` for the default cadence (every 3 frames, 3 pixels),
        `False` for none, or a full configuration.

    Returns
    -------
    config : `DitherConfig`
        The dithering to apply.
    """
    if isinstance(dither, DitherConfig):
        return dither
    if isinstance(dither, dict):
        return DitherConfig.model_validate(dither)
    return DitherConfig(enabled=bool(dither))


def dither_pulses_ms(context: ControlContext, config: DitherConfig) -> float:
    """Return the guide pulse length that shifts the image by `config.pixels`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the active telescope and camera.
    config : `DitherConfig`
        The dither size in main-camera pixels.

    Returns
    -------
    duration_ms : `float`
        The pulse length on each axis, in milliseconds.

    Raises
    ------
    ConfigurationError
        If no telescope and camera are active, so pixels cannot be turned
        into arcseconds.
    """
    from astrometricslib import ObservatoryConfiguration

    telescope = context.active_telescope()
    camera = context.active_camera()
    if telescope is None or camera is None:
        raise ConfigurationError(
            "Dithering needs an active telescope and camera to turn pixels into a pulse length."
        )
    plate_scale = ObservatoryConfiguration(telescope=telescope, camera=camera).plate_scale_arcsec_per_px
    return arcsec_to_pulse_ms(config.pixels * plate_scale)


def check_capture(
    context: ControlContext,
    exposure_s: float,
    count: int = 1,
    filter_name: str | None = None,
    dither: bool | DitherConfig = False,
    delay_s: float = 0.0,
) -> float:
    """Check that a capture may run, without touching a device.

    Checks the arguments, that `CAPTURE_ORCHESTRATION` is `AUTHORITATIVE`
    (and `AUTOGUIDING` when the run dithers), and that the active telescope
    and camera can size a dither.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the policy and the active equipment.
    exposure_s : `float`
        Length of each exposure, in seconds.
    count : `int`, optional
        Number of exposures.
    filter_name : `str`, optional
        Filter to turn to first. Not checked against the wheel here.
    dither : `bool` or `DitherConfig`, optional
        Dithering between exposures (see `resolve_dither`).
    delay_s : `float`, optional
        Pause between exposures, in seconds.

    Returns
    -------
    pulse_ms : `float`
        The dither pulse length, in milliseconds (0 for none).

    Raises
    ------
    InvalidArgumentError
        If `exposure_s` or `count` is not above zero, or `delay_s` is
        negative.

    Notes
    -----
    Raises `PermissionDeniedError` in monitor mode and
    `ConfigurationError` when a dither cannot be sized.
    """
    del filter_name  # the wheel checks the name when it turns
    if exposure_s <= 0:
        raise InvalidArgumentError("exposure_s must be greater than zero.")
    if count <= 0:
        raise InvalidArgumentError("count must be greater than zero.")
    if delay_s < 0:
        raise InvalidArgumentError("delay_s cannot be negative.")
    hardware_operations.require_authoritative(context, ObservatoryCapability.CAPTURE_ORCHESTRATION)
    dither_config = resolve_dither(dither)
    if not (dither_config.enabled and count > 1):
        return 0.0
    hardware_operations.require_authoritative(context, ObservatoryCapability.AUTOGUIDING)
    return dither_pulses_ms(context, dither_config)


def capture_frames(
    context: ControlContext,
    exposure_s: float,
    count: int = 1,
    filter_name: str | None = None,
    dither: bool | DitherConfig = False,
    delay_s: float = 0.0,
) -> CaptureResult:
    """Take `count` exposures with the main camera, as a ``capture`` job.

    Runs `check_capture` first, so nothing reaches a device when the run
    is refused.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers and the delegation policy.
    exposure_s : `float`
        Length of each exposure, in seconds.
    count : `int`, optional
        Number of exposures.
    filter_name : `str`, optional
        Filter to turn to before the first exposure. `None` leaves the
        wheel where it is.
    dither : `bool` or `DitherConfig`, optional
        Dithering between exposures (see `resolve_dither`).
    delay_s : `float`, optional
        Pause between exposures, for example to let the mount settle
        after a dither. Not applied after the last exposure.

    Returns
    -------
    result : `CaptureResult`
        How many frames were taken, with which filter, and how many
        dithers.

    Notes
    -----
    Raises what `check_capture` raises, and `HardwareError` if the camera
    does not start an exposure or the filter wheel does not turn.
    """
    pulse_ms = check_capture(context, exposure_s, count, filter_name, dither, delay_s)
    dither_config = resolve_dither(dither)
    with registered_job(job_type="capture", target_id=filter_name or "capture"):
        stop_flag = context.capture_runs.begin()
        try:
            return _take_frames(
                context, exposure_s, count, filter_name, dither_config, pulse_ms, delay_s, stop_flag
            )
        finally:
            context.capture_runs.end(stop_flag)


def _take_frames(
    context: ControlContext,
    exposure_seconds: float,
    count: int,
    filter_name: str | None,
    dither_config: DitherConfig,
    pulse_ms: float,
    delay_seconds: float,
    stop_flag: threading.Event,
) -> CaptureResult:
    """Run the filter change and the exposures of one registered run.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers.
    exposure_seconds : `float`
        Length of each exposure, in seconds.
    count : `int`
        Number of exposures.
    filter_name : `str` or `None`
        Filter to turn to first, or `None` to leave the wheel.
    dither_config : `DitherConfig`
        The dithering to apply.
    pulse_ms : `float`
        The dither pulse length, in milliseconds (0 for none).
    delay_seconds : `float`
        Pause between exposures.
    stop_flag : `threading.Event`
        Set from another thread to ask the run to stop.

    Returns
    -------
    result : `CaptureResult`
        What was taken. ``stopped`` is `True` if the flag ended the run.

    Raises
    ------
    HardwareError
        If the camera does not start an exposure, or the filter wheel
        does not turn.
    """
    job = get_current_job()

    # One filter for the whole run: a run is defined as one filter, and
    # turning the wheel between frames only adds settling time.
    if filter_name:
        if job is not None:
            job.stage(0, f"Selecting filter {filter_name}")
        hardware_operations.set_filter(context, filter_name)

    result = CaptureResult(exposure_s=exposure_seconds, filter_name=filter_name or None)
    for index in range(count):
        if stop_flag.is_set():
            return _finish_stopped(result)
        if job is not None:
            job.stage(int(index / count * 100), f"Capturing frame {index + 1}/{count}")
        logger.info("Capturing frame %s/%s (%s s)", index + 1, count, exposure_seconds)
        if not hardware_operations.capture_image(context, exposure_seconds):
            raise HardwareError(f"The camera did not start frame {index + 1} of {count}.")
        if _wait_or_stop(stop_flag, exposure_seconds + READOUT_SECONDS):
            # Stopped during the exposure. Abort it here too, in case the
            # stop arrived just as the exposure started.
            try:
                hardware_operations.abort_exposure(context)
            except Exception:  # the run is ending anyway; the abort is a courtesy
                logger.warning("The camera did not accept the abort for the stopped run.", exc_info=True)
            return _finish_stopped(result)
        result.frames_captured += 1
        if index == count - 1:
            break
        if pulse_ms > 0 and (index + 1) % dither_config.every_n_frames == 0:
            directions = DITHER_PATTERN[result.dithers % len(DITHER_PATTERN)]
            logger.info("Dithering %s by %.0f ms", " and ".join(directions), pulse_ms)
            for direction in directions:
                hardware_operations.pulse(context, direction, pulse_ms)
            result.dithers += 1
        if delay_seconds and _wait_or_stop(stop_flag, delay_seconds):
            return _finish_stopped(result)
    return result


def _wait_or_stop(stop_flag: threading.Event, seconds: float) -> bool:
    """Wait for a time, ending early if a stop is requested.

    Parameters
    ----------
    stop_flag : `threading.Event`
        The run's stop flag.
    seconds : `float`
        How long to wait.

    Returns
    -------
    stopped : `bool`
        `True` if the stop flag was set before the time passed.
    """
    return stop_flag.wait(seconds)


def _finish_stopped(result: CaptureResult) -> CaptureResult:
    """Mark a run as stopped and say so in the log and the job.

    Parameters
    ----------
    result : `CaptureResult`
        The run's result so far.

    Returns
    -------
    result : `CaptureResult`
        The same result, with ``stopped`` set.
    """
    result.stopped = True
    count = result.frames_captured
    message = f"Capture stopped after {count} frame{'' if count == 1 else 's'}"
    logger.info(message)
    job = get_current_job()
    if job is not None:
        job.info(message)
    return result
