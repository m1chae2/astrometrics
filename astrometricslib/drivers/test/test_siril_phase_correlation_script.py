"""Purpose: Unit tests for process_target's phase-correlation script.

Description: "phase_correlation" is not a Siril star-detection setting --
it tells `process_target` to skip Siril's own registration entirely and
align the calibrated frames in Python instead (see
`spectral_frame_alignment`). This checks that the calibration script sent
to Siril for that mode omits the register/stack commands other modes
send, and converts to loose per-frame FITS files (not a packed fitseq
file) so the alignment step can read them back afterward.

Reuses the same Siril-stubbing convention as
`test_siril_frame_filter_placement.py`.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.drivers import siril_interface


@pytest.fixture
def captured_calibration_script(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Run process_target in phase_correlation mode with Siril stubbed out.

    Returns
    -------
    run_process_target : `callable`
        Returns the list of commands sent to the first (calibration)
        Siril process. Alignment finds no real calibrated frames on disk
        in this stubbed setup, so `process_target` returns `None` after
        sending exactly this one script -- which is all this test needs
        to check.
    """
    sent_scripts: list[list[str]] = []

    def fake_send_commands(self, command_pipe, commands, job_logger=None, status_queue=None, process=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        sent_scripts.append(list(commands))

    def fake_build_directories(self, id, image_files, camera_filter=None, job_logger=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target_folder = tmp_path / "work" / id
        for subdirectory in ("biases", "darks", "flats", "lights", "process"):
            (target_folder / subdirectory).mkdir(parents=True, exist_ok=True)
        for frame_index in range(20):
            (target_folder / "lights" / f"light_source_{frame_index:05d}.fits").touch()
        (target_folder / "darks" / "dark_00000.fits").touch()
        return str(target_folder)

    monkeypatch.setattr(siril_interface.ImageProcessing, "build_directories", fake_build_directories)
    monkeypatch.setattr(siril_interface.ImageProcessing, "send_commands", fake_send_commands)
    monkeypatch.setattr(
        siril_interface.ImageProcessing, "create_named_pipes", lambda self, base: ("cmd", "out")
    )
    monkeypatch.setattr(
        siril_interface.ImageProcessing, "run_siril_headless", lambda self, *a, **k: MagicMock()
    )
    monkeypatch.setattr(siril_interface.ImageProcessing, "read_output", lambda self, *a, **k: None)
    monkeypatch.setattr(siril_interface.ImageProcessing, "_kill_process_tree", lambda self, *a, **k: None)
    monkeypatch.setattr(siril_interface.ImageProcessing, "cleanup_subprocesses", lambda self: None)
    monkeypatch.setattr(
        siril_interface.ImageProcessing, "restore_cached_calibration_masters", lambda self, *a, **k: set()
    )
    monkeypatch.setattr(siril_interface, "_frames_use_color_filter_array", lambda path: False)
    monkeypatch.setattr(siril_interface, "siril_process_lock", lambda **kwargs: _NullContext())

    def run_process_target() -> list[str]:
        mock_config = MagicMock()
        mock_config.get_siril_executable.return_value = "siril"
        mock_config.get_logs_path.return_value = str(tmp_path)
        mock_config.get_frames_path.return_value = str(tmp_path / "frames")
        mock_config.get_stack_rejection_sigma_mode.return_value = "fixed"
        mock_config.get_stack_rejection_sigma.return_value = (3.0, 3.0)
        mock_config.get_stack_weight.return_value = "wfwhm"
        mock_config.get_stack_generate_rejmap.return_value = False
        mock_config.get_auto_open_siril_gui.return_value = False
        mock_config.get_stack_filter_wfwhm_percentile.return_value = None
        mock_config.get_stack_filter_round_percentile.return_value = None

        driver = siril_interface.ImageProcessing(mock_config, MagicMock())
        driver.workdir = str(tmp_path / "work")
        result = driver.process_target(
            id="PhaseCorrelation",
            image_files=[{"path": "a.fits", "camera": "Cam"}],
            is_spectral=True,
            spectral_star_detection="phase_correlation",
        )
        assert result is None, "no real calibrated frames exist in this stubbed setup"
        assert len(sent_scripts) == 1, "alignment should find nothing and stop before a second Siril run"
        return sent_scripts[0]

    return run_process_target


class _NullContext:
    """A context manager that does nothing, standing in for the Siril lock."""

    def __enter__(self):  # ruff: ignore[missing-return-type-special-method]
        """Enter the no-op context.

        Returns
        -------
        `_NullContext`
            The context instance itself.
        """
        return self

    def __exit__(self, *exception_details):  # ruff: ignore[missing-return-type-special-method,missing-type-args]
        """Leave the no-op context without suppressing anything.

        Returns
        -------
        `bool`
            False so that any exceptions are propagated.
        """
        return False


def test_phase_correlation_converts_to_loose_files_not_a_fitseq(captured_calibration_script: Any) -> None:
    """The alignment step needs individual files, not one packed sequence."""
    commands = captured_calibration_script()

    convert_commands = [command for command in commands if command.startswith("convert light_source")]
    assert len(convert_commands) == 1
    assert "-fitseq" not in convert_commands[0]


def test_phase_correlation_sends_no_register_or_stack_command(captured_calibration_script: Any) -> None:
    """Siril's own registration and stacking are not part of this run."""
    commands = captured_calibration_script()

    assert not [command for command in commands if command.startswith("register")]
    assert not [command for command in commands if command.startswith("stack")]
    assert not [command for command in commands if command.startswith("setfindstar")]


def test_phase_correlation_still_calibrates_the_lights(captured_calibration_script: Any) -> None:
    """Dark/bias/flat calibration still happens before alignment."""
    commands = captured_calibration_script()

    calibrate_commands = [command for command in commands if command.startswith("calibrate light_source")]
    assert len(calibrate_commands) == 1
