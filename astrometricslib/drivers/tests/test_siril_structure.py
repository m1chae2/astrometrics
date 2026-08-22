"""Unit tests for SirilInterface's directory-structure building logic."""

import gc
import logging
from unittest.mock import MagicMock

from astrometricslib.drivers import siril_interface


class MockLogger:
    """No-op stand-in for the standard logger interface."""

    def info(self, msg):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Discard an info-level log message."""
        pass

    def warning(self, msg):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Discard a warning-level log message."""
        pass

    def error(self, msg):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Discard an error-level log message."""
        pass


def test_build_directories_structure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify that build_directories correctly parses both legacy (5-level).

    and new (6-level with Date) image file structures.
    """
    from unittest.mock import MagicMock

    mock_config = MagicMock()
    mock_config.get_siril_executable.return_value = "siril"
    mock_library = MagicMock()

    driver = siril_interface.ImageProcessing(mock_config, mock_library)
    # Mock makedirs/rmtree/symlink to avoid filesystem touches
    # We just want to check if it iterates and finds files

    import os
    import shutil

    # Capture what would be symlinked
    symlinked_files = []

    def mock_symlink(src, dst):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        symlinked_files.append(src)

    original_symlink = os.symlink
    original_makedirs = os.makedirs
    original_rmtree = shutil.rmtree
    original_exists = os.path.exists

    os.symlink = mock_symlink
    os.makedirs = lambda x, exist_ok=False: None
    shutil.rmtree = lambda x: None
    os.path.exists = lambda x: False  # Simulate empty target dir

    try:
        # Legacy Structure (5-level)
        legacy_files = {"Telescope": {"Camera": {"800": {"1.0": {"None": ["/path/to/legacy_file.fits"]}}}}}

        symlinked_files = []
        driver.build_directories("test_legacy", legacy_files, camera_filter="Camera", job_logger=MockLogger())
        assert "/path/to/legacy_file.fits" in symlinked_files

        # New Structure (6-level with Date)
        new_files = {
            "Telescope": {
                "Camera": {
                    "800": {
                        "1.0": {
                            "None": {
                                "2023-10-14": ["/path/to/new_file_1.fits"],
                                "2023-10-15": ["/path/to/new_file_2.fits"],
                            }
                        }
                    }
                }
            }
        }

        symlinked_files = []
        driver.build_directories("test_new", new_files, camera_filter="Camera", job_logger=MockLogger())
        assert "/path/to/new_file_1.fits" in symlinked_files
        assert "/path/to/new_file_2.fits" in symlinked_files

    finally:
        os.symlink = original_symlink
        os.makedirs = original_makedirs
        shutil.rmtree = original_rmtree
        os.path.exists = original_exists


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


def test_process_target_closes_logger_handler_on_completion(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify process_target closes its FileHandler even on failure."""
    mock_config = MagicMock()
    mock_config.get_siril_executable.return_value = "siril"
    mock_config.get_logs_path.return_value = str(tmp_path)
    mock_config.get_stack_rejection_sigma.return_value = (3.0, 3.0)
    mock_config.get_stack_filter_wfwhm_percentile.return_value = None
    mock_config.get_stack_filter_round_percentile.return_value = None
    mock_config.get_stack_weight.return_value = "wfwhm"
    mock_config.get_stack_generate_rejmap.return_value = True
    # Force an early failure inside process_target's try block so we
    # reach `finally` quickly
    mock_config.get_frames_path.side_effect = RuntimeError("boom")
    mock_library = MagicMock()

    driver = siril_interface.ImageProcessing(mock_config, mock_library)
    result = driver.process_target(id="TestLeakTarget", image_files=[])

    assert result is None
    job_logger = logging.getLogger("siril_TestLeakTarget")
    assert job_logger.handlers == []


if __name__ == "__main__":
    test_build_directories_structure()
