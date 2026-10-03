"""Purpose: Unit tests for listing INDI devices through the diagnostics class.

Description: The UI's INDI panel lists devices through `IndiDiagnostics`. The
session it wraps can be a proxy to a worker process, where every attribute
read returns a function and device objects cannot be sent between processes.
These tests check the listing uses the picklable `get_device_names` method and
never reads `deviceMap`.
"""

from types import SimpleNamespace

from wayfindinglib.drivers.indi.diagnostics import IndiDiagnostics
from wayfindinglib.drivers.indi_interface import IndiInterface


class _ProxySession:
    """A session like `IndiWorkerProxy`: every attribute is a callable."""

    def __init__(self, names: list[str]) -> None:
        """Remember the device names to report."""
        self.names = names
        self.connect_calls = 0

    def isServerConnected(self) -> bool:  # ruff: ignore[invalid-function-name] (PyIndi's own name)
        """Report a connected server.

        Returns
        -------
        connected : `bool`
            Always `True`.
        """
        return True

    def get_device_names(self) -> list[str]:
        """Report the device names.

        Returns
        -------
        names : `list` [`str`]
            The names given at construction.
        """
        return list(self.names)

    def __getattr__(self, name: str) -> object:
        """Return a callable for any other name, as the proxy does.

        Returns
        -------
        forward : `callable`
            A function that ignores its arguments.
        """
        return lambda *args, **kwargs: None


def test_devices_are_listed_from_the_names_method_not_the_device_map() -> None:
    """A proxy's callable `deviceMap` is never read."""
    diagnostics = IndiDiagnostics(_ProxySession(["Telescope Simulator", "CCD Simulator"]))
    assert diagnostics.get_devices() == ["Telescope Simulator", "CCD Simulator"]


def test_a_disconnected_session_is_connected_first() -> None:
    """The listing connects to the server when it is not connected."""
    calls = []
    session = SimpleNamespace(
        isServerConnected=lambda: False,
        connect_to_server=lambda: calls.append("connect"),
        get_device_names=lambda: ["Mount"],
    )
    assert IndiDiagnostics(session).get_devices() == ["Mount"]
    assert calls == ["connect"]


def test_names_come_from_the_device_map_when_it_has_entries() -> None:
    """Blank names are left out of the map's keys."""
    session = SimpleNamespace(deviceMap={"Mount": object(), " ": object()}, getDevices=lambda: [])
    assert IndiInterface.get_device_names(session) == ["Mount"]


def test_names_fall_back_to_the_server_device_list() -> None:
    """With an empty map, the names are read from the server's devices."""
    broken = SimpleNamespace(getDeviceName=lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    camera = SimpleNamespace(getDeviceName=lambda: "CCD Simulator")
    session = SimpleNamespace(deviceMap={}, getDevices=lambda: [broken, camera])
    assert IndiInterface.get_device_names(session) == ["CCD Simulator"]
