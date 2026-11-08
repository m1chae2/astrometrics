"""Purpose: Save the app's settings and pass them on to the hardware driver.

Description: The settings page sends the whole configuration back when the
user saves it. This service writes it through the shared configuration
object, then tells the observatory control to read the telescope hostname
again, so a changed hostname is used on the next connection.

A save is refused if it changes a setting the launch locked (the
``locked_keys`` of the ``[Launch]`` section, see
`astrometricslib.foundation.launch_identity`), or the ``[Launch]`` section
itself. A save that sends the same value back is not a change, because the
settings page sends the whole configuration.
"""

from collections.abc import Callable
from typing import Any

from astrometricslib.drivers import LaunchIdentity, launch_identity_from_settings
from astrometricslib.exceptions import ConfigurationError
from backend.services.infrastructure.static_mounts import static_mounts


def _comparable(value: object) -> str:
    """Turn a setting value into text that compares the way a person would.

    Parameters
    ----------
    value : `object`
        A value from the settings file or from the settings page.

    Returns
    -------
    text : `str`
        The value as stripped text, with ``True`` and ``true`` made equal.
    """
    text = str(value).strip()
    return text.lower() if text.lower() in ("true", "false") else text


class SettingsService:
    """Save the configuration and keep the hardware in step."""

    def __init__(
        self,
        config: Any,
        equipment: Any = None,
        launch_reader: Callable[[], LaunchIdentity | None] | None = None,
    ) -> None:
        """Keep the configuration and the equipment control.

        Parameters
        ----------
        config : `AstrometricsConfiguration`
            The shared configuration object.
        equipment : `EquipmentControl`, optional
            The Wayfinder's ``control.equipment``. `None` when there is no
            hardware control.
        launch_reader : `~collections.abc.Callable`, optional
            Gives the launch identity. Default: the configuration's own
            ``get_launch_identity``, or no identity if it has none.
        """
        reader = launch_reader or getattr(config, "get_launch_identity", None)
        self._launch_reader: Callable[[], LaunchIdentity | None] = reader or (lambda: None)
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
        self._refuse_locked_changes(config)
        self._config.update_config(config)
        if self._equipment is not None:
            self._equipment.apply_configuration()
        return True

    def _refuse_locked_changes(self, update: dict[str, Any]) -> None:
        """Refuse a save that changes a locked setting or the launch identity.

        Parameters
        ----------
        update : `dict` [`str`, `dict`]
            The settings being saved, one dictionary per section.

        Raises
        ------
        ConfigurationError
            If a key in the launch's ``locked_keys`` gets a new value, or the
            ``[Launch]`` section changes.
        """
        identity = self._launch_reader()
        if identity is None:
            return
        read_value = getattr(self._config, "get_value", None)
        changed: list[str] = []
        for section, parameters in update.items():
            if not isinstance(parameters, dict):
                continue
            if section == "Launch":
                if (
                    launch_identity_from_settings({"Launch": {**identity.as_dict(), **parameters}})
                    != identity
                ):
                    changed.append("Launch")
                continue
            for key, value in parameters.items():
                if not identity.is_locked(section, key):
                    continue
                current = read_value(section, key, None) if read_value is not None else None
                if _comparable(value if value is not None else "") != _comparable(
                    current if current is not None else ""
                ):
                    changed.append(f"{section}.{key}")
        if changed:
            raise ConfigurationError(
                "These settings are locked by how the app was started and cannot be changed here: "
                + ", ".join(changed)
                + ".",
                details={"locked_keys": changed},
            )

    def get_static_mounts(self) -> list[dict[str, str]]:
        """Tell a client which folders the backend serves and at which URL.

        Returns
        -------
        mounts : `list` [`dict`]
            ``{"root", "urlPrefix"}`` items from `static_mounts`.
        """
        return static_mounts(self._config)
