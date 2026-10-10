"""INDI implementation of the `WeatherDriver` protocol ABC.

Wraps the same shared `IndiInterface` session as `IndiMountDriver`,
backed by `wayfindinglib/drivers/indi/weather_controller.py`'s
`WeatherController` (M12) -- the heuristically-discovered powerbox
device's `WEATHER_PARAMETERS` property, the same one
`IndiInterface.get_environmentals` already reads for its status dict.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.interfaces.weather_driver import SensorReadings, WeatherDriver


class IndiWeatherDriver(WeatherDriver):
    """Adapts a shared `IndiInterface` session to the `WeatherDriver` ABC."""

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
        """INDI weather sensing connects as part of the shared session.

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

    async def get_readings(self) -> SensorReadings:
        """Return the powerbox's current environmental sensor readings.

        Returns
        -------
        readings : `SensorReadings`
            Measurement name to `(value, observed_at)`.
        """
        return await asyncio.to_thread(self._session.get_weather_readings)
