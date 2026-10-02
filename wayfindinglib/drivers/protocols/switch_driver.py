"""Abstract base class for switch/power-distribution protocol drivers.

Mirrors ASCOM's `ISwitch` split between boolean switches (a power
outlet is on/off) and variable switches (a dew heater is often a PWM
percentage, not just on/off) -- genuinely new ground, like
`EnclosureDriver`, since no existing INDI controller commands power
outlets or dew heaters today.
"""

import abc

from wayfindinglib.drivers.protocols.base_protocol_driver import ProtocolDriver


class SwitchDriver(ProtocolDriver):
    """Abstract base for switch/power-distribution protocol drivers."""

    @abc.abstractmethod
    async def get_switch_states(self) -> dict[str, bool]:
        """Return every boolean switch's current on/off state.

        Returns
        -------
        states : `dict` [`str`, `bool`]
            Switch name to on/off state, e.g. power outlets.
        """

    @abc.abstractmethod
    async def set_switch_state(self, switch_name: str, on: bool) -> bool:
        """Command one boolean switch on or off.

        Returns
        -------
        success : `bool`
            Whether the command was issued successfully.
        """

    @abc.abstractmethod
    async def get_variable_values(self) -> dict[str, float]:
        """Return every variable switch's current value.

        Returns
        -------
        values : `dict` [`str`, `float`]
            Variable name to value, e.g. dew heater PWM percentage,
            or a read-only reading such as voltage or current.
        """

    @abc.abstractmethod
    async def set_variable_value(self, name: str, value: float) -> bool:
        """Command one variable switch to a new value.

        Returns
        -------
        success : `bool`
            Whether the command was issued successfully. `False` for a
            read-only variable (e.g. a voltage/current sensor reading)
            that has no corresponding command.
        """
