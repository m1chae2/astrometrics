"""Purpose: Save the app's settings and pass them on to the hardware driver.

Description: The settings page sends the whole configuration back when the
user saves it. This service writes it through the shared configuration
object, then tells the INDI driver to read the telescope hostname again,
so a changed hostname is used on the next connection.
"""

from typing import Any


class SettingsService:
    """Save the configuration and keep the hardware driver in step."""

    def __init__(self, config: Any, driver: Any = None) -> None:
        """Keep the configuration and the driver.

        Parameters
        ----------
        config : `AstrometricsConfiguration`
            The shared configuration object.
        driver : `IndiInterface`, optional
            The INDI driver (or the proxy to the INDI worker process).
            `None` when no driver is set up.
        """
        self._config = config
        self._driver = driver

    def save_config(self, config: dict[str, Any]) -> bool:
        """Save new settings and pass the hostname on to the driver.

        Parameters
        ----------
        config : `dict` [`str`, `dict`]
            The settings, one dictionary per configuration section.

        Returns
        -------
        saved : `bool`
            Always `True`. A failed save raises instead.
        """
        self._config.update_config(config)
        if self._driver is not None:
            self._driver.sync_configuration()
        return True
