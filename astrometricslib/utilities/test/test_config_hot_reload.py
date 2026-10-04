"""Tests that a running process picks up edits to the configuration file.

Settings used to be read once, so a changed setting needed a restart of the
backend and the MCP server. These tests cover the reload that replaced that,
and the separate denoise path and on/off switch that go with it.
"""

import os
from pathlib import Path

import pytest

from astrometricslib.utilities import config_loader
from astrometricslib.utilities.config_loader import AppConfiguration, _TomlSectionedConfig

FIRST_TEXT = '["Processing.Siril"]\npreview_star_tone_enabled = "true"\n'
SECOND_TEXT = '["Processing.Siril"]\npreview_star_tone_enabled = "false"\n'


def write_file(path: Path, text: str, modified_time_ns: int) -> None:
    """Write a config file and give it an exact modification time.

    The time is set by hand so the tests do not depend on how fast the disk
    updates its clock.
    """
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(modified_time_ns, modified_time_ns))


def make_watching_config(path: Path) -> _TomlSectionedConfig:
    """Build a config that has read `path` and checks it on every read.

    Returns
    -------
    config : `_TomlSectionedConfig`
        A config watching the file, with the recheck delay switched off.
    """
    config = _TomlSectionedConfig()
    config.read(str(path))
    config.watch_for_changes()
    return config


def read_toning(config: _TomlSectionedConfig) -> str:
    """Read the star toning setting, without waiting for the recheck delay.

    Returns
    -------
    value : `str`
        The setting as the config currently holds it.
    """
    config._next_check_time = 0.0
    return str(config.get("Processing.Siril", "preview_star_tone_enabled"))


def test_an_edit_to_the_file_is_picked_up_without_a_restart(tmp_path: Path) -> None:
    """A changed file is read again the next time a setting is asked for."""
    path = tmp_path / "config.toml"
    write_file(path, FIRST_TEXT, 1_000_000_000)
    config = make_watching_config(path)
    assert read_toning(config) == "true"

    write_file(path, SECOND_TEXT, 2_000_000_000)

    assert read_toning(config) == "false"


def test_an_unchanged_file_is_not_read_again(tmp_path: Path) -> None:
    """A change made in memory survives while the file stays the same."""
    path = tmp_path / "config.toml"
    write_file(path, FIRST_TEXT, 1_000_000_000)
    config = make_watching_config(path)
    config.set("Processing.Siril", "preview_star_tone_enabled", "false")

    assert read_toning(config) == "false"


def test_a_broken_file_keeps_the_settings_already_loaded(tmp_path: Path) -> None:
    """A file caught half written does not wipe the settings in use."""
    path = tmp_path / "config.toml"
    write_file(path, FIRST_TEXT, 1_000_000_000)
    config = make_watching_config(path)

    write_file(path, "this is [not valid toml", 2_000_000_000)

    assert read_toning(config) == "true"
    write_file(path, SECOND_TEXT, 3_000_000_000)
    assert read_toning(config) == "false"


def test_the_apps_own_save_is_not_read_back_as_an_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving the config leaves it as it is and does not trigger a reload."""
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: tmp_path / "config.toml")
    write_file(tmp_path / "config.toml", FIRST_TEXT, 1_000_000_000)
    configuration = AppConfiguration()
    configuration.watch_for_changes()
    reloads: list[int] = []
    configuration.app_config._after_reload = lambda: reloads.append(1)

    configuration.app_config.set("Processing.Siril", "preview_star_tone_enabled", "false")
    configuration.save_configuration()
    configuration.app_config._next_check_time = 0.0
    configuration.get_preview_star_tone_enabled()

    assert reloads == []
    assert configuration.get_preview_star_tone_enabled() is False


def test_defaults_are_put_back_after_a_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reloaded file lacking a setting still gets its default."""
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: tmp_path / "config.toml")
    write_file(tmp_path / "config.toml", FIRST_TEXT, 1_000_000_000)
    configuration = AppConfiguration()
    configuration.watch_for_changes()

    write_file(
        tmp_path / "config.toml", '["Processing.Siril"]\nsiril_executable = "siril-cli"\n', 2_000_000_000
    )
    configuration.app_config._next_check_time = 0.0

    assert configuration.get_cosmic_clarity_denoise_strength() == pytest.approx(0.9)
    assert configuration.get_cosmic_clarity_denoise_enabled() is True


def test_the_denoise_path_and_switch_are_separate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching denoise off keeps the program path for single runs."""
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: tmp_path / "config.toml")
    write_file(
        tmp_path / "config.toml",
        '["Processing.CosmicClarity"]\ndenoise_executable = "/opt/cc/denoise"\ndenoise_enabled = "false"\n',
        1_000_000_000,
    )
    configuration = AppConfiguration()
    configuration.watch_for_changes()

    assert configuration.get_cosmic_clarity_denoise_executable() is None
    assert configuration.get_cosmic_clarity_denoise_path() == "/opt/cc/denoise"
    assert configuration.get_cosmic_clarity_denoise_enabled() is False


def test_the_module_keeps_one_shared_configuration_object() -> None:
    """Reload is in place, so code holding the config sees new values."""
    assert config_loader.get_configuration() is config_loader.get_configuration()
