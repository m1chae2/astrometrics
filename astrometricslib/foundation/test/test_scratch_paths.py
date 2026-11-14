"""Tests for the working-file folders in `foundation.paths`."""

import os
from pathlib import Path

import pytest

from astrometricslib.foundation import paths
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


def test_default_cache_root_is_home_dot_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `XDG_CACHE_HOME`, the folders sit under `~/.cache` on Linux."""
    monkeypatch.setattr(paths, "_on_windows", lambda: False)
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
    """Without `XDG_DATA_HOME`, Linux uses `~/.local/share`."""
    monkeypatch.setattr(paths, "_on_windows", lambda: False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    expected = os.path.join(os.path.expanduser("~"), ".local", "share", "astrometrics")
    assert data_directory() == expected


@pytest.fixture
def windows_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pretend to be on Windows, with a home and LOCALAPPDATA inside tmp_path.

    Returns
    -------
    home : `pathlib.Path`
        The fake home folder. ``%LOCALAPPDATA%`` is ``<home>/AppData/Local``.
    """
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "_on_windows", lambda: True)
    monkeypatch.setattr(os.path, "expanduser", lambda text: text.replace("~", str(home), 1))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    for variable in ("XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(variable, raising=False)
    return home


def test_windows_folders_are_in_local_app_data(windows_home: Path) -> None:
    r"""On Windows the folders are in %LOCALAPPDATA%\Astrometrics."""
    app_folder = windows_home / "AppData" / "Local" / "Astrometrics"
    assert scratch_directory() == str(app_folder / "Cache")
    assert data_directory() == str(app_folder / "Data")
    assert state_directory() == str(app_folder / "State")


def test_windows_moves_the_old_data_folder_once(windows_home: Path) -> None:
    """An older install's data folder is moved whole into Data."""
    legacy = windows_home / ".local" / "share" / "astrometrics"
    (legacy / "wayfinding").mkdir(parents=True)
    (legacy / "wayfinding" / "wayfinding.db").write_bytes(b"observatory")
    # Before the move, a reader is pointed at the old folder, not an empty
    # new one, and asking for the folder moves nothing.
    assert default_data_path() == str(legacy)
    assert data_directory() == str(legacy)
    assert legacy.is_dir()

    # Only the backend's explicit call moves it.
    assert paths.move_legacy_windows_data() is True
    data_path = Path(data_directory())
    assert data_path == windows_home / "AppData" / "Local" / "Astrometrics" / "Data"
    assert (data_path / "wayfinding" / "wayfinding.db").read_bytes() == b"observatory"
    assert not legacy.exists()
    # A second call has nothing to do.
    assert paths.move_legacy_windows_data() is False


def test_windows_keeps_the_old_folder_when_it_cannot_be_moved(
    windows_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file in use stops the move; the old folder is kept and used."""
    legacy = windows_home / ".local" / "share" / "astrometrics"
    legacy.mkdir(parents=True)
    (legacy / "astrometrics.db").write_bytes(b"library")

    def refuse(*_arguments: object) -> None:
        """Fail like Windows does when a file in the folder is open.

        Raises
        ------
        PermissionError
            Always.
        """
        raise PermissionError("The process cannot access the file")

    monkeypatch.setattr(os, "rename", refuse)
    assert paths.move_legacy_windows_data() is False
    assert data_directory() == str(legacy)
    assert (legacy / "astrometrics.db").read_bytes() == b"library"
    assert not (windows_home / "AppData" / "Local" / "Astrometrics" / "Data").exists()


def test_xdg_variable_wins_on_windows(
    tmp_path: Path, windows_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An XDG variable still chooses the folder on Windows."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "chosen"))
    assert data_directory() == str(tmp_path / "chosen" / "astrometrics")


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
