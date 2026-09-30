"""Abstract base class for weather/environmental-sensing protocol drivers.

Mirrors ASCOM's `IObservingConditions`. Returns exactly the
`SensorReadings` shape `wayfindinglib.tasks.control_tasks.safety_monitor
.assess_safety` already accepts -- verified to have no live caller
anywhere in production code before this driver existed
(`Wayfinding_Library_Architecture.md` §2.5.4); this driver is that live
feed, not new safety logic.
"""

import abc

from wayfindinglib.drivers.protocols.base_protocol_driver import ProtocolDriver
from wayfindinglib.tasks.control_tasks.safety_monitor import SensorReadings

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
            directly to `ObservatoryControl.assess_safety`.
        """
