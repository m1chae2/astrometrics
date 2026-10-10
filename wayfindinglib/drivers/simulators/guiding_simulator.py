"""Purpose: The ``simulator`` guiding driver: a stand-in guide loop.

Description: For tests and demonstrations without a sky. Each cycle
takes a guide exposure, then lets a simulated guide error wander by a
small random step on each axis (a random walk). It sends a correction
pulse proportional to the error, and the error shrinks by most of that
correction, the way a real mount responds. The samples are simulated,
so this app never stores them as guiding records.

Reading other guiders' samples works as for the ``internal`` driver: it
reads the guide pulses the mount (or the INDI simulator) received.
"""

import time

import numpy as np

from wayfindinglib.drivers.interfaces.guiding_driver import GuideCommands, GuidingDriver, GuidingReading
from wayfindinglib.models.session.telemetry import GuidingSample

__all__ = ["SimulatedGuidingDriver"]

DRIFT_STEP_ARCSEC = 0.5
"""Largest random change of the simulated error per cycle, on each axis."""

AGGRESSION = 0.7
"""Fraction of the error each correction tries to remove."""

PULSE_MS_PER_ARCSEC = 100.0
"""Pulse length per arcsecond of correction."""

MAXIMUM_PULSE_MS = 2000.0
"""Longest correction pulse."""

MINIMUM_PULSE_MS = 50.0
"""Corrections shorter than this are not sent."""

MOUNT_RESPONSE = 0.9
"""Fraction of a sent correction the simulated mount carries out."""

READOUT_SECONDS = 0.5
"""Wait after each guide exposure for the camera to read out."""


class SimulatedGuidingDriver(GuidingDriver):
    """A guide loop with a simulated guide error."""

    def __init__(self, fallback: GuidingDriver, random_source: np.random.Generator | None = None) -> None:
        """Keep the reader of other guiders' samples and the random source.

        Parameters
        ----------
        fallback : `GuidingDriver`
            Reads the guide pulses the mount received.
        random_source : `numpy.random.Generator`, optional
            Where the random walk's steps come from. A seeded one makes
            the walk repeatable.
        """
        self._fallback = fallback
        self._random = random_source if random_source is not None else np.random.default_rng()
        self._error_ra_arcsec = 0.0
        self._error_dec_arcsec = 0.0

    @property
    def protocol_name(self) -> str:
        """Registry key for this driver."""
        return "simulator"

    @property
    def display_name(self) -> str:
        """Human-readable label for this driver."""
        return "Guiding simulator"

    async def connect(self) -> bool:
        """Do nothing; there is no device.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    async def disconnect(self) -> bool:
        """Do nothing; there is no device.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    async def is_connected(self) -> bool:
        """Report that the simulator is always available.

        Returns
        -------
        connected : `bool`
            Always `True`.
        """
        return True

    async def read_samples(self) -> GuidingReading:
        """Return the guide pulses the mount received, as samples.

        Returns
        -------
        reading : `GuidingReading`
            The fallback driver's reading.
        """
        return await self._fallback.read_samples()

    def _correct(
        self, error_arcsec: float, positive: str, negative: str, commands: GuideCommands
    ) -> tuple[float, float]:
        """Send the correction for one axis and apply the mount's response.

        Parameters
        ----------
        error_arcsec : `float`
            The simulated error on this axis.
        positive, negative : `str`
            The pulse direction for a positive and a negative correction.
        commands : `GuideCommands`
            Sends the pulse.

        Returns
        -------
        error_arcsec, pulse_ms : `float`
            The error after the correction, and the pulse length (0 when
            none was sent).
        """
        correction = -error_arcsec * AGGRESSION
        pulse_ms = min(abs(correction) * PULSE_MS_PER_ARCSEC, MAXIMUM_PULSE_MS)
        if pulse_ms <= MINIMUM_PULSE_MS:
            return error_arcsec, pulse_ms
        commands.pulse(positive if correction > 0 else negative, int(pulse_ms))
        return error_arcsec + correction * MOUNT_RESPONSE, pulse_ms

    async def run_cycle(
        self, exposure_seconds: float, gain: float | None, commands: GuideCommands
    ) -> GuidingSample | None:
        """Run one simulated guide cycle.

        Parameters
        ----------
        exposure_seconds : `float`
            Guide exposure length.
        gain : `float` or `None`
            Guide camera gain.
        commands : `GuideCommands`
            Exposes, pulses and waits.

        Returns
        -------
        sample : `GuidingSample` or `None`
            The simulated error after the correction and the pulses sent,
            or `None` if the guide camera did not start the exposure.
        """
        if not commands.expose(exposure_seconds, gain):
            return None
        commands.sleep(exposure_seconds + READOUT_SECONDS)
        self._error_ra_arcsec += self._random.uniform(-DRIFT_STEP_ARCSEC, DRIFT_STEP_ARCSEC)
        self._error_dec_arcsec += self._random.uniform(-DRIFT_STEP_ARCSEC, DRIFT_STEP_ARCSEC)
        self._error_ra_arcsec, pulse_ra = self._correct(self._error_ra_arcsec, "west", "east", commands)
        self._error_dec_arcsec, pulse_dec = self._correct(self._error_dec_arcsec, "north", "south", commands)
        return GuidingSample(
            time=time.time(),
            dra=float(self._error_ra_arcsec),
            ddec=float(self._error_dec_arcsec),
            pulse_ra=pulse_ra,
            pulse_dec=pulse_dec,
            snr=float(self._random.uniform(15.0, 45.0)),
            star_mass=float(self._random.uniform(10000.0, 20000.0)),
        )
