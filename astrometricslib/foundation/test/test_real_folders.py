"""Purpose: Test the helper that finds the user's real library folders.

Description: The helper must honour the ``ASTROMETRICS_REAL_*`` variables
that a simulated observatory sets after it moves the ``XDG_*`` folders, and
must read the real settings file without creating anything.
"""

from pathlib import Path

from astrometricslib.drivers import real_folders
from astrometricslib.foundation import paths


def test_defaults_come_from_the_xdg_folders(tmp_path: Path) -> None:
    """With no settings file the folders are the data-home defaults."""
    folders = real_folders({"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_DATA_HOME": str(tmp_path / "d")})
    assert folders.library == tmp_path / "d" / "astrometrics"
    assert folders.frames == folders.library / "frames"
    assert folders.stacks == folders.frames
    assert folders.wayfinding == tmp_path / "d" / "astrometrics" / "wayfinding"
    assert not (tmp_path / "d").exists()


def test_the_real_variables_win_over_the_moved_folders(tmp_path: Path) -> None:
    """After a simulator moved XDG_*, the real folders are still found."""
    config = tmp_path / "real.toml"
    config.write_text(
        f'["Image Library"]\npath = "{tmp_path / "lib"}"\nstacks_path = "{tmp_path / "stk"}"\n'
        f'["Wayfinding Library"]\npath = "{tmp_path / "wf"}"\n'
    )
    folders = real_folders({
        "XDG_CONFIG_HOME": "/moved/config",
        "XDG_DATA_HOME": "/moved/data",
        "ASTROMETRICS_CONFIG_PATH": "/moved/config.toml",
        paths.REAL_CONFIG_PATH_VARIABLE: str(config),
        paths.REAL_DATA_HOME_VARIABLE: str(tmp_path / "data"),
        paths.REAL_CONFIG_HOME_VARIABLE: str(tmp_path / "cfg"),
    })
    assert folders.library == tmp_path / "lib"
    assert folders.frames == tmp_path / "lib" / "frames"
    assert folders.stacks == tmp_path / "stk"
    assert folders.wayfinding == tmp_path / "wf"
    assert folders.config_home == tmp_path / "cfg"
    assert (
        tmp_path / "wf" in folders.protected_paths()
        or (tmp_path / "wf").resolve() in folders.protected_paths()
    )


def test_a_folder_named_for_the_simulator_is_recognised() -> None:
    """A simulator's temporary copy is recognised by its folder name."""
    assert paths.is_in_simulator_world(f"/home/x/.local/share/{paths.SIMULATOR_FOLDER_NAME}/library")
    assert not paths.is_in_simulator_world("/home/x/.local/share/astrometrics/library")
