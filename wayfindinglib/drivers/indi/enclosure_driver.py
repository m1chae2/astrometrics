"""INDI implementation of the `EnclosureDriver` protocol ABC.

Genuinely new ground -- unlike mount/camera/focuser/filter-wheel, there
is no existing `EnclosureController` to adapt (verified: no
`enclosure_controller.py` exists under `wayfindinglib/drivers/indi/`).
This adapter's shape is settled now so the protocol registry resolves
``"indi"`` for every device type; wiring it to a real INDI roof/dome
device (an `EnclosureController` + the actual property names for the
user's hardware) is real, separate work, tracked for M6.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.protocols.enclosure_driver import EnclosureDriver, EnclosureState


class IndiEnclosureDriver(EnclosureDriver):
    """Adapts a shared `IndiInterface` session to the `EnclosureDriver` ABC.

    Not yet backed by a real INDI roof/dome device -- see module
    docstring. Connection lifecycle works today; `get_state`/`open`/
    `close` raise `NotImplementedError` until M6 adds an
    `EnclosureController`.
    """

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
        """Return the enclosure state.

        Raises
        ------
        NotImplementedError
            No `EnclosureController` exists yet (tracked for M6).
        """
        raise NotImplementedError("IndiEnclosureDriver.get_state: no EnclosureController implemented yet")

    async def open(self) -> bool:
        """Open the enclosure.

        Raises
        ------
        NotImplementedError
            No `EnclosureController` exists yet (tracked for M6).
        """
        raise NotImplementedError("IndiEnclosureDriver.open: no EnclosureController implemented yet")

    async def close(self) -> bool:
        """Close the enclosure.

        Raises
        ------
        NotImplementedError
            No `EnclosureController` exists yet (tracked for M6).
        """
        raise NotImplementedError("IndiEnclosureDriver.close: no EnclosureController implemented yet")
