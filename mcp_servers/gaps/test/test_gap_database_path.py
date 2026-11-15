r"""Purpose: Tests for where the capability gap database lives.

Description: The gap server cannot import the library, so it repeats the data
folder rule: ``$XDG_DATA_HOME/astrometrics`` when that variable is set, and
otherwise the system's data folder from ``platformdirs``
(``~/.local/share/astrometrics`` on Linux, ``%LOCALAPPDATA%\astrometrics`` on
Windows). The Linux tests change ``HOME``; the Windows test runs only on
Windows.
"""

import os
import sys
from pathlib import Path

import pytest

from mcp_servers.gaps.gap_store import default_database_path

DATABASE_NAME = "capability_gaps.db"


def test_xdg_data_home_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A chosen data folder is used, whatever the system."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "chosen"))
    assert default_database_path() == tmp_path / "chosen" / "astrometrics" / DATABASE_NAME


@pytest.mark.skipif(sys.platform == "win32", reason="the default folder is the Linux one")
def test_linux_uses_local_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux the database is in ``~/.local/share/astrometrics``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert default_database_path() == tmp_path / ".local" / "share" / "astrometrics" / DATABASE_NAME


@pytest.mark.skipif(sys.platform != "win32", reason="the folder is in %LOCALAPPDATA% on Windows only")
def test_windows_uses_local_app_data(monkeypatch: pytest.MonkeyPatch) -> None:
    r"""On Windows the database is in ``%LOCALAPPDATA%\astrometrics``.

    ``platformdirs`` asks Windows for the folder rather than reading the
    ``LOCALAPPDATA`` variable, so the test compares with the real folder.
    """
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    local_app_data = Path(os.environ["LOCALAPPDATA"])
    assert default_database_path() == local_app_data / "astrometrics" / DATABASE_NAME
