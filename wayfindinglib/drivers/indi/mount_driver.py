"""INDI implementation of the `MountDriver` protocol ABC.

Wraps an `IndiInterface` session -- the session's connection lifecycle,
device discovery, and passive telemetry capture are shared across every
`Indi*Driver` adapter for the same rig, per
`Wayfinding_Library_Architecture.md` §2.5.1a: one INDI client connection
serves every device type, so splitting connection ownership per device
type would duplicate and re-test that shared lifecycle. `IndiInterface`
itself is synchronous (`PyIndi.BaseClient` is callback-based); each
method here bridges with `asyncio.to_thread`.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.protocols.mount_driver import MountDriver, MountStatus


class IndiMountDriver(MountDriver):
    """Adapts a shared `IndiInterface` session to the `MountDriver` ABC."""

    def __init__(self, session: Any) -> None:
        """Wrap an existing `IndiInterface`/`SimulatorIndiInterface`.

        Parameters
        ----------
        session : `IndiInterface`
            The shared INDI session other `Indi*Driver` adapters for
            this rig also wrap.
        """
        self._session = session

    @property
    def protocol_name(self) -> str:
        """Registry key for this driver."""
        return "indi"

    @property
    def display_name(self) -> str:
        """Human-readable label for this driver."""
        return "INDI"

    async def connect(self) -> bool:
        """Establish the shared INDI session's server connection.

        Returns
        -------
        success : `bool`
            Whether a telescope device was found on the session.
        """
        return await asyncio.to_thread(lambda: bool(self._session.connect_to_telescope()))

    async def disconnect(self) -> bool:
        """Tear down the shared INDI session's server connection.

        The mount driver owns the one real teardown call (every other
        `Indi*Driver` shares the same session and no-ops here instead
        of disconnecting it a second time).

        Returns
        -------
        success : `bool`
            Always `True` -- `disconnectServer` is best-effort against
            a connection that may already be down.
        """
        await asyncio.to_thread(self._session.disconnectServer)
        return True

    async def is_connected(self) -> bool:
        """Report whether the shared INDI session's server is connected.

        Returns
        -------
        connected : `bool`
            Whether the underlying INDI server connection is live.
        """
        return await asyncio.to_thread(self._session.isServerConnected)

    async def get_status(self) -> MountStatus:
        """Return the mount-only subset of the session's telescope status.

        Returns
        -------
        status : `MountStatus`
            The mount's current coordinates, tracking, and target.
        """
        status = await asyncio.to_thread(self._session.get_status)
        return MountStatus(
            ra=status.ra,
            dec=status.dec,
            altitude=status.altitude,
            azimuth=status.azimuth,
            trackingStatus=status.tracking_status,
            connectionStatus=status.connection_status,
            targetName=status.target_name,
        )

    async def slew(self, ra: float, dec: float) -> bool:
        """Slew the mount, per `MountDriver.slew`.

        Returns
        -------
        success : `bool`
            Whether the slew command was issued successfully.
        """
        return await asyncio.to_thread(self._session.slew, ra, dec)

    async def sync(self, ra: float, dec: float) -> bool:
        """Sync the mount, per `MountDriver.sync`.

        Returns
        -------
        success : `bool`
            Whether the sync command was issued successfully.
        """
        return await asyncio.to_thread(self._session.sync_coordinates, ra, dec)

    async def park(self) -> bool:
        """Park the mount, per `MountDriver.park`.

        Returns
        -------
        success : `bool`
            Whether the park command was issued successfully.
        """
        return await asyncio.to_thread(self._session.park)

    async def unpark(self) -> bool:
        """Unpark the mount, per `MountDriver.unpark`.

        Returns
        -------
        success : `bool`
            Whether the unpark command was issued successfully.
        """
        return await asyncio.to_thread(self._session.unpark)

    async def set_tracking(self, enabled: bool) -> bool:
        """Set mount tracking, per `MountDriver.set_tracking`.

        Returns
        -------
        success : `bool`
            Whether the tracking command was issued successfully.
        """
        return await asyncio.to_thread(self._session.set_tracking, enabled)

    async def abort_motion(self) -> bool:
        """Abort mount motion, per `MountDriver.abort_motion`.

        Returns
        -------
        success : `bool`
            Whether the abort command was issued successfully.
        """
        return await asyncio.to_thread(self._session.abort_motion)

    async def move(self, direction: str, start: bool) -> bool:
        """Manually move the mount, per `MountDriver.move`.

        Returns
        -------
        success : `bool`
            Whether the move command was issued successfully.
        """
        return await asyncio.to_thread(self._session.move, direction, start)

    async def set_slew_rate(self, rate_index: int) -> bool:
        """Set the mount's manual-slew rate.

        Returns
        -------
        success : `bool`
            Whether the slew-rate command was issued successfully.
        """
        return await asyncio.to_thread(self._session.set_slew_rate, rate_index)

    async def pulse_guide(self, direction: str, duration_ms: float) -> bool:
        """Send an ST4 pulse, per `MountDriver.pulse_guide`.

        Returns
        -------
        success : `bool`
            Whether the pulse-guide command was issued successfully.
        """
        return await asyncio.to_thread(self._session.pulse_guide, direction, duration_ms)

    async def get_observer_location(self) -> dict[str, float] | None:
        """Return the mount's configured observer location.

        Returns
        -------
        location : `dict` [`str`, `float`] | `None`
            Latitude/longitude/elevation, or `None` if unavailable.
        """
        location = await asyncio.to_thread(self._session.get_observer_location)
        if location is None:
            return None
        latitude, longitude, elevation = location
        return {"latitude": latitude, "longitude": longitude, "elevation": elevation}

    async def drain_external_pulses(self) -> list[dict]:
        """Drain externally-issued guide pulses.

        Returns
        -------
        pulses : `list` [`dict`]
            Guide pulses detected since the last drain.
        """
        return await asyncio.to_thread(self._session.drain_external_pulses)
