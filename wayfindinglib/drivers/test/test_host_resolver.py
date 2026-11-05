"""Purpose: Unit tests for the saved telescope address and its use.

Description: A failed ``.local`` name lookup used to look like a dead
telescope. These tests patch the lookup, the sockets and ``subprocess.run``;
they never touch the network. They cover the resolver, the INDI handshake
probe and reconnect rule, and the ssh and rsync commands of the StellarMate
driver.
"""

import logging
import socket
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from wayfindinglib.drivers.host_resolver import KnownHostAddress
from wayfindinglib.drivers.indi.connection_manager import (
    MAX_CONSECUTIVE_PROBE_FAILURES,
    ConnectionManager,
)
from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface
from wayfindinglib.drivers.test.test_indi_reconnect import _client

_GOOD = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 7624))]


def _lookup(outcomes: list[Any]) -> Any:
    """Build a fake ``getaddrinfo`` that plays back outcomes in turn.

    Returns
    -------
    fake : `callable`
        The replacement function; it repeats the last outcome when used up.
    """
    remaining = list(outcomes)

    def fake(*args: object, **kwargs: object) -> list:
        """Return the next result, or raise it if it is an exception.

        Returns
        -------
        results : `list`
            The next canned lookup result.
        """
        outcome = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return fake


def test_failed_lookup_uses_saved_address_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    """After one success, failures give the saved address, logged once."""
    failure = socket.gaierror("Name or service not known")
    known = KnownHostAddress("scope.local")
    with (
        patch("socket.getaddrinfo", _lookup([_GOOD, failure])),
        caplog.at_level(logging.INFO, logger="wayfindinglib.drivers.host_resolver"),
    ):
        assert known.resolve(7624) == "scope.local"
        assert known.resolve(7624) == "192.168.1.50"
        assert known.resolve(7624) == "192.168.1.50"
    fallbacks = [r for r in caplog.records if "last known address" in r.getMessage()]
    assert len(fallbacks) == 1


def test_name_resolving_again_returns_to_the_name_with_one_info(caplog: pytest.LogCaptureFixture) -> None:
    """When the lookup works again the name is used and one line is logged."""
    failure = socket.gaierror("fail")
    known = KnownHostAddress("scope.local")
    with (
        patch("socket.getaddrinfo", _lookup([_GOOD])),
        caplog.at_level(logging.INFO, logger="wayfindinglib.drivers.host_resolver"),
    ):
        known.resolve(7624)
    with patch("socket.getaddrinfo", _lookup([failure])):
        known.resolve(7624)
    with (
        patch("socket.getaddrinfo", _lookup([_GOOD])),
        caplog.at_level(logging.INFO, logger="wayfindinglib.drivers.host_resolver"),
    ):
        assert known.resolve(7624) == "scope.local"
        assert known.resolve(7624) == "scope.local"
    again = [r for r in caplog.records if "resolves again" in r.getMessage()]
    assert len(again) == 1


def test_failure_with_nothing_saved_warns_once_and_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    """With no earlier address there is nothing to fall back to."""
    known = KnownHostAddress("scope.local")
    with (
        patch("socket.getaddrinfo", _lookup([socket.gaierror("fail")])),
        caplog.at_level(logging.WARNING, logger="wayfindinglib.drivers.host_resolver"),
    ):
        assert known.resolve(7624) is None
        assert known.resolve(7624) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_changed_hostname_clears_saved_address() -> None:
    """A new host name from the configuration drops the old address."""
    known = KnownHostAddress("scope.local")
    with patch("socket.getaddrinfo", _lookup([_GOOD])):
        known.resolve(7624)
    assert known.cached_address == "192.168.1.50"
    known.set_hostname("scope.local")
    assert known.cached_address == "192.168.1.50"
    known.set_hostname("other.local")
    assert known.cached_address is None
    with patch("socket.getaddrinfo", _lookup([socket.gaierror("fail")])):
        assert known.resolve(7624) is None


def test_ip_address_hostname_is_never_looked_up() -> None:
    """An IP address is returned as it is, with no lookup and no saving."""
    known = KnownHostAddress("192.168.1.9")
    with patch("socket.getaddrinfo", side_effect=AssertionError("no lookup expected")):
        assert known.resolve(7624) == "192.168.1.9"
    assert known.cached_address is None


def _manager_with_probe(reply: bytes | OSError) -> tuple[ConnectionManager, MagicMock]:
    """Build a manager whose handshake uses a fake socket.

    Returns
    -------
    manager : `ConnectionManager`
        A manager for ``scope.local`` that has resolved the name once.
    create_connection : `MagicMock`
        The patched ``socket.create_connection``.
    """
    manager = ConnectionManager("scope.local", 7624)
    create_connection = MagicMock()
    if isinstance(reply, OSError):
        create_connection.side_effect = reply
    else:
        create_connection.return_value.__enter__.return_value.recv.return_value = reply
    return manager, create_connection


def test_probe_uses_saved_address_when_lookup_fails() -> None:
    """The handshake connects to the saved IP while the name lookup fails."""
    manager, create_connection = _manager_with_probe(b"<ok/>")
    with patch("socket.getaddrinfo", _lookup([_GOOD])):
        manager.known_address.resolve(7624)
    with (
        patch("socket.getaddrinfo", _lookup([socket.gaierror("fail")])),
        patch("socket.create_connection", create_connection),
    ):
        assert manager.is_server_responsive() is True
    assert create_connection.call_args.args[0] == ("192.168.1.50", 7624)


def test_lookup_failure_alone_is_not_counted_as_probe_failure() -> None:
    """With no saved address a failed lookup is not counted."""
    manager, create_connection = _manager_with_probe(OSError("unused"))
    with (
        patch("socket.getaddrinfo", _lookup([socket.gaierror("fail")])),
        patch("socket.create_connection", create_connection),
    ):
        assert manager.is_server_responsive() is False
    create_connection.assert_not_called()
    assert manager.consecutive_probe_failures == 0


def _connected_client() -> Any:
    """Build a fake connected client with a device and a real manager.

    Returns
    -------
    client : `_FakeNetworkInterface`
        A connected client whose host name resolves.
    """
    client = _client(["Mount"])
    client.connected = True
    client.deviceMap = {"Mount": object()}
    return client


def _ensure_after_probe(client: Any, probe_ok: bool, now: float) -> None:
    """Run `_ensure_connection` once with the handshake forced."""
    manager = client.connection_manager
    manager.last_responsive_check = 0

    def fake_probe() -> bool:
        """Record one probe the way the real one does.

        Returns
        -------
        responsive : `bool`
            The forced outcome.
        """
        if probe_ok:
            manager.consecutive_probe_failures = 0
        else:
            manager.consecutive_probe_failures += 1
        return probe_ok

    manager.is_server_responsive = fake_probe
    manager.last_connection_attempt = now - 1000.0
    client._ensure_connection()


def test_one_or_two_failed_probes_do_not_reconnect() -> None:
    """A connected client survives fewer than the limit of failed probes."""
    client = _connected_client()
    client.supports_rebuild = True
    for attempt in range(MAX_CONSECUTIVE_PROBE_FAILURES - 1):
        _ensure_after_probe(client, probe_ok=False, now=1000.0 + attempt)
    assert not client.stale_client
    assert client.calls == []


def test_consecutive_failed_probes_reconnect() -> None:
    """The limit of failed probes in a row triggers the reconnect."""
    client = _connected_client()
    client.supports_rebuild = True
    for attempt in range(MAX_CONSECUTIVE_PROBE_FAILURES):
        _ensure_after_probe(client, probe_ok=False, now=1000.0 + attempt)
    assert client.stale_client


def test_a_good_probe_resets_the_failure_run() -> None:
    """Failures that are not in a row never add up to a reconnect."""
    client = _connected_client()
    client.supports_rebuild = True
    for attempt in range(MAX_CONSECUTIVE_PROBE_FAILURES * 2):
        _ensure_after_probe(client, probe_ok=attempt % 2 == 0, now=1000.0 + attempt)
    assert not client.stale_client


def test_disconnected_client_reconnects_without_probe_count() -> None:
    """A client that reports no server connection reconnects as before."""
    client = _connected_client()
    client.connected = False
    client.connection_manager.last_connection_attempt = 0
    client._ensure_connection()
    assert client.calls == ["connectServer"]


def test_connect_uses_saved_address_for_the_server() -> None:
    """A new connection goes to the saved IP while the name lookup fails."""
    client = _client(["Mount"])
    servers: list[tuple[str, int]] = []
    client.setServer = lambda host, port: servers.append((host, port))  # type: ignore[method-assign]
    client.connection_manager = ConnectionManager("localhost", 7624)
    client.connection_manager.known_address.cached_address = "192.168.1.50"
    client.connection_manager.known_address.hostname = "localhost"
    with patch("socket.getaddrinfo", _lookup([socket.gaierror("fail")])):
        client.connect_to_server()
    assert servers == [("192.168.1.50", 7624)]
    assert client.calls == ["connectServer"]


def _completed(stdout: str = "ok") -> MagicMock:
    """Build a fake successful ``subprocess.run`` result.

    Returns
    -------
    result : `MagicMock`
        A result with the given standard output.
    """
    result = MagicMock()
    result.stdout = stdout
    return result


def test_ssh_uses_saved_address_with_host_key_alias() -> None:
    """While the lookup fails, ssh gets the IP and the name as key alias."""
    driver = StellarMateInterface(host_alias="scope.local")
    with patch("socket.getaddrinfo", _lookup([_GOOD])):
        driver._known_address.resolve(22)
    with (
        patch("socket.getaddrinfo", _lookup([socket.gaierror("fail")])),
        patch("subprocess.run", return_value=_completed()) as run,
    ):
        driver._execute_command(["ssh", "scope.local", "echo", "hi"])
    command = run.call_args.args[0]
    assert "HostName=192.168.1.50" in command
    assert "HostKeyAlias=scope.local" in command
    assert command.index("scope.local") > command.index("HostKeyAlias=scope.local")


def test_ssh_uses_the_name_when_it_resolves() -> None:
    """With a working lookup the ssh command is unchanged."""
    driver = StellarMateInterface(host_alias="scope.local")
    with (
        patch("socket.getaddrinfo", _lookup([_GOOD])),
        patch("subprocess.run", return_value=_completed()) as run,
    ):
        driver._execute_command(["ssh", "scope.local", "echo", "hi"])
    command = run.call_args.args[0]
    assert not any(token.startswith(("HostName=", "HostKeyAlias=")) for token in command)


def test_ssh_to_an_ip_address_skips_the_lookup() -> None:
    """An IP address as the host needs no lookup and no extra options."""
    driver = StellarMateInterface(host_alias="192.168.1.50")
    with (
        patch("socket.getaddrinfo", side_effect=AssertionError("no lookup expected")),
        patch("subprocess.run", return_value=_completed()) as run,
    ):
        driver._execute_command(["ssh", "192.168.1.50", "echo", "hi"])
    assert not any(token.startswith("HostName=") for token in run.call_args.args[0])


def test_rsync_uses_remote_shell_option_with_saved_address() -> None:
    """The rsync remote shell carries the saved address and key alias."""
    driver = StellarMateInterface(host_alias="scope.local")
    with patch("socket.getaddrinfo", _lookup([_GOOD])):
        driver._known_address.resolve(22)
    process = MagicMock()
    process.stdout.readline.return_value = ""
    process.wait.return_value = 0
    process.returncode = 0
    with (
        patch("socket.getaddrinfo", _lookup([socket.gaierror("fail")])),
        patch("subprocess.Popen", return_value=process) as popen,
    ):
        driver._run_rsync("/remote/", "/tmp/local", [], "test files")
    command = popen.call_args.args[0]
    remote_shell = command[command.index("-e") + 1]
    assert "HostName=192.168.1.50" in remote_shell
    assert "HostKeyAlias=scope.local" in remote_shell
    assert "scope.local:/remote/" in command
