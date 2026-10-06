"""Purpose: Equipment Catalog Resolution.

Description: Resolves the configured `Telescope`/`Camera` specifications
and which of each is active, from `astrometrics.config.toml`. Foundation
concern -- both peer functions need the active specifications; changing
which entry is active is a Control operation
(`Wayfinding_Library_Architecture.md` §2.2.2, §2.5.2).

Cameras and telescopes share one multi-section config pattern (the
camera side matches
`observatorylib.equipment_configuration.EquipmentConfigurationManager`).
For telescopes, an `[Observatory.Telescope]` base section carries a
comma-separated `models` list and an `active_telescope` key, with each
named telescope's fields in `[Observatory.Telescope.<Name>]`, falling
back through `Telescope.<Name>`, `Observatory.Telescope`, then
`Telescope`, mirroring `AppConfiguration.get_camera_config`'s exact
fallback chain.

Per `Wayfinding_Library_Architecture.md` §2.2.2 ("Documented Safety
Fallback"): a per-rig altitude limit is preferred when present; when
absent, resolution falls back to the single global
`[Observatory.Constraints]` section this library previously used
exclusively, so a rig that has not yet been given its own section does
not silently change behavior.

Where no `models` list is configured at all -- today's state, before an
operator has split telescopes into named sections -- resolution falls
back to constructing exactly one `Telescope` from the flat
`[Observatory.Telescope]` section's existing `focal_length_mm`/
`focal_ratio`, named `_SINGLE_TELESCOPE_FALLBACK_NAME`.
"""

import logging

from astrometricslib import ConfigurationError
from wayfindinglib.models.equipment_and_site.equipment import Camera, EquipmentCatalog, GuideScope, Telescope

logger = logging.getLogger(__name__)

TELESCOPE_SECTION = "Observatory.Telescope"
ACTIVE_TELESCOPE_KEY = "active_telescope"
CAMERA_SECTION = "Observatory.Camera"
ACTIVE_CAMERA_KEY = "default_primary_camera"
ACTIVE_GUIDE_CAMERA_KEY = "default_guide_camera"
_SECONDARY_CAMERA_KEY = "default_secondary_camera"
_SINGLE_TELESCOPE_FALLBACK_NAME = "Apertura 75Q"
GUIDE_SCOPE_SECTION = "Observatory.GuideScope"
ACTIVE_GUIDE_SCOPE_KEY = "active_guide_scope"

_BOOL_TRUE_STRINGS = {"true", "1", "yes", "on"}


def _as_bool(value: str | bool | None, default: bool) -> bool:
    """Parse a config string as a boolean, tolerant of common spellings.

    Returns
    -------
    parsed : `bool`
        The parsed value, or `default` if `value` is `None`.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in _BOOL_TRUE_STRINGS


def _telescope_section_for_name(config, telescope_name: str) -> dict[str, str]:  # ruff: ignore[missing-type-function-argument]
    """Return the resolved config section dict for a named telescope.

    Checked in order of ``Observatory.Telescope.<name>``, then
    ``Observatory.Telescope``.

    Returns
    -------
    section : `dict` [`str`, `str`]
        The resolved config section, or an empty dict if none matched.
    """
    for section in [
        f"{TELESCOPE_SECTION}.{telescope_name}",
        TELESCOPE_SECTION,
    ]:
        if section in config.app_config:
            return dict(config.app_config[section])
    return {}


def _build_telescope(config, telescope_name: str, section: dict[str, str]) -> Telescope | None:  # ruff: ignore[missing-type-function-argument]
    """Construct a `Telescope` from a resolved config section.

    Per-rig altitude limits fall back to the global
    `[Observatory.Constraints]` section when absent, per the
    "Documented Safety Fallback" invariant.

    Returns
    -------
    telescope : `Telescope` or `None`
        The constructed telescope, or `None` if `section` lacks a
        parseable, positive focal length.
    """
    try:
        focal_length_mm = float(section.get("focal_length_mm", "0.0"))
        focal_ratio = float(section.get("focal_ratio", "0.0"))
    except TypeError, ValueError:
        logger.warning("Skipping telescope '%s': unparseable focal geometry", telescope_name)
        return None
    if focal_length_mm <= 0.0:
        logger.debug("Skipping telescope '%s': no configured focal length", telescope_name)
        return None

    min_altitude_deg = float(section.get("min_altitude_deg", config.get_min_altitude()))
    max_altitude_deg = float(section.get("max_altitude_deg", config.get_max_altitude()))

    try:
        return Telescope(
            id=telescope_name,
            name=telescope_name,
            focal_length_mm=focal_length_mm,
            focal_ratio=focal_ratio,
            altitude_limits_enabled=_as_bool(section.get("altitude_limits_enabled"), default=True),
            min_altitude_deg=min_altitude_deg,
            max_altitude_deg=max_altitude_deg,
            hour_angle_limits_enabled=_as_bool(section.get("hour_angle_limits_enabled"), default=False),
            max_hour_angle_hours=float(section.get("max_hour_angle_hours", "2.0")),
            flip_hour_angle_deg=float(section.get("flip_hour_angle_deg", "1.0")),
            mount_protocol=section.get("mount_protocol", "indi"),
            focuser_protocol=section.get("focuser_protocol", "indi"),
            filter_wheel_protocol=section.get("filter_wheel_protocol", "indi"),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Skipping telescope '%s': %s", telescope_name, exc)
        return None


def list_telescopes(config) -> list[Telescope]:  # ruff: ignore[missing-type-function-argument]
    """Return every configured `Telescope`.

    Reads the comma-separated ``models`` list under
    ``[Observatory.Telescope]``. Where no such list is configured,
    falls back to one telescope built from the existing flat
    ``[Observatory.Telescope]`` section, so an unconfigured catalog
    behaves exactly as the single pre-existing telescope did.

    Returns
    -------
    telescopes : `list` [`Telescope`]
        Every configured telescope.
    """
    models_str = config.get_value(TELESCOPE_SECTION, "models")
    telescope_names = [m.strip() for m in models_str.split(",") if m.strip()] if models_str else []

    if not telescope_names:
        section = _telescope_section_for_name(config, _SINGLE_TELESCOPE_FALLBACK_NAME)
        telescope = _build_telescope(config, _SINGLE_TELESCOPE_FALLBACK_NAME, section)
        return [telescope] if telescope is not None else []

    telescopes = []
    for name in telescope_names:
        section = _telescope_section_for_name(config, name)
        telescope = _build_telescope(config, name, section)
        if telescope is not None:
            telescopes.append(telescope)
    return telescopes


def get_active_telescope_id(config) -> str | None:  # ruff: ignore[missing-type-function-argument]
    """Return the configured active telescope id, or `None` if unset.

    Returns
    -------
    telescope_id : `str` or `None`
        The configured active telescope id, or `None` if unset.
    """
    return config.get_value(TELESCOPE_SECTION, ACTIVE_TELESCOPE_KEY)


def list_cameras(config) -> list[Camera]:  # ruff: ignore[missing-type-function-argument]
    """Return every configured `Camera`, reusing the camera-catalog reader.

    Returns
    -------
    cameras : `list` [`Camera`]
        Every configured camera with parseable sensor parameters.
    """
    cameras = []
    for camera_name in config.get_available_cameras():
        camera_data = config.get_camera_config(camera_name)
        try:
            pixel_size_um = float(camera_data.get("pixel_size_μm") or camera_data.get("pixel_size_um") or 0.0)
            sensor_width_px = int(camera_data.get("sensor_width_px", 0))
            sensor_height_px = int(camera_data.get("sensor_height_px", 0))
        except TypeError, ValueError:
            logger.warning("Skipping camera '%s': unparseable sensor parameters", camera_name)
            continue
        if pixel_size_um <= 0.0 or sensor_width_px <= 0 or sensor_height_px <= 0:
            logger.debug("Skipping camera '%s': missing sensor parameters", camera_name)
            continue
        cameras.append(
            Camera(
                id=camera_name,
                name=camera_name,
                pixel_size_um=pixel_size_um,
                sensor_width_px=sensor_width_px,
                sensor_height_px=sensor_height_px,
                protocol=camera_data.get("protocol", "indi"),
            )
        )
    return cameras


def get_active_camera_id(config) -> str | None:  # ruff: ignore[missing-type-function-argument]
    """Return the configured active camera identifier, or `None` if unset.

    Returns
    -------
    camera_id : `str` or `None`
        The configured active camera id, or `None` if unset.
    """
    return config.get_value(CAMERA_SECTION, ACTIVE_CAMERA_KEY)


def _build_guide_scope(guide_scope_name: str, section: dict[str, str]) -> GuideScope | None:
    """Construct a `GuideScope` from a resolved config section.

    Unlike telescopes/cameras, an unconfigured guide scope is the
    common case (guiding through the main OTA) -- there is no
    single-entry fallback the way `_SINGLE_TELESCOPE_FALLBACK_NAME`
    provides, so an absent or unparseable section simply yields no
    guide scope.

    Returns
    -------
    guide_scope : `GuideScope` or `None`
        The constructed guide scope, or `None` if `section` lacks a
        parseable, positive focal length.
    """
    try:
        focal_length_mm = float(section.get("focal_length_mm", "0.0"))
        aperture_mm_str = section.get("aperture_mm")
        aperture_mm = float(aperture_mm_str) if aperture_mm_str else None
    except TypeError, ValueError:
        logger.warning("Skipping guide scope '%s': unparseable optics", guide_scope_name)
        return None
    if focal_length_mm <= 0.0:
        logger.debug("Skipping guide scope '%s': no configured focal length", guide_scope_name)
        return None

    try:
        return GuideScope(
            id=guide_scope_name,
            name=guide_scope_name,
            focal_length_mm=focal_length_mm,
            aperture_mm=aperture_mm,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Skipping guide scope '%s': %s", guide_scope_name, exc)
        return None


def list_guide_scopes(config) -> list[GuideScope]:  # ruff: ignore[missing-type-function-argument]
    """Return every configured `GuideScope`.

    Reads the comma-separated ``models`` list under
    ``[Observatory.GuideScope]``, each named guide scope's fields in
    ``[Observatory.GuideScope.<name>]``. Unlike telescopes, there is no
    fallback to a single unnamed entry -- an unconfigured operator has
    no guide scope, which is the correct default (§1a).

    Returns
    -------
    guide_scopes : `list` [`GuideScope`]
        Every configured guide scope.
    """
    models_str = config.get_value(GUIDE_SCOPE_SECTION, "models")
    guide_scope_names = [m.strip() for m in models_str.split(",") if m.strip()] if models_str else []

    guide_scopes = []
    for name in guide_scope_names:
        section_key = f"{GUIDE_SCOPE_SECTION}.{name}"
        section = dict(config.app_config[section_key]) if section_key in config.app_config else {}
        guide_scope = _build_guide_scope(name, section)
        if guide_scope is not None:
            guide_scopes.append(guide_scope)
    return guide_scopes


def get_active_guide_scope_id(config) -> str | None:  # ruff: ignore[missing-type-function-argument]
    """Return the configured active guide scope id, or `None` if unset.

    Returns
    -------
    guide_scope_id : `str` or `None`
        The configured active guide scope id, or `None` if unset --
        the common case of guiding through the main OTA.
    """
    return config.get_value(GUIDE_SCOPE_SECTION, ACTIVE_GUIDE_SCOPE_KEY)


def get_active_guide_camera_id(config) -> str | None:  # ruff: ignore[missing-type-function-argument]
    """Return the configured guide camera identifier, or `None` if unset.

    Reads ``default_guide_camera`` under ``[Observatory.Camera]``. A
    config written before that key existed names its second camera
    with ``default_secondary_camera``; that camera is the guide camera
    in every setup this library has been used with, so it is used when
    the explicit key is absent.

    Returns
    -------
    guide_camera_id : `str` or `None`
        The configured guide camera id, or `None` when the main camera
        also does the guiding.
    """
    return config.get_value(CAMERA_SECTION, ACTIVE_GUIDE_CAMERA_KEY) or config.get_value(
        CAMERA_SECTION, _SECONDARY_CAMERA_KEY
    )


def _validate_protocol(entity_kind: str, entity_name: str, field_name: str, protocol: str) -> None:
    """Raise a clear error if a configured protocol name has no driver.

    Checked at catalog-load time rather than left to fail the first
    time a `*_driver` property happens to be accessed mid-session.

    Raises
    ------
    ConfigurationError
        Raised if `protocol` is not a key in the corresponding
        protocol-driver registry.
    """
    from wayfindinglib.drivers.protocols import registry

    registry_by_field = {
        "mount_protocol": registry.build_mount_driver_registry,
        "focuser_protocol": registry.build_focuser_driver_registry,
        "filter_wheel_protocol": registry.build_filter_wheel_driver_registry,
        "protocol": registry.build_camera_driver_registry,
    }
    valid_protocols = set(registry_by_field[field_name]())
    if protocol not in valid_protocols:
        raise ConfigurationError(
            f"{entity_kind} '{entity_name}' has {field_name}='{protocol}', which has no "
            f"registered driver. Valid choices: {sorted(valid_protocols)}"
        )


def get_equipment_catalog(config) -> EquipmentCatalog:  # ruff: ignore[missing-type-function-argument]
    """Return the full resolved `EquipmentCatalog`.

    The active identifiers default to the first configured entry of
    each kind when no active selection is configured, matching the
    the "first available" fallback of
    `EquipmentConfigurationManager.get_active_camera_profile`.

    Every configured protocol-selection field is validated against its
    driver registry here, so a misconfigured protocol name fails at
    config load, not the first time a `*_driver` property is accessed.

    Returns
    -------
    catalog : `EquipmentCatalog`
        The full resolved catalog of telescopes and cameras.
    """
    telescopes = list_telescopes(config)
    cameras = list_cameras(config)
    guide_scopes = list_guide_scopes(config)

    for telescope in telescopes:
        _validate_protocol("Telescope", telescope.name, "mount_protocol", telescope.mount_protocol)
        _validate_protocol("Telescope", telescope.name, "focuser_protocol", telescope.focuser_protocol)
        _validate_protocol(
            "Telescope", telescope.name, "filter_wheel_protocol", telescope.filter_wheel_protocol
        )
    for camera in cameras:
        _validate_protocol("Camera", camera.name, "protocol", camera.protocol)

    active_telescope_id = get_active_telescope_id(config)
    if active_telescope_id not in {t.id for t in telescopes}:
        active_telescope_id = telescopes[0].id if telescopes else None

    active_camera_id = get_active_camera_id(config)
    if active_camera_id not in {c.id for c in cameras}:
        active_camera_id = cameras[0].id if cameras else None

    active_guide_scope_id = get_active_guide_scope_id(config)
    if active_guide_scope_id not in {g.id for g in guide_scopes}:
        # Unlike telescopes/cameras, no "first available" fallback: an
        # unconfigured guide scope means guiding through the main OTA,
        # not an oversight to silently correct.
        active_guide_scope_id = None

    active_guide_camera_id = get_active_guide_camera_id(config)
    if active_guide_camera_id not in {c.id for c in cameras}:
        # No "first available" fallback, for the same reason as the guide
        # scope: an unset guide camera means the main camera guides.
        active_guide_camera_id = None

    return EquipmentCatalog(
        id="default",
        telescopes=telescopes,
        cameras=cameras,
        guide_scopes=guide_scopes,
        active_telescope_id=active_telescope_id,
        active_camera_id=active_camera_id,
        active_guide_scope_id=active_guide_scope_id,
        active_guide_camera_id=active_guide_camera_id,
    )
