"""Purpose: Unit tests for dynamic INDI device discovery and registration.

Description: Verifies that IndiInterface and DeviceDiscovery handle
asynchronous device arrivals and removals cleanly, rejecting empty-string
device names that occur during connection handshake races, and evicting
disconnected devices.
"""

from typing import Any
from unittest.mock import MagicMock

from wayfindinglib.drivers.indi.device_discovery import DeviceDiscovery
from wayfindinglib.drivers.indi_interface import IndiInterface


class _FakeDevice:
    """Mock device exposing get_device_name and getDeviceName."""

    def __init__(self, name: str) -> None:
        """Initialize the fake device with a name.

        Parameters
        ----------
        name : `str`
            The name of the mock device.
        """
        self._name = name

    def getDeviceName(self) -> str:  # ruff: ignore[invalid-function-name]
        """Return the device name.

        Returns
        -------
        name : `str`
            The device name.
        """
        return self._name


def test_new_device_registers_valid_name() -> None:
    """A device with a valid name is added to deviceMap on newDevice."""
    interface = IndiInterface.__new__(IndiInterface)
    interface.deviceMap = {}

    device = _FakeDevice("Star Adventurer GTi")
    interface.newDevice(device)

    assert "Star Adventurer GTi" in interface.deviceMap
    assert interface.deviceMap["Star Adventurer GTi"] is device


def test_new_device_ignores_empty_name() -> None:
    """A device with an empty name is ignored to prevent corrupted mappings."""
    interface = IndiInterface.__new__(IndiInterface)
    interface.deviceMap = {}

    interface.newDevice(_FakeDevice(""))
    interface.newDevice(_FakeDevice("   "))

    assert interface.deviceMap == {}


def test_remove_device_evicts_from_device_map() -> None:
    """When removeDevice is invoked, the device is pruned from deviceMap."""
    interface = IndiInterface.__new__(IndiInterface)
    device = _FakeDevice("Pegasus PPBA")
    interface.deviceMap = {"Pegasus PPBA": device}

    interface.removeDevice(device)

    assert "Pegasus PPBA" not in interface.deviceMap


def test_refresh_device_map_skips_empty_names_and_cleans_legacy_keys() -> None:
    """Refresh_device_map skips empty names and cleans up legacy empty keys."""
    mock_client: Any = MagicMock()
    mock_client.isServerConnected.return_value = True
    mock_client.deviceMap = {"": "old_stale_device"}

    dev1 = _FakeDevice("")
    dev2 = _FakeDevice("ZWO EFW")

    mock_client.getDevices.return_value = [dev1, dev2]
    mock_client.getDevice.side_effect = lambda name: dev2 if name == "ZWO EFW" else None

    discovery = DeviceDiscovery(mock_client)
    discovery.refresh_device_map()

    assert "" not in mock_client.deviceMap
    assert "ZWO EFW" in mock_client.deviceMap
    assert mock_client.deviceMap["ZWO EFW"] is dev2
