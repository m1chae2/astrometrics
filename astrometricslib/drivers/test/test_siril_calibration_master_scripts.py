"""Purpose: Unit tests for how the Siril script builds masters and lone lights.

Description: A master bias, dark or flat built from one or two frames is weak,
and a lone flat or light used to skip a calibration step altogether. These
tests read the text of the Siril commands that the script generator writes for
1, 2 and 3 frames of each kind. They check that a lone flat has the bias
removed, that a master from fewer frames than the minimum is flagged as a
blocking calibration flag, and that a stack of one light is still calibrated.
Siril is never run: the tests only read the generated command lists, and the
end-to-end cases stub out Siril's launch and capture the script that
`process_target` would have sent.
"""

import io
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.drivers import siril_interface
from astrometricslib.drivers.siril_interface import (
    CALIBRATION_BLOCKING_FLAGS_KEY,
    build_bias_master_commands,
    build_dark_master_commands,
    build_flat_master_commands,
    build_single_light_commands,
    calibration_count_flags,
)
from astrometricslib.foundation.config import AppConfiguration

MINIMUM_FRAMES = 3


class _NullContext:
    """A context manager that does nothing, standing in for the Siril lock."""

    def __enter__(self) -> _NullContext:
        """Enter the no-op context.

        Returns
        -------
        context : `_NullContext`
            This object.
        """
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Leave the no-op context without suppressing errors.

        Parameters
        ----------
        *exc_info : `object`
            The exception details, which are ignored.
        """


# --------------------------------------------------------------- flats


def test_a_lone_flat_with_a_bias_is_calibrated_with_the_bias() -> None:
    """One flat still holds the bias pedestal, so the bias master comes off."""
    commands = build_flat_master_commands(1, True, "", None)

    calibrate_lines = [line for line in commands if line.startswith("calibrate")]
    assert len(calibrate_lines) == 1
    assert "-bias=bias_stacked" in calibrate_lines[0]
    # The calibrated copy, not the raw flat, is what is saved as the master.
    assert commands.index(calibrate_lines[0]) < commands.index("load pp_flat_00001.fits")
    assert "load flat_00001.fits" not in commands
    assert commands[-1] == "save flat_stacked"


def test_a_lone_flat_keeps_the_colour_sensor_options_when_calibrated() -> None:
    """A lone flat gets the same colour-sensor flags as several flats."""
    commands = build_flat_master_commands(1, True, " -cfa -equalize_cfa", None)

    assert "calibrate_single flat_00001.fits -bias=bias_stacked -cfa -equalize_cfa" in commands


def test_a_lone_flat_without_a_bias_has_nothing_to_subtract() -> None:
    """With no bias master the lone flat is loaded and saved as it is."""
    commands = build_flat_master_commands(1, False, "", None)

    assert not any(line.startswith("calibrate") for line in commands)
    assert "load flat_00001.fits" in commands


@pytest.mark.parametrize("num_flats", [2, 3])
def test_several_flats_are_calibrated_with_the_bias_and_stacked(num_flats: int) -> None:
    """Two or more flats become a sequence, are calibrated, then stacked."""
    commands = build_flat_master_commands(num_flats, True, "", None)

    assert commands[0] == "convert flat -out=../process -fitseq"
    assert "calibrate flat -bias=bias_stacked" in commands
    assert "stack pp_flat rej 3 3 -norm=mul -out=flat_stacked" in commands


# -------------------------------------------------------- biases and darks


@pytest.mark.parametrize(
    ("builder", "kind"),
    [(build_bias_master_commands, "bias"), (build_dark_master_commands, "dark")],
)
def test_a_lone_bias_or_dark_is_loaded_and_saved_without_a_stack(
    builder: Callable[[int], list[str]], kind: str
) -> None:
    """One frame has nothing to reject against, so it is the master."""
    commands = builder(1)

    assert f"load {kind}_00001.fits" in commands
    assert f"save {kind}_stacked" in commands
    assert not any(line.startswith("stack") for line in commands)


@pytest.mark.parametrize(
    ("builder", "kind"),
    [(build_bias_master_commands, "bias"), (build_dark_master_commands, "dark")],
)
@pytest.mark.parametrize("num_frames", [2, 3])
def test_several_biases_or_darks_are_stacked_with_rejection(
    builder: Callable[[int], list[str]], kind: str, num_frames: int
) -> None:
    """Two or more frames are stacked with sigma rejection, unnormalised."""
    commands = builder(num_frames)

    assert f"convert {kind} -out=../process -fitseq" in commands
    assert f"stack {kind} rej 3 3 -nonorm -out={kind}_stacked" in commands


# ----------------------------------------------------------- the lights


@pytest.mark.parametrize(
    ("dark_flag", "flat_flag", "bias_flag", "expected_options"),
    [
        ("-dark=dark_stacked", "-flat=flat_stacked", "", "-dark=dark_stacked -flat=flat_stacked"),
        ("", "-flat=flat_stacked", "-bias=bias_stacked", "-flat=flat_stacked -bias=bias_stacked"),
        ("-dark=dark_stacked", "", "", "-dark=dark_stacked"),
    ],
)
def test_a_lone_light_is_calibrated_before_it_is_saved(
    dark_flag: str, flat_flag: str, bias_flag: str, expected_options: str
) -> None:
    """One light gets its masters applied, and the calibrated copy is saved."""
    commands = build_single_light_commands(dark_flag, flat_flag, bias_flag, "", "")

    assert f"calibrate_single light_source_00001.fits {expected_options}" in commands
    assert commands.index("load pp_light_source_00001.fits") < commands.index("save result_stacked")
    assert "load light_source_00001.fits" not in commands


def test_a_lone_colour_light_is_debayered_when_calibrated() -> None:
    """A colour-sensor light gets the colour options a sequence light gets."""
    commands = build_single_light_commands("-dark=dark_stacked", "", "", " -cfa -equalize_cfa", " -debayer")

    assert (
        "calibrate_single light_source_00001.fits -dark=dark_stacked -cfa -equalize_cfa -debayer" in commands
    )


def test_a_lone_light_with_no_masters_is_saved_as_it_is() -> None:
    """With nothing to apply, the light is loaded and saved as it is."""
    commands = build_single_light_commands("", "", "", "", "")

    assert not any(line.startswith("calibrate") for line in commands)
    assert commands[-2:] == ["load light_source_00001.fits", "save result_stacked"]


# ------------------------------------------------- minimum frame counts


@pytest.mark.parametrize("kind", ["bias", "dark", "flat"])
@pytest.mark.parametrize("num_frames", [1, 2])
def test_a_master_from_fewer_frames_than_the_minimum_is_flagged(kind: str, num_frames: int) -> None:
    """One or two frames is below the default minimum, and the flag says so."""
    counts = {"num_biases": 0, "num_darks": 0, "num_flats": 0}
    counts[{"bias": "num_biases", "dark": "num_darks", "flat": "num_flats"}[kind]] = num_frames

    flags = calibration_count_flags(**counts, minimum_frames=MINIMUM_FRAMES)

    assert len(flags) == 1
    assert flags[0].startswith(f"{kind} master built from {num_frames} frame(s)")
    assert f"minimum of {MINIMUM_FRAMES}" in flags[0]


def test_masters_with_the_minimum_number_of_frames_are_not_flagged() -> None:
    """Three frames of each kind meets the minimum."""
    assert calibration_count_flags(3, 3, 3, MINIMUM_FRAMES) == []


def test_a_kind_with_no_frames_is_not_flagged_because_no_master_is_built() -> None:
    """A missing master is another problem; the flag covers only short ones."""
    assert calibration_count_flags(0, 0, 0, MINIMUM_FRAMES) == []


def test_every_short_master_gets_its_own_flag_in_a_fixed_order() -> None:
    """The flags list bias, dark, then flat."""
    flags = calibration_count_flags(1, 2, 1, MINIMUM_FRAMES)

    assert [flag.split(" master")[0] for flag in flags] == ["bias", "dark", "flat"]


def test_a_lower_minimum_accepts_the_same_counts() -> None:
    """With a minimum of 1, a single frame is enough."""
    assert calibration_count_flags(1, 1, 1, 1) == []


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("3", 3), ("5", 5), (" 2 ", 2), ("0", 1), ("-4", 1), ("many", 3), ("", 3), (None, 3)],
)
def test_the_minimum_comes_from_the_configuration(configured: str | None, expected: int) -> None:
    """The setting is a whole number of at least 1; a bad entry gives 3."""
    configuration = SimpleNamespace(get_value=lambda section, key, fallback=None: configured)

    assert AppConfiguration.get_minimum_calibration_frames(configuration) == expected  # type: ignore[arg-type]


def test_the_minimum_defaults_to_three_when_the_entry_is_absent() -> None:
    """With no entry at all, the fallback in the getter is 3."""
    configuration = SimpleNamespace(get_value=lambda section, key, fallback=None: fallback)

    assert AppConfiguration.get_minimum_calibration_frames(configuration) == 3  # type: ignore[arg-type]


# ------------------------------------- the whole script from process_target


@pytest.fixture
def run_with_staged_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Any]:
    """Run process_target with Siril stubbed out and chosen frame counts.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's temporary folder, used as the work directory.
    monkeypatch : `pytest.MonkeyPatch`
        Replaces Siril's launch, pipes and reading with stand-ins.

    Returns
    -------
    run : `callable`
        Called with the number of biases, darks, flats and lights to stage.
        It returns the script that `process_target` would have sent and the
        run's diagnostics dictionary.
    """
    sent_commands: list[str] = []

    def fake_send_commands(self: Any, command_pipe: Any, commands: Any, **kwargs: Any) -> None:
        """Keep the commands instead of sending them to Siril.

        Parameters
        ----------
        self : `Any`
            The processor, unused.
        command_pipe : `Any`
            The command pipe, unused.
        commands : `Any`
            The commands that would have been sent.
        **kwargs : `Any`
            The remaining arguments, unused.
        """
        sent_commands.clear()
        sent_commands.extend(commands)

    staged_counts: dict[str, int] = {}

    def fake_build_directories(self: Any, directory_name: str, image_files: Any, **kwargs: Any) -> str:
        """Create the staging folders with the chosen number of empty frames.

        Parameters
        ----------
        self : `Any`
            The processor, unused.
        directory_name : `str`
            The name of the target's work folder.
        image_files : `Any`
            The frames submitted, unused.
        **kwargs : `Any`
            The remaining arguments, unused.

        Returns
        -------
        target_folder : `str`
            The staging folder.
        """
        target_folder = tmp_path / "work" / directory_name
        for subdirectory in ("biases", "darks", "flats", "lights", "process"):
            (target_folder / subdirectory).mkdir(parents=True, exist_ok=True)
        for subdirectory, count in staged_counts.items():
            for index in range(count):
                (target_folder / subdirectory / f"frame_{index:05d}.fits").touch()
        return str(target_folder)

    monkeypatch.setattr(siril_interface.ImageProcessing, "build_directories", fake_build_directories)
    monkeypatch.setattr(siril_interface.ImageProcessing, "send_commands", fake_send_commands)
    monkeypatch.setattr(
        siril_interface.ImageProcessing, "create_named_pipes", lambda self, base: ("cmd", "out")
    )
    monkeypatch.setattr(
        siril_interface.ImageProcessing,
        "run_siril_headless",
        lambda self, *a, **k: MagicMock(stdout=io.BytesIO()),
    )
    monkeypatch.setattr(siril_interface.ImageProcessing, "read_output", lambda self, *a, **k: None)
    monkeypatch.setattr(siril_interface.ImageProcessing, "_kill_process_tree", lambda self, *a, **k: None)
    monkeypatch.setattr(siril_interface.ImageProcessing, "cleanup_subprocesses", lambda self: None)
    monkeypatch.setattr(
        siril_interface.ImageProcessing, "restore_cached_calibration_masters", lambda self, *a, **k: set()
    )
    monkeypatch.setattr(
        siril_interface.ImageProcessing,
        "assess_staged_flats",
        lambda self, *a, **k: MagicMock(smoothing_sigma_pixels=None, as_diagnostics=lambda: {}),
    )
    monkeypatch.setattr(siril_interface, "_frames_use_color_filter_array", lambda path: False)
    monkeypatch.setattr(siril_interface, "siril_process_lock", lambda **kwargs: _NullContext())

    def run(
        num_biases: int,
        num_darks: int,
        num_flats: int,
        num_lights: int,
        rejection_mode: str = "fixed",
        rejection_sigma: tuple[float, float] | None = None,
    ) -> tuple[list[str], dict]:
        """Stage the frames, run `process_target`, and return what it built.

        Parameters
        ----------
        num_biases, num_darks, num_flats, num_lights : `int`
            How many frames of each kind to stage.
        rejection_mode : `str`, optional
            The configured rejection mode, ``"fixed"`` or ``"adaptive"``.
        rejection_sigma : `tuple` [`float`, `float`], optional
            An explicit (low, high) pair passed to `process_target`.

        Returns
        -------
        script : `list` [`str`]
            The Siril commands `process_target` would have sent.
        diagnostics : `dict`
            The run's `last_run_diagnostics`.
        """
        staged_counts.update(biases=num_biases, darks=num_darks, flats=num_flats, lights=num_lights)
        mock_config = MagicMock()
        mock_config.get_siril_executable.return_value = "siril"
        mock_config.get_logs_path.return_value = str(tmp_path)
        mock_config.get_frames_path.return_value = str(tmp_path / "frames")
        mock_config.get_stacks_path.return_value = str(tmp_path / "frames")
        mock_config.get_stack_rejection_sigma_mode.return_value = rejection_mode
        mock_config.get_stack_rejection_sigma.return_value = (3.0, 3.0)
        mock_config.get_stack_rejection_sigma_floor.return_value = 2.5
        mock_config.get_stack_rejection_low_extra_sigma.return_value = 0.5
        mock_config.get_stack_weight.return_value = None
        mock_config.get_stack_generate_rejmap.return_value = False
        mock_config.get_auto_open_siril_gui.return_value = False
        mock_config.get_stack_filter_wfwhm_percentile.return_value = None
        mock_config.get_stack_filter_round_percentile.return_value = None
        mock_config.get_minimum_calibration_frames.return_value = MINIMUM_FRAMES

        driver = siril_interface.ImageProcessing(mock_config, MagicMock())
        driver.workdir = str(tmp_path / "work")
        driver.process_target(
            id="MasterScripts",
            image_files=[{"path": "a.fits", "camera": "Cam"}],
            is_spectral=False,
            rejection_sigma=rejection_sigma,
        )
        logging.getLogger("siril_MasterScripts").handlers.clear()
        return list(sent_commands), driver.last_run_diagnostics

    return run


def test_a_run_with_one_flat_and_a_bias_calibrates_the_flat(
    run_with_staged_frames: Callable[..., Any],
) -> None:
    """A one-flat run calibrates the flat with the bias before the lights."""
    script, _ = run_with_staged_frames(num_biases=5, num_darks=5, num_flats=1, num_lights=20)

    flat_line = next(line for line in script if line.startswith("calibrate_single flat_"))
    assert "-bias=bias_stacked" in flat_line
    assert script.index(flat_line) < script.index("convert light_source -out=../process -fitseq")


@pytest.mark.parametrize("num_flats", [1, 2])
def test_a_run_with_too_few_flats_records_a_blocking_flag(
    run_with_staged_frames: Callable[..., Any], num_flats: int
) -> None:
    """The run still builds the master and records the blocking flag."""
    script, diagnostics = run_with_staged_frames(
        num_biases=5, num_darks=5, num_flats=num_flats, num_lights=20
    )

    assert any("flat_stacked" in line for line in script)
    assert len(diagnostics[CALIBRATION_BLOCKING_FLAGS_KEY]) == 1
    assert diagnostics[CALIBRATION_BLOCKING_FLAGS_KEY][0].startswith(
        f"flat master built from {num_flats} frame(s)"
    )


def test_a_run_with_enough_frames_of_each_kind_records_no_blocking_flag(
    run_with_staged_frames: Callable[..., Any],
) -> None:
    """Three of each calibration kind meets the minimum."""
    _, diagnostics = run_with_staged_frames(num_biases=3, num_darks=3, num_flats=3, num_lights=20)

    assert diagnostics[CALIBRATION_BLOCKING_FLAGS_KEY] == []


def test_a_run_with_one_light_calibrates_it(run_with_staged_frames: Callable[..., Any]) -> None:
    """A one-light run applies the dark and flat and records that it did."""
    script, diagnostics = run_with_staged_frames(num_biases=0, num_darks=5, num_flats=5, num_lights=1)

    light_line = next(line for line in script if line.startswith("calibrate_single light_source_"))
    assert "-dark=dark_stacked" in light_line
    assert "-flat=flat_stacked" in light_line
    assert script.index("load pp_light_source_00001.fits") < script.index("save result_stacked")
    assert diagnostics["calibration_applied"] == {"dark": True, "flat": True, "bias": False}


def test_a_run_with_one_light_and_no_masters_records_no_calibration(
    run_with_staged_frames: Callable[..., Any],
) -> None:
    """With no master to apply, the lone light is saved as it is."""
    script, diagnostics = run_with_staged_frames(num_biases=0, num_darks=0, num_flats=0, num_lights=1)

    assert not any(line.startswith("calibrate") for line in script)
    assert diagnostics["calibration_applied"] == {"dark": False, "flat": False, "bias": False}


# ------------------------------------------------------- rejection limits


def _rejection_line(script: list[str]) -> str:
    """Return the `stack` command that combines the lights.

    Parameters
    ----------
    script : `list` [`str`]
        The Siril commands `process_target` would have sent.

    Returns
    -------
    line : `str`
        The stack command that writes ``result_stacked``, with its ``rej``
        limits.
    """
    return next(line for line in script if line.startswith("stack ") and "-out=result_stacked" in line)


def test_a_five_frame_adaptive_stack_rejects_at_the_floor_with_a_looser_low_bound(
    run_with_staged_frames: Callable[..., Any],
) -> None:
    """Five lights give `rej 3.0000 2.5000` and record the floor."""
    script, diagnostics = run_with_staged_frames(
        num_biases=3, num_darks=3, num_flats=3, num_lights=5, rejection_mode="adaptive"
    )

    assert " rej 3.0000 2.5000 " in _rejection_line(script)
    assert diagnostics["rejection_sigma_low"] == pytest.approx(3.0)
    assert diagnostics["rejection_sigma_high"] == pytest.approx(2.5)
    assert diagnostics["rejection_sigma_mode"] == "adaptive"
    assert diagnostics["rejection_sigma_floor"] == pytest.approx(2.5)
    assert diagnostics["rejection_sigma_low_extra"] == pytest.approx(0.5)
    assert diagnostics["rejection_sigma_floor_applied"] is True


def test_a_large_adaptive_stack_keeps_its_own_limit_above_the_floor(
    run_with_staged_frames: Callable[..., Any],
) -> None:
    """Seventy lights keep the Chauvenet limit 2.6901 on the high side."""
    script, diagnostics = run_with_staged_frames(
        num_biases=3, num_darks=3, num_flats=3, num_lights=70, rejection_mode="adaptive"
    )

    assert " rej 3.1901 2.6901 " in _rejection_line(script)
    assert diagnostics["rejection_sigma_floor_applied"] is False


def test_an_explicit_rejection_override_is_passed_through_unchanged(
    run_with_staged_frames: Callable[..., Any],
) -> None:
    """A caller's (low, high) pair skips the floor and the extra."""
    script, diagnostics = run_with_staged_frames(
        num_biases=3,
        num_darks=3,
        num_flats=3,
        num_lights=5,
        rejection_mode="adaptive",
        rejection_sigma=(2.0, 2.2),
    )

    assert " rej 2.0000 2.2000 " in _rejection_line(script)
    assert diagnostics["rejection_sigma_mode"] == "override"
    assert diagnostics["rejection_sigma_floor"] is None
    assert diagnostics["rejection_sigma_floor_applied"] is False


def test_fixed_mode_uses_the_configured_pair_without_a_floor(
    run_with_staged_frames: Callable[..., Any],
) -> None:
    """Fixed mode sends the configured pair as it is."""
    script, diagnostics = run_with_staged_frames(
        num_biases=3, num_darks=3, num_flats=3, num_lights=5, rejection_mode="fixed"
    )

    assert " rej 3.0000 3.0000 " in _rejection_line(script)
    assert diagnostics["rejection_sigma_floor_applied"] is False


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("2.5", 2.5),
        ("3", 3.0),
        (" 2.0 ", 2.0),
        ("0", 2.5),
        ("-1", 2.5),
        ("nan", 2.5),
        ("tight", 2.5),
        (None, 2.5),
    ],
)
def test_the_floor_comes_from_the_configuration(configured: str | None, expected: float) -> None:
    """The floor is a number above zero; a bad entry gives 2.5."""
    configuration = SimpleNamespace(get_value=lambda section, key, fallback=None: configured)

    assert AppConfiguration.get_stack_rejection_sigma_floor(configuration) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("0.5", 0.5), ("0", 0.0), ("1", 1.0), ("-2", 0.0), ("inf", 0.5), ("wide", 0.5), (None, 0.5)],
)
def test_the_low_extra_comes_from_the_configuration(configured: str | None, expected: float) -> None:
    """The extra is a number of at least 0; a bad entry gives 0.5."""
    configuration = SimpleNamespace(get_value=lambda section, key, fallback=None: configured)

    assert AppConfiguration.get_stack_rejection_low_extra_sigma(configuration) == expected  # type: ignore[arg-type]


def test_the_floor_and_extra_default_when_the_entries_are_absent() -> None:
    """With no entry at all, the fallbacks in the getters are 2.5 and 0.5."""
    configuration = SimpleNamespace(get_value=lambda section, key, fallback=None: fallback)

    floor = AppConfiguration.get_stack_rejection_sigma_floor(configuration)  # type: ignore[arg-type]
    extra = AppConfiguration.get_stack_rejection_low_extra_sigma(configuration)  # type: ignore[arg-type]
    assert floor == pytest.approx(2.5)
    assert extra == pytest.approx(0.5)
