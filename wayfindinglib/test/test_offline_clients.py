"""Purpose: Test that wayfindinglib's outgoing clients honor offline.

Description: When the launch says ``offline = true`` (see
`astrometricslib.foundation.launch_identity`), nothing may leave the
computer. Each test turns the switch on, makes any attempt to open a
connection, look a name up or start ssh or rsync fail the test, and calls one
client. A host that is this computer (the stand-ins) must still be allowed.
Nothing in these tests uses the network.
"""

import socket
from types import SimpleNamespace

import pytest

from astrometricslib.drivers import LaunchIdentity
from astrometricslib.exceptions import ExternalServiceError


@pytest.fixture(autouse=True)
def offline_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the offline switch on and forbid every connection and program.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the settings the switch reads, the socket calls and the
        calls that start a program.
    """

    class _Configuration:
        """A settings object whose launch is offline."""

        @staticmethod
        def get_launch_identity() -> LaunchIdentity:
            """Give an offline identity.

            Returns
            -------
            identity : `LaunchIdentity`
                One with ``offline`` set.
            """
            return LaunchIdentity(offline=True)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fail the test: an offline client touched the network."""
        pytest.fail("an offline client touched the network")

    monkeypatch.setattr("astrometricslib.foundation.offline.get_configuration", _Configuration)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)


def test_ssh_and_rsync_to_another_computer_are_refused_as_unreachable() -> None:
    """The telescope computer commands fail like an unreachable host."""
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    driver = StellarMateInterface("stellarmate.local", "/home/pi/Pictures")

    with pytest.raises(ExternalServiceError, match="sends nothing off"):
        driver._execute_command(["ssh", "--", "stellarmate.local", "echo", "connected"])
    assert driver.check_connection() is False
    with pytest.raises(ExternalServiceError, match="sends nothing off"):
        driver._run_rsync("/remote/a", "/local/a", [], "test")
    with pytest.raises(ConnectionError, match="telescope computer"):
        driver.port_forward_command(20000, 7624)


def test_the_port_forward_waits_quietly(caplog: pytest.LogCaptureFixture) -> None:
    """The port forward builds without error and does not start ssh."""
    from wayfindinglib.drivers.ssh_port_forward import SshPortForward
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    driver = StellarMateInterface("stellarmate.local", "/home/pi/Pictures")

    forward = SshPortForward(lambda port: driver.port_forward_command(port, 7624), local_port=20000)

    assert forward.command == []
    assert not [record for record in caplog.records if record.levelname in ("WARNING", "ERROR")]


def test_the_indi_connection_manager_reports_a_status_not_a_failure() -> None:
    """The INDI probes of another computer say "not responding" at once."""
    from wayfindinglib.drivers.indi.connection_manager import ConnectionManager

    manager = ConnectionManager("stellarmate.local", 7624)

    assert manager.is_server_responsive() is False
    assert manager.is_host_resolvable("stellarmate.local", 7624) is False
    assert manager.connect_address is None
    assert manager.consecutive_probe_failures == 0


def test_the_indi_connection_manager_still_probes_this_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A loopback INDI server (a simulator's stand-in) is not refused."""
    from wayfindinglib.drivers.indi.connection_manager import ConnectionManager

    manager = ConnectionManager("127.0.0.1", 7624)
    reached: list[tuple[str, int]] = []

    def create_connection(address: tuple[str, int], timeout: float) -> None:
        """Record the attempt, then fail like a closed port.

        Raises
        ------
        ConnectionRefusedError
            Always.
        """
        reached.append(address)
        raise ConnectionRefusedError

    monkeypatch.setattr(socket, "create_connection", create_connection)

    assert manager.is_server_responsive() is False
    assert reached == [("127.0.0.1", 7624)]


def test_phd2_on_another_computer_is_refused_but_this_computer_is_not() -> None:
    """The PHD2 client refuses a remote host and tries a local one."""
    from wayfindinglib.drivers.phd2.phd2_client import PHD2Client

    with pytest.raises(OSError, match="PHD2"):
        PHD2Client(host="stellarmate.local")._connect()

    attempts: list[tuple[str, int]] = []

    class _Socket:
        """A socket that records the connect and fails."""

        def settimeout(self, _seconds: float) -> None:
            """Ignore the timeout."""

        def connect(self, address: tuple[str, int]) -> None:
            """Record the attempt, then fail like a closed port.

            Raises
            ------
            ConnectionRefusedError
                Always.
            """
            attempts.append(address)
            raise ConnectionRefusedError

        def close(self) -> None:
            """Ignore the close."""

    with pytest.raises(ConnectionRefusedError):
        PHD2Client(host="localhost", socket_factory=_Socket)._connect()
    assert attempts == [("localhost", 4400)]


def test_the_deep_catalog_download_refuses_the_gaia_archive() -> None:
    """The one archive call of the deep catalog builder refuses."""
    from wayfindinglib.drivers.catalog import deep_star_catalog_builder as builder

    gaia = SimpleNamespace(launch_job_async=lambda *_args, **_kwargs: pytest.fail("asked the archive"))

    with pytest.raises(ConnectionError, match="Gaia archive"):
        builder._run_query(gaia, "SELECT 1", 5.0)


def test_simbad_lookups_by_name_and_region_are_refused_or_empty(caplog: pytest.LogCaptureFixture) -> None:
    """A name lookup is a service error, a region search is empty and quiet."""
    from wayfindinglib.tasks.planning_tasks import catalog_operations, resolution_operations

    sky = SimpleNamespace(
        _astrometrics=SimpleNamespace(
            targets=SimpleNamespace(list=lambda: []),
            stars=SimpleNamespace(query=lambda **_kwargs: SimpleNamespace(objects=[])),
        )
    )

    with pytest.raises(ExternalServiceError, match="SIMBAD"):
        resolution_operations.resolve_target_coordinates(sky, "M 31")
    assert catalog_operations.global_catalog(sky, 10.0, 20.0, 0.5) == []
    assert resolution_operations._query_visual_magnitude("Vega") is None
    assert not [record for record in caplog.records if record.levelname in ("WARNING", "ERROR")]
