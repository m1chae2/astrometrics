"""Tests for finding and loading camera profiles.

Covers name matching, the generic fallback, the checks on a config's camera
sections, and a parity check against the constants the pipelines still use.
While the pipelines are being moved onto profiles, the parity tests prove
that the shipped config holds exactly the numbers the code used before.
"""

import logging
import threading
from pathlib import Path

import pytest
import tomlkit

from astrometricslib.drivers import camera_profile_store
from astrometricslib.drivers.camera_profile_store import (
    camera_identity,
    load_camera_profiles,
    record_name_for_camera,
    resolve_camera_profile,
    save_sensor_setting_noise,
)
from astrometricslib.foundation.config import AppConfiguration, _TomlSectionedConfig
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.models.camera_profile import ProvenancedValue, SensorSettingNoise, ValueProvenance

EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "astrometrics.config.example.toml"


class _FakeConfig:
    """A minimal, hashable stand-in offering the `.app_config` attribute.

    `load_camera_profiles` and friends only ever read `.app_config` off
    whatever they are given, and cache by identity -- a real
    `AppConfiguration` is overkill for these tests, but it must still be
    hashable, unlike `types.SimpleNamespace`.
    """

    def __init__(self, app_config: _TomlSectionedConfig) -> None:
        """Store the parsed config."""
        self.app_config = app_config


def _config_from_text(text: str) -> _FakeConfig:
    """Build a minimal config-like object from TOML text.

    Returns
    -------
    config : `_FakeConfig`
        An object with the `.app_config` attribute `load_camera_profiles`
        and friends need.
    """
    parser = _TomlSectionedConfig()
    parser.read_string(text)
    return _FakeConfig(parser)


def _shipped_config() -> _FakeConfig:
    """Build a config from the shipped example file.

    Returns
    -------
    config : `_FakeConfig`
        A config holding the real, shipped camera profiles.
    """
    return _config_from_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))


def write_profile(
    camera_name: str, aliases: tuple[str, ...] = (), generic: bool = False, record_name: str | None = None
) -> str:
    """Build one camera section's TOML text.

    Parameters
    ----------
    camera_name : `str`
        The camera name to store.
    aliases : `tuple` [`str`, ...], optional
        Other spellings to store.
    generic : `bool`, optional
        Whether this is the generic fallback profile.
    record_name : `str`, optional
        The spelling frame records should use.

    Returns
    -------
    section_text : `str`
        One ``[Observatory.Camera.<name>]`` section, ready to append to a
        config's text.
    """
    lines = [f'["Observatory.Camera.{camera_name}"]', f'name = "{camera_name}"']
    if aliases:
        lines.append(f'name_aliases = "{", ".join(aliases)}"')
    if record_name:
        lines.append(f'record_name = "{record_name}"')
    if generic:
        lines.append('is_generic_fallback = "true"')
    lines.append('clip_ceiling_adu = { value = 65535.0, kind = "assumed", source = "a test" }')
    lines.append('saturation_threshold_adu = { value = 65000.0, kind = "assumed", source = "a test" }')
    return "\n".join(lines) + "\n"


def test_the_shipped_config_loads_and_has_one_generic_fallback() -> None:
    """Check that the real, shipped camera sections are valid together."""
    profiles = load_camera_profiles(_shipped_config())
    assert sum(profile.is_generic_fallback for profile in profiles) == 1
    assert {profile.camera_name for profile in profiles if not profile.is_generic_fallback} == {
        "ZWO ASI533MM Pro",
        "Nikon D5300",
        "ZWO ASI120MC-S",
    }


def test_the_shipped_asi533_profile_carries_its_detector_noise_terms() -> None:
    """Gain and read noise load per setting as provenanced values.

    The photometric error budget needs both. The shipped example records
    them for the setting they were measured at (gain 0, offset 10, 1x1).
    """
    profiles = {profile.camera_name: profile for profile in load_camera_profiles(_shipped_config())}
    asi533 = profiles["ZWO ASI533MM Pro"]
    entry = asi533.sensor_noise_for("0.0", "10", "1x1")
    assert entry is not None
    assert entry.gain_e_per_adu is not None
    assert entry.read_noise_e is not None
    # In stored 16-bit ADU. The old 3.6 e-/ADU was in 14-bit ADU and made the
    # error bars about 2 times too small.
    assert entry.gain_e_per_adu.value == pytest.approx(0.783)
    assert entry.read_noise_e.value == pytest.approx(3.8)
    assert entry.gain_e_per_adu.provenance.kind.value == "measured"
    assert entry.temperature_c == pytest.approx(-10.0)
    assert asi533.sensor_noise_for("100", "10", "1x1") is None
    assert profiles["Nikon D5300"].sensor_noise == ()


@pytest.mark.parametrize(
    ("spelling", "expected_camera"),
    [
        ("ZWO ASI533MM Pro", "ZWO ASI533MM Pro"),
        ("ZWO ASI 533MM Pro", "ZWO ASI533MM Pro"),
        ("ZWO CCD ASI533MM Pro", "ZWO ASI533MM Pro"),
        ("zwo asi533mm pro", "ZWO ASI533MM Pro"),
        ("Nikon D5300", "Nikon D5300"),
        ("Nikon DSLR DSC D5300", "Nikon D5300"),
        ("ZWO ASI120MC-S", "ZWO ASI120MC-S"),
    ],
)
def test_every_known_spelling_of_a_camera_finds_its_profile(spelling: str, expected_camera: str) -> None:
    """Check the spellings that appear in headers, frame records and config."""
    profile = resolve_camera_profile(spelling, _shipped_config())
    assert profile.camera_name == expected_camera
    assert profile.is_generic_fallback is False


@pytest.mark.parametrize("camera_name", [None, "", "   "])
def test_a_missing_name_gives_the_generic_profile_without_a_warning(
    camera_name: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """Check that no name at all is not treated as an unlisted camera."""
    with caplog.at_level(logging.WARNING):
        profile = resolve_camera_profile(camera_name, _shipped_config())
    assert profile.is_generic_fallback is True
    assert caplog.records == []


def test_an_unlisted_camera_gets_the_generic_profile_and_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that the warning appears once per name, not once per frame."""
    camera_profile_store._warn_once_about_unlisted_camera.cache_clear()
    config = _shipped_config()
    with caplog.at_level(logging.WARNING):
        first = resolve_camera_profile("Acme Imager 9000", config)
        second = resolve_camera_profile("Acme Imager 9000", config)
        resolve_camera_profile("Other Imager 1", config)
    assert first.is_generic_fallback and second.is_generic_fallback
    warned_names = [record.getMessage() for record in caplog.records]
    assert len(warned_names) == 2
    assert "Acme Imager 9000" in warned_names[0]
    assert "Other Imager 1" in warned_names[1]


def test_an_unlisted_zwo_camera_gets_the_generic_profile_not_the_zwo_family_ceiling() -> None:
    """Record a deliberate change from the old lookup.

    The old code gave any camera with ZWO in its name the ceiling measured
    on the ASI533 (65532). A profile says only what was measured or assumed
    for that exact model, so an unlisted ZWO camera gets the generic value.
    """
    profile = resolve_camera_profile("ZWO ASI2600MM Pro", _shipped_config())
    assert profile.is_generic_fallback
    assert profile.clip_ceiling_adu.value == pytest.approx(65535.0)


def test_the_nikon_threshold_is_known_to_sit_above_its_ceiling() -> None:
    """Keep the open question about D5300 saturation visible.

    A saturated D5300 pixel tops out at 16383 but the threshold is 65000.
    This test records that on purpose. When the question is settled and
    the threshold is fixed, this test should be changed to expect True.
    """
    profiles = [
        profile
        for profile in load_camera_profiles(_shipped_config())
        if not profile.saturation_threshold_can_be_reached
    ]
    assert [profile.camera_name for profile in profiles] == ["Nikon D5300"]


def test_a_config_without_a_generic_fallback_is_rejected() -> None:
    """Check that the config must say what to do with an unlisted camera."""
    config = _config_from_text(write_profile("Camera One"))
    with pytest.raises(ConfigurationError, match="exactly one generic fallback"):
        load_camera_profiles(config)


def test_a_camera_section_that_is_not_a_valid_profile_is_a_configuration_error() -> None:
    """A section whose numbers do not parse is reported as a config error."""
    broken = write_profile("Camera One").replace("value = 65535.0", 'value = "lots"')
    config = _config_from_text(write_profile("Fallback", generic=True) + broken)
    with pytest.raises(ConfigurationError, match="not a valid profile"):
        load_camera_profiles(config)


def test_a_config_with_two_generic_fallbacks_is_rejected() -> None:
    """Check that there cannot be two competing fallbacks."""
    text = write_profile("Fallback A", generic=True) + write_profile("Fallback B", generic=True)
    config = _config_from_text(text)
    with pytest.raises(ConfigurationError, match="exactly one generic fallback"):
        load_camera_profiles(config)


def test_two_profiles_claiming_the_same_name_are_rejected() -> None:
    """Check that one spelling cannot belong to two cameras."""
    text = (
        write_profile("Fallback", generic=True)
        + write_profile("Camera One", aliases=("Shared Name",))
        + write_profile("Camera Two", aliases=("shared-name",))
    )
    config = _config_from_text(text)
    with pytest.raises(ConfigurationError, match="claimed by both"):
        load_camera_profiles(config)


# ---- The numbers the pipelines used before they read profiles --------------
# The constants these values came from have been removed from the pipelines,
# so the values are pinned here. A change to a profile that alters one of
# them must be a deliberate decision.


@pytest.mark.parametrize(
    ("camera_name", "expected_ceiling_adu"),
    [
        ("ZWO CCD ASI533MM Pro", 65532.0),
        ("ZWO ASI 533MM Pro", 65532.0),
        ("ZWO ASI533MM Pro", 65532.0),
        ("ZWO ASI120MC-S", 65532.0),
        ("Nikon D5300", 16383.0),
        ("Nikon DSLR DSC D5300", 16383.0),
        ("Unknown camera", 65535.0),
        (None, 65535.0),
    ],
)
def test_the_clip_ceiling_is_the_value_the_old_lookup_gave(
    camera_name: str | None, expected_ceiling_adu: float
) -> None:
    """Check each camera's ceiling against the old lookup's answer."""
    profile = resolve_camera_profile(camera_name, _shipped_config())
    assert profile.clip_ceiling_adu.value == pytest.approx(expected_ceiling_adu)


def test_every_saturation_threshold_is_the_value_the_old_constants_had() -> None:
    """Check that every profile keeps the 65000 the old constants held."""
    for profile in load_camera_profiles(_shipped_config()):
        assert profile.saturation_threshold_adu.value == pytest.approx(65000.0)


def test_only_the_asi533_has_a_linearity_limit() -> None:
    """Check the one camera that has a measured linearity limit."""
    limits = {
        profile.camera_name: profile.photometric_linearity_limit_adu
        for profile in load_camera_profiles(_shipped_config())
        if profile.photometric_linearity_limit_adu is not None
    }
    assert list(limits) == ["ZWO ASI533MM Pro"]
    assert limits["ZWO ASI533MM Pro"].value == pytest.approx(60000.0)


def test_the_asi533_quantum_efficiency_curve_is_the_one_read_off_the_zwo_graph() -> None:
    """Pin the curve that used to live in quantum_efficiency_curves.py."""
    config = _shipped_config()
    for spelling in ("ZWO ASI533MM Pro", "ZWO ASI 533MM Pro", "ZWO CCD ASI533MM Pro"):
        curve = resolve_camera_profile(spelling, config).quantum_efficiency
        assert curve is not None
        assert len(curve.wavelength_nm) == 17
        assert curve.wavelength_nm[0] == pytest.approx(400.0)
        assert curve.wavelength_nm[-1] == pytest.approx(1000.0)
        assert curve.quantum_efficiency_fraction[0] == pytest.approx(0.70)
        assert max(curve.quantum_efficiency_fraction) == pytest.approx(0.92)
        assert curve.quantum_efficiency_fraction[-1] == pytest.approx(0.06)
        assert sum(curve.quantum_efficiency_fraction) == pytest.approx(9.87)


@pytest.mark.parametrize("camera_name", ["Nikon D5300", "ZWO ASI120MC-S", "Acme Imager 9000"])
def test_only_the_asi533_has_a_quantum_efficiency_curve(camera_name: str) -> None:
    """Check that every other camera has no curve, so none is applied."""
    assert resolve_camera_profile(camera_name, _shipped_config()).quantum_efficiency is None


@pytest.mark.parametrize(
    ("header_name", "expected_record_name"),
    [
        ("ZWO CCD ASI533MM Pro", "ZWO ASI 533MM Pro"),
        ("ZWO ASI533MM Pro", "ZWO ASI 533MM Pro"),
        ("Nikon DSLR DSC D5300", "Nikon DSLR DSC D5300"),
        ("Nikon D5300", "Nikon DSLR DSC D5300"),
    ],
)
def test_the_record_name_is_the_spelling_the_library_has_always_used(
    header_name: str, expected_record_name: str
) -> None:
    """Check the two names that the old string replacements produced."""
    assert record_name_for_camera(header_name, _shipped_config()) == expected_record_name


def test_a_camera_without_a_record_name_keeps_its_header_text() -> None:
    """Check that cameras with no record name keep their header text."""
    config = _shipped_config()
    assert record_name_for_camera("ZWO CCD ASI120MC-S", config) == "ZWO CCD ASI120MC-S"
    assert record_name_for_camera("Acme Imager 9000", config) == "Acme Imager 9000"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("ZWO CCD ASI533MM Pro", "ZWO ASI 533MM Pro"),
        ("Nikon DSLR DSC D5300", "Nikon D5300"),
        ("zwo-asi533mm-pro", "ZWO ASI533MM Pro"),
    ],
)
def test_every_spelling_of_a_listed_camera_has_the_same_identity(first: str, second: str) -> None:
    """Check spelling, punctuation and profile aliases."""
    config = _shipped_config()
    assert camera_identity(first, config) == camera_identity(second, config)


def test_different_cameras_have_different_identities() -> None:
    """Check that listed and unlisted cameras are kept apart."""
    config = _shipped_config()
    assert camera_identity("ZWO ASI 533MM Pro", config) != camera_identity("Nikon D5300", config)
    assert camera_identity("Acme Imager 9000", config) != camera_identity("Acme Imager 9001", config)
    assert camera_identity("Acme Imager 9000", config) == camera_identity("acme-imager 9000", config)


# ---- Gain and read noise keyed by camera setting ---------------------------

NOISE_SECTION_NAME = "Observatory.Camera.Test Camera"


def _measured(value: float) -> ProvenancedValue:
    """Wrap a number as a measured profile value.

    Returns
    -------
    value : `ProvenancedValue`
        The number with a ``measured`` provenance.
    """
    return ProvenancedValue(value=value, provenance=ValueProvenance(kind="measured", source="a test"))


def _entry(gain_setting: str, gain: float, read_noise: float) -> SensorSettingNoise:
    """Build a noise entry at offset 10 and 1x1 binning.

    Returns
    -------
    entry : `SensorSettingNoise`
        The entry for the given gain setting.
    """
    return SensorSettingNoise(
        gain_setting=gain_setting,
        offset="10",
        binning="1x1",
        gain_e_per_adu=_measured(gain),
        read_noise_e=_measured(read_noise),
        temperature_c=-10.0,
    )


def _real_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile_text: str) -> AppConfiguration:
    """Build a real `AppConfiguration` whose file is a temporary copy.

    Returns
    -------
    config : `AppConfiguration`
        A configuration reading and writing only the temporary file.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    config_path.write_text(write_profile("Fallback", generic=True) + profile_text, encoding="utf-8")
    # The shared test fixtures point every settings object at one shared
    # file, so this test points them at its own file instead.
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    camera_profile_store._load_profiles_from_config.cache_clear()
    return AppConfiguration()


def test_the_old_single_value_layout_loads_but_is_never_applied(caplog: pytest.LogCaptureFixture) -> None:
    """Old single values load, are flagged and are never applied."""
    camera_profile_store._warn_once_about_unkeyed_noise.cache_clear()
    old_text = write_profile("Old Camera") + (
        'gain_e_per_adu = { value = 0.783, kind = "measured", source = "a test" }\n'
        'read_noise_e = { value = 3.8, kind = "measured", source = "a test" }\n'
    )
    config = _config_from_text(write_profile("Fallback", generic=True) + old_text)
    with caplog.at_level(logging.WARNING):
        profiles = {profile.camera_name: profile for profile in load_camera_profiles(config)}
    profile = profiles["Old Camera"]
    assert profile.has_unkeyed_noise_values
    assert profile.sensor_noise == ()
    assert profile.sensor_noise_for("0", "10", "1x1") is None
    assert profile.sensor_noise_gap_reason("0", "10", "1x1") == (
        "camera profile has no setting-keyed gain and read noise; run the profile migration"
    )
    assert sum("Old Camera" in record.getMessage() for record in caplog.records) == 1


def test_a_saved_entry_loads_back_and_replacing_keeps_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round trip: load what save wrote; same key replaces, others stay."""
    config = _real_config(tmp_path, monkeypatch, write_profile("Test Camera"))
    config_path = Path(config.config_file_path)

    first_backup = save_sensor_setting_noise(config, "test camera", _entry("0", 0.78, 3.8))
    save_sensor_setting_noise(config, "Test Camera", _entry("100", 0.2, 1.5))
    save_sensor_setting_noise(config, "Test Camera", _entry("0.0", 0.9, 3.9))

    assert first_backup is not None and first_backup.is_file()
    assert first_backup.name.endswith(".bak")
    assert len(list(tmp_path.glob("*.bak"))) == 3
    reloaded = AppConfiguration()
    profile = next(p for p in load_camera_profiles(reloaded) if p.camera_name == "Test Camera")
    assert [entry.gain_setting for entry in profile.sensor_noise] == ["0", "100"]
    assert profile.sensor_noise_for("0", "10", "1x1").gain_e_per_adu.value == pytest.approx(0.9)
    assert profile.sensor_noise_for("100", "10", "1x1").read_noise_e.value == pytest.approx(1.5)
    assert tomlkit.parse(config_path.read_text(encoding="utf-8"))[NOISE_SECTION_NAME]["sensor_noise"]


def test_saving_into_an_old_layout_section_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old and new values are never mixed in one section."""
    old_text = write_profile("Test Camera") + (
        'gain_e_per_adu = { value = 0.783, kind = "measured", source = "a test" }\n'
    )
    config = _real_config(tmp_path, monkeypatch, old_text)
    with pytest.raises(ConfigurationError, match="migration"):
        save_sensor_setting_noise(config, "Test Camera", _entry("0", 0.78, 3.8))
    with pytest.raises(ConfigurationError, match="no camera profile"):
        save_sensor_setting_noise(config, "Missing Camera", _entry("0", 0.78, 3.8))


def test_the_spread_pair_values_and_robust_read_noise_survive_a_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extra fields of a measured entry round-trip through the file."""
    config = _real_config(tmp_path, monkeypatch, write_profile("Test Camera"))
    entry = SensorSettingNoise(
        gain_setting="0",
        offset="10",
        binning="1x1",
        gain_e_per_adu=_measured(0.783),
        read_noise_e=_measured(3.1),
        gain_spread_e_per_adu=0.0004,
        pair_gains_e_per_adu=(0.7826, 0.7831, 0.7829),
        read_noise_adu_robust=3.95,
        pair_read_noises_adu=(3.97, 4.02),
    )

    save_sensor_setting_noise(config, "Test Camera", entry)

    reloaded = AppConfiguration()
    profile = next(p for p in load_camera_profiles(reloaded) if p.camera_name == "Test Camera")
    saved = profile.sensor_noise_for("0", "10", "1x1")
    assert saved.gain_spread_e_per_adu == pytest.approx(0.0004)
    assert saved.pair_gains_e_per_adu == pytest.approx((0.7826, 0.7831, 0.7829))
    assert saved.read_noise_adu_robust == pytest.approx(3.95)
    assert saved.pair_read_noises_adu == pytest.approx((3.97, 4.02))
    assert saved == entry


def test_an_entry_without_the_extra_fields_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry saved before these fields existed loads with empty values."""
    config = _real_config(tmp_path, monkeypatch, write_profile("Test Camera"))
    save_sensor_setting_noise(config, "Test Camera", _entry("0", 0.78, 3.8))

    profile = next(p for p in load_camera_profiles(AppConfiguration()) if p.camera_name == "Test Camera")
    saved = profile.sensor_noise_for("0", "10", "1x1")

    assert saved.gain_spread_e_per_adu is None
    assert saved.pair_gains_e_per_adu == ()
    assert saved.read_noise_adu_robust is None
    assert saved.pair_read_noises_adu == ()


def test_only_the_newest_config_backups_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Older backups beyond the limit are deleted; the newest stay."""
    monkeypatch.setattr(camera_profile_store, "MAXIMUM_CONFIG_BACKUPS", 2)
    config = _real_config(tmp_path, monkeypatch, write_profile("Test Camera"))

    backups = [
        save_sensor_setting_noise(config, "Test Camera", _entry(str(index), 0.78, 3.8)) for index in range(4)
    ]

    remaining = sorted(path.name for path in tmp_path.glob("*.bak"))
    assert remaining == sorted(backup.name for backup in backups[-2:])


def test_a_failed_write_leaves_the_config_whole_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new text goes to a temporary file first; a failure loses nothing."""
    config = _real_config(tmp_path, monkeypatch, write_profile("Test Camera"))
    config_path = Path(config.config_file_path)
    before = config_path.read_text(encoding="utf-8")

    def fail_halfway(configfile: object) -> None:
        """Write a few characters and then fail.

        Raises
        ------
        OSError
            Always, as a full disk would.
        """
        configfile.write("garbage")
        raise OSError("disk full")

    monkeypatch.setattr(config.app_config, "write", fail_halfway)

    with pytest.raises(OSError, match="disk full"):
        save_sensor_setting_noise(config, "Test Camera", _entry("0", 0.78, 3.8))

    assert config_path.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_two_threads_saving_different_settings_both_keep_their_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write lock makes each read-modify-write finish before the next."""
    config = _real_config(tmp_path, monkeypatch, write_profile("Test Camera"))
    gain_settings = [str(100 * index) for index in range(6)]

    threads = [
        threading.Thread(
            target=save_sensor_setting_noise, args=(config, "Test Camera", _entry(gain_setting, 0.78, 3.8))
        )
        for gain_setting in gain_settings
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    profile = next(p for p in load_camera_profiles(AppConfiguration()) if p.camera_name == "Test Camera")
    assert sorted(entry.gain_setting for entry in profile.sensor_noise) == sorted(gain_settings)
