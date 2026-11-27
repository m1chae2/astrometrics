"""Read the telescopes, cameras and guide scopes listed in the settings file.

The settings file (``astrometrics.config.toml``) lists the equipment the
observatory owns. This module turns its sections into the core equipment
models (`Telescope`, `Camera`, `GuideScope`). `AppConfiguration` offers the
results as `get_telescopes`, `get_cameras` and `get_guide_scopes`; the
observatory control (wayfindinglib) builds its equipment catalog from them,
and the library seeds its observatory configurations from them.

When the file lists setups (``[Observatory.Setups]``, read through
`AppConfiguration.get_observatory_setups`), there is one telescope per
optic, and a telescope's safety limits and device protocols are read from the
``[Observatory.Mount.<name>]`` section of the setup's mount, falling back to
the telescope sections below.

With no setups, an ``[Observatory.Telescope]`` section carries a
comma-separated ``models`` list, with each named telescope's values in
``[Observatory.Telescope.<Name>]``, falling back to
``[Observatory.Telescope]``.
With no ``models`` list either, one telescope is built from the flat
``[Observatory.Telescope]`` section, named `SINGLE_TELESCOPE_FALLBACK_NAME`.

A per-telescope altitude limit is used when present; otherwise the global
``[Observatory.Constraints]`` section gives it
(`Wayfinding_Library_Implementation.md` Section 6.2).
"""

import logging
from typing import Any

from astrometricslib.models.observatory_configuration import Camera, GuideScope, Telescope

logger = logging.getLogger(__name__)

TELESCOPE_SECTION = "Observatory.Telescope"
"""The settings section that lists the telescopes when there are no setups."""

GUIDE_SCOPE_SECTION = "Observatory.GuideScope"
"""The settings section that lists the guide scopes."""

MOUNT_SECTION_PREFIX = "Observatory.Mount"
"""Prefix of the section that holds one mount's limits and protocols."""

SINGLE_TELESCOPE_FALLBACK_NAME = "Apertura 75Q"
"""Name of the one telescope built from a flat ``[Observatory.Telescope]``."""

_BOOL_TRUE_STRINGS = {"true", "1", "yes", "on"}


def _as_bool(value: str | bool | None, default: bool) -> bool:
    """Read a setting as a yes-or-no value, accepting common spellings.

    Parameters
    ----------
    value : `str`, `bool` or `None`
        The setting.
    default : `bool`
        The answer when the setting is missing.

    Returns
    -------
    parsed : `bool`
        The value, or `default` if `value` is `None`.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in _BOOL_TRUE_STRINGS


def _setups(config: Any) -> Any:
    """Read the setups, or `None` when the settings list no optics or setups.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.

    Returns
    -------
    setups : `ObservatorySetups` or `None`
        The setups, when there is at least one optic and one setup.
    """
    setups = config.get_observatory_setups()
    return setups if setups.optics and setups.setups else None


def _setup_for_optic(setups: Any, optic_name: str) -> Any:
    """Choose the setup whose mount carries an optic.

    The active setup wins when it uses the optic; otherwise the first setup
    that uses the optic and names a mount; otherwise the first that uses it.

    Parameters
    ----------
    setups : `ObservatorySetups`
        The setups.
    optic_name : `str`
        The optic.

    Returns
    -------
    setup : `SetupConfig` or `None`
        The setup, or `None` when no setup uses the optic.
    """
    using = [setup for setup in setups.setups if setup.optic_name == optic_name]
    active = setups.active_setup()
    if active is not None and active in using:
        return active
    with_mount = [setup for setup in using if setup.mount_name]
    return (with_mount or using or [None])[0]


def mount_section_name_for(config: Any, telescope_name: str) -> str | None:
    """Give the mount section that holds an optic's safety limits.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    telescope_name : `str`
        The optic.

    Returns
    -------
    section_name : `str` or `None`
        ``Observatory.Mount.<mount>`` of the setup that carries the optic, or
        `None` when the settings have no setups or that setup names no mount.
    """
    setups = _setups(config)
    if setups is None:
        return None
    setup = _setup_for_optic(setups, telescope_name)
    if setup is None or not setup.mount_name:
        return None
    return f"{MOUNT_SECTION_PREFIX}.{setup.mount_name}"


def _section_values(config: Any, section_name: str | None) -> dict[str, str]:
    """Read a whole settings section, or nothing when it does not exist.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    section_name : `str` or `None`
        The section.

    Returns
    -------
    values : `dict` [`str`, `str`]
        The section's keys and values.
    """
    if section_name is None or section_name not in config.app_config:
        return {}
    return dict(config.app_config[section_name])


def telescope_section_name_for(config: Any, telescope_name: str) -> str | None:
    """Give the name of the settings section a named telescope reads from.

    Checked in order: ``Observatory.Telescope.<name>``, then
    ``Observatory.Telescope``. A writer that targets this name changes
    exactly what the reader sees.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    telescope_name : `str`
        The telescope.

    Returns
    -------
    section_name : `str` or `None`
        The first section that exists, or `None` if neither does.
    """
    for section in [f"{TELESCOPE_SECTION}.{telescope_name}", TELESCOPE_SECTION]:
        if section in config.app_config:
            return section
    return None


def limits_section_name_for(config: Any, telescope_name: str) -> str | None:
    """Give the settings section that holds a telescope's safety limits.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    telescope_name : `str`
        The telescope.

    Returns
    -------
    section_name : `str` or `None`
        The mount section of the telescope's setup when there is one, else
        the telescope's own section, else `None`.
    """
    return mount_section_name_for(config, telescope_name) or telescope_section_name_for(
        config, telescope_name
    )


def _build_telescope(config: Any, telescope_name: str, section: dict[str, str]) -> Telescope | None:
    """Build a `Telescope` from its settings section.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, for the global altitude limits.
    telescope_name : `str`
        The telescope's name, also used as its id.
    section : `dict` [`str`, `str`]
        The telescope's settings.

    Returns
    -------
    telescope : `Telescope` or `None`
        The telescope, or `None` if `section` has no readable, positive
        focal length.
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
            guiding_protocol=section.get("guiding_protocol", "phd2"),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Skipping telescope '%s': %s", telescope_name, exc)
        return None


def read_telescopes(config: Any) -> list[Telescope]:
    """Read every telescope the settings list.

    With setups, there is one telescope per optic. Otherwise the
    comma-separated ``models`` list under ``[Observatory.Telescope]`` names
    them; with no such list, one telescope is built from the flat
    ``[Observatory.Telescope]`` section.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.

    Returns
    -------
    telescopes : `list` [`Telescope`]
        Every telescope with a readable focal length.
    """
    setups = _setups(config)
    if setups is not None:
        telescopes = []
        for optic in setups.optics:
            # The optic gives the focal geometry; the mount section, then the
            # old telescope section, give the limits and protocols.
            section_name = telescope_section_name_for(config, optic.name)
            section = {
                **_section_values(config, section_name),
                **_section_values(config, mount_section_name_for(config, optic.name)),
                "focal_length_mm": str(optic.focal_length_mm),
                "focal_ratio": str(optic.focal_ratio or 0.0),
            }
            telescope = _build_telescope(config, optic.name, section)
            if telescope is not None:
                telescopes.append(telescope)
        return telescopes

    models_text = config.get_value(TELESCOPE_SECTION, "models")
    names = [name.strip() for name in models_text.split(",") if name.strip()] if models_text else []
    if not names:
        names = [SINGLE_TELESCOPE_FALLBACK_NAME]
    telescopes = []
    for name in names:
        section = _section_values(config, telescope_section_name_for(config, name))
        telescope = _build_telescope(config, name, section)
        if telescope is not None:
            telescopes.append(telescope)
    return telescopes


def read_cameras(config: Any) -> list[Camera]:
    """Read every camera the settings list.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.

    Returns
    -------
    cameras : `list` [`Camera`]
        Every camera with a readable pixel size and sensor size.
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


def _build_guide_scope(guide_scope_name: str, section: dict[str, str]) -> GuideScope | None:
    """Build a `GuideScope` from its settings section.

    Parameters
    ----------
    guide_scope_name : `str`
        The guide scope's name, also used as its id.
    section : `dict` [`str`, `str`]
        Its settings.

    Returns
    -------
    guide_scope : `GuideScope` or `None`
        The guide scope, or `None` if `section` has no readable, positive
        focal length.
    """
    try:
        focal_length_mm = float(section.get("focal_length_mm", "0.0"))
        aperture_text = section.get("aperture_mm")
        aperture_mm = float(aperture_text) if aperture_text else None
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


def read_guide_scopes(config: Any) -> list[GuideScope]:
    """Read every guide scope the settings list.

    The comma-separated ``models`` list under ``[Observatory.GuideScope]``
    names them, each with its values in ``[Observatory.GuideScope.<name>]``.
    With no list there is no guide scope, which is the common case
    (guiding through the main telescope).

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.

    Returns
    -------
    guide_scopes : `list` [`GuideScope`]
        Every guide scope with a readable focal length.
    """
    models_text = config.get_value(GUIDE_SCOPE_SECTION, "models")
    names = [name.strip() for name in models_text.split(",") if name.strip()] if models_text else []
    guide_scopes = []
    for name in names:
        guide_scope = _build_guide_scope(name, _section_values(config, f"{GUIDE_SCOPE_SECTION}.{name}"))
        if guide_scope is not None:
            guide_scopes.append(guide_scope)
    return guide_scopes
