"""Tests for the app's private Siril settings file.

Every Siril run reads a private copy of the user's Siril settings, so a run
never changes the user's own Siril. The tests make a stand-in for the user's
settings folder and check that it is only read, that the copy turns off
``update_default_scale`` and that the tool paths follow the user's file.
Siril is not started.
"""

import os
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.siril.core import settings
from astrometricslib._internal.drivers.siril.core.program import pipe_session_arguments
from astrometricslib._internal.drivers.siril.core.settings import (
    find_user_settings_file,
    prepare_private_settings,
    read_setting,
    user_settings_folder,
    write_setting,
)

USER_SETTINGS = """[core]
wd=
swap_dir=/tmp
starnet_exe=/home/user/.local/share/StarNetGPU/starnet-gpu.sh
graxpert_path=/home/user/.local/share/GraXpert/graxpert-gpu.sh

[astrometry]
update_default_scale=true
percent_scale_range=20

[gui]
first_start=false
"""


@pytest.fixture
def user_folder(tmp_path: Path) -> Path:
    """Make a stand-in for the user's Siril settings folder.

    Returns
    -------
    folder : `pathlib.Path`
        A folder holding ``config.1.4.ini``, an older ``config.1.2.ini`` and
        one of Siril's half-written copies.
    """
    folder = tmp_path / "user_siril"
    folder.mkdir()
    (folder / "config.1.4.ini").write_text(USER_SETTINGS, encoding="utf-8")
    (folder / "config.1.2.ini").write_text("[core]\nstarnet_exe=/old\n", encoding="utf-8")
    (folder / "config.1.4.ini.ABC123").write_text("[core]\nstarnet_exe=/broken\n", encoding="utf-8")
    return folder


def _snapshot(folder: Path) -> dict[str, tuple[bytes, float]]:
    """Read every file in a folder with its change time.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder.

    Returns
    -------
    files : `dict` [`str`, `tuple` [`bytes`, `float`]]
        Each file's name, its bytes and its modification time.
    """
    return {path.name: (path.read_bytes(), path.stat().st_mtime) for path in sorted(folder.iterdir())}


def test_the_users_settings_are_only_read(tmp_path: Path, user_folder: Path) -> None:
    """Making the private file leaves the user's folder as it was."""
    before = _snapshot(user_folder)

    prepare_private_settings("siril-cli", tmp_path / "private", user_folder)
    prepare_private_settings("siril-cli", tmp_path / "private", user_folder)

    assert _snapshot(user_folder) == before


def test_the_private_copy_never_changes_the_default_scale(tmp_path: Path, user_folder: Path) -> None:
    """The copy keeps the user's settings but not the default scale."""
    path = Path(prepare_private_settings("siril-cli", tmp_path / "private", user_folder))
    lines = path.read_text(encoding="utf-8").splitlines()

    assert path == tmp_path / "private" / "siril_settings.ini"
    assert read_setting(lines, "astrometry", "update_default_scale") == "false"
    assert read_setting(lines, "astrometry", "percent_scale_range") == "20"
    assert read_setting(lines, "core", "swap_dir") == "/tmp"
    assert read_setting(lines, "gui", "first_start") == "false"
    assert sum(line.startswith("update_default_scale=") for line in lines) == 1


def test_the_tool_paths_follow_the_users_file(tmp_path: Path, user_folder: Path) -> None:
    """A tool path the user changes later reaches the private copy."""
    private = tmp_path / "private"
    prepare_private_settings("siril-cli", private, user_folder)
    user_file = user_folder / "config.1.4.ini"
    user_file.write_text(
        USER_SETTINGS.replace("starnet-gpu.sh", "starnet-new.sh").replace(
            "graxpert-gpu.sh", "graxpert-new.sh"
        ),
        encoding="utf-8",
    )

    lines = Path(prepare_private_settings("siril-cli", private, user_folder)).read_text().splitlines()

    assert read_setting(lines, "core", "starnet_exe") == "/home/user/.local/share/StarNetGPU/starnet-new.sh"
    assert read_setting(lines, "core", "graxpert_path") == "/home/user/.local/share/GraXpert/graxpert-new.sh"


def test_settings_siril_wrote_into_the_private_copy_are_kept(tmp_path: Path, user_folder: Path) -> None:
    """The user's file is copied once; Siril's later changes stay."""
    private = tmp_path / "private"
    path = Path(prepare_private_settings("siril-cli", private, user_folder))
    path.write_text(path.read_text().replace("swap_dir=/tmp", "swap_dir=/scratch"), encoding="utf-8")
    path.write_text(path.read_text().replace("update_default_scale=false", "update_default_scale=true"))

    lines = Path(prepare_private_settings("siril-cli", private, user_folder)).read_text().splitlines()

    assert read_setting(lines, "core", "swap_dir") == "/scratch"
    assert read_setting(lines, "astrometry", "update_default_scale") == "false"


def test_without_user_settings_the_private_file_still_fixes_the_scale(tmp_path: Path) -> None:
    """With no user settings, the copy holds only the fixed scale setting."""
    path = Path(prepare_private_settings("siril-cli", tmp_path / "private", tmp_path / "missing"))

    assert path.read_text(encoding="utf-8") == "[astrometry]\nupdate_default_scale=false\n"


def test_the_newest_versioned_settings_file_is_chosen(user_folder: Path, tmp_path: Path) -> None:
    """``config.1.4.ini`` wins over older and half-written files."""
    assert find_user_settings_file(str(user_folder)) == str(user_folder / "config.1.4.ini")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "config.ini").write_text("[core]\n", encoding="utf-8")
    assert find_user_settings_file(str(plain)) == str(plain / "config.ini")
    assert find_user_settings_file(str(tmp_path / "missing")) is None


def test_a_flatpak_siril_reads_its_settings_from_its_own_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Flatpak's settings folder is under ``~/.var/app``."""
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/user/.config")
    monkeypatch.setattr(settings.sys, "platform", "linux")

    assert (
        user_settings_folder("flatpak run org.siril.Siril")
        == "/home/user/.var/app/org.siril.Siril/config/siril"
    )
    assert user_settings_folder("siril-cli") == os.path.join("/home/user/.config", "siril")


def test_a_setting_is_added_to_its_section_or_a_new_one() -> None:
    """A new setting ends its section; a new section ends the file."""
    lines = ["[core]", "wd=", "", "[gui]", "first_start=false"]

    assert write_setting(lines, "core", "starnet_exe", "/s") == [
        "[core]",
        "wd=",
        "starnet_exe=/s",
        "",
        "[gui]",
        "first_start=false",
    ]
    assert write_setting(lines, "astrometry", "update_default_scale", "false")[-2:] == [
        "[astrometry]",
        "update_default_scale=false",
    ]
    assert write_setting(lines, "core", "wd", "/w")[1] == "wd=/w"


def test_the_pipe_session_reads_the_private_settings() -> None:
    """The stack's Siril is given the private file, native or Flatpak."""
    native = pipe_session_arguments("siril-cli", "/w/in", "/w/out", "/data/siril_settings.ini")
    flatpak = pipe_session_arguments(
        "flatpak run org.siril.Siril", "/w/in", "/w/out", "/data/siril_settings.ini"
    )

    assert native == ["siril-cli", "-i", "/data/siril_settings.ini", "-p", "-r", "/w/in", "-w", "/w/out"]
    assert flatpak == [
        "flatpak",
        "run",
        "--filesystem=host",
        "--command=sh",
        "org.siril.Siril",
        "-c",
        "siril -i /data/siril_settings.ini -p -r /w/in -w /w/out",
    ]
