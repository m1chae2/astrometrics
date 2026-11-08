"""Purpose: Test how the fit and the real-frame source are wired into a world.

Description: The launcher knows the backend's port and the fit's results
before the stand-ins start. These tests check, with fake stand-ins, that the
launcher gives the backend's port to the world builder (so the exposure
driver can deliver real frames), that the fit runs after the library copy is
built and before the stand-ins start, that its controller settings reach the
real PHD2 and its results file reaches the exposure driver, that a fit that
fails is a launch note and leaves the defaults, that the exposure driver
needs no guide log with the real PHD2, and that the calibration step reads
the fitted guide rate. No process is started.
"""

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from simulator import launch, observatory
from simulator.fit.fitting import FitResult, FittedValue
from simulator.fit.results import RESULTS_FILE_NAME, fingerprint_library_copy, write_results
from simulator.launch import Launcher, build_world, run_fit_process
from simulator.observatory import GuiderKind, SimulatedObservatory, SimulationKind
from simulator.simulated_calibration import _simulator_guider


class FakeObservatory:
    """A simulated observatory that records how it was built and started."""

    instances: ClassVar[list[FakeObservatory]] = []

    def __init__(self, root: Path, kind: object, indi_port: int, phd2_port: int, **kwargs: object) -> None:
        """Remember the arguments and the order of events."""
        self.root = root
        self.kwargs = kwargs
        self.config_path = root / "astrometrics.config.toml"
        self.phd2 = None
        self.notes: list[str] = []
        self.events: list[str] = []
        FakeObservatory.instances.append(self)

    def environment(self) -> dict[str, str]:
        """Give the variables the world adds.

        Returns
        -------
        variables : `dict` [`str`, `str`]
            A path variable.
        """
        return {"PATH": "/usr/bin"}

    def prepare(self) -> None:
        """Make the folder, as the real preparation does."""
        self.root.mkdir(parents=True, exist_ok=True)
        FakeObservatory.order.append("prepared")

    def start(self) -> None:
        """Note the start."""
        FakeObservatory.order.append("started")

    def wait_ready(self) -> None:
        """Do nothing."""

    def pointed_targets(self) -> list:
        """Give no targets.

        Returns
        -------
        targets : `list`
            Empty.
        """
        return []

    def stop(self) -> None:
        """Do nothing."""

    order: ClassVar[list[str]] = []


@pytest.fixture
def fake_observatory(monkeypatch: pytest.MonkeyPatch) -> type[FakeObservatory]:
    """Replace the observatory class launch builds worlds with.

    Returns
    -------
    fake : `type`
        The fake class; its ``instances`` and ``order`` are cleared.
    """
    FakeObservatory.instances = []
    FakeObservatory.order = []
    monkeypatch.setattr(launch, "SimulatedObservatory", FakeObservatory)
    return FakeObservatory


def _environment(tmp_path: Path) -> dict[str, str]:
    """Give a launcher environment with the stand-in guider.

    Returns
    -------
    environment : `dict` [`str`, `str`]
        Folders in the temporary path, so no real folder is named.
    """
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        launch.GUIDER_VARIABLE: "mock",
    }


def _writing_fitter(controller: dict[str, float] | None) -> object:
    """Make a fit runner that writes a small results file.

    Returns
    -------
    fitter : `~collections.abc.Callable`
        Like `run_fit_process`, but fake.
    """

    def fitter(root: Path, config_path: Path, variables: dict[str, str], environ: dict[str, str]) -> None:
        """Write a results file, as the real fit would."""
        FakeObservatory.order.append("fitted")
        result = FitResult(
            values=[
                FittedValue(
                    "periodic_error",
                    "x",
                    "CCD Simulator",
                    "SIMULATOR_SETTINGS",
                    "SIM_PEMAX",
                    "arcsec",
                    1.0,
                    True,
                    "history fit",
                )
            ],
            controller_settings=controller,
        )
        write_results(root / RESULTS_FILE_NAME, result, fingerprint_library_copy(root))

    return fitter


def test_the_launcher_gives_the_backend_port_to_the_default_world_builder() -> None:
    """The exposure driver needs the port the backend will listen on."""
    launcher = Launcher(environ={}, root=Path("/nonexistent"), port=5123)
    assert isinstance(launcher.world_builder, partial)
    assert launcher.world_builder.func is build_world
    assert launcher.world_builder.keywords["backend_port"] == 5123


def test_a_builder_given_by_a_test_is_left_alone() -> None:
    """Only the default builder gets the port."""

    def own(root: Path, environ: dict[str, str]) -> None:
        """Build nothing."""

    assert Launcher(environ={}, root=Path("/x"), port=5123, world_builder=own).world_builder is own


def test_the_fit_runs_between_the_copy_and_the_stand_ins_and_feeds_both(
    tmp_path: Path, fake_observatory: type[FakeObservatory]
) -> None:
    """Results file and port go to the driver, controller settings to PHD2."""
    controller = {"aggressiveness": 0.45, "hysteresis": 0.25}
    world = build_world(
        tmp_path / "world", _environment(tmp_path), backend_port=5123, fitter=_writing_fitter(controller)
    )
    assert world.source == launch.SIMULATED
    assert fake_observatory.order == ["prepared", "fitted", "started"]
    runner = fake_observatory.instances[1]
    assert runner.kwargs["backend_port"] == 5123
    assert runner.kwargs["fitted_settings_path"] == tmp_path / "world" / RESULTS_FILE_NAME
    assert runner.kwargs["controller_settings"] == controller


def test_explicit_controller_settings_win_over_the_fit(
    tmp_path: Path, fake_observatory: type[FakeObservatory]
) -> None:
    """A caller who gave settings keeps them."""
    build_world(
        tmp_path / "world",
        _environment(tmp_path),
        controller_settings={"aggressiveness": 0.9},
        fitter=_writing_fitter({"aggressiveness": 0.45}),
    )
    assert fake_observatory.instances[1].kwargs["controller_settings"] == {"aggressiveness": 0.9}


def test_a_failed_fit_is_a_note_and_leaves_the_defaults(
    tmp_path: Path, fake_observatory: type[FakeObservatory]
) -> None:
    """The world still starts; there is no results file for the driver."""
    world = build_world(
        tmp_path / "world",
        _environment(tmp_path),
        fitter=lambda *arguments: "Fitted settings: not applied (OSError)",
    )
    assert world.source == launch.SIMULATED
    assert "Fitted settings: not applied (OSError)" in world.notes
    runner = fake_observatory.instances[1]
    assert runner.kwargs["fitted_settings_path"] is None
    assert runner.kwargs["controller_settings"] is None


def test_an_old_results_file_is_removed_before_the_fit(
    tmp_path: Path, fake_observatory: type[FakeObservatory]
) -> None:
    """A file left by an earlier start never stands in for a failed fit."""
    root = tmp_path / "world"
    root.mkdir()
    (root / RESULTS_FILE_NAME).write_text("{}", encoding="utf-8")
    build_world(root, _environment(tmp_path), fitter=lambda *arguments: "failed")
    assert not (root / RESULTS_FILE_NAME).exists()
    assert fake_observatory.instances[1].kwargs["fitted_settings_path"] is None


def test_the_fit_process_runs_the_module_with_the_worlds_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command names the world; its environment names the settings."""
    seen: dict = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        """Record the call and succeed.

        Returns
        -------
        completed : `~types.SimpleNamespace`
            A finished process.
        """
        seen.update(command=command, env=kwargs["env"])
        return SimpleNamespace(returncode=0, stdout='{"values": 3}\n', stderr="")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    config = tmp_path / "world" / "astrometrics.config.toml"
    note = run_fit_process(tmp_path / "world", config, {"PATH": "/usr/bin"}, _environment(tmp_path))
    assert note is None
    assert seen["command"][1:3] == ["-m", "simulator.fit"]
    assert seen["command"][seen["command"].index("--root") + 1] == str(tmp_path / "world")
    assert seen["env"]["ASTROMETRICS_CONFIG"] == str(config)
    assert seen["env"]["XDG_DATA_HOME"] == str(tmp_path / "world" / "xdg_data")


def test_a_fit_that_exits_with_an_error_gives_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason's last line is in the note; nothing is raised."""
    monkeypatch.setattr(
        launch.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="trace\nGuardError: not mine\n"
        ),
    )
    note = run_fit_process(tmp_path / "w", tmp_path / "w" / "c.toml", {}, _environment(tmp_path))
    assert note == "Fitted settings: not applied (GuardError: not mine)"


def test_the_observatory_passes_the_port_and_the_file_to_the_driver(tmp_path: Path) -> None:
    """Optional driver arguments appear only when there is something."""
    world = SimulatedObservatory(
        tmp_path,
        SimulationKind.SIMULATED_SKY,
        4500,
        4501,
        backend_port=5123,
        fitted_settings_path=tmp_path / "f.json",
    )
    assert world._driver_optional_arguments() == [
        "--guide-log", str(observatory.GUIDE_LOG_FIXTURE),
        "--backend-port", "5123",
        "--fitted-settings", str(tmp_path / "f.json"),
    ]  # fmt: skip
    plain = SimulatedObservatory(tmp_path, SimulationKind.SIMULATED_SKY, 4500, 4501)
    assert plain._driver_optional_arguments() == ["--guide-log", str(observatory.GUIDE_LOG_FIXTURE)]
    real = SimulatedObservatory(tmp_path, SimulationKind.SIMULATED_SKY, 4500, 4501, guider=GuiderKind.PHD2)
    real.phd2 = SimpleNamespace()  # type: ignore[assignment]
    assert real._driver_optional_arguments() == []


def test_the_driver_writes_no_guide_log_without_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the real PHD2 the driver starts no writer and makes no log."""
    from simulator.exposure_driver import ExposureDriver

    driver = ExposureDriver(4500, tmp_path, None, tmp_path / "stats.json", 1.0, "M 13", 16.7, 36.4)
    monkeypatch.setattr(driver, "connect_devices", lambda: True)
    monkeypatch.setattr(driver, "wait_for_cooling", lambda: None)
    monkeypatch.setattr(driver, "take_exposures", lambda: None)
    monkeypatch.setattr(driver, "write_guide_log", lambda: pytest.fail("a guide log writer was started"))
    assert driver.run() == 0
    driver.stop_event.set()
    assert not driver.guide_log_directory.exists()


def test_the_calibration_takes_the_fitted_guide_rate() -> None:
    """The recorded guide speed matches the mount the fit configured."""
    _, _, ra_rate, dec_rate, source = _simulator_guider(1.2, {"GUIDE_RATE_WE": 0.4, "GUIDE_RATE_NS": 0.6})
    assert (ra_rate, dec_rate) == pytest.approx((6.0, 9.0))
    assert "fitted guide rate" in source
    _, _, ra_rate, dec_rate, _ = _simulator_guider(1.2)
    assert (ra_rate, dec_rate) == (7.52, 7.52)
