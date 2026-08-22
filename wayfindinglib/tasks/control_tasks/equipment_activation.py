"""Purpose: Equipment Selection.

Description: Changes which configured telescope or camera is active,
per `Wayfinding_Library_Architecture.md` §2.2.2 / §2.5.2: reading the
equipment catalog is a Foundation concern both Control and Planning
need, but *changing* which entry is active is a Control operation, so
it lives here rather than in `data_access/equipment_catalog_reader.py`.

Carries forward the validate-before-persist pattern of the deprecated
`observatorylib.equipment_configuration.EquipmentConfigurationManager.set_active_camera`:
an unrecognized id is rejected rather than silently persisted, so a
typo cannot leave the active selection pointing at nothing.

`list_camera_profiles`/`get_equipment_configuration` delegate to the
still-existing `observatorylib.equipment_configuration
.EquipmentConfigurationManager` (composition, not duplication, mirroring
`api.planning_registry.ObservationPlanning`'s Sky/Observation engines):
the frontend's `observatory:list_cameras`/`observatory
:get_equipment_configuration` RPC endpoints depend on that class's exact
`CameraProfile`/`EquipmentConfiguration` dict shapes, so reimplementing
against the new `Camera`/`Telescope` Foundation models here would change
a live UI contract.
"""

import logging
from typing import Any

from wayfindinglib.data_access.equipment_catalog_reader import (
    ACTIVE_CAMERA_KEY,
    ACTIVE_TELESCOPE_KEY,
    CAMERA_SECTION,
    TELESCOPE_SECTION,
    list_cameras,
    list_telescopes,
)

logger = logging.getLogger(__name__)


def set_active_telescope(config, telescope_id: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Persist a new active telescope selection.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration to update.
    telescope_id : `str`
        Must match the `id` of a `Telescope` returned by `list_telescopes`.

    Returns
    -------
    activated : `bool`
        `True` if `telescope_id` was recognized and saved, `False`
        otherwise -- the config is left unchanged in that case.
    """
    known_ids = {telescope.id for telescope in list_telescopes(config)}
    if telescope_id not in known_ids:
        logger.warning("set_active_telescope: '%s' is not a known telescope", telescope_id)
        return False
    config.update_config({TELESCOPE_SECTION: {ACTIVE_TELESCOPE_KEY: telescope_id}})
    logger.info("Active telescope set to '%s'", telescope_id)
    return True


def set_active_camera(config, camera_id: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Persist a new active camera selection.

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


def list_camera_profiles(config) -> list[dict[str, Any]]:  # ruff: ignore[missing-type-function-argument]
    """Return all camera profiles defined in the config as dicts.

    Returns
    -------
    profiles : `list` [`dict`]
        Serialized camera profile configurations.
    """
    from wayfindinglib.observatorylib.equipment_configuration import EquipmentConfigurationManager

    manager = EquipmentConfigurationManager(config)
    return [profile.model_dump() for profile in manager.list_camera_profiles()]


def get_equipment_configuration(config) -> dict[str, Any] | None:  # ruff: ignore[missing-type-function-argument]
    """Return the active equipment configuration with FOV geometry.

    Returns
    -------
    configuration : `dict` or `None`
        Serialized `EquipmentConfiguration` with ``telescope``,
        ``camera``, ``plate_scale_arcsec_per_px``, ``fov_width_deg``,
        and ``fov_height_deg`` keys, or `None` if no camera is
        configured.
    """
    from wayfindinglib.observatorylib.equipment_configuration import EquipmentConfigurationManager

    manager = EquipmentConfigurationManager(config)
    active_configuration = manager.get_active_configuration()
    return active_configuration.to_dict() if active_configuration else None
