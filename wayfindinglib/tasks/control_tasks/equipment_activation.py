"""Purpose: Equipment Selection.

Description: Reads and changes which observatory configuration (the core
library's record of one set of equipment used together) is active, and saves
the active telescope's safety limits, per `Wayfinding_Library_Architecture.md`
Sections 3.2 and 6.1: reading the equipment catalog is a Foundation concern
both Control and Planning need, but *changing* which entry is active is a
Control operation, so it lives here rather than in
`data_access/equipment_catalog_reader.py`.

One configuration is active at a time. Making one active changes the
settings so its telescope and camera are the catalog's active ones (with
setups, the setup that pairs them becomes active). Selection checks before it
records: equipment the settings do not list is refused instead of saved, so a
typo cannot leave the active selection pointing at nothing.

`list_camera_profiles` and `get_equipment_configuration` read the same
equipment catalog and report it in the camelCase dictionary shapes the
frontend's `observatory:list_cameras` and
`observatory:get_equipment_configuration` RPC methods return.
"""

import logging
from datetime import date
from typing import Any

from astrometricslib import AppConfiguration, Camera, FilterWheel, Mount, ObservatoryConfiguration
from astrometricslib.exceptions import ConfigurationError, InvalidArgumentError
from wayfindinglib.data_access.equipment_catalog_reader import (
    ACTIVE_CAMERA_KEY,
    ACTIVE_GUIDE_CAMERA_KEY,
    ACTIVE_GUIDE_SCOPE_KEY,
    ACTIVE_TELESCOPE_KEY,
    CAMERA_SECTION,
    GUIDE_SCOPE_SECTION,
    TELESCOPE_SECTION,
    camera_id_for,
    camera_key,
    get_equipment_catalog,
    list_cameras,
    list_telescopes,
)
from wayfindinglib.data_access.equipment_catalog_writer import build_telescope_limits_update
from wayfindinglib.models.equipment_and_site.camera_noise import (
    CameraNoiseSetting,
    CameraNoiseValue,
    NoiseEvidenceStatus,
)

logger = logging.getLogger(__name__)


SETUPS_SECTION = "Observatory.Setups"
ACTIVE_SETUP_KEY = "active"
CONFIGURATION_SECTION = "Observatory.Configuration"
"""The settings section that names the active observatory configuration."""
ACTIVE_CONFIGURATION_KEY = "active"
"""The key in `CONFIGURATION_SECTION` that holds the active one's id."""


def live_configuration(config: AppConfiguration) -> ObservatoryConfiguration | None:
    """Describe the equipment the settings make active, as a configuration.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration.

    Returns
    -------
    configuration : `ObservatoryConfiguration` or `None`
        The active telescope, camera, guide scope and guide camera of the
        equipment catalog, with the active setup's mount and filters, or
        `None` when no telescope or no camera is active.
    """
    catalog = get_equipment_catalog(config)
    telescope = catalog.active_telescope()
    camera = catalog.active_camera()
    if telescope is None or camera is None:
        return None
    setups = config.get_observatory_setups()
    setup = setups.setup_for(camera.name, telescope.name, camera_key) if setups.setups else None
    return ObservatoryConfiguration(
        name=setup.name if setup is not None else "",
        telescope=telescope,
        camera=camera,
        guide_scope=catalog.active_guide_scope(),
        guide_camera=catalog.active_guide_camera(),
        mount=Mount(name=setup.mount_name) if setup is not None and setup.mount_name else None,
        filter_wheel=(
            FilterWheel(name=setup.name, filter_names=setup.filter_names)
            if setup is not None and setup.filter_names
            else None
        ),
    )


def _same_equipment(stored: ObservatoryConfiguration, live: ObservatoryConfiguration) -> bool:
    """Say whether a stored configuration has the live camera and telescope.

    Parameters
    ----------
    stored : `ObservatoryConfiguration`
        A stored configuration.
    live : `ObservatoryConfiguration`
        The equipment the settings make active.

    Returns
    -------
    same : `bool`
        `True` when the main camera (any spelling) and the telescope's name
        and focal length (to the millimeter) agree.
    """
    camera_names = {camera_key(name) for name in (stored.camera.name, *stored.camera.aliases)}
    return (
        camera_key(live.camera.name) in camera_names
        and stored.telescope.name == live.telescope.name
        and round(stored.telescope.focal_length_mm) == round(live.telescope.focal_length_mm)
    )


def get_active_configuration(config: AppConfiguration, astrometrics: Any) -> ObservatoryConfiguration | None:
    """Return the stored observatory configuration that is active now.

    The settings say which camera and telescope are active. The stored
    configuration named by ``[Observatory.Configuration] active`` is returned
    while it still has that equipment; otherwise the stored configuration
    with that equipment that is in use today (the newest when several are).
    When none is stored, one is created from the settings, so the equipment
    in use always has a record.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration.
    astrometrics : `Astrometrics`
        The science library, which keeps the configurations.

    Returns
    -------
    configuration : `ObservatoryConfiguration` or `None`
        The active configuration, or `None` when the settings make no
        telescope or no camera active.
    """
    live = live_configuration(config)
    if live is None:
        return None
    configurations = astrometrics.observatories.configurations
    active_id = config.get_value(CONFIGURATION_SECTION, ACTIVE_CONFIGURATION_KEY)
    if active_id:
        stored = configurations.get(active_id)
        if stored is not None and _same_equipment(stored, live):
            return stored
    candidates = [
        stored for stored in configurations.query(time=date.today()) if _same_equipment(stored, live)
    ]
    if candidates:
        return max(candidates, key=lambda stored: (stored.valid_from or date.min, stored.id))
    return configurations.create(live)


def set_active_configuration(config: AppConfiguration, configuration: ObservatoryConfiguration) -> None:
    """Make an observatory configuration the active one, and save the choice.

    The settings are changed so the configuration's telescope, camera, guide
    scope and guide camera are the active ones. When the settings list
    setups, the setup that pairs the camera with the telescope becomes the
    active setup. The configuration's id is saved too.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration to update.
    configuration : `ObservatoryConfiguration`
        A stored configuration.

    Raises
    ------
    InvalidArgumentError
        If the configuration is not stored, or the settings do not list its
        telescope or camera (or, with setups, no setup pairs them). Nothing
        is saved then.
    """
    if not configuration.id:
        raise InvalidArgumentError("Store the observatory configuration before making it active.")
    telescope = configuration.telescope
    camera_id = camera_id_for(config, configuration.camera.name)
    if telescope.id not in {known.id for known in list_telescopes(config)}:
        raise InvalidArgumentError(
            f"The settings list no telescope {telescope.id!r}, so it cannot be made active.",
            details={"telescope_id": telescope.id},
        )
    if camera_id not in {known.id for known in list_cameras(config)}:
        raise InvalidArgumentError(
            f"The settings list no camera {configuration.camera.name!r}, so it cannot be made active.",
            details={"camera": configuration.camera.name},
        )
    update: dict[str, dict[str, str]] = {
        CAMERA_SECTION: {
            ACTIVE_CAMERA_KEY: camera_id,
            ACTIVE_GUIDE_CAMERA_KEY: (
                camera_id_for(config, configuration.guide_camera.name) if configuration.guide_camera else ""
            ),
        },
        GUIDE_SCOPE_SECTION: {
            ACTIVE_GUIDE_SCOPE_KEY: configuration.guide_scope.id if configuration.guide_scope else ""
        },
        CONFIGURATION_SECTION: {ACTIVE_CONFIGURATION_KEY: configuration.id},
    }
    setups = config.get_observatory_setups()
    if setups.setups:
        setup = setups.setup_for(configuration.camera.name, telescope.name, camera_key)
        if setup is None:
            raise InvalidArgumentError(
                f"No setup in the settings pairs {configuration.camera.name!r} with {telescope.name!r}.",
                details={"camera": configuration.camera.name, "telescope_id": telescope.id},
            )
        update[SETUPS_SECTION] = {ACTIVE_SETUP_KEY: setup.name}
    else:
        update[TELESCOPE_SECTION] = {ACTIVE_TELESCOPE_KEY: telescope.id}
    config.update_config(update)
    logger.info("Active observatory configuration set to %r (%s)", configuration.name, configuration.id)


def set_pointing_limits(config: AppConfiguration, limits: dict[str, float | bool]) -> None:
    """Save new safety limits for the active telescope.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration to update.
    limits : `dict` [`str`, `float` or `bool`]
        Any of ``altitude_limits_enabled``, ``min_altitude_deg``,
        ``max_altitude_deg``, ``hour_angle_limits_enabled``,
        ``max_hour_angle_hours`` and ``flip_hour_angle_deg``. They are
        written only to the settings section that already holds the
        telescope's settings (the mount section of its setup, else its own
        section); a section is never created. A per-telescope minimum or
        maximum altitude overrides the global ``[Observatory.Constraints]``
        values.

    Raises
    ------
    InvalidArgumentError
        If no telescope is active, a key is not a limit, the values fail the
        `Telescope` checks (such as a minimum above the maximum), or the
        telescope has no settings section. Nothing is saved then.
    """
    telescope = get_equipment_catalog(config).active_telescope()
    if telescope is None:
        raise InvalidArgumentError("No telescope is active, so there are no limits to set.")
    try:
        update = build_telescope_limits_update(config, telescope.id, limits)
    except ValueError as problem:
        raise InvalidArgumentError(str(problem), details={"telescope_id": telescope.id}) from None
    config.update_config(update)
    logger.info("Safety limits of '%s' saved", telescope.id)


def _camera_profile(camera: Camera) -> dict[str, Any]:
    """Report one camera's sensor in the frontend's camera-profile shape.

    Returns
    -------
    profile : `dict`
        ``name``, ``pixelSizeUm``, ``sensorWidthPx`` and ``sensorHeightPx``.
    """
    return {
        "name": camera.name,
        "pixelSizeUm": camera.pixel_size_um,
        "sensorWidthPx": camera.sensor_width_px,
        "sensorHeightPx": camera.sensor_height_px,
    }


def _noise_value(recorded: Any) -> CameraNoiseValue | None:
    """Turn one profile number into a noise value with its status word.

    Parameters
    ----------
    recorded : `ProvenancedValue` or `None`
        The profile's number, with a ``provenance`` that has a ``kind`` and a
        ``source``.

    Returns
    -------
    value : `CameraNoiseValue` or `None`
        The number with its status, or `None` when nothing is recorded. Only
        a number someone measured is "measured"; a datasheet or assumed
        number is a design estimate.
    """
    if recorded is None:
        return None
    kind = str(recorded.provenance.kind)
    status = NoiseEvidenceStatus.MEASURED if kind == "measured" else NoiseEvidenceStatus.DESIGN_ESTIMATE
    return CameraNoiseValue(value=recorded.value, status=status, kind=kind, source=recorded.provenance.source)


def _noise_status(values: list[CameraNoiseValue]) -> NoiseEvidenceStatus:
    """Summarize how well a setting's recorded numbers are backed.

    Parameters
    ----------
    values : `list` [`CameraNoiseValue`]
        The numbers recorded for one setting (one or two).

    Returns
    -------
    status : `NoiseEvidenceStatus`
        Measured if all are measured, partly measured if some are, otherwise
        a design estimate.
    """
    measured_count = sum(value.status == NoiseEvidenceStatus.MEASURED for value in values)
    if measured_count == len(values):
        return NoiseEvidenceStatus.MEASURED
    return NoiseEvidenceStatus.PARTLY_MEASURED if measured_count else NoiseEvidenceStatus.DESIGN_ESTIMATE


def list_camera_noise(astrometrics: Any, camera_name: str) -> list[CameraNoiseSetting]:
    """Return the gain and read noise recorded for a camera.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``); it
        holds the camera profiles.
    camera_name : `str`
        The camera's name in the configuration.

    Returns
    -------
    settings : `list` [`CameraNoiseSetting`]
        One entry per gain setting, offset and binning in the camera's
        profile, in the order the profile lists them. Empty when the camera
        has no profile or no entries ("not measured").
    """
    try:
        profile = astrometrics.processing.calibration.camera_profile(camera_name)
    except ConfigurationError:
        logger.warning("Camera profiles could not be read for '%s'", camera_name, exc_info=True)
        return []
    if profile.is_generic_fallback:
        return []
    settings = []
    for entry in profile.sensor_noise:
        gain = _noise_value(entry.gain_e_per_adu)
        read_noise = _noise_value(entry.read_noise_e)
        settings.append(
            CameraNoiseSetting(
                camera_name=camera_name,
                gain_setting=entry.gain_setting,
                offset=entry.offset,
                binning=entry.binning,
                temperature_c=entry.temperature_c,
                gain=gain,
                read_noise=read_noise,
                status=_noise_status([value for value in (gain, read_noise) if value is not None]),
            )
        )
    return settings


def list_camera_profiles(config: Any, astrometrics: Any) -> list[dict[str, Any]]:
    """Return every configured camera's sensor profile and recorded noise.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration.
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``); it
        holds the camera profiles.

    Returns
    -------
    profiles : `list` [`dict`]
        One camera profile per configured camera, in the shape
        `_camera_profile` gives, plus ``sensorNoise``: the camera's
        `CameraNoiseSetting` entries as dictionaries with camelCase keys
        (empty when nothing is recorded).
    """
    return [
        {
            **_camera_profile(camera),
            "sensorNoise": [
                setting.model_dump(by_alias=True, mode="json")
                for setting in list_camera_noise(astrometrics, camera.name)
            ],
        }
        for camera in list_cameras(config)
    ]


def get_equipment_configuration(config: Any) -> dict[str, Any] | None:
    """Return the active telescope and camera with their imaging geometry.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration.

    Returns
    -------
    configuration : `dict` or `None`
        ``telescope`` (``name``, ``focalLengthMm``, ``focalRatio``),
        ``camera`` (a camera profile), ``plateScaleArcsecPerPx``,
        ``fovWidthDeg`` and ``fovHeightDeg``, or `None` if no telescope
        or no camera is configured.
    """
    catalog = get_equipment_catalog(config)
    telescope = catalog.active_telescope()
    camera = catalog.active_camera()
    if telescope is None or camera is None:
        return None
    configuration = ObservatoryConfiguration(telescope=telescope, camera=camera)
    return {
        "telescope": {
            "name": telescope.name,
            "focalLengthMm": telescope.focal_length_mm,
            "focalRatio": telescope.focal_ratio,
        },
        "camera": _camera_profile(camera),
        "plateScaleArcsecPerPx": round(configuration.plate_scale_arcsec_per_px, 4),
        "fovWidthDeg": round(configuration.fov_width_deg, 6),
        "fovHeightDeg": round(configuration.fov_height_deg, 6),
    }
