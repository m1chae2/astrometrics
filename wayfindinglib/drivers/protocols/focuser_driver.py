"""Abstract base class for focuser hardware-control protocol drivers."""

import abc

from wayfindinglib.drivers.protocols.base_protocol_driver import ProtocolDriver


class FocuserDriver(ProtocolDriver):
    """Abstract base for focuser hardware-control protocol drivers."""

    @abc.abstractmethod
    async def get_position(self) -> int:
        """Return the focuser's current absolute position.

        Returns
        -------
        position : `int`
            The absolute focuser position.
        """

    @abc.abstractmethod
    async def move_relative(self, steps: int) -> bool:
        """Move the focuser by a relative number of steps.

        Returns
        -------
        success : `bool`
            Whether the move command was issued successfully.
        """
