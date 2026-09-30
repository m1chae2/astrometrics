"""Weather/environmental-sensing controller for INDI devices.

Extends the same heuristically-discovered powerbox device
`IndiInterface._find_powerbox_device` already reads for the status
dict's ``TEMPERATURE``/``HUMIDITY`` display fields
(`indi_interface.py`'s `get_environmentals`), generalizing from that
fixed two-element read into every element the ``WEATHER_PARAMETERS``
vector actually publishes, in the `SensorReadings` shape
`safety_monitor.assess_safety` expects.
"""

from datetime import UTC, datetime

from wayfindinglib.tasks.control_tasks.safety_monitor import SensorReadings

_WEATHER_PARAMETERS_PROPERTY = "WEATHER_PARAMETERS"


class WeatherController:
    """Reads environmental telemetry from a powerbox's WEATHER_PARAMETERS."""

    def get_readings(self, device) -> SensorReadings:  # ruff: ignore[missing-type-function-argument]
        """Return every `WEATHER_PARAMETERS` element as a sensor reading.

        Every element is timestamped with the moment of this read --
        INDI number elements carry no reading-specific timestamp of
        their own, only the enclosing vector's, so this is the
        earliest point an `observed_at` can honestly be assigned.

        Returns
        -------
        readings : `SensorReadings`
            Measurement name to `(value, observed_at)`; empty if the
            device or property is unavailable.
        """
        if not device:
            return {}
        weather_parameters = device.getNumber(_WEATHER_PARAMETERS_PROPERTY)
        if not weather_parameters:
            return {}

        observed_at = datetime.now(UTC)
        return {
            weather_parameters[i].getName(): (weather_parameters[i].value, observed_at)
            for i in range(len(weather_parameters))
        }
