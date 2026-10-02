"""INDI implementation of the `FocuserDriver` protocol ABC.

Wraps the same shared `IndiInterface` session as `IndiMountDriver`, per
`Wayfinding_Library_Architecture.md` §2.5.1a.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.protocols.focuser_driver import FocuserDriver


class IndiFocuserDriver(FocuserDriver):
    """Adapts a shared `IndiInterface` session to the `FocuserDriver` ABC."""

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
        """INDI focusers connect as part of the shared session's discovery.

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

    async def get_position(self) -> int:
        """Return the focuser's current absolute position.

        Returns
        -------
        position : `int`
            The absolute focuser position.
        """
        return await asyncio.to_thread(self._session.get_focuser_position)

    async def move_relative(self, steps: int) -> bool:
        """Move the focuser by a relative number of steps.

        Returns
        -------
        success : `bool`
            Whether the move command was issued successfully.
        """
        return await asyncio.to_thread(self._session.focus_move, steps)
