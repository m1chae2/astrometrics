"""Unit tests for SirilInterface's directory-structure building logic."""

import gc
import logging
from unittest.mock import MagicMock

from astrometricslib.drivers import siril_interface


def test_image_processing_instances_do_not_pin_after_deletion():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ImageProcessing instances are only weakly held.

    So they don't leak across many targets.
    """
    mock_config = MagicMock()
    mock_config.get_siril_executable.return_value = "siril"
    mock_library = MagicMock()

    driver = siril_interface.ImageProcessing(mock_config, mock_library)
    assert driver in siril_interface._active_image_processing_instances

    del driver
    gc.collect()

    assert len(siril_interface._active_image_processing_instances) == 0


def test_process_target_closes_logger_handler_on_completion(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify process_target closes its FileHandler even on failure."""
    mock_config = MagicMock()
    mock_config.get_siril_executable.return_value = "siril"
    mock_config.get_logs_path.return_value = str(tmp_path)
    mock_config.get_stack_rejection_sigma.return_value = (3.0, 3.0)
    mock_config.get_stack_filter_wfwhm_percentile.return_value = None
    mock_config.get_stack_filter_round_percentile.return_value = None
    mock_config.get_stack_weight.return_value = "wfwhm"
    mock_config.get_stack_generate_rejmap.return_value = True
    mock_library = MagicMock()

    # Force an early failure inside process_target's try block so we
    # reach `finally` quickly. It has to be raised from build_directories
    # rather than get_frames_path: process_target catches the latter and
    # carries on to open Siril's command FIFO, whose blocking open never
    # returns when no Siril is running to open the other end.
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(siril_interface.ImageProcessing, "build_directories", explode)

    driver = siril_interface.ImageProcessing(mock_config, mock_library)
    result = driver.process_target(id="TestLeakTarget", image_files=[])

    assert result is None
    job_logger = logging.getLogger("siril_TestLeakTarget")
    assert job_logger.handlers == []
