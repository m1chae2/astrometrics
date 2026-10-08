"""Purpose: Abstract base class for guiding drivers.

Description: Guiding keeps a guide star still on the guide camera by
sending the mount short correction pulses. Three kinds of guider can do
this, chosen by the active telescope's ``guiding_protocol`` setting:

- ``phd2``: PHD2 runs the guiding. The driver reads PHD2's guide steps,
  and the pulses KStars/Ekos sends to the mount when PHD2 is quiet.
- ``internal``: KStars/Ekos (or this app) commands the mount itself. The
  driver reads the pulses the mount received.
- ``simulator``: a stand-in guider for tests and demonstrations. Its
  guide error is simulated.

A guiding driver has two calls. `read_samples` returns what another
guider measured since the last call. `run_cycle` runs one cycle of this
app's own guiding loop: expose, measure the error, and send the
correction. It receives the device commands it may use as
`GuideCommands`, so every pulse still passes the delegation check of
`control.guiding.pulse`.
"""

import abc
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.drivers.interfaces.base_protocol_driver import ProtocolDriver
from wayfindinglib.models.session.telemetry import GuidingSample, GuidingSampleSource

__all__ = ["GuideCommands", "GuidingDriver", "GuidingReading"]


class GuidingReading(BaseModel):
    """The guide samples a driver read, and where they came from."""

    model_config = ConfigDict(populate_by_name=True)

    samples: list[GuidingSample] = Field(default_factory=list, description="New samples, oldest first.")
    source: GuidingSampleSource | None = Field(
        default=None, description="Where the samples came from, or `None` when there are none."
    )


@dataclass(frozen=True)
class GuideCommands:
    """The device commands one guiding cycle may use.

    Attributes
    ----------
    expose : `Callable` [[`float`, `float` or `None`], `Any`]
        Start a guide camera exposure of the given length and gain.
    pulse : `Callable` [[`str`, `float`], `bool`]
        Send one guide pulse: a direction (``north``, ``south``, ``east``
        or ``west``) and a length in milliseconds.
    sleep : `Callable` [[`float`], `None`]
        Wait a number of seconds.
    """

    expose: Callable[[float, float | None], Any]
    pulse: Callable[[str, float], bool]
    sleep: Callable[[float], None]


class GuidingDriver(ProtocolDriver):
    """Abstract base for guiding drivers."""

    @abc.abstractmethod
    async def read_samples(self) -> GuidingReading:
        """Return what another guider measured since the last call.

        Returns
        -------
        reading : `GuidingReading`
            The new samples and their source. Empty when nothing new
            arrived.
        """

    @abc.abstractmethod
    async def run_cycle(
        self, exposure_seconds: float, gain: float | None, commands: GuideCommands
    ) -> GuidingSample | None:
        """Run one cycle of this app's own guiding loop.

        Parameters
        ----------
        exposure_seconds : `float`
            Guide exposure length.
        gain : `float` or `None`
            Guide camera gain, or `None` to leave it alone.
        commands : `GuideCommands`
            The device commands the cycle may use.

        Returns
        -------
        sample : `GuidingSample` or `None`
            The measured guide error and the pulses sent, or `None` if the
            cycle measured nothing.
        """
