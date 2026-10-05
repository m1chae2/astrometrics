"""Tests for the guard that keeps test settings files inside the test folders.

The guard (in the root `conftest.py`) fails any test that makes a settings
object reading a file outside the shared test folder or the test's own
folder. Without it, one test that touched the real settings file, or another
test's file, would pass or fail depending on the machine and on test order.
"""

import tempfile
from pathlib import Path

import pytest

from astrometricslib import AppConfiguration


def test_a_settings_object_over_a_file_outside_the_test_folders_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file in another folder is refused before it is read or written."""
    with tempfile.TemporaryDirectory() as outside_folder:
        outside_file = Path(outside_folder) / "astrometrics.config.toml"
        monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: outside_file)

        with pytest.raises(AssertionError, match="outside the test folders"):
            AppConfiguration()

        assert not outside_file.exists()


def test_a_settings_object_over_the_tests_own_file_is_allowed(
    config_in_tmp_path: AppConfiguration, tmp_path: Path
) -> None:
    """The per-test settings file lies inside the test's own folder."""
    assert tmp_path in Path(config_in_tmp_path.config_file_path).parents


def test_a_settings_object_over_the_shared_test_file_is_allowed(test_directories: object) -> None:
    """The shared test settings file is allowed."""
    configuration = AppConfiguration()

    assert Path(configuration.config_file_path) == test_directories.config


def test_saving_a_per_test_config_leaves_the_shared_file_alone(
    config_in_tmp_path: AppConfiguration, test_directories: object
) -> None:
    """Changing and saving settings in a test leaves the shared file alone."""
    shared_text_before = test_directories.config.read_text(encoding="utf-8")

    config_in_tmp_path.update_config({"Processing.CosmicClarity": {"denoise_strength": "0.2"}})

    assert test_directories.config.read_text(encoding="utf-8") == shared_text_before
    assert config_in_tmp_path.get_cosmic_clarity_denoise_strength() == pytest.approx(0.2)


def test_saving_a_plain_settings_object_does_not_change_the_shared_file(
    test_directories: object,
) -> None:
    """A settings object that reads the shared file saves somewhere else."""
    shared_text_before = test_directories.config.read_text(encoding="utf-8")
    configuration = AppConfiguration()

    configuration.update_config({"Processing.CosmicClarity": {"denoise_strength": "0.1"}})

    assert test_directories.config.read_text(encoding="utf-8") == shared_text_before
    assert Path(configuration.config_file_path) != test_directories.config
    assert configuration.get_cosmic_clarity_denoise_strength() == pytest.approx(0.1)
