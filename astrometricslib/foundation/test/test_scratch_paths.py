"""Tests for the working-file folders in `foundation.paths`."""

import os
import sys
from pathlib import Path

import pytest

from astrometricslib.foundation.paths import (
    catalog_cache_directory,
    config_directory,
    data_directory,
    default_data_path,
    locks_directory,
    scratch_directory,
    siril_work_directory,
    state_directory,
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


@pytest.fixture
def linux_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temporary home folder and clear every XDG variable.

    Returns
    -------
    home : `pathlib.Path`
        The temporary home folder.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for variable in ("XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(variable, raising=False)
    return home


@pytest.mark.skipif(sys.platform == "win32", reason="the default folders are the Linux ones")
def test_default_folders_are_the_xdg_defaults_on_linux(linux_home: Path) -> None:
    """Without XDG variables, the folders follow the XDG defaults."""
    assert scratch_directory() == str(linux_home / ".cache" / "astrometrics")
    assert siril_work_directory() == str(linux_home / ".cache" / "astrometrics" / "siril")
    assert data_directory() == str(linux_home / ".local" / "share" / "astrometrics")
    assert state_directory() == str(linux_home / ".local" / "state" / "astrometrics")
    assert config_directory() == str(linux_home / ".config" / "astrometrics")


@pytest.mark.skipif(sys.platform == "win32", reason="the default folders are the Linux ones")
def test_default_data_path_follows_the_environment(
    linux_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`XDG_DATA_HOME` wins when set; otherwise the Linux default is given."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "chosen"))
    assert default_data_path() == str(tmp_path / "chosen" / "astrometrics")
    monkeypatch.delenv("XDG_DATA_HOME")
    assert default_data_path() == str(linux_home / ".local" / "share" / "astrometrics")


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


@pytest.mark.skipif(sys.platform != "win32", reason="the folders are in %LOCALAPPDATA% on Windows only")
def test_windows_folders_are_in_local_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r"""On Windows the folders are in ``%LOCALAPPDATA%\astrometrics``."""
    local_app_data = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    for variable in ("XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(variable, raising=False)
    app_folder = local_app_data / "astrometrics"
    assert Path(data_directory()) == app_folder
    assert Path(state_directory()) == app_folder
    assert Path(scratch_directory()) == app_folder / "Cache"
    assert Path(config_directory()) == app_folder


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
