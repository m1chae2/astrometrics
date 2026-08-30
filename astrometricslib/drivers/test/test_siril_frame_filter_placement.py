"""Purpose: Unit tests for where process_target hangs its frame filters.

Description: Siril accepts the same ``-filter-wfwhm``/``-filter-round``
options on both ``seqapplyreg`` and ``stack``, selecting on the same
per-frame FWHM and roundness that ``register -2pass`` measured. Placing
them on ``seqapplyreg`` picks the same frames but skips interpolating
and writing the ones it drops, so the standard imaging path puts them
there. The spectral path uses single-pass ``register``, which applies
transforms as it goes and has no separate filtering step, so its filters
stay on ``stack``.

The failure these guard against is passing the filters twice: a 90%
setting applied at both stages compounds to 81% of the input frames,
silently discarding frames the operator asked to keep. That is invisible
in the output -- the stack simply has fewer contributing frames -- so it
needs a test on the generated command script rather than on any result.

Drives `process_target` with Siril's launch, pipe, and read steps
stubbed out, capturing the command script that would have been sent.
"""

import logging
from unittest.mock import MagicMock

import pytest

from astrometricslib.drivers import siril_interface


@pytest.fixture
def captured_siril_script(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Run process_target with Siril stubbed, returning the script it built.

    Returns
    -------
    run_process_target : `callable`
        Called with ``is_spectral``, ``filter_wfwhm``, and
        ``filter_round``; returns the list of Siril commands
        `process_target` would have sent.
    """
    sent_commands: list[str] = []

    def fake_send_commands(self, command_pipe, commands, job_logger=None, status_queue=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        sent_commands.clear()
        sent_commands.extend(commands)

    def fake_build_directories(self, id, image_files, camera_filter=None, job_logger=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target_folder = tmp_path / "work" / id
        for subdirectory in ("biases", "darks", "flats", "lights", "process"):
            (target_folder / subdirectory).mkdir(parents=True, exist_ok=True)
        # Twenty lights so process_target takes its multi-frame branch
        # (the one that registers and stacks) and, more importantly, so
        # a 90% filter still leaves more than
        # stack_filter_floor.DEFAULT_MINIMUM_SURVIVING_FRAMES standing.
        # Below that floor the filter is loosened away entirely and
        # these tests would be asserting against an unfiltered run.
        for frame_index in range(20):
            (target_folder / "lights" / f"light_source_{frame_index:05d}.fits").touch()
        # One dark, so the lights are calibrated and the registered
        # sequence is named for the calibrated frames (pp_light_source)
        # exactly as it is in a real run.
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

    def run_process_target(is_spectral: bool, filter_wfwhm, filter_round):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        mock_config = MagicMock()
        mock_config.get_siril_executable.return_value = "siril"
        mock_config.get_logs_path.return_value = str(tmp_path)
        mock_config.get_frames_path.return_value = str(tmp_path / "frames")
        mock_config.get_stack_rejection_sigma_mode.return_value = "fixed"
        mock_config.get_stack_rejection_sigma.return_value = (3.0, 3.0)
        mock_config.get_stack_weight.return_value = "wfwhm"
        mock_config.get_stack_generate_rejmap.return_value = False
        mock_config.get_auto_open_siril_gui.return_value = False
        # process_target falls back to these when a filter argument is
        # None, so they must answer None too rather than a MagicMock.
        mock_config.get_stack_filter_wfwhm_percentile.return_value = None
        mock_config.get_stack_filter_round_percentile.return_value = None

        driver = siril_interface.ImageProcessing(mock_config, MagicMock())
        driver.workdir = str(tmp_path / "work")
        driver.process_target(
            id="FilterPlacement",
            image_files=[{"path": "a.fits", "camera": "Cam"}],
            is_spectral=is_spectral,
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
        )
        logging.getLogger("siril_FilterPlacement").handlers.clear()
        return list(sent_commands)

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


def _find_command(commands: list[str], verb: str) -> str:
    """Return the single command starting with `verb`.

    Returns
    -------
    command : `str`
        The matching command line.
    """
    matches = [command for command in commands if command.startswith(verb)]
    assert len(matches) == 1, f"expected exactly one {verb!r} command, got {matches}"
    return matches[0]


def test_standard_path_filters_on_seqapplyreg_not_stack(captured_siril_script):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The imaging path drops frames before they are ever resampled."""
    commands = captured_siril_script(is_spectral=False, filter_wfwhm="90%", filter_round="90%")

    seqapplyreg_command = _find_command(commands, "seqapplyreg")
    assert "-filter-wfwhm=90%" in seqapplyreg_command
    assert "-filter-round=90%" in seqapplyreg_command

    stack_command = _find_command(commands, "stack r_")
    assert "-filter-wfwhm" not in stack_command
    assert "-filter-round" not in stack_command


def test_spectral_path_filters_on_stack(captured_siril_script):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Single-pass registration leaves the filters on stack."""
    commands = captured_siril_script(is_spectral=True, filter_wfwhm="90%", filter_round="90%")

    assert not [command for command in commands if command.startswith("seqapplyreg")]

    stack_command = _find_command(commands, "stack r_")
    assert "-filter-wfwhm=90%" in stack_command
    assert "-filter-round=90%" in stack_command


def test_filters_are_never_applied_twice(captured_siril_script):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """No single run passes a frame filter at both stages.

    Compounding 90% at seqapplyreg with 90% at stack keeps 81% of the
    input, quietly discarding frames the operator asked to keep.
    """
    for is_spectral in (False, True):
        commands = captured_siril_script(is_spectral=is_spectral, filter_wfwhm="90%", filter_round="90%")
        for filter_flag in ("-filter-wfwhm", "-filter-round"):
            occurrences = sum(filter_flag in command for command in commands)
            assert occurrences == 1, f"{filter_flag} appears {occurrences}x (is_spectral={is_spectral})"


def test_no_filter_options_when_none_configured(captured_siril_script):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """An unfiltered run passes bare commands, not empty option strings."""
    commands = captured_siril_script(is_spectral=False, filter_wfwhm=None, filter_round=None)

    assert _find_command(commands, "seqapplyreg") == "seqapplyreg pp_light_source"
    assert "-filter-" not in _find_command(commands, "stack r_")
