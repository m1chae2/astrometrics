"""Purpose: Save the app's settings and pass them on to the hardware driver.

Description: The settings page sends the whole configuration back when the
user saves it. This service writes it through the shared configuration
object, then tells the observatory control to read the telescope hostname
again, so a changed hostname is used on the next connection.
"""

from collections.abc import Callable
from typing import Any

from backend.services.infrastructure.static_mounts import static_mounts


class SettingsService:
    """Save the configuration and keep the hardware in step."""

    def __init__(
        self,
        config: Any,
        equipment: Any = None,
        guard: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Keep the configuration and the equipment control.

        Parameters
        ----------
        config : `AstrometricsConfiguration`
            The shared configuration object.
        equipment : `EquipmentControl`, optional
            The Wayfinder's ``control.equipment``. `None` when there is no
            hardware control.
        guard : `~collections.abc.Callable`, optional
            Called with the settings before they are saved. It raises to
            refuse the save (the simulator source refuses real devices).
        """
        self._guard = guard
        self._config = config
        self._equipment = equipment

    def save_config(self, config: dict[str, Any]) -> bool:
        """Save new settings and pass the hostname on to the hardware.

        Parameters
        ----------
        config : `dict` [`str`, `dict`]
            The settings, one dictionary per configuration section.

        Returns
        -------
        saved : `bool`
            Always `True`. A failed save raises instead.
        """
        if self._guard is not None:
            self._guard(config)
        self._config.update_config(config)
        if self._equipment is not None:
            self._equipment.apply_configuration()
        return True

    def get_static_mounts(self) -> list[dict[str, str]]:
        """Tell a client which folders the backend serves and at which URL.

        Returns
        -------
        mounts : `list` [`dict`]
            ``{"root", "urlPrefix"}`` items from `static_mounts`.
        """
        return static_mounts(self._config)
