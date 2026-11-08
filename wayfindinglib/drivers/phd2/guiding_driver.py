"""Purpose: The ``phd2`` guiding driver: read PHD2's guide steps.

Description: PHD2 reports every guide step over its event connection.
`PHD2GuidingService` keeps the connection open on a background thread
and stores each step as a guide sample. This driver hands those samples
on, labelled `PHD2_LIVE`. While the connection to PHD2 is open, PHD2 is the
only authority: the guide pulses the mount received are drained and thrown
away, because PHD2 also sends pulses while it calibrates (it sends no
GuideStep then), and those are not guiding. Only when PHD2 cannot be reached
(it is not running, or KStars/Ekos guides with its own guider) does the
driver read the mount's guide pulses instead, through the ``internal``
driver.

PHD2 runs its own guiding loop, so this driver refuses `run_cycle`.
"""

import logging

from astrometricslib.exceptions import ConflictError
from wayfindinglib.drivers.interfaces.guiding_driver import GuideCommands, GuidingDriver, GuidingReading
from wayfindinglib.drivers.phd2.phd2_guiding_service import PHD2GuidingService
from wayfindinglib.models.session.telemetry import GuidingSample, GuidingSampleSource

__all__ = ["Phd2GuidingDriver"]

logger = logging.getLogger(__name__)


class Phd2GuidingDriver(GuidingDriver):
    """Read guide steps from PHD2, or the mount's pulses when PHD2 is quiet."""

    def __init__(self, phd2_service: PHD2GuidingService, fallback: GuidingDriver) -> None:
        """Keep the PHD2 connection and the driver to fall back on.

        Parameters
        ----------
        phd2_service : `PHD2GuidingService`
            The PHD2 event connection.
        fallback : `GuidingDriver`
            Reads the mount's guide pulses when PHD2 has nothing new.
        """
        self._phd2 = phd2_service
        self._fallback = fallback

    @property
    def protocol_name(self) -> str:
        """Registry key for this driver."""
        return "phd2"

    @property
    def display_name(self) -> str:
        """Human-readable label for this driver."""
        return "PHD2"

    async def connect(self) -> bool:
        """Start reading PHD2's events in the background.

        Returns
        -------
        started : `bool`
            Always `True`; the connection is retried in the background.
        """
        self._phd2.poll_external_telemetry()
        return True

    async def disconnect(self) -> bool:
        """Stop reading PHD2's events.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self._phd2.stop()
        return True

    def close(self) -> None:
        """Stop reading PHD2's events for good, as at shutdown."""
        self._phd2.close()

    async def is_connected(self) -> bool:
        """Report whether the PHD2 event reader is running.

        Returns
        -------
        running : `bool`
            Whether the background reader is alive.
        """
        return self._phd2.is_running()

    async def read_samples(self) -> GuidingReading:
        """Return PHD2's new guide steps, or else the mount's pulses.

        While PHD2 is reachable it is the authority: the fallback driver is
        still read, so its pulses do not pile up, but its samples are
        dropped. They would include PHD2's calibration pulses, which are
        not guide corrections.

        Returns
        -------
        reading : `GuidingReading`
            ``PHD2_LIVE`` samples; an empty reading while PHD2 is reachable
            and has sent no step; the fallback driver's reading when PHD2 is
            not reachable.
        """
        try:
            self._phd2.poll_external_telemetry()
            samples = self._phd2.drain_guiding_samples()
        except RuntimeError as error:  # the reader thread could not start
            logger.debug("PHD2 telemetry poll skipped: %s", error)
            samples = []
        # Always drain the mount's pulses, so they do not pile up.
        fallback_reading = await self._fallback.read_samples()
        if samples:
            return GuidingReading(samples=samples, source=GuidingSampleSource.PHD2_LIVE)
        if self._phd2.is_reachable():
            return GuidingReading(samples=[], source=None)
        return fallback_reading

    async def run_cycle(
        self, exposure_seconds: float, gain: float | None, commands: GuideCommands
    ) -> GuidingSample | None:
        """Refuse: PHD2 runs its own guiding loop.

        Raises
        ------
        ConflictError
            Always.
        """
        raise ConflictError("PHD2 runs its own guiding loop. Start guiding in PHD2.")
