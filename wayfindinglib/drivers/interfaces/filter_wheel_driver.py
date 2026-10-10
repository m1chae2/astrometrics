"""Abstract base class for filter wheel hardware-control protocol drivers."""

import abc

from wayfindinglib.drivers.interfaces.base_protocol_driver import ProtocolDriver


class FilterWheelDriver(ProtocolDriver):
    """Abstract base for filter wheel hardware-control protocol drivers."""

    @abc.abstractmethod
    async def get_names(self) -> list[str]:
        """Return the names of the filters configured on this wheel.

        Returns
        -------
        filter_names : `list` [`str`]
            Names of the configured filters.
        """

    @abc.abstractmethod
    async def get_current_filter(self) -> str | None:
        """Return the currently-selected filter name.

        Returns
        -------
        filter_name : `str` | `None`
            The current filter name, or `None` if unknown.
        """

    @abc.abstractmethod
    async def resolve_name(self, filter_name: str) -> str | None:
        """Resolve a fuzzy filter name to a configured filter name.

        Returns
        -------
        resolved_name : `str` | `None`
            The matching configured filter name, or `None` if no
            filter matches.
        """

    @abc.abstractmethod
    async def set_position(self, filter_name: str) -> bool:
        """Slew the filter wheel to a named filter.

        Returns
        -------
        success : `bool`
            Whether the filter-wheel command was issued successfully.
        """
