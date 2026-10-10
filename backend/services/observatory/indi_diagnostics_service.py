"""Backend service for raw INDI device and property inspection.

Serves the three raw INDI actions that `IndiStatusPanel` calls, through
`control.equipment`: `status(include=["indi_devices"])`,
`status(include=["indi_properties"], device_name=...)` and
`set_device_property`. Each returns an empty answer when the active mount
does not use INDI.
"""

from typing import Any

from wayfindinglib import ObservatoryControl


class IndiDiagnosticsService:
    """Expose raw INDI device/property inspection over the RPC layer."""

    def __init__(self, observatory_api: ObservatoryControl) -> None:
        """Wrap the `ObservatoryControl` this service's actions serve."""
        self._observatory = observatory_api

    def get_devices(self) -> list[str]:
        """List connected INDI device names.

        Returns
        -------
        device_names : `list` [`str`]
            Names of the currently connected INDI devices, or an empty
            list if the active mount protocol isn't ``"indi"``.
        """
        return self._observatory.equipment.status(include=["indi_devices"]).indi_devices or []

    def get_properties(self, device_name: str) -> dict[str, Any]:
        """List all registered properties for an INDI device.

        Returns
        -------
        properties : `dict`
            Mapping of property name to its details for the device, or
            an empty dict if the active mount protocol isn't ``"indi"``.
        """
        status = self._observatory.equipment.status(include=["indi_properties"], device_name=device_name)
        return status.indi_properties or {}

    def set_property(
        self, device_name: str, property_name: str, value: Any, element: str | None = None
    ) -> bool:
        """Modify a property element on an INDI device.

        Returns
        -------
        success : `bool`
            `True` if the property element was updated, `False` if the
            active mount protocol isn't ``"indi"``.
        """
        return self._observatory.equipment.set_device_property(device_name, property_name, value, element)
