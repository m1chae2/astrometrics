"""Integration test: process_target must not hang if Siril dies at launch.

This is the production shape of the bug `_open_pipe_or_die` fixes:
`run_siril_headless` launches Siril and returns immediately (its own 2s
grace sleep aside); if the launched process crashes before opening
either FIFO -- a bad sandbox, a Siril version too old for `-p/-r/-w`, a
wrong path that still happens to exist as *something* executable -- the
open() calls inside `send_commands` and `read_output` used to block
forever with no way for `process_target`'s caller to ever get control
back. Here `run_siril_headless` is replaced with a real subprocess
(`true`) that exits immediately without touching either pipe, and
`create_named_pipes` is left real, so `send_commands`/`read_output` run
unstubbed against real FIFOs -- only `run_siril_headless` itself and the
calibration/logging scaffolding around it are faked.
"""

import logging
import subprocess
import time
from unittest.mock import MagicMock

import pytest

from astrometricslib.drivers import siril_interface


class _NullContext:
    """A context manager that does nothing, standing in for the Siril lock."""

    def __enter__(self):  # ruff: ignore[missing-return-type-special-method]
        return self

    def __exit__(self, *exception_details: object) -> bool:
        return False


def test_process_target_does_not_hang_when_siril_dies_at_launch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Siril that exits before opening its pipes fails fast, not forever."""

    def fake_build_directories(self, id, image_files, camera_filter=None, job_logger=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target_folder = tmp_path / "work" / id
        for subdirectory in ("biases", "darks", "flats", "lights", "process"):
            (target_folder / subdirectory).mkdir(parents=True, exist_ok=True)
        (target_folder / "lights" / "light_00000.fits").touch()
        return str(target_folder)

    def fake_run_siril_headless(self, command_pipe, output_pipe, **kwargs: object):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        # Stands in for a Siril that crashes immediately: a real process
        # that exits right away, having opened neither FIFO.
        process = subprocess.Popen(["true"])
        self.subprocesses.append(process)
        process.wait()
        return process

    monkeypatch.setattr(siril_interface.ImageProcessing, "build_directories", fake_build_directories)
    monkeypatch.setattr(siril_interface.ImageProcessing, "run_siril_headless", fake_run_siril_headless)
    monkeypatch.setattr(siril_interface.ImageProcessing, "_kill_process_tree", lambda self, *a, **k: None)
    monkeypatch.setattr(siril_interface.ImageProcessing, "cleanup_subprocesses", lambda self: None)
    monkeypatch.setattr(
        siril_interface.ImageProcessing, "restore_cached_calibration_masters", lambda self, *a, **k: set()
    )
    monkeypatch.setattr(siril_interface, "_frames_use_color_filter_array", lambda path: False)
    monkeypatch.setattr(siril_interface, "siril_process_lock", lambda **kwargs: _NullContext())
    # This test's whole point is bounding SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS's
    # wait -- keep it short so a regression fails the test in seconds, not
    # by tying up the suite for the full 30s production default.
    monkeypatch.setattr(siril_interface, "SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS", 3)

    mock_config = MagicMock()
    mock_config.get_siril_executable.return_value = "siril"
    mock_config.get_logs_path.return_value = str(tmp_path)
    mock_config.get_frames_path.return_value = str(tmp_path / "frames")
    mock_config.get_stack_rejection_sigma_mode.return_value = "fixed"
    mock_config.get_stack_rejection_sigma.return_value = (3.0, 3.0)
    mock_config.get_stack_weight.return_value = None
    mock_config.get_stack_generate_rejmap.return_value = False
    mock_config.get_auto_open_siril_gui.return_value = False
    mock_config.get_stack_filter_wfwhm_percentile.return_value = None
    mock_config.get_stack_filter_round_percentile.return_value = None

    driver = siril_interface.ImageProcessing(mock_config, MagicMock())
    driver.workdir = str(tmp_path / "work")

    started_at = time.monotonic()
    result = driver.process_target(
        id="DeadSirilTarget",
        image_files=[{"path": "a.fits", "camera": "Cam"}],
    )
    elapsed = time.monotonic() - started_at
    logging.getLogger("siril_DeadSirilTarget").handlers.clear()

    assert result is None
    # Bounded by the (shortened) connect timeout plus a small margin for
    # the two open() attempts and process_target's own overhead -- not
    # by hanging until the test runner kills it.
    assert elapsed < 15, f"process_target took {elapsed:.1f}s -- looks hung, not bounded"
