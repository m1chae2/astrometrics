"""Raw INDI device/property inspection, split out of `ObservatoryControl`.

`get_indi_devices`/`indi_properties`/`set_indi_property` never belonged
on the generic `ObservatoryControl` facade -- no ASCOM Alpaca equivalent
exists for "list every arbitrary property on every device," since this
is inherently INDI-specific (`Wayfinding_Library_Architecture.md`
§2.5.1a). `IndiStatusPanel` (the UI this backs) is, by its own
requirement tags, a raw INDI-only inspector -- exactly the narrow
escape hatch this class provides, wrapping the shared `IndiInterface`
session directly rather than going through a `ProtocolDriver` ABC.
"""

from typing import Any


class IndiDiagnostics:
    """Raw INDI device/property inspection over a shared session."""

    def __init__(self, session: Any) -> None:
        """Wrap an existing `IndiInterface`/`SimulatorIndiInterface`.

        Parameters
        ----------
        session : `IndiInterface`
            The shared INDI session `Indi*Driver` adapters also wrap.
        """
        self._session = session

    def get_devices(self) -> list[str]:
        """List connected INDI device names.

        Returns
        -------
        device_names : `list` [`str`]
            Names of the currently connected INDI devices.
        """
        if not self._session.isServerConnected():
            self._session.connect_to_server()
        if hasattr(self._session, "deviceMap") and self._session.deviceMap:
            return list(self._session.deviceMap.keys())
        devices = self._session.getDevices()
        if devices:
            return [d.getDeviceName() for d in devices]
        return []

    def get_properties(self, device_name: str) -> dict[str, Any]:
        """List all registered properties for an INDI device.

        Returns
        -------
        properties : `dict`
            Mapping of property name to its details for the device.
        """
        self._session._ensure_connection()
        return self._session.get_device_properties(device_name)

    def set_property(
        self, device_name: str, property_name: str, value: Any, element: str | None = None
    ) -> bool:
        """Modify a property element on an INDI device.

        Returns
        -------
        success : `bool`
            `True` if the property element was updated.
        """
        self._session._ensure_connection()
        return self._session.set_property(device_name, property_name, element, value)
