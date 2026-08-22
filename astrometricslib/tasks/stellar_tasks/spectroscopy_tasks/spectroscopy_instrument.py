"""Physical domain model for a spectroscopy instrument setup.

Calculates dispersion, theta, and expected spectrum characteristics.

REQ: SR-3.2
"""

import logging

import numpy as np

from astrometricslib.utilities import SpectroscopyConfig

logger = logging.getLogger(__name__)


class SpectroscopyInstrument:
    """Encapsulate the physics of the spectroscopy setup.

    Calculates expected dispersion and offsets based on grating
    equations.

    Attributes
    ----------
    config : `SpectroscopyConfig`
        The spectroscopy configuration this instrument was built from.
    theta : `float`
        Dispersion angle (radians) at the center wavelength.
    dx_dlambda : `float`
        Linear dispersion (mm/mm) at the center wavelength.
    expected_length_mm : `float`
        Expected physical length of the dispersed spectrum on the
        sensor (in mm).
    expected_length_px : `float`
        Expected length of the dispersed spectrum on the sensor (in
        pixels).
    zero_order_offset_px : `float`
        Pixel offset of the start of the spectrum relative to the
        zero-order star, either taken directly from configuration or
        derived from the theoretical minimum wavelength.
    """

    def __init__(self, config: SpectroscopyConfig):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the instrument model and derive its properties.

        Parameters
        ----------
        config : `SpectroscopyConfig`
            The spectroscopy configuration to build this instrument
            model from.
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
        """Return a unit vector pointing in the direction of dispersion.

        Returns
        -------
        dispersion_vector : `np.ndarray`
            2-element unit vector `(cos(angle), sin(angle))` giving the
            dispersion direction, combining the configured orientation
            (horizontal/vertical), direction (positive/negative), and
            fine-tuning `dispersion_angle_degrees`.
        """
        orient = self.config.dispersion_orientation
        direc = self.config.dispersion_direction

        base_angle = 0.0 if orient == "horizontal" else 90.0
        if direc == "negative":
            base_angle += 180.0

        total_angle_rad = np.radians(base_angle + self.config.dispersion_angle_degrees)
        return np.array([np.cos(total_angle_rad), np.sin(total_angle_rad)])

    def wavelength_at_pixel_offset(self, px_offset: float) -> float:
        """Calculate wavelength (nm) at a pixel offset from zero order.

        Parameters
        ----------
        px_offset : `float`
            Pixel offset relative to the zero-order star position.

        Returns
        -------
        wavelength_nm : `float`
            Calibrated wavelength (in nanometers) at the given offset.
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
