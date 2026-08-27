"""A digital model of our spectrograph camera setup.

This calculates where the rainbow should fall on the camera sensor.
"""

import logging

import numpy as np

from astrometricslib.utilities import SpectroscopyConfig

logger = logging.getLogger(__name__)


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

        # Angstrom to mm conversion
        ang_to_mm = 1e-7

        # Center wavelength in mm
        lambda_c_mm = (
            ((c.camera.sensor_min_wavelength + c.camera.sensor_max_wavelength) / 2.0) * ang_to_mm * 10
        )

        # Grating spacing (d) is already in config as d_mm
        sin_theta = lambda_c_mm / c.d_mm
        if abs(sin_theta) >= 1:
            logger.warning(f"sin(theta) out of range ({sin_theta}). Check grating lines/mm.")
            self.theta = 0.0
        else:
            self.theta = np.arcsin(sin_theta)

        # Linear dispersion (dx/dlambda) in mm/mm
        # dx/dlambda = L / (d * cos(theta))
        self.dx_dlambda = c.grating_distance_mm / (c.d_mm * np.cos(self.theta))

        # Delta lambda in mm
        delta_lambda_mm = (c.camera.sensor_max_wavelength - c.camera.sensor_min_wavelength) * 10 * ang_to_mm

        # Expected length in pixels
        self.expected_length_mm = self.dx_dlambda * delta_lambda_mm
        self.expected_length_px = self.expected_length_mm / c.pixel_pitch_mm

        # Zero order offset
        if c.dispersion_start_px is not None:
            self.zero_order_offset_px = c.dispersion_start_px
        else:
            # Theoretical offset for min wavelength
            lambda_min_mm = c.camera.sensor_min_wavelength * 10 * ang_to_mm
            sin_theta_min = lambda_min_mm / c.d_mm
            if abs(sin_theta_min) < 1:
                theta_min = np.arcsin(sin_theta_min)
                self.zero_order_offset_px = (c.grating_distance_mm * np.tan(theta_min)) / c.pixel_pitch_mm
            else:
                self.zero_order_offset_px = 0.0

        # Adjust for flare masking when ZWO ASI533MM Pro camera is used
        if c.camera.name == "ZWO ASI533MM Pro":
            self.expected_length_px = 750.0 - self.zero_order_offset_px

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
        # px_offset is relative to zero order star
        # mm_offset = px_offset * pixel_pitch
        mm_offset = px_offset * self.config.pixel_pitch_mm

        # d * sin(theta) = m * lambda
        # tan(theta) = x / L  => theta = atan(x/L)
        # lambda = d * sin(atan(x/L))
        theta = np.arctan(mm_offset / self.config.grating_distance_mm)
        wavelength_mm = self.config.d_mm * np.sin(theta)

        return wavelength_mm * 1e6  # mm to nm
