"""Abstract base class for weather/environmental-sensing protocol drivers.

Mirrors ASCOM's `IObservingConditions`. Returns exactly the
`SensorReadings` shape (defined in `wayfindinglib.models.policy.safety`)
that `wayfindinglib.tasks.control_tasks.safety_monitor.assess_safety`
accepts. This driver is the live feed of readings, not new safety logic
(`Wayfinding_Library_Architecture.md`). `SensorReadings` is re-exported
here because it is part of this interface's signature.
"""

import abc

from wayfindinglib.drivers.interfaces.base_protocol_driver import ProtocolDriver
from wayfindinglib.models.policy.safety import SensorReadings

__all__ = ["SensorReadings", "WeatherDriver"]


class WeatherDriver(ProtocolDriver):
    """Abstract base for weather/environmental-sensing protocol drivers."""

    @abc.abstractmethod
    async def get_readings(self) -> SensorReadings:
        """Return the current environmental sensor readings.

        Returns
        -------
        readings : `SensorReadings`
            Measurement name to `(value, observed_at)`, ready to pass
            directly to `control.safety.assess`.
        """
