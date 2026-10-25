"""Purpose: Equipment Selection.

Description: Changes which configured telescope or camera is active,
per `Wayfinding_Library_Architecture.md` Sections 3.2 and 6.1: reading the
equipment catalog is a Foundation concern both Control and Planning
need, but *changing* which entry is active is a Control operation, so
it lives here rather than in `data_access/equipment_catalog_reader.py`.

Selection validates before it records. An unrecognized id is rejected
instead of being saved, so a typo cannot leave the active selection
pointing at nothing.

`list_camera_profiles` and `get_equipment_configuration` read the same
equipment catalog and report it in the camelCase dictionary shapes the
frontend's `observatory:list_cameras` and
`observatory:get_equipment_configuration` RPC methods return.
"""

import logging
from typing import Any

from astrometricslib import AppConfiguration
from wayfindinglib.data_access.equipment_catalog_reader import (
    ACTIVE_CAMERA_KEY,
    ACTIVE_TELESCOPE_KEY,
    CAMERA_SECTION,
    TELESCOPE_SECTION,
    get_equipment_catalog,
    list_cameras,
    list_telescopes,
)
from wayfindinglib.data_access.equipment_catalog_writer import build_telescope_limits_update
from wayfindinglib.models.equipment_and_site.equipment import Camera, EquipmentConfiguration

logger = logging.getLogger(__name__)


def set_active_telescope(
    config: AppConfiguration, telescope_id: str, limits: dict[str, float | bool] | None = None
) -> bool:
    """Record the active telescope, and optionally its safety limits.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration to update.
    telescope_id : `str`
        Must match the `id` of a `Telescope` returned by `list_telescopes`.
    limits : `dict`, optional
        New safety limits for this telescope: any of
        ``altitude_limits_enabled``, ``min_altitude_deg``,
        ``max_altitude_deg``, ``hour_angle_limits_enabled``,
        ``max_hour_angle_hours`` and ``flip_hour_angle_deg``. They are
        written only to the config section that already holds this
        telescope's settings, and a section is never created. A
        per-telescope minimum or maximum altitude overrides the global
        ``[Observatory.Constraints]`` values. The limits and the active
        choice are saved together.

    Returns
    -------
    activated : `bool`
        `True` if `telescope_id` was recognized and saved, `False`
        otherwise -- the config is left unchanged in that case.

    Notes
    -----
    Invalid `limits` (not a limit key, minimum above maximum, a value out
    of range), or a telescope with no config section to write to, raise
    `ValueError` and save nothing.
    """
    known_ids = {telescope.id for telescope in list_telescopes(config)}
    if telescope_id not in known_ids:
        logger.warning("set_active_telescope: '%s' is not a known telescope", telescope_id)
        return False
    update: dict[str, dict[str, str]] = {}
    if limits:
        update = build_telescope_limits_update(config, telescope_id, limits)
    update.setdefault(TELESCOPE_SECTION, {})[ACTIVE_TELESCOPE_KEY] = telescope_id
    config.update_config(update)
    logger.info("Active telescope set to '%s'", telescope_id)
    return True


def set_active_camera(config: AppConfiguration, camera_id: str) -> bool:
    """Record a new active camera selection.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration to update.
    camera_id : `str`
        Must match the `id` of a `Camera` returned by `list_cameras`.

    Returns
    -------
    activated : `bool`
        `True` if `camera_id` was recognized and saved, `False`
        otherwise -- the config is left unchanged in that case.
    """
    known_ids = {camera.id for camera in list_cameras(config)}
    if camera_id not in known_ids:
        logger.warning("set_active_camera: '%s' is not a known camera", camera_id)
        return False
    config.update_config({CAMERA_SECTION: {ACTIVE_CAMERA_KEY: camera_id}})
    logger.info("Active camera set to '%s'", camera_id)
    return True


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


def list_camera_profiles(config: Any) -> list[dict[str, Any]]:
    """Return every configured camera's sensor profile.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration.

    Returns
    -------
    profiles : `list` [`dict`]
        One camera profile per configured camera, in the shape
        `_camera_profile` gives.
    """
    return [_camera_profile(camera) for camera in list_cameras(config)]


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
    configuration = EquipmentConfiguration(telescope=telescope, camera=camera)
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
