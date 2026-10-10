"""Switch/power-distribution controller for INDI devices.

Genuinely new ground -- no existing controller commands power outlets
or dew heaters today, unlike the mount/camera/focuser/filter-wheel
controllers. Targets the real PegasusAstro powerbox INDI driver's
properties: ``POWER_CONTROL`` (per-outlet boolean switches),
``DEW_PWM`` (per-channel dew heater duty cycle, 0-100%), and
``POWER_SENSORS`` (read-only voltage/current/power telemetry).

Reads the same heuristically-discovered powerbox device
`IndiInterface._find_powerbox_device` already uses for weather
telemetry -- no first-class `EquipmentCatalog` entry exists for
this device, the same limitation the focuser and filter wheel have.
"""

from typing import TYPE_CHECKING

from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi

if TYPE_CHECKING:
    from wayfindinglib.drivers.indi_interface import IndiInterface

_POWER_CONTROL_PROPERTY = "POWER_CONTROL"
_DEW_PWM_PROPERTY = "DEW_PWM"
_POWER_SENSORS_PROPERTY = "POWER_SENSORS"


class SwitchController:
    """Manages power-outlet and dew-heater switches via INDI."""

    def __init__(self, client: IndiInterface) -> None:
        self.client = client

    def get_switch_states(self, device: PyIndi.BaseDevice) -> dict[str, bool]:
        """Return every `POWER_CONTROL` outlet's current on/off state.

        Returns
        -------
        states : `dict` [`str`, `bool`]
            Outlet name to on/off state; empty if the device or
            property is unavailable.
        """
        if not device:
            return {}
        power_control = device.getSwitch(_POWER_CONTROL_PROPERTY)
        if not power_control:
            return {}
        return {
            power_control[i].getName(): power_control[i].getState() == PyIndi.ISS_ON
            for i in range(len(power_control))
        }

    def set_switch_state(self, device: PyIndi.BaseDevice, switch_name: str, on: bool) -> bool:
        """Command one `POWER_CONTROL` outlet on or off.

        Every other outlet's current state is preserved -- `POWER_CONTROL`
        is a multi-element switch vector where each element toggles
        independently, unlike `DOME_SHUTTER`'s mutually exclusive pair.

        Returns
        -------
        success : `bool`
            Whether the command was sent, i.e. `switch_name` was found.
        """
        if not device:
            return False
        power_control = device.getSwitch(_POWER_CONTROL_PROPERTY)
        if not power_control:
            return False

        found = False
        for i in range(len(power_control)):
            if power_control[i].getName() == switch_name:
                power_control[i].s = PyIndi.ISS_ON if on else PyIndi.ISS_OFF
                found = True
        if not found:
            return False
        self.client.sendNewSwitch(power_control)
        return True

    def get_variable_values(self, device: PyIndi.BaseDevice) -> dict[str, float]:
        """Return every `DEW_PWM`/`POWER_SENSORS` element's current value.

        Returns
        -------
        values : `dict` [`str`, `float`]
            Element name to value, merging the settable dew-heater
            duty cycles with the read-only voltage/current/power
            sensor readings; empty if the device has neither property.
        """
        if not device:
            return {}
        values: dict[str, float] = {}
        for property_name in (_DEW_PWM_PROPERTY, _POWER_SENSORS_PROPERTY):
            number_vector = device.getNumber(property_name)
            if number_vector:
                for i in range(len(number_vector)):
                    values[number_vector[i].getName()] = number_vector[i].value
        return values

    def set_variable_value(self, device: PyIndi.BaseDevice, name: str, value: float) -> bool:
        """Command one `DEW_PWM` element to a new duty cycle.

        `POWER_SENSORS` elements are read-only telemetry and are never
        matched here, so commanding one honestly reports failure
        rather than silently doing nothing.

        Returns
        -------
        success : `bool`
            Whether the command was sent, i.e. `name` was found among
            the settable `DEW_PWM` elements.
        """
        if not device:
            return False
        dew_pwm = device.getNumber(_DEW_PWM_PROPERTY)
        if not dew_pwm:
            return False

        found = False
        for i in range(len(dew_pwm)):
            if dew_pwm[i].getName() == name:
                dew_pwm[i].value = value
                found = True
        if not found:
            return False
        self.client.sendNewNumber(dew_pwm)
        return True
