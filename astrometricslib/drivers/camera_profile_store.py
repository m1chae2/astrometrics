"""Finds and loads the stored profile for a camera.

The profiles live in the same config file every other camera setting does
(see `astrometricslib.foundation.config`), one
``[Observatory.Camera.<name>]`` section per camera model (see
`astrometricslib.models.camera_profile` for what a profile holds). This
module reads that config and picks the right profile for a camera name
taken from a FITS header, a frame record, or the config itself.

A camera that has no profile gets the generic fallback profile instead of
an error, so a new camera does not stop processing. A warning is logged
the first time each unlisted name is seen, so the gap is not silent.

A profile keeps its gain and read noise per camera setting, as an array of
inline tables named ``sensor_noise`` (see
`astrometricslib.models.camera_profile.SensorSettingNoise`). This module also
writes such an entry back to the config file (`save_sensor_setting_noise`),
after copying the file to a timestamped backup. The older layout, one
``gain_e_per_adu`` and one ``read_noise_e`` per camera with no setting, still
loads, but its values are not used: nobody recorded the setting they were
measured at. A warning is logged once per camera, and the migration in
`astrometricslib.drivers.camera_profile_migration` converts the file.
"""

import datetime
import functools
import glob
import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tomlkit

from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    QuantumEfficiencyRecord,
    SensorSettingNoise,
    ValueProvenance,
)

__all__ = [
    "CAMERA_SECTION_PREFIX",
    "LEGACY_NOISE_KEYS",
    "MAXIMUM_CONFIG_BACKUPS",
    "SENSOR_NOISE_KEY",
    "backup_config_file",
    "build_camera_profile",
    "build_provenanced_value",
    "camera_identity",
    "load_camera_profiles",
    "load_profiles_from_config",
    "record_name_for_camera",
    "resolve_camera_profile",
    "save_sensor_setting_noise",
    "sensor_noise_to_toml_array",
]

logger = logging.getLogger(__name__)

CAMERA_SECTION_PREFIX = "Observatory.Camera."

# The keys of the old layout, which held one gain and one read noise for the
# whole camera, whatever its setting. They are read only to be flagged and
# migrated, never applied.
LEGACY_NOISE_KEYS = ("gain_e_per_adu", "read_noise_e")

# The key of the current layout: an array of inline tables, one per setting.
SENSOR_NOISE_KEY = "sensor_noise"

# How many ``.bak`` copies of one config file are kept. Each saved measurement
# or migration copies the config first, and the config is a few tens of
# kilobytes, so ten copies cost a few hundred kilobytes and cover the last ten
# edits, enough to undo a mistake noticed within a session or two. Older copies
# are deleted when a new one is made. A design choice, not a tuned value.
MAXIMUM_CONFIG_BACKUPS = 10


def build_provenanced_value(field: Mapping[str, Any]) -> ProvenancedValue:
    """Build a `ProvenancedValue` from a config inline table.

    Returns
    -------
    provenanced_value : `ProvenancedValue`
        The value and its provenance.
    """
    return ProvenancedValue(
        value=float(field["value"]),
        provenance=ValueProvenance(kind=field["kind"], source=field["source"]),
    )


def _build_quantum_efficiency(field: Mapping[str, Any]) -> QuantumEfficiencyRecord:
    """Build a `QuantumEfficiencyRecord` from a config inline table.

    Returns
    -------
    quantum_efficiency : `QuantumEfficiencyRecord`
        The curve and its provenance.
    """
    return QuantumEfficiencyRecord(
        wavelength_nm=tuple(float(value) for value in field["wavelength_nm"]),
        quantum_efficiency_fraction=tuple(float(value) for value in field["quantum_efficiency_fraction"]),
        provenance=ValueProvenance(kind=field["kind"], source=field["source"]),
    )


def _build_sensor_setting_noise(field: Mapping[str, Any]) -> SensorSettingNoise:
    """Build a `SensorSettingNoise` from one ``sensor_noise`` entry.

    Returns
    -------
    entry : `SensorSettingNoise`
        The gain and read noise recorded for one camera setting.
    """
    kwargs = {
        "gain_setting": str(field["gain_setting"]),
        "offset": str(field.get("offset", "0")),
        "binning": str(field.get("binning", "1x1")),
    }
    for name in ("gain_e_per_adu", "read_noise_e"):
        if name in field:
            kwargs[name] = build_provenanced_value(field[name])
    if "temperature_c" in field:
        kwargs["temperature_c"] = float(field["temperature_c"])
    for name in ("gain_spread_e_per_adu", "read_noise_adu_robust"):
        if name in field:
            kwargs[name] = float(field[name])
    for name in ("pair_gains_e_per_adu", "pair_read_noises_adu"):
        if name in field:
            kwargs[name] = tuple(float(value) for value in field[name])
    return SensorSettingNoise(**kwargs)


@functools.cache
def _warn_once_about_unkeyed_noise(camera_name: str) -> None:
    """Log a warning once per camera with the old noise layout.

    Parameters
    ----------
    camera_name : `str`
        The camera whose section holds a single gain or read noise with no
        setting. Because the result is cached, a second call with the same
        name does nothing.
    """
    logger.warning(
        "The camera profile for %r holds a gain or read noise with no camera setting. These values are "
        "not used, because nobody recorded the gain, offset and binning they were measured at. Run "
        "astrometricslib/scripts/migrate_camera_profiles.py to move them into a setting-keyed "
        "sensor_noise entry.",
        camera_name,
    )


def build_camera_profile(section_name: str, section: dict) -> CameraProfile:
    """Build a `CameraProfile` from one config section.

    Returns
    -------
    profile : `CameraProfile`
        The profile described by `section`.
    """
    camera_name = section.get("name") or section_name[len(CAMERA_SECTION_PREFIX) :]
    name_aliases = tuple(
        alias.strip() for alias in str(section.get("name_aliases", "")).split(",") if alias.strip()
    )
    kwargs = {
        "camera_name": camera_name,
        "name_aliases": name_aliases,
        "record_name": section.get("record_name") or None,
        "is_generic_fallback": str(section.get("is_generic_fallback", "false")).strip().lower() == "true",
        "clip_ceiling_adu": build_provenanced_value(section["clip_ceiling_adu"]),
        "saturation_threshold_adu": build_provenanced_value(section["saturation_threshold_adu"]),
    }
    if "photometric_linearity_limit_adu" in section:
        kwargs["photometric_linearity_limit_adu"] = build_provenanced_value(
            section["photometric_linearity_limit_adu"]
        )
    if "quantum_efficiency" in section:
        kwargs["quantum_efficiency"] = _build_quantum_efficiency(section["quantum_efficiency"])
    # Detector noise terms for the photometric error budget, one entry per
    # camera setting. Both are optional; without a matching entry the pipeline
    # falls back to the FITS header, then assumes unit gain and no read noise,
    # and says so.
    if SENSOR_NOISE_KEY in section:
        kwargs["sensor_noise"] = tuple(
            _build_sensor_setting_noise(entry) for entry in section[SENSOR_NOISE_KEY]
        )
    if any(key in section for key in LEGACY_NOISE_KEYS):
        # Old layout: one value with no setting. It is not applied (decision 7
        # of the quality-metrics plan), only flagged.
        kwargs["has_unkeyed_noise_values"] = True
        _warn_once_about_unkeyed_noise(camera_name)
    return CameraProfile(**kwargs)


@functools.cache
def load_profiles_from_config(config: AppConfiguration) -> tuple[CameraProfile, ...]:
    """Read and check every camera profile section in `config`.

    Parameters
    ----------
    config : `AppConfiguration`
        The config to read camera sections from.

    Returns
    -------
    profiles : `tuple` [`CameraProfile`, ...]
        Every profile found, in section order. The result is cached per
        `config` instance, so each config is read from disk only once per
        run.

    Raises
    ------
    ConfigurationError
        If a camera section is not a valid profile, if the config does
        not hold exactly one generic fallback profile, or if two profiles
        claim the same camera name.
    """
    profiles = []
    for section_name in config.app_config.sections():
        if not section_name.startswith(CAMERA_SECTION_PREFIX) or section_name == "Observatory.Camera":
            continue
        section = dict(config.app_config[section_name])
        if "clip_ceiling_adu" not in section:
            # A camera section can exist purely for per-setup facts (pixel
            # size, grating geometry) without ever becoming a profile --
            # only a section with model-level facts is one.
            continue
        try:
            profiles.append(build_camera_profile(section_name, section))
        except ValueError as error:  # a number that does not parse, or the model's own checks
            raise ConfigurationError(
                f"The camera section [{section_name}] is not a valid profile: {error}",
                details={"section": section_name},
            ) from error
    profiles = tuple(profiles)

    fallback_count = sum(1 for profile in profiles if profile.is_generic_fallback)
    if fallback_count != 1:
        raise ConfigurationError(
            f"the configuration must hold exactly one generic fallback camera profile, found {fallback_count}"
        )

    owner_by_normalized_name: dict[str, str] = {}
    for profile in profiles:
        if profile.is_generic_fallback:
            continue
        for name in (profile.camera_name, *profile.name_aliases):
            normalized_name = normalize_camera_name(name)
            owner = owner_by_normalized_name.setdefault(normalized_name, profile.camera_name)
            if owner != profile.camera_name:
                raise ConfigurationError(
                    f"the name {name!r} is claimed by both {owner!r} and {profile.camera_name!r}"
                )
    return profiles


def load_camera_profiles(config: AppConfiguration | None = None) -> tuple[CameraProfile, ...]:
    """Return every stored camera profile, including the generic fallback.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The config to read. The process-wide singleton is used when this
        is left out.

    Returns
    -------
    profiles : `tuple` [`CameraProfile`, ...]
        The profiles, in section order.
    """
    return load_profiles_from_config(config or get_configuration())


@functools.cache
def _warn_once_about_unlisted_camera(camera_name: str) -> None:
    """Log a warning the first time a camera name has no profile.

    Parameters
    ----------
    camera_name : `str`
        The camera name that matched no profile. Because the result is
        cached, a second call with the same name does nothing.
    """
    logger.warning(
        "No camera profile matches %r; using the generic profile. Add a "
        "[Observatory.Camera.%s] section with its own model facts to "
        "astrometrics.config.toml to give this camera its own profile.",
        camera_name,
        camera_name,
    )


def resolve_camera_profile(camera_name: str | None, config: AppConfiguration | None = None) -> CameraProfile:
    """Pick the profile that matches a camera name.

    Parameters
    ----------
    camera_name : `str` or `None`
        The camera name, written in any spelling. Case, spaces and
        punctuation are ignored when names are compared.
    config : `AppConfiguration`, optional
        The config to read. The process-wide singleton is used when this
        is left out.

    Returns
    -------
    profile : `CameraProfile`
        The matching profile. When nothing matches, or no name is given,
        this is the generic fallback profile, whose ``is_generic_fallback``
        is `True`. A name that is given but matches nothing also logs a
        warning, once per distinct name.
    """
    profiles = load_camera_profiles(config)
    generic_profile = next(profile for profile in profiles if profile.is_generic_fallback)

    if not camera_name or not camera_name.strip():
        return generic_profile

    wanted_name = normalize_camera_name(camera_name)
    for profile in profiles:
        if profile.is_generic_fallback:
            continue
        known_names = (profile.camera_name, *profile.name_aliases)
        if any(normalize_camera_name(known_name) == wanted_name for known_name in known_names):
            return profile

    _warn_once_about_unlisted_camera(camera_name)
    return generic_profile


def camera_identity(camera_name: str, config: AppConfiguration | None = None) -> str:
    """Reduce a camera name to text that is the same for every spelling of it.

    Two names give the same identity when they are the same camera, whether
    they differ in case, spaces and punctuation or are listed as aliases in the
    camera's profile (for example ``ZWO CCD ASI533MM Pro`` and
    ``ZWO ASI 533MM Pro``).

    Parameters
    ----------
    camera_name : `str`
        A camera name, for example from a header or from the config.
    config : `AppConfiguration`, optional
        The config to read profiles from. The process-wide singleton is
        used when this is left out.

    Returns
    -------
    identity : `str`
        The camera's profile name, reduced to lowercase letters and digits,
        when it has a profile. Otherwise the name itself reduced the same way.
    """
    profile = resolve_camera_profile(camera_name, config)
    if profile.is_generic_fallback:
        return normalize_camera_name(camera_name)
    return normalize_camera_name(profile.camera_name)


def record_name_for_camera(camera_name: str, config: AppConfiguration | None = None) -> str:
    """Give the spelling of a camera's name that frame records use.

    Parameters
    ----------
    camera_name : `str`
        The camera's name as written in an image header.
    config : `AppConfiguration`, optional
        The config to read profiles from. The process-wide singleton is
        used when this is left out.

    Returns
    -------
    record_name : `str`
        The profile's ``record_name`` when the camera has a profile that sets
        one, otherwise `camera_name` unchanged.
    """
    profile = resolve_camera_profile(camera_name, config)
    return profile.record_name or camera_name


def _find_camera_section_name(config: AppConfiguration, camera_name: str) -> str:
    """Find the config section that holds a camera's profile.

    Parameters
    ----------
    config : `AppConfiguration`
        The config to search.
    camera_name : `str`
        The camera, written in any spelling the profile knows (its name or
        one of its aliases).

    Returns
    -------
    section_name : `str`
        The full section name, such as ``Observatory.Camera.ZWO ASI533MM Pro``.

    Raises
    ------
    ConfigurationError
        If no camera profile section has that name.
    """
    wanted_name = normalize_camera_name(camera_name)
    for section_name in config.app_config.sections():
        if not section_name.startswith(CAMERA_SECTION_PREFIX) or section_name == "Observatory.Camera":
            continue
        section = dict(config.app_config[section_name])
        if "clip_ceiling_adu" not in section:
            continue
        profile = build_camera_profile(section_name, section)
        if any(
            normalize_camera_name(name) == wanted_name
            for name in (profile.camera_name, *profile.name_aliases)
        ):
            return section_name
    raise ConfigurationError(f"no camera profile section matches the camera {camera_name!r}")


def backup_config_file(config: AppConfiguration) -> Path | None:
    """Copy the config file to a new timestamped ``.bak`` file beside it.

    At most `MAXIMUM_CONFIG_BACKUPS` backups of this config are kept: after
    the copy is made, the oldest ones beyond that number are deleted. Only
    files named like this function's own backups are touched.

    Parameters
    ----------
    config : `AppConfiguration`
        The config whose file is copied.

    Returns
    -------
    backup_path : `Path` or `None`
        The new copy, named like
        ``astrometrics.config.toml.20261021-101500-123456.bak``.
        `None` when the config has no file on disk yet.
    """
    path = getattr(config, "config_file_path", None)
    if path is None or not Path(path).is_file():
        return None
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = Path(path).with_name(f"{Path(path).name}.{stamp}.bak")
    shutil.copy2(path, backup_path)
    # The timestamp in the name sorts in time order, so the oldest come first.
    backups = sorted(Path(path).parent.glob(f"{glob.escape(Path(path).name)}.????????-??????-??????.bak"))
    for stale_backup in backups[:-MAXIMUM_CONFIG_BACKUPS]:
        try:
            stale_backup.unlink()
        except OSError as error:
            logger.warning("Could not delete the old config backup %s: %s", stale_backup, error)
    return backup_path


def sensor_noise_to_toml_array(
    entries: tuple[SensorSettingNoise, ...] | list[SensorSettingNoise],
) -> tomlkit.items.Array:
    """Write noise entries as the TOML array a profile section stores.

    Parameters
    ----------
    entries : `tuple` [`SensorSettingNoise`, ...]
        The entries, in the order to store them.

    Returns
    -------
    array : `tomlkit.items.Array`
        An array of inline tables, one per entry, one entry per line.
    """

    def provenanced_table(value: ProvenancedValue) -> tomlkit.items.InlineTable:
        """Write one value and its provenance as an inline table.

        Returns
        -------
        table : `tomlkit.items.InlineTable`
            ``{ value = ..., kind = ..., source = ... }``.
        """
        table = tomlkit.inline_table()
        table["value"] = value.value
        table["kind"] = value.provenance.kind.value
        table["source"] = value.provenance.source
        return table

    array = tomlkit.array()
    array.multiline(True)
    for entry in entries:
        table = tomlkit.inline_table()
        table["gain_setting"] = entry.gain_setting
        table["offset"] = entry.offset
        table["binning"] = entry.binning
        if entry.temperature_c is not None:
            table["temperature_c"] = entry.temperature_c
        if entry.gain_e_per_adu is not None:
            table["gain_e_per_adu"] = provenanced_table(entry.gain_e_per_adu)
        if entry.read_noise_e is not None:
            table["read_noise_e"] = provenanced_table(entry.read_noise_e)
        if entry.gain_spread_e_per_adu is not None:
            table["gain_spread_e_per_adu"] = entry.gain_spread_e_per_adu
        if entry.pair_gains_e_per_adu:
            table["pair_gains_e_per_adu"] = list(entry.pair_gains_e_per_adu)
        if entry.read_noise_adu_robust is not None:
            table["read_noise_adu_robust"] = entry.read_noise_adu_robust
        if entry.pair_read_noises_adu:
            table["pair_read_noises_adu"] = list(entry.pair_read_noises_adu)
        array.append(table)
    return array


def save_sensor_setting_noise(
    config: AppConfiguration, camera_name: str, entry: SensorSettingNoise
) -> Path | None:
    """Write the gain and read noise of one camera setting to the config.

    An existing entry for the same gain setting, offset and binning is
    replaced in place. Every other entry is kept. The config file is copied
    to a timestamped backup first, and the profile cache is cleared after the
    write so the next load sees the new entry.

    Parameters
    ----------
    config : `AppConfiguration`
        The config to change. Its file is rewritten.
    camera_name : `str`
        The camera the entry belongs to, written in any spelling its profile
        knows.
    entry : `SensorSettingNoise`
        The gain and read noise to store.

    Returns
    -------
    backup_path : `Path` or `None`
        The backup made before the write, or `None` when the config had no
        file yet.

    Raises
    ------
    ConfigurationError
        If no profile matches `camera_name`, or if its section still holds the
        old single gain or read noise (run the profile migration first, so
        old and new values are never mixed).
    """
    # The whole read-modify-write holds the config's write lock, so two saves
    # (two threads, or the app and a script) cannot each read the file before
    # the other writes it and then overwrite each other's entry.
    with config.write_lock():
        section_name = _find_camera_section_name(config, camera_name)
        section = dict(config.app_config[section_name])
        if any(key in section for key in LEGACY_NOISE_KEYS):
            raise ConfigurationError(
                f"The camera section [{section_name}] still holds a gain or read noise with no camera "
                "setting. Run the profile migration before adding setting-keyed entries.",
                details={"section": section_name},
            )
        existing = [_build_sensor_setting_noise(item) for item in section.get(SENSOR_NOISE_KEY, [])]
        merged = [entry if item.setting_key == entry.setting_key else item for item in existing]
        if all(item.setting_key != entry.setting_key for item in existing):
            merged.append(entry)

        backup_path = backup_config_file(config)
        config.app_config.set_item(section_name, SENSOR_NOISE_KEY, sensor_noise_to_toml_array(merged))
        config.save_configuration()
        load_profiles_from_config.cache_clear()
    return backup_path
