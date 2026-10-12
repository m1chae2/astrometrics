"""Tests for the working-file folders in `foundation.paths`."""

import os
from pathlib import Path

import pytest

from astrometricslib.foundation.paths import (
    catalog_cache_directory,
    config_directory,
    data_directory,
    locks_directory,
    scratch_directory,
    siril_work_directory,
)


def test_scratch_directory_under_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scratch folder is `<cache>/astrometrics` and is made on demand."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    scratch_path = scratch_directory()
    assert scratch_path == os.path.join(str(tmp_path), "astrometrics")
    assert os.path.isdir(scratch_path)


def test_siril_directory_under_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Siril's work folder is `<cache>/astrometrics/siril`."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    work_path = siril_work_directory()
    assert work_path == os.path.join(str(tmp_path), "astrometrics", "siril")
    assert os.path.isdir(work_path)


def test_default_cache_root_is_home_dot_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `XDG_CACHE_HOME`, the folders sit under `~/.cache`."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    expected_root = os.path.join(os.path.expanduser("~"), ".cache")
    assert scratch_directory() == os.path.join(expected_root, "astrometrics")
    assert siril_work_directory() == os.path.join(expected_root, "astrometrics", "siril")


def test_locks_and_catalog_folders_sit_inside_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The locks and catalog folders are inside the scratch folder."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert locks_directory() == os.path.join(str(tmp_path), "astrometrics", "locks")
    assert catalog_cache_directory() == os.path.join(str(tmp_path), "astrometrics", "catalogs")
    assert os.path.isdir(locks_directory())
    assert os.path.isdir(catalog_cache_directory())


def test_data_directory_uses_xdg_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The durable folder is `<data home>/astrometrics`, made on demand."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    data_path = data_directory()
    assert data_path == os.path.join(str(tmp_path), "astrometrics")
    assert os.path.isdir(data_path)


def test_data_directory_default_is_local_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `XDG_DATA_HOME`, the folder is under `~/.local/share`."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    expected = os.path.join(os.path.expanduser("~"), ".local", "share", "astrometrics")
    assert data_directory() == expected


def test_config_directory_uses_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings folder is `<config>/astrometrics` and is not made."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = config_directory()
    assert config_path == os.path.join(str(tmp_path), "astrometrics")
    assert not os.path.exists(config_path)


def test_settings_file_is_found_in_the_config_folder_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file in ``~/.config/astrometrics`` wins over one in the project."""
    from astrometricslib.foundation.config import CONFIG_FILE_NAME, find_config_file

    monkeypatch.delenv("ASTROMETRICS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ASTROMETRICS_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings_file = tmp_path / "astrometrics" / CONFIG_FILE_NAME
    package_dir = tmp_path / "project" / "astrometricslib"
    # Nothing exists yet: the first save goes to the settings folder.
    assert find_config_file(package_dir) == settings_file
    legacy_file = package_dir / CONFIG_FILE_NAME
    package_dir.mkdir(parents=True)
    legacy_file.write_text("", encoding="utf-8")
    # An older install's file inside the project is still used.
    assert find_config_file(package_dir) == legacy_file
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("", encoding="utf-8")
    assert find_config_file(package_dir) == settings_file
