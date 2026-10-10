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


class _FakeCamera(_FakeDevice):
    """Fake camera with an exposure property and a reported sensor size."""

    def __init__(self, name: str, width: int, height: int) -> None:
        """Initialize the fake camera.

        Parameters
        ----------
        name : `str`
            The INDI device name.
        width : `int`
            Sensor width in pixels.
        height : `int`
            Sensor height in pixels.
        """
        super().__init__(name)
        self._info = [
            MagicMock(value=width, **{"name": "CCD_MAX_X"}),
            MagicMock(value=height, **{"name": "CCD_MAX_Y"}),
        ]
        for element, key in zip(self._info, ("CCD_MAX_X", "CCD_MAX_Y"), strict=True):
            element.name = key

    def getNumber(self, property_name: str) -> Any:  # ruff: ignore[invalid-function-name]
        """Return the fake property for CCD_EXPOSURE or CCD_INFO.

        Parameters
        ----------
        property_name : `str`
            INDI property name.

        Returns
        -------
        property : `Any`
            A truthy property, or `None` for other names.
        """
        if property_name == "CCD_INFO":
            return self._info
        return [object()] if property_name == "CCD_EXPOSURE" else None


def _discovery_with_cameras(*cameras: _FakeCamera) -> DeviceDiscovery:
    """Build a `DeviceDiscovery` over a client holding the given cameras.

    Returns
    -------
    discovery : `DeviceDiscovery`
        Discovery object bound to a fake connected client.
    """
    client = MagicMock()
    client.isServerConnected.return_value = True
    client.deviceMap = {camera.getDeviceName(): camera for camera in cameras}
    return DeviceDiscovery(client)


def test_main_camera_is_largest_sensor_when_guide_not_named() -> None:
    """The ASI533 is the main camera even if the ASI120 is listed first."""
    guide = _FakeCamera("ZWO CCD ASI120MC-S", 1280, 960)
    main = _FakeCamera("ZWO CCD ASI533MM Pro", 3008, 3008)
    discovery = _discovery_with_cameras(guide, main)

    assert discovery.find_main_camera() is main
    assert discovery.find_guide_camera() is guide
