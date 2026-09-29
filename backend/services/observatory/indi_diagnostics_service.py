"""Backend service wrapping `ObservatoryControl.indi_diagnostics`.

Registered as its own router service so `rpc_router.py` can repoint the
three raw-INDI-inspection actions at it once those methods are removed
from `ObservatoryControl` outright (M5) -- `IndiStatusPanel`, the UI
this backs, needs no change: it only ever calls the action name, never
the backend method directly.
"""

from typing import Any

from wayfindinglib.api.control_registry import ObservatoryControl


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
        diagnostics = self._observatory.indi_diagnostics
        return diagnostics.get_devices() if diagnostics else []

    def get_properties(self, device_name: str) -> dict[str, Any]:
        """List all registered properties for an INDI device.

        Returns
        -------
        properties : `dict`
            Mapping of property name to its details for the device, or
            an empty dict if the active mount protocol isn't ``"indi"``.
        """
        diagnostics = self._observatory.indi_diagnostics
        return diagnostics.get_properties(device_name) if diagnostics else {}

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
        diagnostics = self._observatory.indi_diagnostics
        return diagnostics.set_property(device_name, property_name, value, element) if diagnostics else False
