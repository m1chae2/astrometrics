"""Purpose: Run a capture with the main camera: filter, exposures, dithering.

Description: `capture_frames` is the work behind
`control.imaging.capture_image`. It turns the filter wheel once (a run
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
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from astrometricslib import ConfigurationError, HardwareError, InvalidArgumentError, get_current_job
from wayfindinglib.analytics.guide_pulses import arcsec_to_pulse_ms
from wayfindinglib.models.planning.observation_package import DitherConfig
from wayfindinglib.models.session.capture_result import CaptureResult
from wayfindinglib.tasks.control_tasks import hardware_operations

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = ["READOUT_SECONDS", "capture_frames", "dither_pulses_ms", "resolve_dither"]

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
    from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration

    telescope = context.active_telescope()
    camera = context.active_camera()
    if telescope is None or camera is None:
        raise ConfigurationError(
            "Dithering needs an active telescope and camera to turn pixels into a pulse length."
        )
    plate_scale = EquipmentConfiguration(telescope=telescope, camera=camera).plate_scale_arcsec_per_px()
    return arcsec_to_pulse_ms(config.pixels * plate_scale)


def capture_frames(
    context: ControlContext,
    exposure_seconds: float,
    count: int = 1,
    filter_name: str | None = None,
    dither: bool | DitherConfig | dict = False,
    delay_seconds: float = 0.0,
) -> CaptureResult:
    """Take `count` exposures with the main camera.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers and the delegation policy.
    exposure_seconds : `float`
        Length of each exposure, in seconds.
    count : `int`, optional
        Number of exposures.
    filter_name : `str`, optional
        Filter to turn to before the first exposure. `None` leaves the
        wheel where it is.
    dither : `bool`, `DitherConfig` or `dict`, optional
        Dithering between exposures (see `resolve_dither`).
    delay_seconds : `float`, optional
        Pause between exposures, for example to let the mount settle
        after a dither. Not applied after the last exposure.

    Returns
    -------
    result : `CaptureResult`
        How many frames were taken, with which filter, and how many
        dithers.

    Raises
    ------
    InvalidArgumentError
        If `exposure_seconds` or `count` is not above zero, or
        `delay_seconds` is negative.
    HardwareError
        If the camera does not start an exposure, or the filter wheel
        does not turn.
    """
    if exposure_seconds <= 0:
        raise InvalidArgumentError("exposure_seconds must be greater than zero.")
    if count <= 0:
        raise InvalidArgumentError("count must be greater than zero.")
    if delay_seconds < 0:
        raise InvalidArgumentError("delay_seconds cannot be negative.")
    dither_config = resolve_dither(dither)
    pulse_ms = dither_pulses_ms(context, dither_config) if dither_config.enabled and count > 1 else 0.0
    job = get_current_job()

    # One filter for the whole run: a run is defined as one filter, and
    # turning the wheel between frames only adds settling time.
    if filter_name:
        if job is not None:
            job.stage(0, f"Selecting filter {filter_name}")
        hardware_operations.set_filter(context, filter_name)

    result = CaptureResult(exposure_seconds=exposure_seconds, filter_name=filter_name or None)
    for index in range(count):
        if job is not None:
            job.stage(int(index / count * 100), f"Capturing frame {index + 1}/{count}")
        logger.info("Capturing frame %s/%s (%s s)", index + 1, count, exposure_seconds)
        if not hardware_operations.capture_image(context, exposure_seconds):
            raise HardwareError(f"The camera did not start frame {index + 1} of {count}.")
        time.sleep(exposure_seconds + READOUT_SECONDS)
        result.frames_captured += 1
        if index == count - 1:
            break
        if pulse_ms > 0 and (index + 1) % dither_config.every_n_frames == 0:
            directions = DITHER_PATTERN[result.dithers % len(DITHER_PATTERN)]
            logger.info("Dithering %s by %.0f ms", " and ".join(directions), pulse_ms)
            for direction in directions:
                hardware_operations.pulse(context, direction, pulse_ms)
            result.dithers += 1
        if delay_seconds:
            time.sleep(delay_seconds)
    return result
