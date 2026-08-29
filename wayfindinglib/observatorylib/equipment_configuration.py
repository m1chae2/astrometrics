"""Equipment configuration models and manager for telescope + camera pairings.

Provides plate-scale and angular field-of-view calculations derived from the
observatory hardware specs in astrometrics.config.

REQ: BKD-2.1
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PLATE_SCALE_CONSTANT = 206.265
"""Numerator in the standard plate-scale formula (radians × arcsec/rad ÷ 1000).

plate_scale (arcsec/px) = 206.265 × pixel_size_μm / focal_length_mm
"""


class TelescopeProfile(BaseModel):
    """Physical optics properties of the imaging telescope."""

    name: str
    focal_length_mm: float = Field(..., gt=0.0, description="Effective focal length in millimetres.")
    focal_ratio: float = Field(default=0.0, ge=0.0, description="Focal ratio (f/number).")


class CameraProfile(BaseModel):
    """Physical sensor properties of an imaging camera."""

    name: str
    pixel_size_um: float = Field(..., gt=0.0, description="Pixel pitch in micrometres.")
    sensor_width_px: int = Field(..., gt=0, description="Sensor width in pixels.")
    sensor_height_px: int = Field(..., gt=0, description="Sensor height in pixels.")


class EquipmentConfiguration(BaseModel):
    """A paired telescope and camera with derived imaging geometry.

    All geometry properties are computed on access from the stored profiles.
    """

    telescope: TelescopeProfile
    camera: CameraProfile

    @property
    def plate_scale_arcsec_per_px(self) -> float:
        """Plate scale in arcseconds per pixel.

        Derived via: 206.265 × pixel_size_μm / focal_length_mm
        """
        return PLATE_SCALE_CONSTANT * self.camera.pixel_size_um / self.telescope.focal_length_mm

    @property
    def fov_width_deg(self) -> float:
        """Sensor field-of-view width in degrees."""
        return self.plate_scale_arcsec_per_px * self.camera.sensor_width_px / 3600.0

    @property
    def fov_height_deg(self) -> float:
        """Sensor field-of-view height in degrees."""
        return self.plate_scale_arcsec_per_px * self.camera.sensor_height_px / 3600.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation with computed geometry.

        Returns
        -------
        config_dict : `dict`
            Serialized telescope/camera profiles plus computed plate
            scale and field-of-view values.
        """
        return {
            "telescope": self.telescope.model_dump(),
            "camera": self.camera.model_dump(),
            "plate_scale_arcsec_per_px": round(self.plate_scale_arcsec_per_px, 4),
            "fov_width_deg": round(self.fov_width_deg, 6),
            "fov_height_deg": round(self.fov_height_deg, 6),
        }


class EquipmentConfigurationManager:
    """Reads telescope and camera profiles from astrometrics.config.

    The observatory supports a single telescope (from [Observatory.Telescope])
    and multiple camera profiles (from [Observatory.Camera.<Name>] sections).
    The active camera is tracked via the ``default_primary_camera`` config key.
    """

    # Config key for the active camera name
    _CAMERA_SECTION = "Observatory.Camera"
    _ACTIVE_CAMERA_KEY = "default_primary_camera"
    # Telescope name is not yet a config key; stored here for a
    # single-observatory setup
    _TELESCOPE_NAME = "Apertura 75Q"

    def __init__(self, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialise the manager with the application config instance.

        Parameters
        ----------
        config:
            An ``AppConfig`` / ``ConfigLoader`` instance providing
            ``get_focal_length_mm()``, ``get_focal_ratio()``,
            ``get_available_cameras()``, ``get_camera_config()``, and
            ``update_config()`` methods.
        """
        self._config = config

    def get_telescope_profile(self) -> TelescopeProfile:
        """Return the single telescope profile from [Observatory.Telescope].

        Returns
        -------
        profile : `TelescopeProfile`
            The configured telescope's name, focal length, and focal
            ratio.
        """
        return TelescopeProfile(
            name=self._TELESCOPE_NAME,
            focal_length_mm=self._config.get_focal_length_mm(),
            focal_ratio=self._config.get_focal_ratio(),
        )

    def list_camera_profiles(self) -> list[CameraProfile]:
        """Return all camera profiles defined in the config.

        Reads every name listed under ``[Observatory.Camera] models`` and
        resolves its corresponding ``[Observatory.Camera.<Name>]`` section.
        Cameras that are missing required sensor parameters are
        silently skipped.

        Returns
        -------
        profiles : `list` [`CameraProfile`]
            Camera profiles for every valid camera section found.
        """
        profiles: list[CameraProfile] = []
        for camera_name in self._config.get_available_cameras():
            camera_data = self._config.get_camera_config(camera_name)
            pixel_size = camera_data.get("pixel_size_μm")
            sensor_width = camera_data.get("sensor_width_px")
            sensor_height = camera_data.get("sensor_height_px")
            if not (pixel_size and sensor_width and sensor_height):
                logger.debug("Skipping camera '%s': missing sensor parameters", camera_name)
                continue
            try:
                profiles.append(
                    CameraProfile(
                        name=camera_name,
                        pixel_size_um=float(pixel_size),
                        sensor_width_px=int(sensor_width),
                        sensor_height_px=int(sensor_height),
                    )
                )
            except ValueError, TypeError:
                logger.warning("Failed to parse camera profile for '%s'", camera_name)
        return profiles

    def get_active_camera_name(self) -> str | None:
        """Return the currently configured primary camera name, or None.

        Returns
        -------
        camera_name : `str` or `None`
            The active camera name, or `None` if unset.
        """
        return self._config.get_value(self._CAMERA_SECTION, self._ACTIVE_CAMERA_KEY)

    def get_active_camera_profile(self) -> CameraProfile | None:
        """Return the CameraProfile for the active primary camera, or None.

        Returns
        -------
        profile : `CameraProfile` or `None`
            The active camera's profile, the first available profile
            if no active camera is set, or `None` if no profiles
            exist.
        """
        active_name = self.get_active_camera_name()
        if not active_name:
            profiles = self.list_camera_profiles()
            return profiles[0] if profiles else None
        for profile in self.list_camera_profiles():
            if profile.name == active_name:
                return profile
        logger.warning("Active camera '%s' not found in profile list", active_name)
        return None

    def get_active_configuration(self) -> EquipmentConfiguration | None:
        """Return the active EquipmentConfiguration, or None if not set.

        Returns
        -------
        configuration : `EquipmentConfiguration` or `None`
            The active telescope/camera configuration, or `None` if
            no active camera is configured.
        """
        telescope = self.get_telescope_profile()
        camera = self.get_active_camera_profile()
        if camera is None:
            return None
        return EquipmentConfiguration(telescope=telescope, camera=camera)

    def set_active_camera(self, camera_name: str) -> bool:
        """Record a new active camera selection to config.

        Parameters
        ----------
        camera_name:
            Must match a name returned by :meth:`list_camera_profiles`.

        Returns
        -------
        bool
            ``True`` if the name was recognised and saved, ``False`` otherwise.
        """
        available = {profile.name for profile in self.list_camera_profiles()}
        if camera_name not in available:
            logger.warning("set_active_camera: '%s' is not a known camera", camera_name)
            return False
        self._config.update_config({self._CAMERA_SECTION: {self._ACTIVE_CAMERA_KEY: camera_name}})
        logger.info("Active camera set to '%s'", camera_name)
        return True
