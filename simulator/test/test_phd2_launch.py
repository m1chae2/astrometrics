"""Purpose: Test how the simulator prepares and starts the real PHD2.

Description: Checks the pure parts of `simulator.phd2_launch` (instance
numbers and ports, the profile text, the environment and the command), the
launcher's choice of guider, and the fall back to the stand-in guider with a
status note. No PHD2 and no INDI server is started here; the live test is in
`test_phd2_live.py`.
"""

import configparser
from pathlib import Path

import pytest

from simulator import launch, observatory, phd2_launch
from simulator.observatory import GuiderKind, SimulatedObservatory, SimulationKind


def test_the_instance_ports_follow_phd2s_rule() -> None:
    """Instance i listens on 4399 + i, with the second port at 4299 + i."""
    assert phd2_launch.event_port(1) == 4400
    assert phd2_launch.event_port(2) == 4401
    assert phd2_launch.second_port(2) == 4301


def test_the_first_instance_avoids_the_real_phd2_port() -> None:
    """Instance 1 would use 4400, so the choice starts at 2."""
    assert phd2_launch.choose_instance(lambda port: True) == 2


def test_an_instance_with_a_busy_port_is_skipped() -> None:
    """Both ports of an instance must be free."""
    busy = {4401, 4302}
    assert phd2_launch.choose_instance(lambda port: port not in busy) == 4


def test_no_free_instance_gives_none() -> None:
    """When every port is busy there is no instance."""
    assert phd2_launch.choose_instance(lambda port: False) is None


def test_the_config_file_name_per_instance() -> None:
    """PHD2 names the settings file after the instance number."""
    assert phd2_launch.config_file_name(1) == ".PHDGuidingV2"
    assert phd2_launch.config_file_name(3) == ".PHDGuidingV2-instance3"


def test_the_profile_connects_to_the_worlds_indi_server() -> None:
    """The INDI host, port and device names are the world's."""
    text, milliseconds = phd2_launch.profile_text(2, 4567)
    assert "INDIhost=127.0.0.1" in text
    assert "INDIport=4567" in text
    assert "INDIcam=Guide Simulator" in text
    assert "INDImount=Telescope Simulator" in text
    assert "name=Astrometrics simulator 2" in text
    assert "ServerMode=1" in text
    assert milliseconds == 1000
    assert "GuideAlgorithm" not in text


def test_controller_settings_go_into_the_algorithm_sections() -> None:
    """Aggressiveness, minimum move, hysteresis and exposure are written."""
    text, milliseconds = phd2_launch.profile_text(
        2,
        4567,
        {"aggressiveness": 0.6, "minimum_move": 0.15, "hysteresis": 0.2, "exposure_seconds": 2.5},
    )
    assert milliseconds == 2500
    assert "ExposureDurationMs=2500" in text
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # keep the key case
    parser.read_string(text.split("\n", 4)[4].replace("[profile]\n", "", 1))
    right_ascension = parser["profile/1/scope/GuideAlgorithm/X/Hysteresis"]
    declination = parser["profile/1/scope/GuideAlgorithm/Y/ResistSwitch"]
    assert float(right_ascension["aggression"]) == pytest.approx(0.6)
    assert float(right_ascension["minMove"]) == pytest.approx(0.15)
    assert float(right_ascension["hysteresis"]) == pytest.approx(0.2)
    assert float(declination["aggression"]) == pytest.approx(0.6)
    assert float(declination["minMove"]) == pytest.approx(0.15)
    assert "hysteresis" not in declination


def test_a_setting_left_out_keeps_the_phd2_default() -> None:
    """Only the given controller settings are written."""
    text, _ = phd2_launch.profile_text(2, 4567, {"hysteresis": 0.3})
    assert "hysteresis=0.3" in text
    assert "aggression" not in text
    assert "ResistSwitch" not in text


@pytest.mark.parametrize(
    "settings",
    [{"speed": 1.0}, {"aggressiveness": 5.0}, {"hysteresis": 1.0}, {"exposure_seconds": 0.0}],
)
def test_bad_controller_settings_are_refused(settings: dict[str, float]) -> None:
    """An unknown key or a value out of range raises a ValueError."""
    with pytest.raises(ValueError):
        phd2_launch.controller_settings_text(settings)


def test_the_profile_is_written_in_the_stand_in_home(tmp_path: Path) -> None:
    """The file lands in the home folder, named for the instance."""
    home = tmp_path / "standin" / "home"
    record = phd2_launch.write_profile(home, tmp_path, 3, 4567, {"aggressiveness": 0.5})
    assert (home / ".PHDGuidingV2-instance3").read_text().count("INDIport=4567") == 1
    assert record.event_port == 4402
    assert record.profile_name == "Astrometrics simulator 3"
    assert record.home == home


def test_a_home_outside_the_world_is_refused(tmp_path: Path) -> None:
    """PHD2 never gets a HOME outside the world, nor the user's own."""
    with pytest.raises(ValueError, match="not inside the simulator folder"):
        phd2_launch.write_profile(tmp_path / "elsewhere", tmp_path / "world", 2, 4567)
    with pytest.raises(ValueError):
        phd2_launch.require_world_home(Path.home(), Path.home())


def test_the_environment_keeps_phd2_in_the_world(tmp_path: Path) -> None:
    """HOME and the XDG folders are inside the home; the display is X."""
    variables = phd2_launch.phd2_environment(tmp_path, {"XAUTHORITY": "/run/user/1/auth"})
    assert variables["HOME"] == str(tmp_path)
    assert all(
        Path(variables[name]).is_relative_to(tmp_path)
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
    )
    assert variables["GDK_BACKEND"] == "x11"
    assert variables["GSETTINGS_BACKEND"] == "memory"
    assert "XAUTHORITY" not in variables
    assert "WAYLAND_DISPLAY" in phd2_launch.UNSET_VARIABLES


def test_the_command_uses_xvfb_first_and_the_users_display_as_a_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """xvfb-run is the default; DISPLAY is only the fallback."""
    program = tmp_path / "phd2"
    xvfb = ["xvfb-run", "-a", str(program), "-i", "3"]
    monkeypatch.setattr(phd2_launch.shutil, "which", lambda name, path=None: "/usr/bin/xvfb-run")
    assert phd2_launch.phd2_command(program, 3, {"PATH": "/usr/bin"}) == xvfb
    assert phd2_launch.phd2_command(program, 3, {"DISPLAY": ":0"}) == xvfb
    assert phd2_launch.display_note({"DISPLAY": ":0"}) is None
    monkeypatch.setattr(phd2_launch.shutil, "which", lambda name, path=None: None)
    assert phd2_launch.phd2_command(program, 3, {"DISPLAY": ":0"}) == [str(program), "-i", "3"]
    note = phd2_launch.display_note({"DISPLAY": ":0"})
    assert note is not None
    assert "xvfb-run is not installed" in note
    with pytest.raises(RuntimeError):
        phd2_launch.phd2_command(program, 3, {"PATH": ""})
    assert phd2_launch.display_note({"PATH": ""}) is None


def test_the_guider_choice_reports_the_visible_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Using the user's display is a status note; PHD2 still plays."""
    monkeypatch.setattr(phd2_launch, "find_phd2", lambda environ=None: Path("/x/phd2"))
    monkeypatch.setattr(phd2_launch.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(phd2_launch, "choose_instance", lambda *a, **k: 5)
    guider, port, notes = launch.choose_guider({"DISPLAY": ":0"})
    assert (guider, port) == (GuiderKind.PHD2, 4404)
    assert notes == [phd2_launch.PHD2_VISIBLE_NOTE]


def test_a_missing_phd2_is_a_status_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """No phd2 program gives a note that names the stand-in, not an error."""
    monkeypatch.setattr(phd2_launch, "find_phd2", lambda environ=None: None)
    note = phd2_launch.phd2_missing_note({})
    assert note is not None
    assert "stand-in guider plays PHD2" in note
    assert "phd2 is not installed" in note


def test_the_guider_choice_uses_phd2_when_it_can_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real PHD2 is chosen, with the port of a free instance."""
    monkeypatch.setattr(phd2_launch, "phd2_missing_note", lambda environ=None: None)
    monkeypatch.setattr(phd2_launch, "choose_instance", lambda *a, **k: 5)
    assert launch.choose_guider({}) == (GuiderKind.PHD2, 4404, [])


def test_the_guider_choice_falls_back_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without PHD2 the stand-in plays, and the note says why."""
    monkeypatch.setattr(phd2_launch, "phd2_missing_note", lambda environ=None: "note text")
    assert launch.choose_guider({}) == (GuiderKind.MOCK, None, ["note text"])


def test_the_environment_variable_forces_the_stand_in() -> None:
    """Setting the guider variable to mock asks for the stand-in."""
    assert launch.choose_guider({launch.GUIDER_VARIABLE: "mock"}) == (GuiderKind.MOCK, None, [])


def test_no_free_instance_falls_back_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """When every instance is busy the stand-in plays."""
    monkeypatch.setattr(phd2_launch, "phd2_missing_note", lambda environ=None: None)
    monkeypatch.setattr(phd2_launch, "choose_instance", lambda *a, **k: None)
    guider, port, notes = launch.choose_guider({})
    assert (guider, port) == (GuiderKind.MOCK, None)
    assert "no free PHD2 instance" in notes[0]


def make_observatory(tmp_path: Path, guider: GuiderKind, port: int = 4401) -> SimulatedObservatory:
    """Build an observatory object for a world folder (nothing starts).

    Returns
    -------
    observatory : `SimulatedObservatory`
        With a settings file that gives the guide optics.
    """
    result = SimulatedObservatory(
        tmp_path / "world",
        SimulationKind.SIMULATED_SKY,
        4500,
        port,
        guider=guider,
        controller_settings={"aggressiveness": 0.5},
    )
    result.config_path.parent.mkdir(parents=True)
    result.config_path.write_text(
        '["Observatory.GuideScope"]\nactive_guide_scope = "Mini"\n'
        '["Observatory.GuideScope.Mini"]\nfocal_length_mm = "240"\n'
        '["Observatory.Camera"]\ndefault_secondary_camera = "Guide"\n'
        '["Observatory.Camera.Guide"]\n"pixel_size_μm" = "2.9"\n',
        encoding="utf-8",
    )
    return result


def test_the_guide_optics_come_from_the_worlds_settings(tmp_path: Path) -> None:
    """The profile gets the active guide scope and the guide camera."""
    assert make_observatory(tmp_path, GuiderKind.PHD2)._guide_optics() == (240.0, 2.9)


def test_real_phd2_that_cannot_start_falls_back_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PHD2 that never opens its port leaves a note and no PHD2 record."""
    monkeypatch.setattr(phd2_launch, "phd2_missing_reason", lambda environ=None: "phd2 is not installed")
    world = make_observatory(tmp_path, GuiderKind.PHD2)
    assert world._start_real_phd2() == "phd2 is not installed"
    assert world.phd2 is None
    assert not list((tmp_path / "world").rglob(".PHDGuidingV2*"))


def test_the_driver_replays_no_guide_log_with_real_phd2(tmp_path: Path) -> None:
    """With the real PHD2 the exposure driver gets no guide log."""
    world = make_observatory(tmp_path, GuiderKind.PHD2)
    (tmp_path / "world" / "logs").mkdir()
    assert world._driver_guide_log() == observatory.GUIDE_LOG_FIXTURE
    world.phd2 = phd2_launch.Phd2Instance(2, 4401, "name", 4500, tmp_path)
    assert world._driver_guide_log() is None
    assert not list((tmp_path / "world").rglob("no_guide_log_replay.txt"))


def test_the_focal_length_is_a_whole_number_because_phd2_reads_an_integer() -> None:
    """121.05 mm is written as 121, or the pixel scale is unspecified."""
    text, _ = phd2_launch.profile_text(2, 4500, None, 121.05, 3.75)
    assert "\nfocalLength=121\n" in text


def test_the_calibration_step_reaches_the_distance_in_about_twelve_steps() -> None:
    """At the fitted rate the step crosses 25 pixels in about 12 steps."""
    step = phd2_launch.calibration_step_milliseconds(3.172, 6.39, 36.46)
    assert step % 50 == 0
    moved_per_step = 3.172 * step / 1000.0 / 6.39 * 0.8043  # pixels, with the declination factor
    steps_needed = 25 / moved_per_step
    assert 10 <= steps_needed <= 13
    assert steps_needed < 60


def test_the_calibration_step_follows_phd2s_formula() -> None:
    """The step is distance x scale / rate / steps / cos(dec), up to 50 ms."""
    assert phd2_launch.calibration_step_milliseconds(7.5, 6.39, 0.0) == 1800
    assert phd2_launch.calibration_step_milliseconds(7.5, 6.39, 36.46) == 2250
    with pytest.raises(ValueError):
        phd2_launch.calibration_step_milliseconds(0.0, 6.39, 0.0)


def test_the_profile_holds_the_calibration_step() -> None:
    """The profile names PHD2's calibration step key given a guide rate."""
    text, _ = phd2_launch.profile_text(2, 7624, None, 121.05, 3.75, 3.172, 36.46)
    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text.split("[profile]", 1)[1].replace("[profile]", ""))
    assert int(parser["profile/1/scope"]["CalibrationDuration"]) == phd2_launch.calibration_step_milliseconds(
        3.172, 206.265 * 3.75 / 121, 36.46
    )
    without, _ = phd2_launch.profile_text(2, 7624)
    assert "CalibrationDuration" not in without
