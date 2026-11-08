"""Purpose: Test the helper that finds the user's real library folders.

Description: The helper must honour the ``ASTROMETRICS_REAL_*`` variables
that a simulated observatory sets after it moves the ``XDG_*`` folders, and
must read the real settings file without creating anything.
"""

from pathlib import Path

import pytest

from simulator import real_folders as real_folders_module
from simulator.real_folders import real_folders


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
        real_folders_module.REAL_CONFIG_PATH_VARIABLE: str(config),
        real_folders_module.REAL_DATA_HOME_VARIABLE: str(tmp_path / "data"),
        real_folders_module.REAL_CONFIG_HOME_VARIABLE: str(tmp_path / "cfg"),
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


def test_real_logs_folder_follows_the_real_state_variable(tmp_path: Path) -> None:
    """The real logs folder follows the real state variable, not XDG."""
    folders = real_folders({
        real_folders_module.REAL_STATE_HOME_VARIABLE: str(tmp_path / "real_state"),
        "XDG_STATE_HOME": str(tmp_path / "world_state"),
    })
    assert folders.logs == tmp_path / "real_state" / "astrometrics" / "logs"


def test_the_folders_follow_the_apps_own_path_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative setting resolves as `AppConfiguration` resolves it."""
    from astrometricslib.foundation.config import AppConfiguration, _TomlSectionedConfig

    text = (
        '["Image Library"]\npath = "./relative_library"\nframes_path = "./relative_frames"\n'
        'stacks_path = "./relative_stacks"\n["Wayfinding Library"]\npath = "absolute_wf"\n'
    )
    config_file = tmp_path / "real.toml"
    config_file.write_text(text, encoding="utf-8")
    config = AppConfiguration.__new__(AppConfiguration)
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(AppConfiguration, "get_project_root", lambda self: repository)
    config.app_config = _TomlSectionedConfig()
    config.app_config.read_string(text)

    folders = real_folders({real_folders_module.REAL_CONFIG_PATH_VARIABLE: str(config_file)})

    assert folders.library == config.get_library_path()
    assert folders.frames == config.get_frames_path()
    assert folders.stacks == config.get_stacks_path()
    assert folders.wayfinding == repository / "absolute_wf"
