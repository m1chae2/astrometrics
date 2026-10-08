"""Purpose: Follow the guiding going on now, and run this app's guide loop.

Description: `LiveGuidingMonitor` keeps the newest guide samples and the
running guiding error. Each new sample's root-mean-square (RMS) error
over the kept samples is the usual measure of guiding accuracy, in
arcseconds. When a guider reports its own RMS (PHD2 does), that value
is kept.

Two tasks feed the monitor, both through the active guiding driver
(`ControlContext.guiding_driver`):

- `poll` reads what another guider (PHD2, or KStars/Ekos through the
  mount's pulses) measured since the last call, adds it to the monitor
  and records it in the log database, labelled with where it came from.
  `control.guiding.status(include=["live"])` calls it.
- `run_loop` runs this app's own guide loop until asked to stop or until
  the mount stops tracking: one `run_cycle` of the driver after another.
  Every pulse goes through `control.guiding.pulse`, so it needs
  `AUTOGUIDING` to be `AUTHORITATIVE`. The loop's samples are shown but
  not recorded, because only the simulator can run it today.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from astrometricslib import ConflictError, ExternalServiceError, HardwareError
from wayfindinglib.analytics.guide_pulses import rms_arcsec
from wayfindinglib.drivers.interfaces.guiding_driver import GuideCommands
from wayfindinglib.models.session.telemetry import GuidingSample, GuidingStats, LiveGuidingStatus
from wayfindinglib.tasks.control_tasks import hardware_operations

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = ["LiveGuidingMonitor", "poll", "run_loop"]

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 100
"""How many of the newest samples the monitor keeps."""

ACTIVE_WINDOW_SECONDS = 5.0
"""A sample newer than this means another guider is guiding now."""

TRACKING_CHECKS = 5
"""How many times the loop checks that the mount tracks before it starts."""

TRACKING_RETRY_SECONDS = 0.5
"""Wait between those checks."""

LOST_TRACKING_LIMIT = 3
"""Cycles in a row without tracking after which the loop stops."""

RETRY_SECONDS = 1.0
"""Wait after a cycle that could not run, before the next try."""


class LiveGuidingMonitor:
    """The newest guide samples, the running RMS and the loop's settings."""

    def __init__(self) -> None:
        """Start with no samples, no loop, a 1 second exposure and gain 0."""
        self._lock = threading.Lock()
        self._history: deque[GuidingSample] = deque(maxlen=HISTORY_LIMIT)
        self._stats = GuidingStats()
        self.running = False
        self.exposure_seconds = 1.0
        self.gain = 0.0

    def add(self, samples: list[GuidingSample]) -> None:
        """Add new samples and update the running error.

        A sample without its own RMS gets the RMS of the kept samples up
        to and including it, rounded to a thousandth of an arcsecond.

        Parameters
        ----------
        samples : `list` [`GuidingSample`]
            New samples, oldest first.
        """
        with self._lock:
            for sample in samples:
                self._history.append(sample)
                if sample.rms_ra is None:
                    sample.rms_ra = round(rms_arcsec(item.dra for item in self._history), 3)
                if sample.rms_dec is None:
                    sample.rms_dec = round(rms_arcsec(item.ddec for item in self._history), 3)
                self._stats.rms_ra = sample.rms_ra
                self._stats.rms_dec = sample.rms_dec
                self._stats.rms_total = math.hypot(sample.rms_ra, sample.rms_dec)
                if sample.snr is not None:
                    self._stats.snr = sample.snr
                if sample.star_mass is not None:
                    self._stats.star_mass = sample.star_mass

    def start(self, exposure_seconds: float | None, gain: float | None) -> None:
        """Mark the loop as running, keep its settings and clear the samples.

        Parameters
        ----------
        exposure_seconds : `float` or `None`
            Guide exposure length. `None` keeps the last one.
        gain : `float` or `None`
            Guide camera gain. `None` keeps the last one.
        """
        with self._lock:
            if exposure_seconds is not None:
                self.exposure_seconds = exposure_seconds
            if gain is not None:
                self.gain = gain
            self._history.clear()
            self._stats = GuidingStats()
            self.running = True

    def stop(self) -> None:
        """Mark the loop as stopped."""
        self.running = False

    def status(self, now: float | None = None) -> LiveGuidingStatus:
        """Report the guiding going on now.

        Parameters
        ----------
        now : `float`, optional
            The current Unix time. The clock when omitted.

        Returns
        -------
        status : `LiveGuidingStatus`
            Guiding counts as active while the loop runs or while another
            guider's newest sample is under `ACTIVE_WINDOW_SECONDS` old.
        """
        now = time.time() if now is None else now
        with self._lock:
            history = list(self._history)
            stats = self._stats.model_copy()
        recent = bool(history) and now - history[-1].time < ACTIVE_WINDOW_SECONDS
        return LiveGuidingStatus(
            is_guiding=self.running or recent,
            stats=stats,
            history=history,
            exposure=self.exposure_seconds,
            gain=self.gain,
        )


def poll(context: ControlContext) -> None:
    """Read another guider's new samples into the monitor and the log database.

    Does nothing while this app's own loop runs: its samples would mix
    with the loop's.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the guiding driver, the monitor and the log database.
    """
    monitor = context.live_guiding
    if monitor.running:
        return
    reading = hardware_operations._run_sync(context.guiding_driver.read_samples())
    if not reading.samples:
        return
    monitor.add(reading.samples)
    target_name = hardware_operations.mount_status(context, ["mount"]).get("targetName")
    context.logger_interface.record_guiding_samples([
        {
            **sample.model_dump(),
            "target_name": target_name,
            "source": reading.source.value if reading.source else None,
        }
        for sample in reading.samples
    ])


def _tracking_status(context: ControlContext) -> str:
    """Read the mount's tracking state.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver.

    Returns
    -------
    status : `str`
        Such as ``"Tracking"``, ``"Idle"`` or ``"Parked"``.
    """
    return hardware_operations.mount_status(context, ["mount"]).get("trackingStatus", "Unknown")


def _wait_for_tracking(context: ControlContext, sleep: Callable[[float], None]) -> None:
    """Wait briefly for the mount to report that it tracks.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver.
    sleep : `Callable` [[`float`], `None`]
        Waits between checks.

    Raises
    ------
    ConflictError
        If the mount does not track after `TRACKING_CHECKS` checks.
    """
    status = "Unknown"
    for _ in range(TRACKING_CHECKS):
        status = _tracking_status(context)
        if status == "Tracking":
            return
        hardware_operations.connect(context)
        sleep(TRACKING_RETRY_SECONDS)
    raise ConflictError(
        f"Cannot start guiding: the mount is not tracking (status: {status}).", details={"status": status}
    )


def run_loop(
    context: ControlContext,
    stop: threading.Event,
    exposure_seconds: float | None = None,
    gain: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run this app's guide loop until `stop` is set or tracking is lost.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers, the policy and the monitor.
    stop : `threading.Event`
        Set it to end the loop after the current cycle.
    exposure_seconds : `float`, optional
        Guide exposure length. The last one used when omitted.
    gain : `float`, optional
        Guide camera gain. The last one used when omitted.
    sleep : `Callable` [[`float`], `None`], optional
        Waits; tests pass one that returns at once.

    Notes
    -----
    Raises `ConflictError` if the mount does not track when the loop
    starts or the guiding driver runs its own loop (PHD2), and
    `ConfigurationError` if the guiding driver cannot run this loop.
    """
    _wait_for_tracking(context, sleep)
    monitor = context.live_guiding
    monitor.start(exposure_seconds, gain)
    commands = GuideCommands(
        expose=lambda seconds, camera_gain: hardware_operations.guide_expose(context, seconds, camera_gain),
        pulse=lambda direction, duration_ms: hardware_operations.pulse(context, direction, duration_ms),
        sleep=sleep,
    )
    lost_tracking = 0
    logger.info("Guide loop started: %s s exposures, gain %s", monitor.exposure_seconds, monitor.gain)
    try:
        while not stop.is_set():
            status = _tracking_status(context)
            if status != "Tracking":
                lost_tracking += 1
                if lost_tracking >= LOST_TRACKING_LIMIT:
                    logger.warning("Guide loop stopped: the mount stopped tracking (%s).", status)
                    return
                sleep(RETRY_SECONDS)
                continue
            lost_tracking = 0
            try:
                sample = hardware_operations._run_sync(
                    context.guiding_driver.run_cycle(monitor.exposure_seconds, monitor.gain, commands)
                )
            except (HardwareError, ExternalServiceError) as error:
                logger.warning("Guide cycle failed, retrying: %s", error)
                sleep(RETRY_SECONDS)
                continue
            if sample is None:
                logger.warning("The guide camera did not start an exposure; retrying.")
                sleep(RETRY_SECONDS)
                continue
            monitor.add([sample])
    finally:
        monitor.stop()
        logger.info("Guide loop finished")
