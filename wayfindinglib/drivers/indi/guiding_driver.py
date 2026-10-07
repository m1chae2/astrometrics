"""Purpose: The ``internal`` guiding driver: the pulses the mount received.

Description: When KStars/Ekos guides with its own guider, it sends the
mount timed guide pulses directly. The shared INDI session watches the
mount's guide properties and queues each pulse it sees
(`IndiInterface.drain_external_pulses`). This driver turns those pulses
into guide samples: the right ascension and declination pulses of one
correction are merged, and each pulse length becomes the sky motion it
caused at the guide rate.

These samples are estimates, not measurements of the star: they say
how far the guider moved the mount, which is close to the error it saw.
They are labelled `INDI_PULSE_ESTIMATE`, and nothing is added to them.

This app's own guiding loop (`run_cycle`) needs to measure the guide
star on the guide camera, which the library cannot do yet, so this
driver refuses it.
"""

import asyncio
import time
from typing import Any

from astrometricslib import ConfigurationError
from wayfindinglib.analytics.guide_pulses import merge_close_pulses, pulse_ms_to_arcsec, signed_pulses_ms
from wayfindinglib.drivers.protocols.guiding_driver import GuideCommands, GuidingDriver, GuidingReading
from wayfindinglib.models.session.telemetry import GuidingSample, GuidingSampleSource

__all__ = ["IndiGuidingDriver", "samples_from_pulses"]

_SMALLEST_PULSE_MS = 1e-3
"""Pulses shorter than this count as no pulse."""


def samples_from_pulses(pulses: list[dict[str, Any]]) -> list[GuidingSample]:
    """Turn guide pulse records into guide samples.

    Parameters
    ----------
    pulses : `list` [`dict` [`str`, `Any`]]
        Pulse records in time order, each with ``time`` and the length in
        milliseconds of ``pulse_n``, ``pulse_s``, ``pulse_w`` and
        ``pulse_e``.

    Returns
    -------
    samples : `list` [`GuidingSample`]
        One sample per merged correction. ``dra`` and ``ddec`` are the
        sky motion in arcseconds (west and north positive), and
        ``pulse_ra`` and ``pulse_dec`` the pulse lengths. A correction
        with no pulse on either axis is left out.
    """
    samples = []
    for pulse in merge_close_pulses(pulses):
        ra_ms, dec_ms = signed_pulses_ms(pulse)
        if abs(ra_ms) < _SMALLEST_PULSE_MS and abs(dec_ms) < _SMALLEST_PULSE_MS:
            continue
        samples.append(
            GuidingSample(
                time=pulse.get("time", time.time()),
                dra=round(pulse_ms_to_arcsec(ra_ms), 3),
                ddec=round(pulse_ms_to_arcsec(dec_ms), 3),
                pulse_ra=abs(ra_ms),
                pulse_dec=abs(dec_ms),
            )
        )
    return samples


class IndiGuidingDriver(GuidingDriver):
    """Read the guide pulses another program sent to the mount over INDI."""

    def __init__(self, session: Any) -> None:
        """Wrap the shared INDI session.

        Parameters
        ----------
        session : `IndiInterface`
            The INDI session the other INDI drivers also wrap.
        """
        self._session = session

    @property
    def protocol_name(self) -> str:
        """Registry key for this driver."""
        return "internal"

    @property
    def display_name(self) -> str:
        """Human-readable label for this driver."""
        return "Guide pulses seen on the mount (INDI)"

    async def connect(self) -> bool:
        """Report whether the shared INDI session is connected.

        Returns
        -------
        connected : `bool`
            Whether the INDI server connection is live.
        """
        return await asyncio.to_thread(self._session.isServerConnected)

    async def disconnect(self) -> bool:
        """Do nothing; the shared session owns the connection.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    async def is_connected(self) -> bool:
        """Report whether the shared INDI session is connected.

        Returns
        -------
        connected : `bool`
            Whether the INDI server connection is live.
        """
        return await asyncio.to_thread(self._session.isServerConnected)

    async def read_samples(self) -> GuidingReading:
        """Return the pulses the mount got since the last call, as samples.

        Returns
        -------
        reading : `GuidingReading`
            The samples, labelled `INDI_PULSE_ESTIMATE`.
        """
        pulses = await asyncio.to_thread(self._session.drain_external_pulses)
        samples = samples_from_pulses(pulses or [])
        return GuidingReading(
            samples=samples, source=GuidingSampleSource.INDI_PULSE_ESTIMATE if samples else None
        )

    async def run_cycle(
        self, exposure_seconds: float, gain: float | None, commands: GuideCommands
    ) -> GuidingSample | None:
        """Refuse: the library cannot measure the guide star yet.

        Raises
        ------
        ConfigurationError
            Always.
        """
        raise ConfigurationError(
            "The internal guider cannot measure the guide star yet. Guide with PHD2 or KStars/Ekos, "
            "or set the telescope's guiding_protocol to 'simulator' to try the guiding loop."
        )
