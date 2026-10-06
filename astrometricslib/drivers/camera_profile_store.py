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
"""

import functools
import logging

from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    QuantumEfficiencyRecord,
    ValueProvenance,
)

logger = logging.getLogger(__name__)

_CAMERA_SECTION_PREFIX = "Observatory.Camera."


def _build_provenanced_value(field) -> ProvenancedValue:  # ruff: ignore[missing-type-function-argument]
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


def _build_quantum_efficiency(field) -> QuantumEfficiencyRecord:  # ruff: ignore[missing-type-function-argument]
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


def _build_camera_profile(section_name: str, section: dict) -> CameraProfile:
    """Build a `CameraProfile` from one config section.

    Returns
    -------
    profile : `CameraProfile`
        The profile described by `section`.
    """
    camera_name = section.get("name") or section_name[len(_CAMERA_SECTION_PREFIX) :]
    name_aliases = tuple(
        alias.strip() for alias in str(section.get("name_aliases", "")).split(",") if alias.strip()
    )
    kwargs = {
        "camera_name": camera_name,
        "name_aliases": name_aliases,
        "record_name": section.get("record_name") or None,
        "is_generic_fallback": str(section.get("is_generic_fallback", "false")).strip().lower() == "true",
        "clip_ceiling_adu": _build_provenanced_value(section["clip_ceiling_adu"]),
        "saturation_threshold_adu": _build_provenanced_value(section["saturation_threshold_adu"]),
    }
    if "photometric_linearity_limit_adu" in section:
        kwargs["photometric_linearity_limit_adu"] = _build_provenanced_value(
            section["photometric_linearity_limit_adu"]
        )
    if "quantum_efficiency" in section:
        kwargs["quantum_efficiency"] = _build_quantum_efficiency(section["quantum_efficiency"])
    return CameraProfile(**kwargs)


@functools.cache
def _load_profiles_from_config(config: AppConfiguration) -> tuple[CameraProfile, ...]:
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
        if not section_name.startswith(_CAMERA_SECTION_PREFIX) or section_name == "Observatory.Camera":
            continue
        section = dict(config.app_config[section_name])
        if "clip_ceiling_adu" not in section:
            # A camera section can exist purely for per-setup facts (pixel
            # size, grating geometry) without ever becoming a profile --
            # only a section with model-level facts is one.
            continue
        try:
            profiles.append(_build_camera_profile(section_name, section))
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
    return _load_profiles_from_config(config or get_configuration())


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
