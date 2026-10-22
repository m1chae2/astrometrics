"""Tests for the camera profile migration.

Checks the dry run, the real run on a temporary config (backup made, old keys
gone, new entry loads), and the refusal to run twice.
"""

from pathlib import Path

import pytest

from astrometricslib.drivers import camera_profile_store
from astrometricslib.drivers.camera_profile_migration import MigrationSetting, migrate_camera_profiles
from astrometricslib.drivers.camera_profile_store import load_camera_profiles
from astrometricslib.drivers.test.test_camera_profile_store import write_profile
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConfigurationError

OLD_VALUES = (
    'gain_e_per_adu = { value = 0.783, kind = "measured", source = "gain 0, offset 10" }\n'
    'read_noise_e = { value = 3.8, kind = "measured", source = "gain 0, offset 10" }\n'
)
SETTING = MigrationSetting(gain_setting="0", offset="10", binning="1x1", temperature_c=-10.0)


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Build a real configuration over a temporary file.

    Returns
    -------
    config : `AppConfiguration`
        A configuration that reads and writes only the temporary file.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    config_path.write_text(
        write_profile("Fallback", generic=True) + write_profile("Test Camera") + OLD_VALUES, encoding="utf-8"
    )
    # The shared test fixtures point every settings object at one shared
    # file, so this test points them at its own file instead.
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    camera_profile_store._load_profiles_from_config.cache_clear()
    return AppConfiguration()


def test_a_dry_run_reports_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default run lists the change and leaves the file alone."""
    config = _config(tmp_path, monkeypatch)
    before = Path(config.config_file_path).read_text(encoding="utf-8")
    report = migrate_camera_profiles(config, {"test camera": SETTING})
    assert report.dry_run and report.backup_path is None
    assert [change.migrated for change in report.changes] == [True]
    assert Path(config.config_file_path).read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*.bak")) == []


def test_a_real_run_backs_up_converts_and_refuses_a_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real run backs up the file, and the new profile loads."""
    config = _config(tmp_path, monkeypatch)
    before = Path(config.config_file_path).read_text(encoding="utf-8")
    report = migrate_camera_profiles(config, {"Test Camera": SETTING}, dry_run=False)
    assert report.backup_path is not None
    assert report.backup_path.read_text(encoding="utf-8") == before
    profile = next(p for p in load_camera_profiles(AppConfiguration()) if p.camera_name == "Test Camera")
    assert not profile.has_unkeyed_noise_values
    entry = profile.sensor_noise_for("0", "10", "1x1")
    assert entry is not None
    assert entry.gain_e_per_adu.value == pytest.approx(0.783)
    assert entry.read_noise_e.provenance.source == "gain 0, offset 10"
    assert entry.temperature_c == pytest.approx(-10.0)
    with pytest.raises(ConfigurationError, match="already"):
        migrate_camera_profiles(config, {"Test Camera": SETTING}, dry_run=False)
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_an_unknown_camera_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A camera with no profile stops the run before anything is written."""
    config = _config(tmp_path, monkeypatch)
    with pytest.raises(ConfigurationError, match="no camera profile"):
        migrate_camera_profiles(config, {"Nope": SETTING}, dry_run=False)
    assert list(tmp_path.glob("*.bak")) == []
