"""INDI implementation of the `EnclosureDriver` protocol ABC.

Wraps the same shared `IndiInterface` session as `IndiMountDriver`,
backed by `wayfindinglib/drivers/indi/enclosure_controller.py`'s
`EnclosureController` (M6) -- the standard INDI Dome Interface's
``DOME_SHUTTER`` switch, the same property both roll-off-roof and
dome INDI drivers publish for shutter motion.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.protocols.enclosure_driver import EnclosureDriver, EnclosureState


class IndiEnclosureDriver(EnclosureDriver):
    """Adapts a shared `IndiInterface` session to the `EnclosureDriver` ABC."""

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
        """INDI enclosures connect as part of the shared session's discovery.

        Returns
        -------
        success : `bool`
            Whether the underlying INDI server connection is live.
        """
        return await asyncio.to_thread(self._session.isServerConnected)

    async def disconnect(self) -> bool:
        """INDI has no per-device disconnect; the shared session owns this.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    async def is_connected(self) -> bool:
        """Report whether the shared INDI session's server is connected.

        Returns
        -------
        connected : `bool`
            Whether the underlying INDI server connection is live.
        """
        return await asyncio.to_thread(self._session.isServerConnected)

    async def get_state(self) -> EnclosureState:
        """Return the enclosure's current motion state.

        Returns
        -------
        state : `EnclosureState`
            `UNKNOWN` if no enclosure device is discovered.
        """
        return await asyncio.to_thread(self._session.get_enclosure_state)

    async def open(self) -> bool:
        """Open the enclosure.

        Returns
        -------
        success : `bool`
            Whether the open command was issued and confirmed.
        """
        return await asyncio.to_thread(self._session.open_enclosure)

    async def close(self) -> bool:
        """Close the enclosure.

        Returns
        -------
        success : `bool`
            Whether the close command was issued and confirmed.
        """
        return await asyncio.to_thread(self._session.close_enclosure)
