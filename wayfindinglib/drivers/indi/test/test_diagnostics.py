"""Purpose: Unit tests for listing INDI devices through the diagnostics class.

Description: The UI's INDI panel lists devices through `IndiDiagnostics`. The
session it wraps can be a proxy to a worker process, where every attribute
read returns a function and device objects cannot be sent between processes.
These tests check the listing uses the picklable `get_device_names` method and
never reads `deviceMap`.
"""

import socket
import time
from types import SimpleNamespace

import pytest

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
        connect_to_server_if_due=lambda: calls.append("connect"),
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


def test_a_down_server_is_not_retried_before_the_wait_has_passed() -> None:
    """The session's connect-if-due method shares the connection wait timer."""
    from wayfindinglib.drivers.indi.connection_manager import ConnectionManager

    calls = []
    fake_session = SimpleNamespace(
        connection_manager=ConnectionManager("host"),
        connect_to_server=lambda: calls.append("connect"),
        isServerConnected=lambda: False,
    )

    for _ in range(3):
        IndiInterface.connect_to_server_if_due(fake_session)

    assert calls == ["connect"]
    assert fake_session.connection_manager.consecutive_failures == 1


def test_the_wait_between_connection_attempts_doubles_up_to_a_limit_and_resets() -> None:
    """Repeated failures back off; a success returns to the base wait."""
    from wayfindinglib.drivers.indi.connection_manager import ConnectionManager

    manager = ConnectionManager("host")
    waits = []
    for _ in range(6):
        manager.record_connection_result(False)
        waits.append(manager.connection_cooldown)

    assert waits == pytest.approx([5.0, 10.0, 20.0, 30.0, 30.0, 30.0])
    manager.record_connection_result(True)
    assert manager.connection_cooldown == pytest.approx(5.0)


def test_a_host_name_that_does_not_resolve_quickly_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow or failing lookup returns False within the timeout."""
    from wayfindinglib.drivers.indi.connection_manager import ConnectionManager

    manager = ConnectionManager("host")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: time.sleep(1.5))
    started = time.monotonic()
    assert manager.is_host_resolvable("slow.local", 7624, timeout=0.2) is False
    assert time.monotonic() - started < 1.0

    def fail(*args: object, **kwargs: object) -> None:
        """Fail the lookup the way an unknown host does.

        Raises
        ------
        socket.gaierror
            Always.
        """
        raise socket.gaierror("unknown host")

    time.sleep(1.5)  # let the slow lookup finish so the helper thread is free
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert manager.is_host_resolvable("nowhere.local", 7624, timeout=1.0) is False

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [("ok",)])
    assert manager.is_host_resolvable("localhost", 7624, timeout=1.0) is True
