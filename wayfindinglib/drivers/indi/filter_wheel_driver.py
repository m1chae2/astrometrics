"""INDI implementation of the `FilterWheelDriver` protocol ABC.

Wraps the same shared `IndiInterface` session as `IndiMountDriver`, per
`Wayfinding_Library_Architecture.md` §2.5.1a.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.protocols.filter_wheel_driver import FilterWheelDriver


class IndiFilterWheelDriver(FilterWheelDriver):
    """Adapts a shared `IndiInterface` session to `FilterWheelDriver`."""

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
        """Filter wheels connect via the shared session's discovery.

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

    async def get_names(self) -> list[str]:
        """Return the names of the filters configured on this wheel.

        Returns
        -------
        filter_names : `list` [`str`]
            Names of the configured filters.
        """
        return await asyncio.to_thread(self._session.get_filter_names)

    async def get_current_filter(self) -> str | None:
        """Return the currently-selected filter name.

        Returns
        -------
        filter_name : `str` | `None`
            The current filter name, or `None` if unknown.
        """
        status = await asyncio.to_thread(self._session.get_status)
        return status.filter

    async def resolve_name(self, filter_name: str) -> str | None:
        """Resolve a fuzzy filter name to a configured filter name.

        Returns
        -------
        resolved_name : `str` | `None`
            The matching configured filter name, or `None` if no
            filter matches.
        """
        return await asyncio.to_thread(self._session.resolve_filter_name, filter_name)

    async def set_position(self, filter_name: str) -> bool:
        """Slew the filter wheel to a named filter.

        Returns
        -------
        success : `bool`
            Whether the filter-wheel command was issued successfully.
        """
        return await asyncio.to_thread(self._session.set_filterwheel_position, filter_name)
