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
            Names of the currently connected INDI devices. Empty when the
            server is down and the wait between connection attempts has not
            passed yet. A connection attempt to a telescope that is switched
            off blocks for seconds, so this shares the session's wait timer
            (and its growing back-off) instead of trying on every call.
        """
        if not self._session.isServerConnected():
            manager = getattr(self._session, "connection_manager", None)
            if manager is not None and not manager.can_attempt_reconnect():
                return []
            self._session.connect_to_server()
            if manager is not None:
                manager.record_connection_result(self._session.isServerConnected())
        return list(self._session.get_device_names())

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
