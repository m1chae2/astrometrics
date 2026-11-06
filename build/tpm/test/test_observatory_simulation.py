"""Purpose: Tests for the simulated observatory profile of the TPM run.

Description: Checks the pieces that keep the simulation safe and honest: the
settings guard refuses anything that points beyond this computer, the
connection check spots a remote peer, the guide log replay builds the right
events, the stand-in PHD2 sends them to a client, the ssh stand-in runs a
command in the stand-in home and refuses other hosts, and the settings writer
names only local stand-ins.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from wayfindinglib.simulation import guide_log_replay, mock_phd2, observatory_guard

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import seal_guard  # ruff: ignore[module-import-not-at-top-of-file]
import write_sandbox_config  # ruff: ignore[module-import-not-at-top-of-file]

SIMULATION_DIR = Path(__file__).resolve().parents[3] / "wayfindinglib" / "simulation"
FIXTURE = SIMULATION_DIR / "guide_log_sample.txt"


def _simulated_config(tmp_path: Path, indi_port: int = 45001, phd2_port: int = 45002) -> Path:
    """Write a simulated-profile settings file in a temporary sandbox.

    Returns
    -------
    config_path : `pathlib.Path`
        The settings file.
    """
    return write_sandbox_config.write_sandbox_config(tmp_path, "simulated", indi_port, phd2_port)


def _check(tmp_path: Path, config: Path, indi_port: int = 45001, phd2_port: int = 45002) -> None:
    """Run the settings guard with the sandbox's own stand-in folder."""
    observatory_guard.check_settings(
        config, indi_port, phd2_port, write_sandbox_config.standin_home(tmp_path), tmp_path
    )


def test_simulated_settings_pass_the_guard(tmp_path: Path) -> None:
    """The settings the harness writes are accepted by its own guard."""
    _check(tmp_path, _simulated_config(tmp_path))


def test_guard_refuses_a_remote_host(tmp_path: Path) -> None:
    """A telescope host that is not this computer is refused."""
    config = _simulated_config(tmp_path)
    config.write_text(config.read_text().replace('hostname = "127.0.0.1"', 'hostname = "stellarmate.local"'))
    with pytest.raises(observatory_guard.GuardError, match=r"stellarmate\.local"):
        _check(tmp_path, config)


def test_guard_refuses_the_real_stellarmate_address_anywhere(tmp_path: Path) -> None:
    """The real telescope computer's address is refused wherever it appears."""
    config = _simulated_config(tmp_path)
    config.write_text(config.read_text() + '\n["Other"]\nnote = "192.168.50.188"\n')
    with pytest.raises(observatory_guard.GuardError, match=r"192\.168\.50\.188"):
        _check(tmp_path, config)


def test_guard_refuses_a_port_the_harness_did_not_start(tmp_path: Path) -> None:
    """An INDI or PHD2 port that differs from the started one is refused."""
    config = _simulated_config(tmp_path)
    with pytest.raises(observatory_guard.GuardError, match="indi_port"):
        _check(tmp_path, config, indi_port=45999)
    with pytest.raises(observatory_guard.GuardError, match="phd2_port"):
        _check(tmp_path, config, phd2_port=45999)


def test_guard_refuses_real_service_ports(tmp_path: Path) -> None:
    """The real PHD2, INDI and backend ports are refused."""
    config = write_sandbox_config.write_sandbox_config(tmp_path, "simulated", 7624, 4400)
    with pytest.raises(observatory_guard.GuardError, match="real ports"):
        _check(tmp_path, config, 7624, 4400)


def test_guard_refuses_remote_phd2_and_outside_pictures(tmp_path: Path) -> None:
    """PHD2 on the observatory or pictures outside the stand-in: refused."""
    config = _simulated_config(tmp_path)
    original = config.read_text()
    config.write_text(original.replace('phd2_location = "local"', 'phd2_location = "observatory"'))
    with pytest.raises(observatory_guard.GuardError, match="phd2_location"):
        _check(tmp_path, config)
    config.write_text(original.replace("/standin/home/Pictures", "/home/someone/Pictures"))
    with pytest.raises(observatory_guard.GuardError, match="remote_pictures_path"):
        _check(tmp_path, config)


def test_connection_check_finds_only_remote_peers_of_the_given_processes() -> None:
    """Only a non-loopback peer of a listed process is reported."""
    output = "\n".join([
        'ESTAB 0 0 127.0.0.1:50000 127.0.0.1:7777 users:(("python",pid=10,fd=5))',
        'ESTAB 0 0 [::ffff:127.0.0.1]:50001 [::ffff:127.0.0.1]:7778 users:(("python",pid=10,fd=6))',
        'ESTAB 0 0 192.168.1.5:50002 192.168.50.188:22 users:(("ssh",pid=11,fd=3))',
        'ESTAB 0 0 192.168.1.5:50003 8.8.8.8:443 users:(("firefox",pid=99,fd=3))',
    ])
    offending = seal_guard.parse_ss_connections(output, {10, 11})
    assert len(offending) == 1
    assert "192.168.50.188" in offending[0]


def test_descendants_include_the_process_and_its_children() -> None:
    """A child started by this process is a descendant of it."""
    child = subprocess.Popen(["sleep", "5"])
    try:
        assert {os.getpid(), child.pid} <= seal_guard.descendant_pids(os.getpid())
    finally:
        child.kill()
        child.wait()


def test_replay_items_carry_guide_steps_with_real_pacing() -> None:
    """The fixture gives GuideStep events paced by the log's time column."""
    items = guide_log_replay.load_replay_items(FIXTURE)
    steps = [item for item in items if item.event and item.event["Event"] == "GuideStep"]
    assert len(steps) > 100
    assert 0 < steps[1].delay_seconds < 10
    assert steps[1].event["RADirection"] in ("East", "West")
    assert any(item.event and item.event["Event"] == "SettleDone" for item in items)
    assert min(item.delay_seconds for item in items) >= 0


def test_render_line_sets_header_dates_to_now() -> None:
    """A "Guiding Begins" header gets the replay time; other lines stay."""
    from datetime import datetime

    header = guide_log_replay.ReplayItem(0.0, "Guiding Begins at 2026-10-03 20:37:12", None)
    other = guide_log_replay.ReplayItem(0.0, "Pixel scale = 6.39 arc-sec/px", None)
    now = datetime(2030, 1, 2, 3, 4, 5)
    assert guide_log_replay.render_line(header, now) == "Guiding Begins at 2030-01-02 03:04:05"
    assert guide_log_replay.render_line(other, now) == other.line


def test_mock_phd2_refuses_real_ports() -> None:
    """The stand-in PHD2 will not use a real PHD2's or the backend's port."""
    for port in (4400, 5000):
        with pytest.raises(ValueError, match="Refusing port"):
            mock_phd2.MockPhd2Server(port, [], None)


def test_mock_phd2_sends_events_to_a_client(tmp_path: Path) -> None:
    """A client gets the greeting and the guide steps; the stats count them."""
    items = [
        guide_log_replay.ReplayItem(0.0, "row", {"Event": "GuideStep", "Frame": index, "RADistanceRaw": 0.1})
        for index in range(3)
    ]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    stats_path = tmp_path / "stats.json"
    server = mock_phd2.MockPhd2Server(port, items, stats_path)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    time.sleep(0.5)
    received = b""
    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.settimeout(1.0)
        deadline = time.time() + 3
        while received.count(b"GuideStep") < 3 and time.time() < deadline:
            try:
                received += client.recv(4096)
            except TimeoutError:
                continue
    server.stop()
    thread.join(timeout=3)
    events = [json.loads(line) for line in received.decode().splitlines()]
    assert events[0]["Event"] == "Version"
    assert [event["Event"] for event in events].count("GuideStep") >= 3
    assert json.loads(stats_path.read_text())["guide_steps_sent"] >= 3


def _run_standin_ssh(home: Path, *arguments: str) -> subprocess.CompletedProcess:
    """Run the ssh stand-in with a stand-in home.

    Returns
    -------
    result : `subprocess.CompletedProcess`
        The finished call.
    """
    environment = {**os.environ, "ASTROMETRICS_STANDIN_HOME": str(home)}
    return subprocess.run(
        [str(SIMULATION_DIR / "standin_ssh.sh"), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_standin_ssh_runs_the_command_in_the_standin_home(tmp_path: Path) -> None:
    """Options and host are dropped; the command sees the stand-in as HOME."""
    result = _run_standin_ssh(tmp_path, "-o", "BatchMode=yes", "--", "127.0.0.1", 'echo "$HOME"')
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path)


def test_standin_ssh_refuses_other_hosts_forwards_and_a_missing_home(tmp_path: Path) -> None:
    """Only the local host is accepted; forwards and a missing home fail."""
    assert _run_standin_ssh(tmp_path, "--", "192.168.50.188", "true").returncode == 255
    assert _run_standin_ssh(tmp_path, "-N", "-L", "1:127.0.0.1:2", "127.0.0.1").returncode == 255
    environment = {key: value for key, value in os.environ.items() if key != "ASTROMETRICS_STANDIN_HOME"}
    result = subprocess.run(
        [str(SIMULATION_DIR / "standin_ssh.sh"), "127.0.0.1", "true"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 255


def test_standin_ssh_refuse_mode_never_runs_a_command(tmp_path: Path) -> None:
    """Refuse mode prints a refusal and exits 255 and runs nothing."""
    marker = tmp_path / "ran"
    environment = {key: value for key, value in os.environ.items() if key != "ASTROMETRICS_STANDIN_HOME"}
    environment["ASTROMETRICS_STANDIN_SSH_MODE"] = "refuse"
    result = subprocess.run(
        [str(SIMULATION_DIR / "standin_ssh.sh"), "-o", "BatchMode=yes", "localhost", f"touch {marker}"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 255
    assert result.stderr.strip() == "ssh: connect to host localhost port 22: Connection refused"
    assert not marker.exists()


def test_offline_settings_still_name_no_working_telescope(tmp_path: Path) -> None:
    """The default profile keeps the unused INDI port and auto download off."""
    text = write_sandbox_config.write_sandbox_config(tmp_path).read_text()
    assert 'indi_port = "1"' in text
    assert 'auto_download = "false"' in text
