"""Purpose: Test the simulated observatory's settings, guards and lifecycle.

Description: The processes are fakes (``sleep``), so the tests need no INDI
programs. They check that nothing outlives its owner.
"""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from wayfindinglib.simulation import observatory as observatory_module
from wayfindinglib.simulation import observatory_guard
from wayfindinglib.simulation.observatory import (
    SimulatedObservatory,
    SimulationKind,
    SimulationUnavailableError,
)

REPOSITORY = Path(__file__).resolve().parents[3]


def _alive(pid: int) -> bool:
    """Say whether a process exists and is not a zombie.

    Parameters
    ----------
    pid : `int`
        The process id.

    Returns
    -------
    alive : `bool`
        `True` if the process is running.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False


def _wait_gone(pid: int, seconds: float = 8.0) -> bool:
    """Wait until a process has gone.

    Parameters
    ----------
    pid : `int`
        The process id.
    seconds : `float`, optional
        How long to wait.

    Returns
    -------
    gone : `bool`
        `True` if the process ended in time.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def observatory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimulatedObservatory:
    """Build a simulated observatory in a temporary folder.

    Returns
    -------
    observatory : `SimulatedObservatory`
        A simulated-sky observatory on made-up free ports.
    """
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: [])
    return SimulatedObservatory(
        tmp_path / "simulator",
        SimulationKind.SIMULATED_SKY,
        observatory_module.choose_free_port(),
        observatory_module.choose_free_port(),
    )


def test_prepare_writes_settings_that_pass_the_guard(observatory: SimulatedObservatory) -> None:
    """A fresh world has loopback settings, the ssh stand-in and folders."""
    observatory.prepare()
    text = observatory.config_path.read_text()
    assert 'hostname = "127.0.0.1"' in text
    assert f'indi_port = "{observatory.indi_port}"' in text
    assert (observatory.bin_directory / "ssh").is_symlink()
    assert (observatory.standin_home / "Pictures").is_dir()
    assert observatory.environment()["PATH"].startswith(str(observatory.bin_directory))
    assert observatory.environment()["ASTROMETRICS_STANDIN_HOME"] == str(observatory.standin_home)


def test_prepare_resets_the_stand_in_lines_and_keeps_other_settings(
    observatory: SimulatedObservatory,
) -> None:
    """A second launch gets new ports and keeps a setting the user changed."""
    observatory.prepare()
    changed = observatory.config_path.read_text().replace(
        'focal_length_mm = "405"', 'focal_length_mm = "999"'
    )
    observatory.config_path.write_text(changed.replace(f'"{observatory.indi_port}"', '"1234"'))
    observatory.prepare()
    text = observatory.config_path.read_text()
    assert 'focal_length_mm = "999"' in text
    assert f'indi_port = "{observatory.indi_port}"' in text


def test_prepare_refuses_a_real_address_anywhere_in_the_settings(observatory: SimulatedObservatory) -> None:
    """A real address in the simulator settings stops the start."""
    observatory.prepare()
    observatory.config_path.write_text(
        observatory.config_path.read_text() + '\n["Notes"]\nreal = "192.168.50.188"\n'
    )
    with pytest.raises(SimulationUnavailableError, match=r"192\.168\.50\.188"):
        observatory.prepare()


def test_prepare_refuses_when_a_program_is_missing(
    observatory: SimulatedObservatory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing INDI program is reported, and nothing is written."""
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: ["indiserver"])
    with pytest.raises(SimulationUnavailableError, match="indiserver"):
        observatory.prepare()
    assert not observatory.config_path.exists()


def test_settings_update_guard_refuses_a_real_host_and_other_ports() -> None:
    """A save that aims the simulator at the real telescope is refused."""
    root = Path("/tmp/does-not-matter")
    standin = root / "standin" / "home"
    observatory_guard.check_settings_update(
        {"Observatory.Telescope": {"focal_length_mm": "1"}}, 1000, 2000, standin, root, protected=()
    )
    with pytest.raises(observatory_guard.GuardError):
        observatory_guard.check_settings_update(
            {"Observatory.Telescope": {"hostname": "stellarmate.local"}},
            1000,
            2000,
            standin,
            root,
            protected=(),
        )
    with pytest.raises(observatory_guard.GuardError, match=r"192\.168\.50\.188"):
        observatory_guard.check_settings_update(
            {"Observatory.Telescope": {"hostname": "192.168.50.188"}}, 1000, 2000, standin, root, protected=()
        )
    with pytest.raises(observatory_guard.GuardError):
        observatory_guard.check_settings_update(
            {"Observatory.Telescope": {"indi_port": "7624"}}, 1000, 2000, standin, root, protected=()
        )
    with pytest.raises(observatory_guard.GuardError):
        observatory_guard.check_settings_update(
            {"Observatory.Guiding": {"phd2_location": "remote"}}, 1000, 2000, standin, root, protected=()
        )


def test_lifecycle_starts_and_stops_every_process(
    observatory: SimulatedObservatory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start launches the stand-ins; stop ends them all."""

    def fake_start_processes() -> None:
        """Start two sleeping stand-ins for the INDI server and PHD2."""
        observatory._launch("indiserver", ["sleep", "60"])
        observatory._launch("phd2", ["sleep", "60"])

    monkeypatch.setattr(observatory, "_start_processes", fake_start_processes)
    observatory.start()
    assert observatory.is_running()
    assert observatory.status()["running"] is True
    pids = [process.pid for process in observatory._processes.values()]
    assert all(_alive(pid) for pid in pids)
    observatory.stop()
    assert not observatory.is_running()
    assert all(_wait_gone(pid) for pid in pids)
    observatory.stop()


def test_failed_start_stops_what_started(
    observatory: SimulatedObservatory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing step ends the earlier processes and keeps the error."""

    def failing_start() -> None:
        """Start one process, then fail.

        Raises
        ------
        SimulationUnavailableError
            Always.
        """
        observatory._launch("indiserver", ["sleep", "60"])
        raise SimulationUnavailableError("phd2 did not come up")

    monkeypatch.setattr(observatory, "_start_processes", failing_start)
    with pytest.raises(SimulationUnavailableError):
        observatory.start()
    assert not observatory._processes
    assert observatory.status()["error"] == "phd2 did not come up"


def test_supervised_command_ends_when_its_parent_is_killed(tmp_path: Path) -> None:
    """Killing a supervisor's parent ends the supervised command."""
    pid_file = tmp_path / "pids"
    parent_code = textwrap.dedent(
        f"""
        import subprocess, sys, time
        supervisor = {str(observatory_module.SUPERVISOR_SCRIPT)!r}
        child = subprocess.Popen([sys.executable, supervisor, "--", "sleep", "60"])
        time.sleep(1.0)
        open({str(pid_file)!r}, "w").write(str(child.pid))
        time.sleep(60)
        """
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code], cwd=REPOSITORY, env={**os.environ, "PYTHONPATH": str(REPOSITORY)}
    )
    try:
        deadline = time.time() + 20
        while not pid_file.exists() and time.time() < deadline:
            time.sleep(0.1)
        supervisor_pid = int(pid_file.read_text())
        sleeper = [
            int(p)
            for p in subprocess.run(
                ["pgrep", "-P", str(supervisor_pid)], capture_output=True, text=True, check=False
            ).stdout.split()
        ]
        assert sleeper
        os.kill(parent.pid, signal.SIGKILL)
        assert _wait_gone(supervisor_pid)
        assert all(_wait_gone(pid) for pid in sleeper)
    finally:
        parent.kill()
        parent.wait()


def _write_library_targets(
    root: Path, targets: list[tuple[str, str, str]], common_names: dict[str, str] | None = None
) -> None:
    """Make a copy of the library database that holds the given targets.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    targets : `list` [`tuple` [`str`, `str`, `str`]]
        ``(id, right ascension text, declination text)`` of each target.
    common_names : `dict` [`str`, `str`], optional
        Common name of each target id that has one.
    """
    import json
    import sqlite3

    from astrometricslib import Target

    database = root / "library" / "astrometrics.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE targets (id TEXT PRIMARY KEY, name TEXT, ra TEXT, dec TEXT, data_json TEXT)"
    )
    for target_id, right_ascension, declination in targets:
        extra = {"common_name": common_names[target_id]} if common_names and target_id in common_names else {}
        target = Target(id=target_id, ra=right_ascension, dec=declination, **extra)
        connection.execute(
            "INSERT INTO targets VALUES (?, ?, ?, ?, ?)",
            (target_id, "", right_ascension, declination, json.dumps(target.serialize())),
        )
    connection.commit()
    connection.close()


def test_simulated_frames_are_filed_under_the_name_the_app_downloads(
    observatory: SimulatedObservatory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exposure driver gets the name of the library target nearest the sky.

    The app takes the library target nearest the mount and downloads the
    folder named by its id, never by its common name. A target with id
    "M 13" and the common name "Test Target Name" must make the stand-in
    file its frames under "M 13", so the download finds them.
    """
    from wayfindinglib.simulation.exposure_driver import ExposureDriver

    _write_library_targets(
        observatory.root,
        [
            ("M 13", "16h 41m 41s", "+36 27 36"),
            ("M 31", "0h 42m", "+41 16"),
        ],
        common_names={"M 13": "Test Target Name"},
    )
    launched: dict[str, list[str]] = {}
    monkeypatch.setattr(
        observatory, "_launch", lambda name, command, log_name=None: launched.update({name: command})
    )
    monkeypatch.setattr(observatory, "_wait_listening", lambda ports: None)

    observatory._start_processes()

    command = launched["driver"]
    assert command[command.index("--target") + 1] == "M 13"
    assert observatory.pointed_targets()[0][0] == "M 13"
    driver = ExposureDriver(
        1, observatory.standin_home, Path("log"), Path("stats"), 1.0, "M 13", 16.695, 36.46
    )
    assert driver.pictures_directory == observatory.standin_home / "Pictures" / "M 13" / "Light"


def test_the_sky_keeps_the_default_target_when_the_library_has_none_near(
    observatory: SimulatedObservatory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a library target within a degree, the frames go under M 13."""
    _write_library_targets(observatory.root, [("M 31", "0h 42m", "+41 16")])
    launched: dict[str, list[str]] = {}
    monkeypatch.setattr(
        observatory, "_launch", lambda name, command, log_name=None: launched.update({name: command})
    )
    monkeypatch.setattr(observatory, "_wait_listening", lambda ports: None)

    observatory._start_processes()

    command = launched["driver"]
    assert command[command.index("--target") + 1] == "M 13"


def test_an_existing_world_is_checked_but_not_prepared_again(
    observatory: SimulatedObservatory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `existing_world`, start runs the guard and rewrites nothing."""
    observatory.prepare()
    existing = SimulatedObservatory(
        observatory.root,
        SimulationKind.SIMULATED_SKY,
        observatory.indi_port,
        observatory.phd2_port,
        copy_library=True,
        existing_world=True,
    )
    before = existing.config_path.read_text()
    monkeypatch.setattr(
        observatory_module.library_copy,
        "prepare_library_copy",
        lambda *a, **k: pytest.fail("the library copy must not be made again"),
    )
    existing.prepare()
    assert existing.config_path.read_text() == before
    existing.config_path.write_text(before + '\n["Notes"]\nreal = "192.168.50.188"\n')
    with pytest.raises(SimulationUnavailableError, match=r"192\.168\.50\.188"):
        existing.prepare()
