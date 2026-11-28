"""Purpose: Keep the Siril driver's tests away from the user's Siril settings.

Description: Every Siril run reads a private copy of the user's Siril
settings (``siril/core/settings.py``), so a test that starts a stand-in Siril
would read the real ``~/.config/siril`` or the Flatpak's
``~/.var/app/org.siril.Siril/config/siril``. The autouse fixture
`temporary_siril_settings_home` points the home folder, ``XDG_CONFIG_HOME``
and ``LOCALAPPDATA`` at a temporary folder holding a small made-up settings
file for the native and the Flatpak Siril, so the copy logic still runs. It
also stops the test at once if the settings code tries to read a file under
the real home folder's Siril settings.

The stacking pipeline's tests have the same fixture in their own
``conftest.py``.
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.siril.core import settings

# The user's real home folder and settings folder, read once before any test
# changes them.
REAL_HOME = os.path.realpath(os.path.expanduser("~"))
REAL_CONFIG_HOME = os.path.realpath(os.environ.get("XDG_CONFIG_HOME") or os.path.join(REAL_HOME, ".config"))

# The folders of the user's own Siril settings that no test may read.
REAL_SIRIL_SETTINGS_FOLDERS = (
    os.path.join(REAL_CONFIG_HOME, "siril"),
    os.path.join(REAL_HOME, ".var", "app", "org.siril.Siril", "config"),
    # Siril on Windows keeps its settings in %LOCALAPPDATA%\siril.
    os.path.join(
        os.path.realpath(os.environ.get("LOCALAPPDATA") or os.path.join(REAL_HOME, "AppData", "Local")),
        "siril",
    ),
)

# The made-up settings file the fake folders hold.
FAKE_SIRIL_SETTINGS = (
    "[core]\nwd=\nstarnet_exe=/fake/starnet\ngraxpert_path=/fake/graxpert\n\n"
    "[astrometry]\nupdate_default_scale=true\n"
)


def is_real_siril_settings_path(path: str) -> bool:
    """Say whether a path lies in the user's own Siril settings folders.

    Parameters
    ----------
    path : `str`
        The path to check.

    Returns
    -------
    real : `bool`
        `True` if the path is inside one of `REAL_SIRIL_SETTINGS_FOLDERS`.
    """
    resolved = os.path.realpath(path)
    return any(
        resolved == folder or resolved.startswith(folder + os.sep) for folder in REAL_SIRIL_SETTINGS_FOLDERS
    )


@pytest.fixture(autouse=True)
def temporary_siril_settings_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Give every test a made-up home folder for Siril's settings.

    Returns
    -------
    home : `pathlib.Path`
        The temporary home folder. Its ``.config/siril`` and
        ``.var/app/org.siril.Siril/config/siril`` each hold a
        ``config.1.4.ini``.
    """
    home = tmp_path_factory.mktemp("siril_home")
    for folder in (
        home / ".config" / "siril",
        home / ".var" / "app" / "org.siril.Siril" / "config" / "siril",
    ):
        folder.mkdir(parents=True)
        (folder / "config.1.4.ini").write_text(FAKE_SIRIL_SETTINGS, encoding="utf-8")
    monkeypatch.setattr(settings, "user_home_folder", lambda: str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / ".config"))
    real_read_lines: Callable[[str], list[str]] = settings._read_lines

    def guarded_read_lines(path: str) -> list[str]:
        """Refuse to read the user's own Siril settings.

        Returns
        -------
        lines : `list` [`str`]
            The file's lines, read by the real function.

        Raises
        ------
        AssertionError
            If the path is in the user's own Siril settings folders.
        """
        if is_real_siril_settings_path(path):
            raise AssertionError(f"A test tried to read the user's own Siril settings: {path}")
        return real_read_lines(path)

    monkeypatch.setattr(settings, "_read_lines", guarded_read_lines)
    return home
