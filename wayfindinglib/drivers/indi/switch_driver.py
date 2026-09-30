"""INDI implementation of the `SwitchDriver` protocol ABC.

Wraps the same shared `IndiInterface` session as `IndiMountDriver`,
backed by `wayfindinglib/drivers/indi/switch_controller.py`'s
`SwitchController` (M12) -- the heuristically-discovered powerbox
device's `POWER_CONTROL`/`DEW_PWM`/`POWER_SENSORS` properties.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.protocols.switch_driver import SwitchDriver


class IndiSwitchDriver(SwitchDriver):
    """Adapts a shared `IndiInterface` session to the `SwitchDriver` ABC."""

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
        """INDI switches connect as part of the shared session's discovery.

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

    async def get_switch_states(self) -> dict[str, bool]:
        """Return the powerbox's boolean outlet states.

        Returns
        -------
        states : `dict` [`str`, `bool`]
            Outlet name to on/off state.
        """
        return await asyncio.to_thread(self._session.get_switch_states)

    async def set_switch_state(self, switch_name: str, on: bool) -> bool:
        """Command one boolean outlet on or off.

        Returns
        -------
        success : `bool`
            Whether the command was issued successfully.
        """
        return await asyncio.to_thread(self._session.set_switch_state, switch_name, on)

    async def get_variable_values(self) -> dict[str, float]:
        """Return the powerbox's dew-heater/sensor variable values.

        Returns
        -------
        values : `dict` [`str`, `float`]
            Variable name to value.
        """
        return await asyncio.to_thread(self._session.get_switch_variable_values)

    async def set_variable_value(self, name: str, value: float) -> bool:
        """Command one dew-heater variable to a new duty cycle.

        Returns
        -------
        success : `bool`
            Whether the command was issued successfully.
        """
        return await asyncio.to_thread(self._session.set_switch_variable_value, name, value)
