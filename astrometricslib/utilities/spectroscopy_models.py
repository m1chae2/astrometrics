"""Pydantic models for spectroscopy camera and session configuration."""

from __future__ import annotations

import functools
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# --- Placeholder values for a camera that is missing from the config --------
#
# When a camera's section in the config file does not give one of its
# hardware settings, the loader uses the value below and logs a warning that
# names the setting. These are stand-ins so that processing can carry on. They
# are NOT measurements of that camera: results built from them describe a
# made-up camera. `FALLBACK_PIXEL_SIZE_UM` is the ZWO ASI533MM Pro's pixel
# size, kept from before the warning existed so that nothing changed. The
# others are generic guesses that were never checked against any camera.
FALLBACK_PIXEL_SIZE_UM = 3.76
FALLBACK_SENSOR_WIDTH_PX = 3000
FALLBACK_SENSOR_HEIGHT_PX = 2000
FALLBACK_SENSOR_MIN_WAVELENGTH_NM = 350.0
FALLBACK_SENSOR_MAX_WAVELENGTH_NM = 900.0
FALLBACK_GRATING_LINES_PER_MM = 100.0
FALLBACK_GRATING_DISTANCE_MM = 0.0
FALLBACK_DISPERSION_ORIENTATION = "horizontal"
FALLBACK_DISPERSION_DIRECTION = "negative"

# The bluest wavelength the calibration tuner places the start of the
# spectrum at (see `SpectroscopyConfig.extraction_start_wavelength_nm`). It is
# the start of the visible spectrum. It was chosen, not measured. The
# instrument response and the classifier only use light redder than 420 nm.
DEFAULT_EXTRACTION_START_WAVELENGTH_NM = 380.0


class CameraConfig(BaseModel):
    """Configuration for a specific camera sensor.

    Attributes
    ----------
    name : `str`
        Full name of the camera sensor.
    pixel_size_um : `float`
        Pixel size in micrometers.
    sensor_width_px : `int`
        Sensor width in pixels.
    sensor_height_px : `int`
        Sensor height in pixels.
    grating_distance_mm : `float`
        Default grating distance for this camera, by default 0.0.
    sensor_min_wavelength : `float`
        Minimum wavelength sensor is sensitive to (nm), by default
        350.0.
    sensor_max_wavelength : `float`
        Maximum wavelength sensor is sensitive to (nm), by default
        900.0.
    """

    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(..., description="Full name of the camera sensor")
    pixel_size_um: float = Field(..., alias="pixel_size_μm", description="Pixel size in micrometers")
    sensor_width_px: int = Field(..., description="Sensor width in pixels")
    sensor_height_px: int = Field(..., description="Sensor height in pixels")

    # Spectroscopy specific defaults that can be overridden
    grating_distance_mm: float = Field(0.0, description="Default grating distance for this camera")
    sensor_min_wavelength: float = Field(350.0, description="Minimum wavelength sensor is sensitive to (nm)")
    sensor_max_wavelength: float = Field(900.0, description="Maximum wavelength sensor is sensitive to (nm)")


class SpectroscopyConfig(BaseModel):
    """Configuration for the spectroscopy session/setup.

    Attributes
    ----------
    camera : `CameraConfig`
        Camera sensor configuration used for this session.
    grating_lines_per_mm : `float`
        Lines per mm of the grating filter, by default 100.0.
    grating_distance_mm : `float`
        Current physical distance between grating and sensor. Normally
        fitted from a calibration frame by
        ``SpectroscopyCalibrationTuner.tune_calibration()`` rather than
        hand-set.
    dispersion_orientation : {"horizontal", "vertical"}
        Orientation of the dispersion axis, by default
        ``"horizontal"``.
    dispersion_direction : {"positive", "negative"}
        Direction of increasing wavelength along the dispersion axis,
        by default ``"negative"``.
    dispersion_start_px : `float` or `None`
        Manual zero-order offset override, by default `None`. Normally
        fitted from a calibration frame by
        ``SpectroscopyCalibrationTuner.tune_calibration()`` rather than
        hand-set.
    dispersion_offset_x : `float`
        Fine-tuning horizontal offset of the dispersion box, by
        default 0.0.
    dispersion_offset_y : `float`
        Fine-tuning vertical offset of the dispersion box, by default
        0.0.
    dispersion_angle_degrees : `float`
        Rotation of the dispersion axis relative to orientation, by
        default 0.0.
    extraction_start_wavelength_nm : `float`
        The wavelength, in nanometers, that
        ``SpectroscopyCalibrationTuner.tune_calibration()`` treats as the
        start of the spectrum when it works out where extraction begins,
        by default 380.0. Set it per camera in the config file with the key
        ``extraction_start_wavelength_nm``.
    expected_fwhm : `float`
        Expected FWHM of stars in pixels, by default 8.0.
    extraction_radius : `int`
        Radius for spectrum extraction box, by default 10.
    extraction_method : {"fixed", "traced"}
        "traced" (default) fits a per-position Gaussian cross-section,
        smoothed into a trail-centerline polynomial, with an aperture
        adaptive to the locally measured width (rung 3). "fixed" is the
        original fixed-width/fixed-offset box-sum extraction (rung 1),
        kept as an escape hatch/comparison mode.
    centerline_polynomial_degree : `int`
        Degree of the polynomial fit to the traced extraction's per-step
        raw centers, by default 2. Unused when extraction_method is
        "fixed".
    subtract_sky_background : `bool`
        Whether extraction measures the night-sky glow in strips beside
        the spectrum and subtracts it from every reading, by default
        `True`. See the module docstring of
        ``spectrum_extractor`` for why. Turn it off only to compare
        against the raw, un-subtracted spectrum.
    reject_narrow_contaminants : `bool`
        Whether extraction replaces narrow bright spikes in the reading box
        (other stars' trails and zero orders) by the smooth level, by
        default `False`. The pipeline turns it on for a nebula's wide box
        only.
    use_flare_mask_extraction : `bool`
        Whether to extract starting from an offset anchored past the
        star's own position, to avoid a bright flare/astigmatism
        overlapping the spectrum near zero order, by default `False`
        (extract along the instrument's default dispersion line
        instead). Calculated from a calibration frame by
        ``SpectroscopyCalibrationTuner.tune_calibration()``, which
        checks whether the star's own light still saturates the pixels
        where extraction would begin, rather than hand-set.
    max_extraction_length_px : `float` or `None`
        A hard cap, in pixels, on how far along the dispersion axis
        from the zero-order anchor extraction may reach, by default
        `None` (uncapped, using the physics-derived length). Calculated
        from a calibration frame by
        ``SpectroscopyCalibrationTuner.tune_calibration()``, which
        checks whether the physics-derived extraction length would run
        past the usable sensor area -- for example because the setup
        vignettes, or the calibrated/usable region stops short of the
        sensor edge -- rather than hand-set.
    """

    model_config = ConfigDict(populate_by_name=True)
    camera: CameraConfig

    # Grating properties
    grating_lines_per_mm: float = Field(100.0, description="Lines per mm of the grating filter")
    grating_distance_mm: float = Field(
        ..., description="Current physical distance between grating and sensor"
    )

    # Dispersion characteristics
    dispersion_orientation: Literal["horizontal", "vertical"] = Field("horizontal")
    dispersion_direction: Literal["positive", "negative"] = Field("negative")
    dispersion_start_px: float | None = Field(None, description="Manual zero-order offset override")
    dispersion_offset_x: float = Field(0.0, description="Fine-tuning horizontal offset of the dispersion box")
    dispersion_offset_y: float = Field(0.0, description="Fine-tuning vertical offset of the dispersion box")
    dispersion_angle_degrees: float = Field(
        0.0, description="Rotation of the dispersion axis relative to orientation"
    )

    extraction_start_wavelength_nm: float = Field(
        DEFAULT_EXTRACTION_START_WAVELENGTH_NM,
        gt=0.0,
        description="Wavelength the calibration tuner treats as the start of the spectrum (nm)",
    )

    # Processing hints
    expected_fwhm: float = Field(8.0, description="Expected FWHM of stars in pixels")
    extraction_radius: int = Field(10, description="Radius for spectrum extraction box")
    extraction_method: Literal["fixed", "traced"] = Field(
        "traced", description="Traced (rung 3, adaptive) or fixed (rung 1, original) extraction"
    )
    centerline_polynomial_degree: int = Field(
        2, description="Polynomial degree for the traced-extraction trail centerline fit"
    )
    subtract_sky_background: bool = Field(
        True,
        description="Subtract the night-sky glow, measured in strips beside the spectrum, from every reading",
    )
    reject_narrow_contaminants: bool = Field(
        False,
        description="Replace narrow bright spikes (other stars' trails) in the box by the smooth level",
    )
    use_flare_mask_extraction: bool = Field(
        False,
        description=(
            "Extract from an anchor offset past the star to avoid its own flare/astigmatism "
            "(calculated by SpectroscopyCalibrationTuner.tune_calibration())"
        ),
    )
    max_extraction_length_px: float | None = Field(
        None,
        description=(
            "Hard cap on extraction length along the dispersion axis, in pixels "
            "(calculated by SpectroscopyCalibrationTuner.tune_calibration())"
        ),
    )

    def with_overrides(self, **kwargs) -> SpectroscopyConfig:  # ruff: ignore[missing-type-kwargs]
        """Return a new `SpectroscopyConfig` instance with overrides applied.

        Parameters
        ----------
        **kwargs
            Field name/value pairs to override in the returned copy.

        Returns
        -------
        updated_config : `SpectroscopyConfig`
            A new instance with the given fields overridden and all
            other fields copied from this instance.
        """
        data = self.model_dump()
        data.update(kwargs)
        return SpectroscopyConfig(**data)

    @property
    def pixel_pitch_mm(self) -> float:
        """`float`: Pixel pitch of the camera sensor, in millimeters."""
        return self.camera.pixel_size_um * 1e-3

    @property
    def d_mm(self) -> float:
        """`float`: Grating spacing in mm."""
        return 1.0 / self.grating_lines_per_mm


@functools.cache
def _warn_once_about_fallback_settings(camera_name: str, setting_names: tuple[str, ...]) -> None:
    """Log a warning the first time a camera falls back to placeholder values.

    Parameters
    ----------
    camera_name : `str`
        The camera whose config section is missing settings.
    setting_names : `tuple` [`str`, ...]
        The config keys that were not found. Because the result is cached,
        a second call with the same arguments does nothing.
    """
    logger.warning(
        "The config has no %s for camera %r; placeholder values are being used, so any "
        "results are not for a real camera. Add the settings to the camera's section.",
        ", ".join(setting_names),
        camera_name,
    )


class ConfigLoader:
    """Load and validate configurations into Pydantic models."""

    @staticmethod
    def load_spectroscopy_config(
        app_config: Any | None = None, camera_name: str | None = None
    ) -> SpectroscopyConfig:
        """Load spectroscopy configuration for a specific camera.

        Parameters
        ----------
        app_config : `Any`, optional
            The loaded application configuration object providing
            ``get_camera_config``/``get_value`` accessors, by default
            `None`, in which case the system configuration is loaded
            automatically via `get_configuration`.
        camera_name : `str`, optional
            Name of the camera to load configuration for, by default
            `None`.

        Returns
        -------
        spectroscopy_config : `SpectroscopyConfig`
            The resolved spectroscopy configuration for the requested
            camera, with legacy config values coerced to the expected
            types and defaults applied where unset.
        """
        if app_config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            app_config = get_configuration()
        cam_data = app_config.get_camera_config(camera_name)
        resolved_camera_name = camera_name or cam_data.get("name", "Unknown")
        settings_using_fallback: list[str] = []

        def read_setting(key: str, default: Any, *, is_hardware_setting: bool = False) -> Any:
            """Read one setting from the camera's section, then generic ones.

            Parameters
            ----------
            key : `str`
                The config key to look for.
            default : `Any`
                What to return when no section gives the key.
            is_hardware_setting : `bool`, optional
                `True` for a setting that describes the camera's hardware.
                When such a setting falls back to its default, its name is
                recorded so that one warning can list every missing setting.

            Returns
            -------
            value : `Any`
                The text found in the config, or `default`.
            """
            value = cam_data.get(key)
            if value is None:
                value = app_config.get_value(
                    "Observatory.Camera", key, app_config.get_value("Camera", key, None)
                )
            if value is None:
                if is_hardware_setting:
                    settings_using_fallback.append(key)
                return default
            return value

        def get_f(key: str, default: float | None = 0.0, *, is_hardware_setting: bool = False) -> Any:
            """Read a number from the config.

            Returns
            -------
            value : `float` or `None`
                The number, or `default` when the key is missing.
            """
            value = read_setting(key, default, is_hardware_setting=is_hardware_setting)
            return None if value is None else float(value)

        def get_s(key: str, default: str = "", *, is_hardware_setting: bool = False) -> str:
            """Read text from the config.

            Returns
            -------
            value : `str`
                The text, or `default` when the key is missing.
            """
            return str(read_setting(key, default, is_hardware_setting=is_hardware_setting))

        def get_bool(key: str, default: bool = False) -> bool:
            """Read a true/false setting from the config (stored as text).

            Returns
            -------
            value : `bool`
                The setting, or `default` when the key is missing.
            """
            value = read_setting(key, None)
            if value is None:
                return default
            return str(value).strip().lower() in ("1", "true", "yes", "on")

        grating_distance_mm = get_f(
            "grating_distance_mm", FALLBACK_GRATING_DISTANCE_MM, is_hardware_setting=True
        )
        camera = CameraConfig(
            name=resolved_camera_name,
            pixel_size_um=get_f("pixel_size_μm", FALLBACK_PIXEL_SIZE_UM, is_hardware_setting=True),
            sensor_width_px=int(get_f("sensor_width_px", FALLBACK_SENSOR_WIDTH_PX, is_hardware_setting=True)),
            sensor_height_px=int(
                get_f("sensor_height_px", FALLBACK_SENSOR_HEIGHT_PX, is_hardware_setting=True)
            ),
            grating_distance_mm=grating_distance_mm,
            sensor_min_wavelength=get_f(
                "sensor_min_wavelength", FALLBACK_SENSOR_MIN_WAVELENGTH_NM, is_hardware_setting=True
            ),
            sensor_max_wavelength=get_f(
                "sensor_max_wavelength", FALLBACK_SENSOR_MAX_WAVELENGTH_NM, is_hardware_setting=True
            ),
        )

        spectroscopy_config = SpectroscopyConfig(
            camera=camera,
            grating_lines_per_mm=get_f(
                "grating_lines_per_mm", FALLBACK_GRATING_LINES_PER_MM, is_hardware_setting=True
            ),
            grating_distance_mm=grating_distance_mm,
            dispersion_orientation=get_s(
                "dispersion_orientation", FALLBACK_DISPERSION_ORIENTATION, is_hardware_setting=True
            ).lower(),
            dispersion_direction=get_s(
                "dispersion_direction", FALLBACK_DISPERSION_DIRECTION, is_hardware_setting=True
            ).lower(),
            dispersion_start_px=(
                get_f("dispersion_start_px", None) if "dispersion_start_px" in cam_data else None
            ),
            dispersion_offset_x=get_f("dispersion_offset_x", 0.0),
            dispersion_offset_y=get_f("dispersion_offset_y", 0.0),
            dispersion_angle_degrees=get_f("dispersion_angle_degrees", 0.0),
            extraction_start_wavelength_nm=get_f(
                "extraction_start_wavelength_nm", DEFAULT_EXTRACTION_START_WAVELENGTH_NM
            ),
            expected_fwhm=get_f("expected_fwhm", 8.0),
            extraction_radius=int(get_f("extraction_radius", 10)),
            use_flare_mask_extraction=get_bool("use_flare_mask_extraction", False),
            max_extraction_length_px=(
                get_f("max_extraction_length_px", None) if "max_extraction_length_px" in cam_data else None
            ),
        )

        if settings_using_fallback:
            _warn_once_about_fallback_settings(
                str(resolved_camera_name), tuple(dict.fromkeys(settings_using_fallback))
            )
        return spectroscopy_config
