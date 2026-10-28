"""Moves a camera's single gain and read noise into a setting-keyed entry.

Older config files kept one ``gain_e_per_adu`` and one ``read_noise_e`` for
each camera, with no record of the gain setting, offset and binning they were
measured at. A camera profile now keeps these per setting (see
`astrometricslib.models.camera_profile.SensorSettingNoise`), and the old
values are ignored because they could be applied to a frame taken at another
setting.

`migrate_camera_profiles` converts a config file once. The person running it
states the setting each old value was measured at, usually from the value's
own source text. The run copies the config file to a timestamped backup
first, refuses to touch a camera that already uses the new layout, and by
default only reports what it would change.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.drivers.camera_profile_store import (
    CAMERA_SECTION_PREFIX,
    LEGACY_NOISE_KEYS,
    SENSOR_NOISE_KEY,
    backup_config_file,
    build_camera_profile,
    build_provenanced_value,
    load_profiles_from_config,
    sensor_noise_to_toml_array,
)
from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.models.camera_profile import SensorSettingNoise


@dataclass(frozen=True)
class MigrationSetting:
    """The camera setting an old gain and read noise were measured at.

    Attributes
    ----------
    gain_setting : `str`
        The gain setting (or ISO) of the frames the values came from.
    offset : `str`
        The offset of those frames.
    binning : `str`
        The binning of those frames, such as ``"1x1"``.
    temperature_c : `float` or `None`
        The sensor temperature of those frames, in degrees C, if known.
    """

    gain_setting: str
    offset: str
    binning: str = "1x1"
    temperature_c: float | None = None


@dataclass(frozen=True)
class MigrationChange:
    """What the migration did, or would do, to one camera's section.

    Attributes
    ----------
    section_name : `str`
        The config section, such as ``Observatory.Camera.ZWO ASI533MM Pro``.
    migrated : `bool`
        `True` when the section's old values become a setting-keyed entry.
    description : `str`
        One sentence saying what happens and why.
    """

    section_name: str
    migrated: bool
    description: str


@dataclass(frozen=True)
class MigrationReport:
    """The result of one `migrate_camera_profiles` call.

    Attributes
    ----------
    changes : `list` [`MigrationChange`]
        One line per camera section that has a profile.
    dry_run : `bool`
        `True` when nothing was written.
    backup_path : `Path` or `None`
        The backup made before writing, or `None` for a dry run or when
        nothing needed to change.
    """

    changes: list[MigrationChange]
    dry_run: bool
    backup_path: Path | None


def migrate_camera_profiles(
    config: AppConfiguration, settings_by_camera: Mapping[str, MigrationSetting], dry_run: bool = True
) -> MigrationReport:
    """Rewrite old-layout camera sections into setting-keyed entries.

    Parameters
    ----------
    config : `AppConfiguration`
        The config to migrate. Its file is rewritten unless `dry_run`.
    settings_by_camera : `Mapping` [`str`, `MigrationSetting`]
        For each camera to migrate, the setting its old values were measured
        at. The camera may be written in any spelling its profile knows. A
        camera with old values that is not listed is left alone and reported.
    dry_run : `bool`, optional
        When `True` (the default) only report what would change.

    Returns
    -------
    report : `MigrationReport`
        What changed, or would change, and the backup path.

    Raises
    ------
    ConfigurationError
        If a listed camera has no profile, or if a listed camera's section
        holds both the old values and a ``sensor_noise`` entry (a mix of the
        two layouts). Nothing is written in either case. A section that
        already holds only a ``sensor_noise`` entry is not an error: the
        report says it is already migrated and nothing changes.
    """
    wanted_by_normalized_name = {
        normalize_camera_name(camera): (camera, setting) for camera, setting in settings_by_camera.items()
    }
    sections: list[tuple[str, dict, MigrationSetting | None]] = []
    matched_names: set[str] = set()
    for section_name in config.app_config.sections():
        if not section_name.startswith(CAMERA_SECTION_PREFIX) or section_name == "Observatory.Camera":
            continue
        section = dict(config.app_config[section_name])
        if "clip_ceiling_adu" not in section:
            continue
        profile = build_camera_profile(section_name, section)
        setting = None
        for name in (profile.camera_name, *profile.name_aliases):
            normalized_name = normalize_camera_name(name)
            if normalized_name in wanted_by_normalized_name:
                matched_names.add(normalized_name)
                setting = wanted_by_normalized_name[normalized_name][1]
        sections.append((section_name, section, setting))

    unknown = [camera for key, (camera, _) in wanted_by_normalized_name.items() if key not in matched_names]
    if unknown:
        raise ConfigurationError(f"no camera profile matches {', '.join(repr(name) for name in unknown)}")
    # A section that holds both the old keys and a sensor_noise entry mixes the
    # two layouts: which values are current is unknown, so nothing is touched.
    # A section with a sensor_noise entry and no old keys is simply migrated
    # already, and is reported as such below.
    mixed_layouts = [
        name
        for name, section, setting in sections
        if setting and SENSOR_NOISE_KEY in section and any(key in section for key in LEGACY_NOISE_KEYS)
    ]
    if mixed_layouts:
        raise ConfigurationError(
            f"{', '.join(mixed_layouts)} holds both the old gain or read noise and a {SENSOR_NOISE_KEY} "
            "entry, so which values are current is unknown. Nothing was changed.",
            details={"sections": mixed_layouts},
        )

    changes: list[MigrationChange] = []
    new_arrays: dict[str, SensorSettingNoise] = {}
    for section_name, section, setting in sections:
        has_old_values = any(key in section for key in LEGACY_NOISE_KEYS)
        if setting is None:
            if has_old_values:
                changes.append(
                    MigrationChange(
                        section_name, False, "has old gain or read noise but no setting was given; left alone"
                    )
                )
            continue
        if not has_old_values:
            if SENSOR_NOISE_KEY in section:
                description = (
                    f"already migrated: it holds a {SENSOR_NOISE_KEY} entry and no old gain or read noise; "
                    "nothing to do"
                )
            else:
                description = "has no old gain or read noise; nothing to migrate"
            changes.append(MigrationChange(section_name, False, description))
            continue
        kwargs = {
            "gain_setting": setting.gain_setting,
            "offset": setting.offset,
            "binning": setting.binning,
            "temperature_c": setting.temperature_c,
        }
        for key in LEGACY_NOISE_KEYS:
            if key in section:
                kwargs[key] = build_provenanced_value(section[key])
        entry = SensorSettingNoise(**kwargs)
        new_arrays[section_name] = entry
        held = " and ".join(key for key in LEGACY_NOISE_KEYS if key in section)
        changes.append(
            MigrationChange(
                section_name,
                True,
                f"moves {held} into {SENSOR_NOISE_KEY} for gain {entry.gain_setting}, offset {entry.offset}, "
                f"binning {entry.binning}",
            )
        )

    backup_path = None
    if not dry_run and new_arrays:
        with config.write_lock():
            backup_path = backup_config_file(config)
            for section_name, entry in new_arrays.items():
                config.app_config.set_item(
                    section_name, SENSOR_NOISE_KEY, sensor_noise_to_toml_array([entry])
                )
                for key in LEGACY_NOISE_KEYS:
                    config.app_config.remove_key(section_name, key)
            config.save_configuration()
            load_profiles_from_config.cache_clear()
    return MigrationReport(changes=changes, dry_run=dry_run, backup_path=backup_path)
