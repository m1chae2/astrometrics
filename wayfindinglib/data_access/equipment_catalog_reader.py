"""Purpose: Equipment Catalog Resolution.

Description: Builds the `EquipmentCatalog`: the telescopes, cameras and
guide scopes listed in `astrometrics.config.toml`, and which of each is
active. The equipment itself is read by the core library
(`AppConfiguration.get_telescopes`, `get_cameras` and `get_guide_scopes`,
which return the core `Telescope`, `Camera` and `GuideScope` models); this
module adds the active choice and checks each device protocol against the
drivers this library has. Foundation concern -- both peer functions need the
active specifications; changing which entry is active is a Control
operation (`Wayfinding_Library_Architecture.md` Sections 3.2 and 6.1).

When the config lists setups (``[Observatory.Setups]``), the active
telescope and camera are those of the active setup. Otherwise
``[Observatory.Telescope] active_telescope`` and ``[Observatory.Camera]
default_primary_camera`` name them.

`get_meridian_flip_delay_min` reads the planning code's meridian-flip
delay, the minutes past the meridian at which a target needs a flip.
"""

import logging
from typing import Any

from astrometricslib import AppConfiguration, Camera, GuideScope, Telescope
from astrometricslib.exceptions import ConfigurationError
from astrometricslib.parameters import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.models.equipment_and_site.driver_protocols import (
    CAMERA_PROTOCOLS,
    FILTER_WHEEL_PROTOCOLS,
    FOCUSER_PROTOCOLS,
    GUIDING_PROTOCOLS,
    MOUNT_PROTOCOLS,
)
from wayfindinglib.models.equipment_and_site.equipment import EquipmentCatalog

logger = logging.getLogger(__name__)

_VALID_PROTOCOLS_BY_FIELD = {
    "mount_protocol": MOUNT_PROTOCOLS,
    "focuser_protocol": FOCUSER_PROTOCOLS,
    "filter_wheel_protocol": FILTER_WHEEL_PROTOCOLS,
    "guiding_protocol": GUIDING_PROTOCOLS,
    "protocol": CAMERA_PROTOCOLS,
}
"""Equipment field name -> the protocol names valid for it."""

TELESCOPE_SECTION = "Observatory.Telescope"
ACTIVE_TELESCOPE_KEY = "active_telescope"
CAMERA_SECTION = "Observatory.Camera"
ACTIVE_CAMERA_KEY = "default_primary_camera"
ACTIVE_GUIDE_CAMERA_KEY = "default_guide_camera"
_SECONDARY_CAMERA_KEY = "default_secondary_camera"
GUIDE_SCOPE_SECTION = "Observatory.GuideScope"
ACTIVE_GUIDE_SCOPE_KEY = "active_guide_scope"

DEFAULT_MERIDIAN_FLIP_DELAY_MIN = TunedParameter(
    name="wayfinding.default_meridian_flip_delay_min",
    default=5.0,
    unit="min",
    description="Meridian-flip delay used when the configuration does not set one.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.EQUIPMENT_PROFILE,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D131",
)
"""Meridian-flip delay used when the configuration does not set one."""


def list_telescopes(config: AppConfiguration) -> list[Telescope]:
    """Return every configured `Telescope`.

    Returns
    -------
    telescopes : `list` [`Telescope`]
        Every telescope the settings list (`AppConfiguration.get_telescopes`).
    """
    return config.get_telescopes()


def list_cameras(config: AppConfiguration) -> list[Camera]:
    """Return every configured `Camera`.

    Returns
    -------
    cameras : `list` [`Camera`]
        Every camera the settings list (`AppConfiguration.get_cameras`).
    """
    return config.get_cameras()


def list_guide_scopes(config: AppConfiguration) -> list[GuideScope]:
    """Return every configured `GuideScope`.

    Returns
    -------
    guide_scopes : `list` [`GuideScope`]
        Every guide scope the settings list
        (`AppConfiguration.get_guide_scopes`).
    """
    return config.get_guide_scopes()


def get_active_telescope_id(config: AppConfiguration) -> str | None:
    """Return the configured active telescope id, or `None` if unset.

    Returns
    -------
    telescope_id : `str` or `None`
        The active setup's optic when the config has setups, otherwise the
        configured active telescope id, or `None` if unset.
    """
    setups = config.get_observatory_setups()
    if setups.optics and setups.setups:
        active = setups.active_setup()
        return active.optic_name if active is not None else None
    return config.get_value(TELESCOPE_SECTION, ACTIVE_TELESCOPE_KEY)


def get_active_camera_id(config: AppConfiguration) -> str | None:
    """Return the configured active camera identifier, or `None` if unset.

    Returns
    -------
    camera_id : `str` or `None`
        The active setup's camera when the config has setups (written as the
        camera list writes it), otherwise the configured active camera id, or
        `None` if unset.
    """
    setups = config.get_observatory_setups()
    if setups.optics and setups.setups:
        active = setups.active_setup()
        return camera_id_for(config, active.camera_name) if active is not None else None
    return config.get_value(CAMERA_SECTION, ACTIVE_CAMERA_KEY)


def camera_key(camera_name: str) -> str:
    """Reduce a camera name to lowercase letters and digits.

    Returns
    -------
    key : `str`
        The name without spaces, punctuation or case.
    """
    return "".join(character for character in camera_name.lower() if character.isalnum())


def camera_id_for(config: AppConfiguration, camera_name: str) -> str:
    """Return the camera list's spelling of a camera named in a setup.

    Returns
    -------
    camera_id : `str`
        The matching name from ``[Observatory.Camera] models`` (spaces and
        case ignored), or ``camera_name`` itself when none matches.
    """
    key = camera_key(camera_name)
    return next((name for name in config.get_available_cameras() if camera_key(name) == key), camera_name)


def get_active_guide_scope_id(config: AppConfiguration) -> str | None:
    """Return the configured active guide scope id, or `None` if unset.

    Returns
    -------
    guide_scope_id : `str` or `None`
        The configured active guide scope id, or `None` if unset --
        the common case of guiding through the main OTA.
    """
    return config.get_value(GUIDE_SCOPE_SECTION, ACTIVE_GUIDE_SCOPE_KEY)


def get_active_guide_camera_id(config: AppConfiguration) -> str | None:
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
    time a `*_driver` property happens to be accessed mid-session. The
    valid names come from `driver_protocols`, a Foundation module, so
    this check loads no hardware driver; a test keeps those names equal
    to the driver registry's keys.

    Raises
    ------
    ConfigurationError
        Raised if `protocol` is not a valid name for `field_name`.
    """
    valid_protocols = _VALID_PROTOCOLS_BY_FIELD[field_name]
    if protocol not in valid_protocols:
        raise ConfigurationError(
            f"{entity_kind} '{entity_name}' has {field_name}='{protocol}', which has no "
            f"registered driver. Valid choices: {sorted(valid_protocols)}"
        )


def get_equipment_catalog(config: AppConfiguration) -> EquipmentCatalog:
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
        _validate_protocol("Telescope", telescope.name, "guiding_protocol", telescope.guiding_protocol)
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


def get_meridian_flip_delay_min(config: Any) -> float:
    """Return the meridian-flip delay the planning code uses, in minutes.

    Read from ``meridian_flip_delay_min`` in ``[Observatory.Telescope]``.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration.

    Returns
    -------
    delay_min : `float`
        Minutes past the meridian at which a target needs a flip, or
        `DEFAULT_MERIDIAN_FLIP_DELAY_MIN` when unset.
    """
    return float(
        config.app_config.get(
            TELESCOPE_SECTION,
            "meridian_flip_delay_min",
            fallback=str(resolve_parameter(DEFAULT_MERIDIAN_FLIP_DELAY_MIN).value),
        )
    )
