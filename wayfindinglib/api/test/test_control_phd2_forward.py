"""Purpose: Tests for how the control context reaches PHD2.

Description: When PHD2 runs on the observatory computer, the context starts an
SSH port forward and points the PHD2 client at the forwarded local port; when
it runs locally the client uses localhost:4400 and no forward starts. The
forward is replaced by a fake, so no test runs ssh.
"""

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from wayfindinglib.api.control.context import ControlContext

instances: list[Any] = []


class FakeForward:
    """A stand-in port forward that records its life cycle."""

    def __init__(self, command_for_port: object) -> None:
        """Record the command builder and pick a fixed port."""
        self.command_for_port = command_for_port
        self.local_port = 45678
        self.started = False
        self.stopped = False
        instances.append(self)

    def start(self) -> None:
        """Record the start."""
        self.started = True

    def stop(self) -> None:
        """Record the stop."""
        self.stopped = True


def _context(location: str, open_port_forwards: bool = True) -> ControlContext:
    """Build a context over a stand-in configuration.

    Returns
    -------
    context : `ControlContext`
        A context whose configuration reports `location` for PHD2, with
        port forwards allowed or not.
    """
    config = MagicMock()
    config.get_phd2_location.return_value = location
    config.get_phd2_remote_port.return_value = 4400
    context = ControlContext(config=config, open_port_forwards=open_port_forwards)
    context.remote_transfer_driver = MagicMock()
    return context


@pytest.fixture(autouse=True)
def _fake_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap the real port forward for the fake, and reset its records."""
    instances.clear()
    monkeypatch.setattr("wayfindinglib.drivers.ssh_port_forward.SshPortForward", FakeForward)


def test_observatory_location_uses_the_forwarded_port_and_close_stops_it() -> None:
    """PHD2 on the observatory computer is reached through the forward."""
    context = _context("observatory")

    client = context._build_phd2_client()

    assert (client.host, client.port) == ("127.0.0.1", 45678)
    (forward,) = instances
    assert forward.started
    forward.command_for_port(45678)
    context.remote_transfer_driver.port_forward_command.assert_called_once_with(45678, 4400)

    context.close()
    context.close()
    assert forward.stopped


def test_local_location_uses_localhost_and_starts_no_forward() -> None:
    """PHD2 on this computer is reached directly, with no ssh."""
    context = _context("local")

    client = context._build_phd2_client()

    assert (client.host, client.port) == ("localhost", 4400)
    assert instances == []


def test_forwards_not_allowed_leaves_the_tunnel_to_the_backend() -> None:
    """Without the option, PHD2 on the observatory computer starts no ssh."""
    context = _context("observatory", open_port_forwards=False)

    client = context._build_phd2_client()

    assert (client.host, client.port) == ("localhost", 4400)
    assert instances == []


def test_local_location_starts_no_forward_even_when_allowed() -> None:
    """PHD2 on this computer never needs a tunnel, allowed or not."""
    context = _context("local", open_port_forwards=True)

    context._build_phd2_client()

    assert instances == []


def _host_context(host: str = "old-host") -> tuple[ControlContext, MagicMock]:
    """Build a context whose configuration names the StellarMate driver.

    Returns
    -------
    context, config : `ControlContext`, `MagicMock`
        The context and its stand-in configuration, whose host can change.
    """
    config = MagicMock()
    config.get_remote_transfer_driver_name.return_value = "stellarmate"
    config.get_telescope_hostname.return_value = host
    config.get_remote_pictures_path.return_value = "/pictures"
    config.get_phd2_location.return_value = "observatory"
    config.get_phd2_remote_port.return_value = 4400
    return ControlContext(config=config, open_port_forwards=True), config


def test_unchanged_settings_return_the_same_remote_transfer_driver() -> None:
    """Two accesses with the same configuration share one driver."""
    context, _ = _host_context()

    assert context.remote_transfer_driver is context.remote_transfer_driver
    assert context.remote_transfer_driver.host_alias == "old-host"


def test_a_changed_host_builds_a_new_remote_transfer_driver(caplog: pytest.LogCaptureFixture) -> None:
    """A new hostname replaces the cached driver and logs one info line."""
    context, config = _host_context()
    first = context.remote_transfer_driver

    config.get_telescope_hostname.return_value = "new-host"
    with caplog.at_level("INFO", logger="wayfindinglib.api.control.context"):
        second = context.remote_transfer_driver

    assert second is not first
    assert second.host_alias == "new-host"
    changes = [record for record in caplog.records if "settings changed" in record.getMessage()]
    assert len(changes) == 1
    assert "old-host" in changes[0].getMessage()
    assert "new-host" in changes[0].getMessage()


def test_a_changed_pictures_path_builds_a_new_remote_transfer_driver() -> None:
    """A new remote pictures folder also replaces the cached driver."""
    context, config = _host_context()
    first = context.remote_transfer_driver

    config.get_remote_pictures_path.return_value = "/elsewhere"

    assert context.remote_transfer_driver is not first
    assert context.remote_transfer_driver.remote_pictures_path == "/elsewhere"


def test_a_driver_set_by_hand_is_never_replaced() -> None:
    """A driver assigned directly stays, whatever the configuration says."""
    context, config = _host_context()
    stand_in = MagicMock()
    context.remote_transfer_driver = stand_in

    config.get_telescope_hostname.return_value = "new-host"

    assert context.remote_transfer_driver is stand_in


def test_a_changed_host_stops_the_forward_and_guiding_driver() -> None:
    """A new host stops the old forward and the guiding driver."""
    context, config = _host_context()
    _ = context.remote_transfer_driver
    guiding_driver = MagicMock()
    context._guiding_driver = guiding_driver
    context._build_phd2_client()
    (forward,) = instances

    config.get_telescope_hostname.return_value = "new-host"
    _ = context.remote_transfer_driver

    assert forward.stopped
    guiding_driver.close.assert_called_once()
    assert context._guiding_driver is None
    assert context._phd2_port_forward is None


def test_a_changed_pictures_path_keeps_the_forward_and_guiding_driver() -> None:
    """Only a new host makes the old forward out of date."""
    context, config = _host_context()
    _ = context.remote_transfer_driver
    guiding_driver = MagicMock()
    context._guiding_driver = guiding_driver
    context._build_phd2_client()
    (forward,) = instances

    config.get_remote_pictures_path.return_value = "/elsewhere"
    _ = context.remote_transfer_driver

    assert not forward.stopped
    guiding_driver.close.assert_not_called()
    assert context._guiding_driver is guiding_driver


def test_building_the_guiding_driver_after_a_host_change_does_not_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guiding driver build holds its lock and must not wait on itself."""
    context, config = _host_context()
    _ = context.remote_transfer_driver
    config.get_telescope_hostname.return_value = "new-host"

    def build_guiding_driver() -> MagicMock:
        """Build only the PHD2 client, as the real build does for PHD2.

        Returns
        -------
        driver : `MagicMock`
            A stand-in guiding driver.
        """
        context._build_phd2_client()
        return MagicMock()

    monkeypatch.setattr(context, "_build_guiding_driver", build_guiding_driver)
    results: list[object] = []
    worker = threading.Thread(target=lambda: results.append(context.guiding_driver), daemon=True)
    worker.start()
    worker.join(timeout=5.0)

    assert not worker.is_alive(), "guiding_driver deadlocked on its own lock"
    assert len(results) == 1
    assert context.remote_transfer_driver.host_alias == "new-host"
