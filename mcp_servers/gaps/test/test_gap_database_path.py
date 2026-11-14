r"""Purpose: Tests for where the capability gap database lives.

Description: The gap server cannot import the library, so it repeats the data
folder rule: ``$XDG_DATA_HOME/astrometrics``, ``~/.local/share/astrometrics``
on Linux and ``%LOCALAPPDATA%\Astrometrics\Data`` on Windows. On Windows
the older folder is used while the new one does not exist yet, so the app's
one-time move of that folder is not blocked. The tests choose the system by
patching ``sys.platform`` and the home folder, so they run the same on both.
"""

import sys
from pathlib import Path

import pytest

from mcp_servers.gaps.gap_store import default_database_path

DATABASE_NAME = "capability_gaps.db"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temporary home folder and clear the folder variables.

    Returns
    -------
    home : `pathlib.Path`
        The fake home folder; ``%LOCALAPPDATA%`` is ``<home>/AppData/Local``.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))
    return fake_home


def test_xdg_data_home_wins_on_every_system(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chosen data folder is used, whatever the system."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "chosen"))
    for platform in ("linux", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        assert default_database_path() == tmp_path / "chosen" / "astrometrics" / DATABASE_NAME


def test_linux_uses_local_share(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux the database is in ``~/.local/share/astrometrics``."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert default_database_path() == home / ".local" / "share" / "astrometrics" / DATABASE_NAME


def test_windows_uses_local_app_data(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, with no older folder, the database is in LocalAppData."""
    monkeypatch.setattr(sys, "platform", "win32")
    expected = home / "AppData" / "Local" / "Astrometrics" / "Data" / DATABASE_NAME
    assert default_database_path() == expected


def test_windows_keeps_the_older_folder_until_the_app_moves_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The older folder is used while the new one is missing."""
    monkeypatch.setattr(sys, "platform", "win32")
    legacy = home / ".local" / "share" / "astrometrics"
    legacy.mkdir(parents=True)
    assert default_database_path() == legacy / DATABASE_NAME

    new_folder = home / "AppData" / "Local" / "Astrometrics" / "Data"
    new_folder.mkdir(parents=True)
    assert default_database_path() == new_folder / DATABASE_NAME


def test_windows_without_local_app_data_uses_the_default_place(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without %LOCALAPPDATA%, the usual ``~/AppData/Local`` is used."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    expected = home / "AppData" / "Local" / "Astrometrics" / "Data" / DATABASE_NAME
    assert default_database_path() == expected
