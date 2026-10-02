"""Abstract base class for enclosure hardware-control protocol drivers.

Genuinely new ground -- no existing INDI controller to adapt, unlike
the mount/camera/focuser/filter-wheel drivers.
"""

import abc

from wayfindinglib.drivers.protocols.base_protocol_driver import ProtocolDriver
from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState

__all__ = ["EnclosureDriver", "EnclosureState"]


class EnclosureDriver(ProtocolDriver):
    """Abstract base for enclosure hardware-control protocol drivers."""

    @abc.abstractmethod
    async def get_state(self) -> EnclosureState:
        """Return the enclosure's current open/closed state.

        Returns
        -------
        state : `EnclosureState`
            The current enclosure state.
        """

    @abc.abstractmethod
    async def open(self) -> bool:
        """Open the enclosure.

        Returns
        -------
        success : `bool`
            Whether the open command was issued successfully.
        """

    @abc.abstractmethod
    async def close(self) -> bool:
        """Close the enclosure.

        Returns
        -------
        success : `bool`
            Whether the close command was issued successfully.
        """
