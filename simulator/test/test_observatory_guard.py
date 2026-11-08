"""Purpose: Test the simulator's safety checks and its offline world.

Description: The checks must stop a simulator from using any of the user's
real library folders: the image library, the frames, the stacks and the
wayfinding library. They run on settings text and on a settings save, and a
simulator's own settings file is set right again on every start. The tests
use made-up "real" folders in a temporary folder and never read the user's
settings.
"""

import os
import signal
import threading
import time
from pathlib import Path

import pytest

from simulator import observatory as observatory_module
from simulator import observatory_guard
from simulator.errors import GuardError, SimulationUnavailableError
from simulator.observatory import (
    SimulatedObservatory,
    SimulationKind,
    prepare_offline_world,
    unavailable_reason,
)
from simulator.settings import simulator_config_text, standin_home


@pytest.fixture
def world(tmp_path: Path) -> dict:
    """Make a simulator root and a made-up set of real folders.

    Returns
    -------
    world : `dict`
        ``root`` (the simulator folder), ``standin`` (its telescope
        computer), ``real`` (a made-up real library folder) and
        ``protected`` (the folders the guard must keep out of).
    """
    root = tmp_path / "simulator"
    real = tmp_path / "real" / "astrometrics"
    (real / "frames").mkdir(parents=True)
    root.mkdir()
    return {"root": root, "standin": standin_home(root), "real": real, "protected": (real.resolve(),)}


def _text(world: dict, **replacements: str) -> str:
    """Build the simulator's settings text, with some text replaced.

    Parameters
    ----------
    world : `dict`
        The `world` fixture.
    **replacements : `Path`
        The setting to change (``library``, ``frames``, ``stacks`` or
        ``wayfinding``) and the folder to name for it.

    Returns
    -------
    text : `str`
        The settings text.
    """
    root = world["root"]
    text = simulator_config_text(root, 11000, 11001)
    names = {
        "library": ('path = "' + str(root / "library") + '"', "path"),
        "frames": ('frames_path = "' + str(root / "library" / "frames") + '"', "frames_path"),
        "stacks": ('stacks_path = "' + str(root / "library" / "frames") + '"', "stacks_path"),
        "wayfinding": ('path = "' + str(root / "wayfinding") + '"', "path"),
    }
    for name, folder in replacements.items():
        old, key = names[name]
        text = text.replace(old, f'{key} = "{folder}"', 1)
    return text


def _check(world: dict, text: str) -> None:
    """Run the whole-file check with the made-up real folders."""
    observatory_guard.check_settings_text(
        text, 11000, 11001, world["standin"], world["root"], world["protected"]
    )


def test_the_simulators_own_settings_pass(world: dict) -> None:
    """The text the simulator writes passes, with a stacks folder named."""
    text = _text(world)
    assert "stacks_path" in text
    _check(world, text)


@pytest.mark.parametrize("name", ["library", "frames", "stacks", "wayfinding"])
def test_a_library_folder_outside_the_simulator_is_refused(world: dict, name: str) -> None:
    """Each library folder must stay inside the simulator folder."""
    elsewhere = world["root"].parent / "elsewhere"
    with pytest.raises(GuardError, match="not inside the simulator folder"):
        _check(world, _text(world, **{name: elsewhere}))


@pytest.mark.parametrize("name", ["library", "frames", "stacks", "wayfinding"])
def test_a_real_library_folder_is_refused_even_through_a_link(world: dict, name: str) -> None:
    """A link inside the simulator to the real library is refused."""
    link = world["root"] / "link"
    link.symlink_to(world["real"], target_is_directory=True)
    with pytest.raises(GuardError, match="not inside the simulator folder"):
        _check(world, _text(world, **{name: link / "frames"}))


def test_a_real_folder_inside_the_simulator_folder_is_refused(world: dict) -> None:
    """A real folder inside the simulator folder is still refused."""
    inside = world["root"] / "library"
    protected = (inside.resolve(),)
    with pytest.raises(GuardError, match="real folder"):
        observatory_guard.check_settings_text(
            _text(world), 11000, 11001, world["standin"], world["root"], protected
        )


def test_a_missing_or_relative_library_folder_is_refused(world: dict) -> None:
    """A whole settings file must name the libraries, as absolute paths."""
    text = _text(world)
    without_wayfinding = text.replace('["Wayfinding Library"]', '["Other"]')
    with pytest.raises(GuardError, match="Wayfinding Library"):
        _check(world, without_wayfinding)
    with pytest.raises(GuardError, match="not an absolute path"):
        _check(world, _text(world, library="relative/library"))


def test_a_settings_save_cannot_move_a_library_out_of_the_simulator(world: dict) -> None:
    """Every library key in a save is checked; other keys are left alone."""
    check = observatory_guard.check_settings_update
    root, standin, protected = world["root"], world["standin"], world["protected"]
    check({"Image Library": {"path": str(root / "library" / "more")}}, 1, 2, standin, root, protected)
    for section, key in (
        ("Image Library", "path"),
        ("Image Library", "frames_path"),
        ("Image Library", "stacks_path"),
        ("Wayfinding Library", "path"),
    ):
        with pytest.raises(GuardError):
            check({section: {key: str(world["real"])}}, 1, 2, standin, root, protected)
        with pytest.raises(GuardError):
            check({section: {key: ""}}, 1, 2, standin, root, protected)


def test_prepare_sets_the_library_keys_right_again(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A settings file that points at the real library is repaired at start."""
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: [])
    monkeypatch.setattr(observatory_guard, "real_protected_paths", lambda environ=None: world["protected"])
    observatory = SimulatedObservatory(world["root"], SimulationKind.SIMULATED_SKY, 11000, 11001)
    observatory.prepare()
    real = world["real"]
    tampered = (
        observatory.config_path
        .read_text()
        .replace(str(world["root"] / "library" / "frames"), str(real / "frames"))
        .replace(f'path = "{world["root"] / "wayfinding"}"', f'path = "{real / "wayfinding"}"')
        .replace(f'path = "{world["root"] / "library"}"', f'path = "{real}"')
    )
    assert str(real) in tampered
    observatory.config_path.write_text(tampered)
    observatory.prepare()
    repaired = observatory.config_path.read_text()
    assert str(real) not in repaired
    assert f'stacks_path = "{world["root"] / "library" / "frames"}"' in repaired


def test_prepare_adds_a_missing_stacks_key_and_section(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings without the stacks key or the guiding section get them."""
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: [])
    observatory = SimulatedObservatory(world["root"], SimulationKind.SIMULATED_SKY, 11000, 11001)
    observatory.prepare()
    text = observatory.config_path.read_text()
    text = "\n".join(line for line in text.splitlines() if not line.startswith("stacks_path"))
    text = text.replace('["Observatory.Guiding"]', '["Observatory.Other"]')
    observatory.config_path.write_text(text)
    observatory.prepare()
    repaired = observatory.config_path.read_text()
    assert "stacks_path" in repaired
    assert f'phd2_port = "{observatory.phd2_port}"' in repaired


def test_a_simulator_folder_inside_a_real_folder_is_refused(world: dict) -> None:
    """The simulator folder must not be, hold or sit inside a real folder."""
    observatory_guard.check_root(world["root"], world["protected"])
    with pytest.raises(GuardError):
        observatory_guard.check_root(world["real"] / "simulator", world["protected"])
    with pytest.raises(GuardError):
        observatory_guard.check_root(world["real"].parent, world["protected"])


def test_the_real_folders_come_from_the_real_variables_or_the_home_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A harness's own settings file is not taken for the real one."""
    config = tmp_path / "real.toml"
    config.write_text(f'["Image Library"]\npath = "{tmp_path / "lib"}"\n')
    named = observatory_guard.real_protected_paths({"ASTROMETRICS_REAL_CONFIG_PATH": str(config)})
    assert (tmp_path / "lib").resolve() in named
    harness = observatory_guard.real_protected_paths({"ASTROMETRICS_CONFIG_PATH": str(config)})
    assert (tmp_path / "lib").resolve() not in harness


def test_the_packaged_app_cannot_run_the_simulator(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A frozen build says so, and the simulator refuses to prepare."""
    assert unavailable_reason() is None or "needs these programs" in unavailable_reason()
    monkeypatch.setattr("sys.frozen", True, raising=False)
    assert unavailable_reason() == "The simulator is not available in the packaged app."
    observatory = SimulatedObservatory(world["root"], SimulationKind.SIMULATED_SKY, 11000, 11001)
    with pytest.raises(SimulationUnavailableError, match="packaged app"):
        observatory.prepare()
    assert not observatory.config_path.exists()


def test_the_offline_world_names_no_telescope(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline world has dead ports, no downloads and a refusing ssh."""
    monkeypatch.setattr(observatory_guard, "real_protected_paths", lambda environ=None: world["protected"])
    config = prepare_offline_world(world["root"])
    text = config.read_text()
    assert 'hostname = "localhost"' in text
    assert 'indi_port = "1"' in text
    assert 'phd2_port = "1"' in text
    assert 'auto_download = "false"' in text
    assert (world["root"] / "standin" / "bin" / "ssh").is_symlink()
    environment = observatory_module.offline_environment(world["root"])
    assert environment["ASTROMETRICS_STANDIN_SSH_MODE"] == "refuse"
    assert environment["PATH"].startswith(str(world["root"] / "standin" / "bin"))
    bad = text.replace('hostname = "localhost"', 'hostname = "stellarmate.local"')
    with pytest.raises(GuardError, match="stellarmate"):
        observatory_guard.check_offline_settings_text(bad, world["root"], world["protected"])
    with pytest.raises(GuardError, match="auto_download"):
        observatory_guard.check_offline_settings_text(
            text.replace('auto_download = "false"', 'auto_download = "true"'),
            world["root"],
            world["protected"],
        )


def test_the_errors_are_the_librarys_configuration_errors() -> None:
    """The two simulator errors are public library errors."""
    from astrometricslib.exceptions import ConfigurationError

    assert issubclass(GuardError, ConfigurationError)
    assert issubclass(SimulationUnavailableError, ConfigurationError)


def test_the_start_command_waits_until_it_is_stopped(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``start`` writes the ports file, waits for SIGTERM, then ends all."""
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: [])
    monkeypatch.setattr(observatory_guard, "real_protected_paths", lambda environ=None: world["protected"])
    ports_file = world["root"].parent / "ports.json"

    def fake_start_processes(self: SimulatedObservatory) -> None:
        """Start two sleeping stand-ins, in place of INDI and PHD2."""
        self._launch("indiserver", ["sleep", "60"])
        self._launch("phd2", ["sleep", "60"])

    monkeypatch.setattr(SimulatedObservatory, "_start_processes", fake_start_processes)
    old_term, old_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)

    def stop_when_ready() -> None:
        """Wait for the ports file, then send SIGTERM to this process."""
        deadline = time.time() + 20
        while not ports_file.exists() and time.time() < deadline:
            time.sleep(0.1)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=stop_when_ready, daemon=True).start()
    arguments = observatory_module.argparse.Namespace(
        kind="simulated",
        root=world["root"],
        indi_port=11000,
        phd2_port=11001,
        ports_file=ports_file,
        replay_night=None,
        replay_staged=False,
        manual_replay_start=False,
        exposure_seconds=60.0,
        existing_world=False,
    )
    try:
        status = observatory_module.run_start(arguments)
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    assert status == 0
    assert ports_file.exists()
    assert not (world["root"] / observatory_module.PROCESS_ID_FILE).exists()
