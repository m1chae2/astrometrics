"""Purpose: Test the launcher: choice, lock, restart, teardown and guard.

Description: The backend is replaced by ``fake_backend.py`` and the world by
a fake `World`, so no library is copied and no stand-in starts. Every test
uses a temporary settings folder (``XDG_CONFIG_HOME``) and a temporary
simulator folder, so the real choice file, library and the user's running
simulator are never touched. One live test builds a real world in a
temporary folder when the INDI server is installed.
"""

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from simulator import launch
from simulator.launch import (
    GUARD_EXIT_CODE,
    LOCKED_EXIT_CODE,
    Launcher,
    World,
    acquire_world_lock,
    release_world_lock,
)
from simulator.observatory import unavailable_reason
from simulator.source_choice import (
    ACTIVE_PORTS_VARIABLE,
    ACTIVE_ROOT_VARIABLE,
    ACTIVE_SOURCE_VARIABLE,
    LAUNCH_ERROR_VARIABLE,
    LAUNCHER_VARIABLE,
    OFFLINE,
    REAL,
    REAL_CONFIG_HOME_VARIABLE,
    RESTART_EXIT_CODE,
    SIMULATED,
    SourceChoice,
    read_choice,
    write_choice,
)

#: INDI servers share one local socket, so a second one cannot start while the
#: app's own simulator (or any other INDI server) runs on this computer.
INDI_SOCKET_TAKEN = "@/tmp/indiserver" in Path("/proc/net/unix").read_text()
FAKE_BACKEND = Path(__file__).with_name("fake_backend.py")
CALLS = [("observatory:record_guider_calibration", {"calibration_id": "c"})]


class Rig:
    """A temporary settings folder, simulator folder and fake world.

    Attributes
    ----------
    environ : `dict` [`str`, `str`]
        The launcher's environment.
    root : `pathlib.Path`
        The simulator folder.
    record : `pathlib.Path`
        The file the fake backend writes its starts into.
    events : `list` [`str`]
        What the fake world was asked to do, in order.
    """

    def __init__(self, tmp_path: Path, behavior: str, source: str = REAL) -> None:
        """Build the folders and save the choice."""
        self.root = tmp_path / "simulator"
        self.record = tmp_path / "starts.json"
        self.marker = tmp_path / "torn_down"
        self.events: list[str] = []
        self.environ = {
            **{key: value for key, value in os.environ.items() if not key.startswith("ASTROMETRICS_")},
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "FAKE_BACKEND_RECORD": str(self.record),
            "FAKE_BACKEND_MARKER": str(self.marker),
            "FAKE_BACKEND_BEHAVIOR": behavior,
        }
        write_choice(SourceChoice(source), self.environ)

    def world(self, root: Path, environ: dict[str, str]) -> World:
        """Give a fake simulated world and note that it was built.

        Returns
        -------
        world : `World`
            A world whose teardown steps are noted in ``events``.
        """
        self.events.append("built")
        root.mkdir(parents=True, exist_ok=True)
        config = root / "astrometrics.config.toml"
        config.write_text("[Launch]\n")

        def remove() -> bool:
            """Note the removal and leave a marker.

            Returns
            -------
            clean : `bool`
                Always `True`.
            """
            self.events.append("removed")
            self.marker.write_text("done")
            return True

        return World(
            root=root,
            source=SIMULATED,
            config_path=config,
            variables={"PATH": "/standin/bin"},
            pointed_targets=lambda: [("M 13", 16.695, 36.46)],
            stop_standins=lambda: self.events.append("stopped"),
            remove_copy=remove,
        )

    def launcher(self, **options: object) -> Launcher:
        """Build a launcher over the fake backend and the fake world.

        Returns
        -------
        launcher : `Launcher`
            The launcher.
        """
        settings: dict = {
            "environ": self.environ,
            "root": self.root,
            "port": 59999,
            "backend_command": [sys.executable, str(FAKE_BACKEND)],
            "world_builder": self.world,
            "calibration_runner": lambda world, environment: CALLS,
            "operator": lambda port, calls, targets: {"allowed": [], "off": {}, "error": None},
            "ready_check": lambda port: True,
            "connection_check": lambda pid: [],
            "guard_interval": 0.1,
        }
        settings.update(options)
        return Launcher(**settings)

    def starts(self) -> list[dict]:
        """Give what the fake backend recorded for each start.

        Returns
        -------
        starts : `list` [`dict`]
            One entry per start.
        """
        return json.loads(self.record.read_text())


def lock_is_free(root: Path) -> bool:
    """Say whether the folder's lock can be taken (and release it).

    Returns
    -------
    free : `bool`
        `True` when nobody holds the lock.
    """
    descriptor = acquire_world_lock(root)
    if descriptor is None:
        return False
    release_world_lock(descriptor)
    return True


def wait_for(condition: Callable[[], bool], seconds: float = 20.0) -> bool:
    """Wait until `condition` is true.

    Returns
    -------
    met : `bool`
        `False` if it did not become true in time.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_real_choice_runs_the_backend_with_the_launcher_variable_and_no_world(tmp_path: Path) -> None:
    """The real telescope: same environment plus the launcher variable."""
    rig = Rig(tmp_path, "exit:3", REAL)
    status = rig.launcher().run()
    assert status == 3
    assert rig.events == []
    environment = rig.starts()[0]["environment"]
    assert environment[LAUNCHER_VARIABLE] == "1"
    assert environment["XDG_CONFIG_HOME"] == rig.environ["XDG_CONFIG_HOME"]
    assert ACTIVE_SOURCE_VARIABLE not in environment
    assert "ASTROMETRICS_CONFIG" not in environment


def test_a_missing_choice_file_means_the_real_telescope(tmp_path: Path) -> None:
    """Without a saved choice the launcher builds no world."""
    rig = Rig(tmp_path, "exit:0")
    rig.environ["XDG_CONFIG_HOME"] = str(tmp_path / "nowhere")
    launcher = rig.launcher(environ=rig.environ)
    assert launcher.run() == 0
    assert launcher.sessions == [REAL]
    assert rig.events == []


def test_simulated_choice_pins_the_backend_to_the_world(tmp_path: Path) -> None:
    """The backend child is pinned to the world and told of the launcher."""
    rig = Rig(tmp_path, "exit:0", SIMULATED)
    assert rig.launcher().run() == 0
    environment = rig.starts()[0]["environment"]
    assert environment[LAUNCHER_VARIABLE] == "1"
    assert environment["ASTROMETRICS_CONFIG"] == str(rig.root / "astrometrics.config.toml")
    assert environment["ASTROMETRICS_CONFIG_PATH"] == environment["ASTROMETRICS_CONFIG"]
    for name in ("DATA", "CACHE", "STATE", "CONFIG"):
        assert environment[f"XDG_{name}_HOME"].startswith(str(rig.root))
    assert environment["PATH"] == "/standin/bin"
    assert environment[ACTIVE_SOURCE_VARIABLE] == SIMULATED
    assert environment[ACTIVE_ROOT_VARIABLE] == str(rig.root)
    # Without the ports the backend has no simulator object to start or stop.
    assert ACTIVE_PORTS_VARIABLE not in environment
    # The real settings folder is still known (the real choice file).
    assert environment[REAL_CONFIG_HOME_VARIABLE] == rig.environ["XDG_CONFIG_HOME"]
    assert rig.events == ["built", "stopped", "removed"]


def test_a_second_launcher_does_not_touch_a_locked_world(tmp_path: Path) -> None:
    """A held lock gives the locked exit code and changes nothing."""
    rig = Rig(tmp_path, "exit:0", SIMULATED)
    descriptor = acquire_world_lock(rig.root)
    assert descriptor is not None
    try:
        before = sorted(path.name for path in rig.root.iterdir())
        assert rig.launcher().run() == LOCKED_EXIT_CODE
        assert rig.events == []
        assert not rig.record.exists()
        assert sorted(path.name for path in rig.root.iterdir()) == before
    finally:
        release_world_lock(descriptor)


def test_a_second_process_is_refused_with_a_clear_message(tmp_path: Path) -> None:
    """A second process against a locked folder exits 73 and says why."""
    rig = Rig(tmp_path, "exit:0", SIMULATED)
    descriptor = acquire_world_lock(rig.root)
    assert descriptor is not None
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "simulator.launch", "--root", str(rig.root), "--port", "59998"],
            env={**rig.environ, "PYTHONPATH": str(Path(launch.__file__).resolve().parents[1])},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        release_world_lock(descriptor)
    assert completed.returncode == LOCKED_EXIT_CODE
    assert "Another launcher holds the simulator folder" in completed.stderr
    assert sorted(path.name for path in rig.root.iterdir()) == ["launcher.lock"]


def test_exit_75_tears_down_and_starts_the_next_session_with_the_new_choice(tmp_path: Path) -> None:
    """Leaving simulation: the world is gone before the next backend."""
    rig = Rig(tmp_path, "switch:real;exit:0", SIMULATED)
    launcher = rig.launcher()
    assert launcher.run() == 0
    assert launcher.sessions == [SIMULATED, REAL]
    assert rig.events == ["built", "stopped", "removed"]
    first, second = rig.starts()
    assert first["environment"][ACTIVE_SOURCE_VARIABLE] == SIMULATED
    assert ACTIVE_SOURCE_VARIABLE not in second["environment"]
    assert second["marker_present"] is True
    assert read_choice(rig.environ).source == REAL
    assert lock_is_free(rig.root)


def test_exit_75_into_simulation_builds_the_world(tmp_path: Path) -> None:
    """Entering simulation from the real telescope builds the world once."""
    rig = Rig(tmp_path, "switch:simulated;exit:0", REAL)
    launcher = rig.launcher()
    assert launcher.run() == 0
    assert launcher.sessions == [REAL, SIMULATED]
    assert rig.events == ["built", "stopped", "removed"]


def test_any_other_exit_code_stops_the_launcher_after_teardown(tmp_path: Path) -> None:
    """A normal exit tears the world down and passes the code on."""
    rig = Rig(tmp_path, "exit:2", SIMULATED)
    assert rig.launcher().run() == 2
    assert rig.events == ["built", "stopped", "removed"]
    assert lock_is_free(rig.root)
    assert len(rig.starts()) == 1


def test_a_crash_of_the_backend_tears_the_world_down(tmp_path: Path) -> None:
    """The backend killed by a signal still leaves no world behind."""
    rig = Rig(tmp_path, "kill", SIMULATED)
    status = rig.launcher().run()
    assert status == -signal.SIGKILL
    assert rig.events == ["built", "stopped", "removed"]
    assert lock_is_free(rig.root)


def test_a_stop_request_ends_the_backend_then_the_world(tmp_path: Path) -> None:
    """A stop request ends the backend, then the world, and gives 0."""
    rig = Rig(tmp_path, "wait", SIMULATED)
    launcher = rig.launcher()
    outcome: list[int] = []
    thread = threading.Thread(target=lambda: outcome.append(launcher.run()))
    thread.start()
    assert wait_for(rig.record.exists)
    launcher.request_stop()
    thread.join(timeout=30)
    assert outcome == [0]
    assert rig.events == ["built", "stopped", "removed"]
    assert lock_is_free(rig.root)


def test_sigterm_to_the_launcher_process_cleans_up_and_exits(tmp_path: Path) -> None:
    """A real SIGTERM, with the real signal handlers, in a separate process."""
    rig = Rig(tmp_path, "wait", SIMULATED)
    script = textwrap.dedent(
        f"""
        import json, sys
        from pathlib import Path
        from simulator.launch import Launcher, World, install_signal_handlers
        root = Path({str(rig.root)!r})
        events = Path({str(tmp_path / "events.txt")!r})
        def build(root, environ):
            root.mkdir(parents=True, exist_ok=True)
            config = root / "astrometrics.config.toml"
            config.write_text("[Launch]")
            def remove():
                events.write_text("removed")
                return True
            return World(root=root, source="simulated", config_path=config, remove_copy=remove)
        launcher = Launcher(root=root, port=59997, backend_command=[sys.executable, {str(FAKE_BACKEND)!r}],
            world_builder=build, calibration_runner=lambda w, e: [], operator=lambda p, c, t: {{}},
            ready_check=lambda p: True, connection_check=lambda pid: [])
        install_signal_handlers(launcher)
        sys.exit(launcher.run())
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        env={**rig.environ, "PYTHONPATH": str(Path(launch.__file__).resolve().parents[1])},
    )
    try:
        assert wait_for(rig.record.exists)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=60) == 0
    finally:
        if process.poll() is None:
            process.kill()
    assert (tmp_path / "events.txt").read_text() == "removed"
    assert lock_is_free(rig.root)


def test_the_operator_runs_after_the_backend_is_ready(tmp_path: Path) -> None:
    """The operator gets the port, the calibration calls and targets."""
    rig = Rig(tmp_path, "wait", SIMULATED)
    seen: list[tuple] = []
    launcher = rig.launcher(operator=lambda port, calls, targets: seen.append((port, calls, targets)) or {})
    thread = threading.Thread(target=launcher.run)
    thread.start()
    assert wait_for(lambda: bool(seen))
    launcher.request_stop()
    thread.join(timeout=30)
    assert seen == [(59999, CALLS, [("M 13", 16.695, 36.46)])]


def test_the_operator_waits_until_the_backend_is_ready(tmp_path: Path) -> None:
    """Not ready means no operator step."""
    rig = Rig(tmp_path, "wait", SIMULATED)
    seen: list[int] = []
    launcher = rig.launcher(ready_check=lambda port: False, operator=lambda *args: seen.append(1) or {})
    thread = threading.Thread(target=launcher.run)
    thread.start()
    assert wait_for(rig.record.exists)
    time.sleep(0.6)
    launcher.request_stop()
    thread.join(timeout=30)
    assert seen == []


def test_the_real_telescope_gets_no_operator_step_and_no_guard(tmp_path: Path) -> None:
    """Only a simulated session is guarded and operated."""
    rig = Rig(tmp_path, "wait", REAL)
    seen: list[str] = []

    def spy(pid: int) -> list[str]:
        """Note a look at the connections.

        Returns
        -------
        offending : `list` [`str`]
            Nothing.
        """
        seen.append("guard")
        return []

    launcher = rig.launcher(connection_check=spy, operator=lambda *args: seen.append("operator") or {})
    thread = threading.Thread(target=launcher.run)
    thread.start()
    assert wait_for(rig.record.exists)
    time.sleep(0.5)
    launcher.request_stop()
    thread.join(timeout=30)
    assert seen == []


def test_the_connection_guard_ends_the_session_on_a_connection_off_the_computer(tmp_path: Path) -> None:
    """A connection off the computer ends the session with code 70."""
    rig = Rig(tmp_path, "wait", SIMULATED)
    launcher = rig.launcher(connection_check=lambda pid: ["ESTAB 10.0.0.5:4000 93.184.216.34:443"])
    assert launcher.run() == GUARD_EXIT_CODE
    assert rig.events == ["built", "stopped", "removed"]
    assert lock_is_free(rig.root)


def test_the_connection_guard_lets_a_clean_session_run(tmp_path: Path) -> None:
    """No offending connection: the session runs until the backend ends."""
    rig = Rig(tmp_path, "wait", SIMULATED)
    looks: list[int] = []

    def clean(pid: int) -> list[str]:
        """Note a look and report nothing.

        Returns
        -------
        offending : `list` [`str`]
            Nothing.
        """
        looks.append(pid)
        return []

    launcher = rig.launcher(connection_check=clean)
    thread = threading.Thread(target=launcher.run)
    thread.start()
    assert wait_for(lambda: len(looks) >= 2)
    launcher.request_stop()
    thread.join(timeout=30)
    assert rig.events == ["built", "stopped", "removed"]


def test_a_world_that_failed_to_start_runs_the_backend_offline(tmp_path: Path) -> None:
    """The offline source and reason reach the backend; no operator."""
    rig = Rig(tmp_path, "exit:0", SIMULATED)
    operated: list[int] = []

    def offline(root: Path, environ: dict[str, str]) -> World:
        """Give an offline world with a reason.

        Returns
        -------
        world : `World`
            The offline world.
        """
        root.mkdir(parents=True, exist_ok=True)
        (root / "astrometrics.config.toml").write_text("[Launch]\n")
        return World(
            root=root,
            source=OFFLINE,
            config_path=root / "astrometrics.config.toml",
            error="SimulationUnavailableError: no indiserver",
        )

    launcher = rig.launcher(world_builder=offline, operator=lambda *args: operated.append(1) or {})
    assert launcher.run() == 0
    environment = rig.starts()[0]["environment"]
    assert environment[ACTIVE_SOURCE_VARIABLE] == OFFLINE
    assert environment[LAUNCH_ERROR_VARIABLE] == "SimulationUnavailableError: no indiserver"
    assert operated == []


def test_build_world_falls_back_to_the_offline_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Any failure to build the simulator gives the offline world."""

    class Broken:
        """A simulator whose preparation fails."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            """Accept anything."""

        def prepare(self) -> None:
            """Fail.

            Raises
            ------
            RuntimeError
                Always.
            """
            raise RuntimeError("fuse-overlayfs is missing")

    monkeypatch.setattr(launch, "SimulatedObservatory", Broken)
    rig = Rig(tmp_path, "exit:0", SIMULATED)
    world = launch.build_world(rig.root, rig.environ)
    assert world.source == OFFLINE
    assert world.error == "RuntimeError: fuse-overlayfs is missing"
    assert world.config_path.exists()
    assert world.variables["ASTROMETRICS_STANDIN_SSH_MODE"] == "refuse"


@pytest.mark.skipif(
    unavailable_reason() is not None or INDI_SOCKET_TAKEN,
    reason="the simulator's programs are missing, or another INDI server holds /tmp/indiserver",
)
def test_live_world_starts_stand_ins_and_is_removed(tmp_path: Path) -> None:
    """A real world in a temporary folder is built, then removed."""
    rig = Rig(tmp_path, "wait", SIMULATED)
    launcher = rig.launcher(world_builder=launch.build_world, calibration_runner=lambda w, e: [])
    outcome: list[int] = []
    thread = threading.Thread(target=lambda: outcome.append(launcher.run()))
    thread.start()
    try:
        assert wait_for(rig.record.exists, 180)
        environment = rig.starts()[0]["environment"]
        assert environment[ACTIVE_SOURCE_VARIABLE] == SIMULATED
        assert Path(environment["ASTROMETRICS_CONFIG"]).exists()
    finally:
        launcher.request_stop()
        thread.join(timeout=120)
    assert outcome == [0]
    assert not (rig.root / "overlay").exists()
    assert lock_is_free(rig.root)


def test_restart_code_is_the_agreed_one() -> None:
    """The launcher and the backend agree on 75."""
    assert RESTART_EXIT_CODE == 75
