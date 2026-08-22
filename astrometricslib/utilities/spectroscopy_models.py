"""Pydantic models for spectroscopy camera and session configuration."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
        Current physical distance between grating and sensor.
    dispersion_orientation : {"horizontal", "vertical"}
        Orientation of the dispersion axis, by default
        ``"horizontal"``.
    dispersion_direction : {"positive", "negative"}
        Direction of increasing wavelength along the dispersion axis,
        by default ``"negative"``.
    dispersion_start_px : `float` or `None`
        Manual zero-order offset override, by default `None`.
    dispersion_offset_x : `float`
        Fine-tuning horizontal offset of the dispersion box, by
        default 0.0.
    dispersion_offset_y : `float`
        Fine-tuning vertical offset of the dispersion box, by default
        0.0.
    dispersion_angle_degrees : `float`
        Rotation of the dispersion axis relative to orientation, by
        default 0.0.
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

    # Processing hints
    expected_fwhm: float = Field(8.0, description="Expected FWHM of stars in pixels")
    extraction_radius: int = Field(10, description="Radius for spectrum extraction box")
    extraction_method: Literal["fixed", "traced"] = Field(
        "traced", description="Traced (rung 3, adaptive) or fixed (rung 1, original) extraction"
    )
    centerline_polynomial_degree: int = Field(
        2, description="Polynomial degree for the traced-extraction trail centerline fit"
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

        # Helper to get float from legacy config
        def get_f(key, default=0.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            val = cam_data.get(key)
            if val is not None:
                return float(val)
            # Fallback to generic sections
            return float(
                app_config.get_value("Observatory.Camera", key, app_config.get_value("Camera", key, default))
            )

        # Helper to get string from legacy config
        def get_s(key, default=""):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            val = cam_data.get(key)
            if val is not None:
                return str(val)
            return str(
                app_config.get_value("Observatory.Camera", key, app_config.get_value("Camera", key, default))
            )

        camera = CameraConfig(
            name=camera_name or cam_data.get("name", "Unknown"),
            pixel_size_um=get_f("pixel_size_μm", 3.76),
            sensor_width_px=int(get_f("sensor_width_px", 3000)),
            sensor_height_px=int(get_f("sensor_height_px", 2000)),
            grating_distance_mm=get_f("grating_distance_mm", 0.0),
            sensor_min_wavelength=get_f("sensor_min_wavelength", 350.0),
            sensor_max_wavelength=get_f("sensor_max_wavelength", 900.0),
        )

        return SpectroscopyConfig(
            camera=camera,
            grating_lines_per_mm=get_f("grating_lines_per_mm", 100.0),
            grating_distance_mm=get_f("grating_distance_mm", camera.grating_distance_mm),
            dispersion_orientation=get_s("dispersion_orientation", "horizontal").lower(),
            dispersion_direction=get_s("dispersion_direction", "negative").lower(),
            dispersion_start_px=(
                get_f("dispersion_start_px", None) if "dispersion_start_px" in cam_data else None
            ),
            dispersion_offset_x=get_f("dispersion_offset_x", 0.0),
            dispersion_offset_y=get_f("dispersion_offset_y", 0.0),
            dispersion_angle_degrees=get_f("dispersion_angle_degrees", 0.0),
            expected_fwhm=get_f("expected_fwhm", 8.0),
            extraction_radius=int(get_f("extraction_radius", 10)),
        )
