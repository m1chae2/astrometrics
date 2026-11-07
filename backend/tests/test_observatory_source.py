"""Purpose: Test the observatory source setting, its launch and its guards.

Description: No simulator process starts here. The environment is a plain
dict in a temporary folder, so the real settings and library are never read.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib.exceptions import ConfigurationError, InvalidArgumentError
from backend import observatory_source as source
from backend.services.infrastructure.settings_service import SettingsService
from backend.services.observatory.observatory_source_service import ObservatorySourceService
from wayfindinglib.simulation import observatory as observatory_module


class _PreparedOnly(observatory_module.SimulatedObservatory):
    """A simulator that skips the library copy (no real library here)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build the simulator without the library copy."""
        kwargs["copy_library"] = False
        kwargs.pop("real_library", None)
        super().__init__(*args, **kwargs)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Give an environment whose real folders are in a temporary folder.

    Returns
    -------
    environment : `dict` [`str`, `str`]
        ``XDG_CONFIG_HOME`` and ``XDG_DATA_HOME`` under `tmp_path`.
    """
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: [])
    monkeypatch.setattr(
        "backend.services.observatory.observatory_source_service.missing_programs",
        lambda include_overlay=True: [],
    )
    monkeypatch.setattr(source, "SimulatedObservatory", _PreparedOnly)
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "PATH": "/usr/bin",
    }


def test_the_choice_defaults_to_real_and_round_trips(environment: dict[str, str]) -> None:
    """A missing, damaged or unknown choice means the real telescope."""
    assert source.read_choice(environment) == source.SourceChoice()
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    assert source.read_choice(environment) == source.SourceChoice(source.SIMULATED)
    source.choice_file_path(environment).write_text("{not json", encoding="utf-8")
    assert source.read_choice(environment).source == source.REAL
    source.choice_file_path(environment).write_text('{"source": "mars"}', encoding="utf-8")
    assert source.read_choice(environment).source == source.REAL


def test_real_source_changes_nothing(environment: dict[str, str]) -> None:
    """With the real telescope the environment is left as it is."""
    before = dict(environment)
    assert source.apply_launch_environment(environment) == source.REAL
    assert environment == before


def test_simulator_source_moves_every_folder_to_the_simulator_world(
    environment: dict[str, str], tmp_path: Path
) -> None:
    """A simulator launch moves every folder and PATH to its own world."""
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    real_data = environment["XDG_DATA_HOME"]
    assert source.apply_launch_environment(environment) == source.SIMULATED
    root = Path(real_data) / source.SIMULATOR_FOLDER_NAME
    assert environment["XDG_DATA_HOME"] == str(root / "xdg_data")
    assert environment["XDG_CACHE_HOME"] == str(root / "xdg_cache")
    assert environment["XDG_STATE_HOME"] == str(root / "xdg_state")
    assert environment["XDG_CONFIG_HOME"] == str(root / "xdg_config")
    assert environment["ASTROMETRICS_CONFIG"] == str(root / "astrometrics.config.toml")
    assert environment["PATH"].startswith(str(root / "standin" / "bin"))
    assert environment[source.REAL_DATA_HOME_VARIABLE] == real_data
    assert environment[source.REAL_CONFIG_HOME_VARIABLE] == str(tmp_path / "config")
    assert 'hostname = "127.0.0.1"' in (root / "astrometrics.config.toml").read_text()
    # Nothing was written in the real data folder's astrometrics folder.
    assert not (Path(real_data) / "astrometrics").exists()
    # A second call in the same process changes nothing more.
    snapshot = dict(environment)
    assert source.apply_launch_environment(environment) == source.SIMULATED
    assert environment == snapshot


def test_a_restart_gets_back_the_environment_from_before_the_launch(environment: dict[str, str]) -> None:
    """A restart environment has no simulator variables."""
    environment["ASTROMETRICS_PORT"] = "5000"
    before = dict(environment)
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    source.apply_launch_environment(environment)
    assert source.original_environment(environment) == before


def test_a_pinned_world_is_left_alone(environment: dict[str, str]) -> None:
    """A run that already chose its own settings file keeps it."""
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    environment["ASTROMETRICS_CONFIG"] = "/sandbox/config.toml"
    before = dict(environment)
    source.apply_launch_environment(environment)
    assert environment == before


def test_a_simulator_that_cannot_start_falls_back_to_offline_never_the_real_telescope(
    environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing program gives an offline world, and says why."""
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: ["indiserver"])
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    assert source.apply_launch_environment(environment) == source.OFFLINE
    assert "indiserver" in environment[source.LAUNCH_ERROR_VARIABLE]
    assert environment[source.ACTIVE_SOURCE_VARIABLE] == source.OFFLINE
    config = Path(environment["ASTROMETRICS_CONFIG"])
    text = config.read_text()
    assert 'indi_port = "1"' in text
    assert 'auto_download = "false"' in text
    assert environment["ASTROMETRICS_STANDIN_SSH_MODE"] == "refuse"
    assert source.active_observatory(environment) is None
    assert environment["XDG_DATA_HOME"].startswith(str(Path(environment[source.REAL_DATA_HOME_VARIABLE])))


def test_any_error_at_launch_is_caught_and_recorded(
    environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settings parse error or a value error does not crash the backend."""

    def broken(self: observatory_module.SimulatedObservatory) -> None:
        """Fail like a damaged settings file does.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("bad settings")

    monkeypatch.setattr(_PreparedOnly, "prepare", broken)
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    assert source.apply_launch_environment(environment) == source.OFFLINE
    assert "ValueError: bad settings" in environment[source.LAUNCH_ERROR_VARIABLE]


def test_the_packaged_app_falls_back_to_offline(
    environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen build cannot run the simulator; it is offline, not real."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    assert source.apply_launch_environment(environment) == source.OFFLINE
    assert "packaged app" in environment[source.LAUNCH_ERROR_VARIABLE]


def test_active_observatory_is_none_for_the_real_telescope(environment: dict[str, str]) -> None:
    """No simulator object, so no process can ever start in real mode."""
    assert source.active_observatory(environment) is None
    source.write_choice(source.SourceChoice(source.SIMULATED), environment)
    source.apply_launch_environment(environment)
    rebuilt = source.active_observatory(environment)
    assert rebuilt is not None
    assert rebuilt.kind.value == "simulated"


def test_an_old_replay_choice_means_the_real_telescope(environment: dict[str, str]) -> None:
    """A saved replay choice from an older version is read as real."""
    source.choice_file_path(environment).parent.mkdir(parents=True, exist_ok=True)
    source.choice_file_path(environment).write_text('{"source": "replay", "replay_night": "2026-10-03"}')
    assert source.read_choice(environment).source == source.REAL


class _FakeObservatory:
    """A simulator that only records what the service asks of it."""

    indi_port = 4001
    phd2_port = 4002

    def __init__(self, root: Path) -> None:
        """Keep the folder and count the calls."""
        self.root = root
        self.standin_home = root / "standin" / "home"
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        """Count a start."""
        self.started += 1

    def stop(self) -> None:
        """Count a stop."""
        self.stopped += 1

    def is_running(self) -> bool:
        """Say it is running after a start.

        Returns
        -------
        running : `bool`
            `True` between a start and a stop.
        """
        return self.started > 0 and self.stopped == 0


def _service(
    environment: dict[str, str], observatory: Any = None, restarts: list | None = None
) -> ObservatorySourceService:
    """Build the service with a recording restarter.

    Returns
    -------
    service : `ObservatorySourceService`
        The service under test.
    """
    sink = restarts if restarts is not None else []
    return ObservatorySourceService(observatory, environment, restarter=lambda stop: sink.append(stop))


def test_service_starts_no_simulator_for_the_real_telescope(environment: dict[str, str]) -> None:
    """With no simulator object the service starts and stops nothing."""
    service = _service(environment)
    service.start()
    service.stop()
    state = service.get_source()
    assert state["source"] == "real"
    assert state["label"] == ""
    assert state["running"] is False


def test_service_starts_and_stops_the_simulator(environment: dict[str, str], tmp_path: Path) -> None:
    """The simulator starts in the background and stops at shutdown."""
    fake = _FakeObservatory(tmp_path)
    environment[source.ACTIVE_SOURCE_VARIABLE] = "simulated"
    service = _service(environment, fake)
    service.start()
    for _ in range(100):
        if fake.started:
            break
        import time

        time.sleep(0.05)
    assert fake.started == 1
    assert service.get_source()["label"] == "SIMULATOR"
    service.stop()
    assert fake.stopped == 1


def test_set_source_saves_the_choice_and_restarts(environment: dict[str, str]) -> None:
    """Switching writes the choice file and asks for one restart."""
    restarts: list = []
    service = _service(environment, restarts=restarts)
    result = service.set_source("simulated")
    assert result["restarting"] is True
    assert source.read_choice(environment).source == "simulated"
    assert len(restarts) == 1
    assert service.get_source()["restart_pending"] is True


def test_the_app_has_no_replay_source(environment: dict[str, str]) -> None:
    """Only real and simulated are accepted; replay and unknown names fail."""
    service = _service(environment)
    for name in ("replay", "mars"):
        with pytest.raises(InvalidArgumentError):
            service.set_source(name)
    assert "replay_night" not in service.get_source()
    assert not hasattr(service, "list_replay_nights")


def test_a_failed_simulator_is_reported_for_the_red_chip(environment: dict[str, str]) -> None:
    """The offline state reports failed, the reason and the red label."""
    environment[source.ACTIVE_SOURCE_VARIABLE] = source.OFFLINE
    environment[source.LAUNCH_ERROR_VARIABLE] = "ValueError: bad settings"
    state = _service(environment).get_source()
    assert state["failed"] is True
    assert state["reason"] == "ValueError: bad settings"
    assert state["label"] == "SIMULATOR FAILED"
    assert state["restart_pending"] is False


def test_switching_is_refused_while_work_runs(environment: dict[str, str]) -> None:
    """A running job, stack or download blocks the switch; nothing is saved."""
    restarts: list = []
    service = _service(environment, restarts=restarts)
    service.set_busy_probe(lambda: ["2 job(s) running (for example stacking)"])
    with pytest.raises(ConfigurationError, match="job"):
        service.set_source("simulated")
    assert restarts == []
    assert source.read_choice(environment).source == "real"


def test_the_packaged_app_refuses_to_switch(
    environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen build has no simulator and refuses to switch."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    service = _service(environment)
    with pytest.raises(ConfigurationError, match="packaged app"):
        service.set_source("simulated")
    state = service.get_source()
    assert state["available"] is False
    assert "packaged app" in state["unavailable_reason"]


def test_the_restart_stops_the_services_and_the_children_before_exec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The restart shuts down, ends children, closes descriptors, execs."""
    import os
    import subprocess

    child = subprocess.Popen(["sleep", "60"])
    pipe_read, pipe_write = os.pipe()
    os.set_inheritable(pipe_write, True)
    events: list[str] = []

    def fake_execve(program: str, arguments: list[str], environment: dict[str, str]) -> None:
        """Record what had stopped when the image would be replaced."""
        events.append(
            f"exec child_gone={child.poll() is not None} inheritable={os.get_inheritable(pipe_write)}"
        )

    try:
        thread = source.restart_backend(lambda: events.append("shutdown"), fake_execve, delay_seconds=0)
        thread.join(timeout=30)
        assert events[0] == "shutdown"
        assert events[1] == "exec child_gone=True inheritable=False"
    finally:
        child.kill()
        os.close(pipe_read)
        os.close(pipe_write)


def test_the_service_restart_runs_the_container_shutdown(environment: dict[str, str]) -> None:
    """A switch hands the container's shutdown to the restart."""
    restarts: list = []
    service = _service(environment, restarts=restarts)
    hook_calls: list[str] = []
    service.set_shutdown_hook(lambda: hook_calls.append("container"))
    service.set_source("simulated")
    restarts[0]()
    assert hook_calls == ["container"]


def test_leftovers_are_removed_when_the_backend_starts_in_real_mode(
    environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real-mode start cleans a crashed session in the simulator folder."""
    root = Path(environment["XDG_DATA_HOME"]) / source.SIMULATOR_FOLDER_NAME
    (root / "overlay" / "upper").mkdir(parents=True)
    (root / "library").mkdir(parents=True)
    (root / "library" / "astrometrics.db").write_text("copy")
    outside = Path(environment["XDG_DATA_HOME"]) / "astrometrics" / "astrometrics.db"
    outside.parent.mkdir(parents=True)
    outside.write_text("real")
    assert source.apply_launch_environment(environment) == source.REAL
    assert not (root / "overlay").exists()
    assert not (root / "library" / "astrometrics.db").exists()
    assert outside.read_text() == "real"


def test_choosing_the_active_source_again_does_not_restart(environment: dict[str, str]) -> None:
    """Picking the source already in force restarts nothing."""
    restarts: list = []
    service = _service(environment, restarts=restarts)
    assert service.set_source("real")["restarting"] is False
    assert restarts == []


def test_a_pinned_run_cannot_change_the_source(environment: dict[str, str]) -> None:
    """A run with its own fixed world refuses the change."""
    environment["ASTROMETRICS_TESTING"] = "1"
    with pytest.raises(ConfigurationError):
        _service(environment).set_source("simulated")


def test_simulator_programs_must_be_installed(
    environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A simulator source is refused when its programs are missing."""
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: ["rsync"])
    with pytest.raises(ConfigurationError, match="rsync"):
        _service(environment).set_source("simulated")


def test_settings_saves_in_simulator_mode_cannot_reach_the_real_telescope(
    environment: dict[str, str], tmp_path: Path
) -> None:
    """A simulator refuses a real host in a save; real mode allows it."""
    saved: list = []

    class _Config:
        """Records what is saved."""

        def update_config(self, config: dict) -> None:
            """Remember the settings."""
            saved.append(config)

    fake = _FakeObservatory(tmp_path)
    environment[source.ACTIVE_SOURCE_VARIABLE] = "simulated"
    simulated = SettingsService(_Config(), guard=_service(environment, fake).check_settings_update)
    with pytest.raises(ConfigurationError, match="Refused in simulator mode"):
        simulated.save_config({"Observatory.Telescope": {"hostname": "stellarmate.local"}})
    assert saved == []
    assert simulated.save_config({"Observatory.Telescope": {"focal_length_mm": "500"}}) is True
    real = SettingsService(_Config(), guard=_service(environment).check_settings_update)
    assert real.save_config({"Observatory.Telescope": {"hostname": "stellarmate.local"}}) is True


class _ReadyObservatory(_FakeObservatory):
    """A fake simulator that is up as soon as it starts."""

    def wait_ready(self) -> None:
        """Return at once: the fake is ready."""

    def pointed_targets(self) -> list:
        """Name no targets.

        Returns
        -------
        targets : `list`
            Always empty.
        """
        return []


def test_a_ready_simulator_puts_the_app_in_command_but_the_real_telescope_does_not(
    environment: dict[str, str], tmp_path: Path
) -> None:
    """The ready hook runs for a started simulator, never for the real one."""
    calls: list[str] = []
    environment[source.ACTIVE_SOURCE_VARIABLE] = "simulated"
    simulated = _service(environment, _ReadyObservatory(tmp_path))
    simulated.set_ready_hook(lambda: calls.append("simulated"))
    simulated._start_now(None)
    assert calls == ["simulated"]
    assert simulated.get_source()["ready"] is True

    real = _service({source.ACTIVE_SOURCE_VARIABLE: "real"})
    real.set_ready_hook(lambda: calls.append("real"))
    real.start()
    assert calls == ["simulated"]
    assert real.get_source()["source"] == "real"


def test_a_failing_ready_hook_does_not_fail_the_simulator(
    environment: dict[str, str], tmp_path: Path
) -> None:
    """If the app cannot take command, the simulator is still ready."""
    environment[source.ACTIVE_SOURCE_VARIABLE] = "simulated"
    service = _service(environment, _ReadyObservatory(tmp_path))

    def fail() -> None:
        """Fail as a refused promotion would.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("not eligible")

    service.set_ready_hook(fail)
    service._start_now(None)
    state = service.get_source()
    assert state["ready"] is True
    assert state["error"] is None


def test_the_outcome_of_the_ready_hook_is_reported_with_reasons(
    environment: dict[str, str], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Allowed and rejected capabilities reach ``get_source`` and the log."""
    from enum import StrEnum
    from types import SimpleNamespace

    class Capability(StrEnum):
        """Stand-in capability names."""

        MOUNT_CONTROL = "MOUNT_CONTROL"
        AUTOFOCUS = "AUTOFOCUS"

    environment[source.ACTIVE_SOURCE_VARIABLE] = "simulated"
    service = _service(environment, _ReadyObservatory(tmp_path))
    outcome = SimpleNamespace(
        applied={Capability.MOUNT_CONTROL: "AUTHORITATIVE"},
        rejected={Capability.AUTOFOCUS: "needs a FocusModel"},
    )
    service.set_ready_hook(lambda: outcome)
    with caplog.at_level("INFO"):
        service._start_now(None)
    commands = service.get_source()["commands"]
    assert commands == {
        "allowed": ["MOUNT_CONTROL"],
        "off": {"AUTOFOCUS": "needs a FocusModel"},
        "error": None,
    }
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert warnings == ["Simulator commands stay off for: AUTOFOCUS (needs a FocusModel)."]


def test_a_hook_failure_is_reported_as_an_error_and_the_real_source_has_no_commands(
    environment: dict[str, str], tmp_path: Path
) -> None:
    """A failed take-over shows its error; real reports no commands."""
    environment[source.ACTIVE_SOURCE_VARIABLE] = "simulated"
    service = _service(environment, _ReadyObservatory(tmp_path))

    def fail() -> None:
        """Fail like a refused promotion.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("boom")

    service.set_ready_hook(fail)
    service._start_now(None)
    assert service.get_source()["commands"]["error"] == "RuntimeError: boom"
    assert _service({source.ACTIVE_SOURCE_VARIABLE: "real"}).get_source()["commands"] is None


def _simulator_wayfinder(root: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a `Wayfinder` over temporary folders and a real-shaped policy.

    Parameters
    ----------
    root : `pathlib.Path`
        The stand-in simulator folder; the wayfinding library is inside it.
    monkeypatch : `pytest.MonkeyPatch`
        Points the settings file into `root`.

    Returns
    -------
    wayfinder : `Wayfinder`
        One telescope and camera are active; the policy has one row,
        ``MOUNT_CONTROL`` ``DELEGATED``.
    """
    from astrometricslib import AppConfiguration
    from wayfindinglib import Wayfinder
    from wayfindinglib.models.policy.delegation import (
        CapabilityDelegation,
        DelegationPolicy,
        DelegationState,
        ObservatoryCapability,
    )

    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: root / "astrometrics.config.toml")
    config = AppConfiguration()
    config.update_config({
        "Wayfinding Library": {"path": str(root / "wayfinding")},
        "Observatory.Telescope": {"models": "Rig A", "active_telescope": "Rig A"},
        "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
        "Observatory.Camera": {"models": "CamA", "default_primary_camera": "CamA"},
        "Observatory.Camera.CamA": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })
    wayfinder = Wayfinder(config)
    policy = DelegationPolicy(
        id="default",
        capability_delegations=[
            CapabilityDelegation(
                capability=ObservatoryCapability.MOUNT_CONTROL, state=DelegationState.DELEGATED
            )
        ],
    )
    wayfinder.control._context.butler.put(policy, "delegation_policy", {"id": "default"})
    return wayfinder


def test_the_simulator_start_records_made_up_calibrations_and_commands_are_all_on(
    environment: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container's hook records the records; every command is on."""
    from types import SimpleNamespace

    from backend.container import Container
    from wayfindinglib.models.policy.delegation import ObservatoryCapability

    wayfinder = _simulator_wayfinder(tmp_path, monkeypatch)
    container = SimpleNamespace(wayfinder=wayfinder)
    environment[source.ACTIVE_SOURCE_VARIABLE] = "simulated"
    service = _service(environment, _ReadyObservatory(tmp_path))
    service.set_ready_hook(lambda: Container._take_command_of_simulator(container, tmp_path))
    service._start_now(None)

    commands = service.get_source()["commands"]
    assert commands["off"] == {}
    assert commands["error"] is None
    assert len(commands["allowed"]) == len(ObservatoryCapability)
    butler = wayfinder.control._context.butler
    assert butler.get_all("guider_calibration")[0].source_note.startswith("Simulator")
    assert butler.get_all("focus_model")[0].source_note.startswith("Simulator")


@pytest.mark.parametrize("active_source", ["real", "offline"])
def test_the_real_and_offline_sources_record_nothing_and_keep_the_policy(
    environment: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active_source: str
) -> None:
    """With no simulator there is no hook: no record, no policy change."""
    wayfinder = _simulator_wayfinder(tmp_path, monkeypatch)
    butler = wayfinder.control._context.butler
    policy_before = butler.get("delegation_policy", {"id": "default"})
    service = _service({**environment, source.ACTIVE_SOURCE_VARIABLE: active_source})
    service.start()
    assert service.get_source()["commands"] is None
    assert butler.get_all("guider_calibration") == []
    assert butler.get_all("focus_model") == []
    assert butler.get("delegation_policy", {"id": "default"}) == policy_before


def test_the_hook_refuses_a_library_that_is_not_the_simulators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wayfinding library outside the simulator folder is refused."""
    from types import SimpleNamespace

    from backend.container import Container
    from wayfindinglib.exceptions import GuardError

    wayfinder = _simulator_wayfinder(tmp_path, monkeypatch)
    other_root = tmp_path / "another_folder"
    other_root.mkdir()
    with pytest.raises(GuardError):
        Container._take_command_of_simulator(SimpleNamespace(wayfinder=wayfinder), other_root)
    assert wayfinder.control._context.butler.get_all("guider_calibration") == []
