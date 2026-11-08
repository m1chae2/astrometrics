"""Purpose: Unit tests for INDI recovery after a server restart.

Description: After indiserver restarted, the long-lived INDI client kept
connecting and being dropped, until the backend was restarted. The fix builds
a new client object for each reconnect. These tests use stand-in clients and a
subclass of `IndiInterface` with the network calls replaced; they never open a
socket and never touch a real INDI server.
"""

import logging
import threading

import pytest

from wayfindinglib.drivers.indi.connection_manager import ConnectionManager
from wayfindinglib.drivers.indi_interface import IndiInterface
from wayfindinglib.drivers.indi_worker import RebuildingSession


class _FakeNetworkInterface(IndiInterface):
    """An `IndiInterface` whose server calls are recorded, not performed."""

    def __init__(self, device_names: list[str], server_up: bool = True) -> None:
        """Set only the fields the reconnect code reads."""
        self.status = {}
        self.deviceMap = {}
        self.supports_rebuild = False
        self.stale_client = False
        self._connection_loss_reported = False
        self._expected_disconnect = False
        self.connection_manager = ConnectionManager("localhost", 7624)
        self.device_names = device_names
        self.server_up = server_up
        self.connected = False
        self.calls: list[str] = []

    def isServerConnected(self) -> bool:
        """Return the fake connection state.

        Returns
        -------
        connected : `bool`
            Whether the fake client is connected.
        """
        return self.connected

    def connectServer(self) -> bool:
        """Pretend to connect when the fake server is up.

        Returns
        -------
        connected : `bool`
            Whether the fake server accepted the connection.
        """
        self.calls.append("connectServer")
        self.connected = self.server_up
        return self.server_up

    def disconnectServer(self, exit_code: int = 0) -> bool:
        """Record the call and drop the fake connection.

        Returns
        -------
        done : `bool`
            Always `True`.
        """
        self.calls.append("disconnectServer")
        self.connected = False
        return True

    def sync_configuration(self) -> None:
        """Skip reading configuration."""

    def getHost(self) -> str:
        """Return a local host name.

        Returns
        -------
        host : `str`
            The host name.
        """
        return "localhost"

    def getPort(self) -> int:
        """Return the INDI port.

        Returns
        -------
        port : `int`
            The port number.
        """
        return 7624

    def getDevices(self) -> list[object]:
        """Return stand-in devices when connected.

        Returns
        -------
        devices : `list`
            One stand-in device per name, or none when not connected.
        """
        return [_FakeDevice(name) for name in self.device_names] if self.connected else []

    def getDevice(self, name: str) -> object:
        """Return a stand-in device.

        Returns
        -------
        device : `_FakeDevice`
            A device with the given name.
        """
        return _FakeDevice(name)


class _FakeDevice:
    """A device that only knows its name."""

    def __init__(self, name: str) -> None:
        """Remember the device name."""
        self.name = name

    def getDeviceName(self) -> str:
        """Return the device name.

        Returns
        -------
        name : `str`
            The device name.
        """
        return self.name


def _client(device_names: list[str], server_up: bool = True) -> _FakeNetworkInterface:
    """Build a fake client that resolves host names and allows replacement.

    Returns
    -------
    client : `_FakeNetworkInterface`
        A new unconnected client.
    """
    client = _FakeNetworkInterface(device_names, server_up)
    client.connection_manager.is_host_resolvable = lambda host, port, timeout=1.0: True
    return client


def test_reconnect_asks_for_new_client_without_touching_old_one() -> None:
    """With replacement on, `_reconnect` only flags the client."""
    client = _client(["Mount"])
    client.supports_rebuild = True
    client.connected = True
    client._reconnect(100.0)
    assert client.stale_client
    assert client.calls == []


def test_server_restart_then_return_yields_devices_from_new_client() -> None:
    """Refused, then back up: the new client reports devices."""
    clients: list[_FakeNetworkInterface] = []

    def factory() -> _FakeNetworkInterface:
        """Build clients; the first finds the server down, later ones up.

        Returns
        -------
        client : `_FakeNetworkInterface`
            A new client.
        """
        client = _client(["Mount", "Camera"], server_up=bool(clients))
        clients.append(client)
        return client

    session = RebuildingSession(factory)
    first = session._client
    assert first.supports_rebuild
    first.connection_manager.last_connection_attempt = 50.0
    first.connection_manager.consecutive_failures = 2
    first._reconnect(100.0)
    session._replace_client_if_stale()  # runs on the caller's thread, as in the worker
    second = session._client
    assert second is not first
    assert first.calls == ["disconnectServer"]
    assert second.isServerConnected()
    assert sorted(second.deviceMap) == ["Camera", "Mount"]
    assert second.connection_manager.consecutive_failures == 0
    assert not second.stale_client


def test_failed_replacement_keeps_clean_retry() -> None:
    """While the server stays down, the retry wait keeps growing."""
    session = RebuildingSession(lambda: _client(["Mount"], server_up=False))
    session._client._reconnect(100.0)
    session._replace_client_if_stale()
    timing = session._client.connection_manager
    assert not session._client.isServerConnected()
    assert timing.consecutive_failures == 1
    assert not session._client.stale_client


def test_repeated_drops_log_one_warning_and_one_restore(caplog: pytest.LogCaptureFixture) -> None:
    """Many drops give one warning; recovery gives one info line."""
    client = _client(["Mount", "Camera"])
    with caplog.at_level(logging.DEBUG, logger="wayfindinglib.drivers.indi_interface"):
        for _ in range(5):
            client.serverDisconnected(0)
        client.connect_to_server()
        client.connect_to_server()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warnings) == 1
    assert len(infos) == 1
    assert "2 device" in infos[0].getMessage()


def test_disconnect_callback_does_no_network_work() -> None:
    """`serverDisconnected` (PyIndi's thread) never connects or disconnects."""
    client = _client(["Mount"])
    thread = threading.Thread(target=client.serverDisconnected, args=(0,))
    thread.start()
    thread.join()
    assert client.calls == []
    assert client.status["CONNECTION_STATUS"] == "Disconnected"


def test_own_disconnect_is_not_reported_as_a_loss(caplog: pytest.LogCaptureFixture) -> None:
    """A disconnect the client asked for is not logged as a loss."""
    client = _client(["Mount"])
    client._expected_disconnect = True
    with caplog.at_level(logging.DEBUG, logger="wayfindinglib.drivers.indi_interface"):
        client.serverDisconnected(0)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_replacement_keeps_the_remembered_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebuild during an outage still uses the address learned earlier."""
    session = RebuildingSession(lambda: _client(["Mount"], server_up=False))
    first = session._client
    first.connection_manager.known_address.cached_address = "10.0.0.5"

    def failing_lookup(*args: object, **kwargs: object) -> list:
        """Fail the way an mDNS lookup fails during an outage.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("name lookup failed")

    monkeypatch.setattr("wayfindinglib.drivers.host_resolver.socket.getaddrinfo", failing_lookup)
    first._reconnect(100.0)
    session._replace_client_if_stale()

    second = session._client
    assert second is not first
    assert second.connection_manager.known_address.resolve(7624) == "10.0.0.5"


def test_failed_swap_does_not_hide_the_calls_result(caplog: pytest.LogCaptureFixture) -> None:
    """A failed client swap is logged; the call still returns."""
    session = RebuildingSession(lambda: _client(["Mount"]))

    def broken_swap() -> None:
        """Fail like a swap that could not build a client.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("swap failed")

    session._replace_client_if_stale = broken_swap  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        assert session.isServerConnected() is False
    assert any("Replacing the stale INDI client failed" in r.getMessage() for r in caplog.records)
