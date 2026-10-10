"""Abstract base class for hardware-control protocol drivers.

One driver interface exists per device type (mount, camera, focuser,
filter wheel, enclosure), not per protocol -- the same shape ASCOM
Alpaca uses (`ITelescopeV4`, `ICameraV4`, ...). INDI is the first
implementation of each; a future protocol implements the same
interfaces so devices in one equipment set can mix protocols.

All methods are `async def`: `ObservatoryControl` and the router above
it are already async, and INDI's own adapters bridge their underlying
synchronous `PyIndi` calls with `asyncio.to_thread`.
"""

import abc


class ProtocolDriver(abc.ABC):
    """Common lifecycle shared by every per-device-type protocol driver."""

    @property
    @abc.abstractmethod
    def protocol_name(self) -> str:
        """Short unique identifier used as a registry key, e.g. ``"indi"``."""

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable label, e.g. ``"INDI"``."""

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Establish the underlying hardware connection.

        Returns
        -------
        success : `bool`
            `True` if the connection was established.
        """

    @abc.abstractmethod
    async def disconnect(self) -> bool:
        """Tear down the underlying hardware connection.

        Returns
        -------
        success : `bool`
            `True` if the connection was closed.
        """

    @abc.abstractmethod
    async def is_connected(self) -> bool:
        """Report whether the underlying hardware connection is live.

        Returns
        -------
        connected : `bool`
            `True` if the device is currently connected.
        """
