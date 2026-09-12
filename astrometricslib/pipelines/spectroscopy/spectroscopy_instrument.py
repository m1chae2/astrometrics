"""A digital model of our spectrograph camera setup.

This calculates where the rainbow should fall on the camera sensor.
"""

import logging

import numpy as np

from astrometricslib.pipelines.spectroscopy.optics_physics import (
    calculate_pixel_offset,
    calculate_wavelength,
)
from astrometricslib.utilities import SpectroscopyConfig

logger = logging.getLogger(__name__)

# nm and mm both measure wavelength here; this file works in mm throughout.
_NM_TO_MM = 1e-6


class SpectroscopyInstrument:
    """The math model of our specific camera and grating.

    Attributes
    ----------
    config : `SpectroscopyConfig`
        The settings for this specific camera.
    theta : `float`
        The angle the light bends at the center of the rainbow (in radians).
    dx_dlambda : `float`
        How spread out the rainbow is (dispersion).
    expected_length_mm : `float`
        How long the rainbow should be on the sensor (in mm).
    expected_length_px : `float`
        How long the rainbow should be on the sensor (in pixels).
    zero_order_offset_px : `float`
        How many pixels away from the star the rainbow starts.
    """

    def __init__(self, config: SpectroscopyConfig):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the instrument model and calculate its properties.

        Parameters
        ----------
        config : `SpectroscopyConfig`
            The camera settings to use.
        """
        self.config = config
        self._calculate_properties()

    def _calculate_properties(self):  # ruff: ignore[missing-return-type-private-function]
        """Calculate physical properties such as dx/dlambda and length."""
        c = self.config

        # Center wavelength in mm
        lambda_c_mm = ((c.camera.sensor_min_wavelength + c.camera.sensor_max_wavelength) / 2.0) * _NM_TO_MM

        # Grating spacing (d) is already in config as d_mm
        sin_theta = lambda_c_mm / c.d_mm
        if abs(sin_theta) >= 1:
            logger.warning(f"sin(theta) out of range ({sin_theta}). Check grating lines/mm.")
            self.theta = 0.0
        else:
            self.theta = np.arcsin(sin_theta)

        # Linear dispersion (dx/dlambda) in mm/mm. This is the exact
        # derivative of this module's own x = L*tan(theta), lambda =
        # d*sin(theta) geometry (see `optics_physics.py`), not the
        # small-angle textbook formula L/(d*cos(theta)) -- that
        # approximation quietly drifts from this pipeline's actual
        # (exact) geometry as theta grows with higher-dispersion
        # gratings.
        self.dx_dlambda = c.grating_distance_mm / (c.d_mm * np.cos(self.theta) ** 3)

        # Delta lambda in mm
        delta_lambda_mm = (c.camera.sensor_max_wavelength - c.camera.sensor_min_wavelength) * _NM_TO_MM

        # Expected length in pixels
        self.expected_length_mm = self.dx_dlambda * delta_lambda_mm
        self.expected_length_px = self.expected_length_mm / c.pixel_pitch_mm

        # Zero order offset
        if c.dispersion_start_px is not None:
            self.zero_order_offset_px = c.dispersion_start_px
        else:
            # Theoretical offset for min wavelength, via the same exact
            # pixel<->wavelength relationship used everywhere else in
            # the pipeline (`optics_physics.calculate_pixel_offset`).
            sin_theta_min = (c.camera.sensor_min_wavelength * _NM_TO_MM) / c.d_mm
            if abs(sin_theta_min) < 1:
                self.zero_order_offset_px = calculate_pixel_offset(
                    wavelength_nm=c.camera.sensor_min_wavelength,
                    grating_distance_mm=c.grating_distance_mm,
                    lines_per_mm=c.grating_lines_per_mm,
                    pixel_size_um=c.camera.pixel_size_um,
                )
            else:
                self.zero_order_offset_px = 0.0

        # Cap the extraction length when the usable sensor area along
        # the dispersion axis is smaller than the physics-derived length
        # (e.g. the setup vignettes, or the calibrated region stops
        # short of the sensor edge).
        if c.max_extraction_length_px is not None:
            self.expected_length_px = c.max_extraction_length_px - self.zero_order_offset_px

    def get_dispersion_vector(self) -> np.ndarray:
        """Get an arrow pointing exactly along the rainbow.

        Returns
        -------
        dispersion_vector : `np.ndarray`
            An arrow `(x, y)` pointing in the direction the rainbow is spread
            out.
        """
        orient = self.config.dispersion_orientation
        direc = self.config.dispersion_direction

        base_angle = 0.0 if orient == "horizontal" else 90.0
        if direc == "negative":
            base_angle += 180.0

        total_angle_rad = np.radians(base_angle + self.config.dispersion_angle_degrees)
        return np.array([np.cos(total_angle_rad), np.sin(total_angle_rad)])

    def wavelength_at_pixel_offset(self, px_offset: float) -> float:
        """Figure out what color is hitting a specific pixel.

        Parameters
        ----------
        px_offset : `float`
            How many pixels away from the main star we are looking.

        Returns
        -------
        wavelength_nm : `float`
            The color at that pixel, in nanometers.
        """
        # px_offset is relative to zero order star. Delegates to the
        # same exact grating-equation implementation used everywhere
        # else in the pipeline, rather than a second hand-derived copy.
        c = self.config
        return calculate_wavelength(
            pixel_offset_px=px_offset,
            grating_distance_mm=c.grating_distance_mm,
            lines_per_mm=c.grating_lines_per_mm,
            pixel_size_um=c.camera.pixel_size_um,
        )
