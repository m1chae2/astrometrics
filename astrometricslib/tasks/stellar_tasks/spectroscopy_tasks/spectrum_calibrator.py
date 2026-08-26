"""Tools for mapping pixels to colors (wavelengths).

Converts a row of pixels from the image into actual light wavelengths
(like red, green, blue) based on the physics of the camera's grating.
It also provides tools to smooth out the jagged data into clean curves.
"""

import logging
from typing import TYPE_CHECKING

import numpy as np
from astropy.convolution import Box1DKernel, convolve

if TYPE_CHECKING:
    from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_instrument import (
        SpectroscopyInstrument,
    )

logger = logging.getLogger(__name__)


class SpectrumCalibrator:
    """Turns pixel numbers into actual colors (wavelengths).

    Attributes
    ----------
    instrument : `SpectroscopyInstrument`
        The math model that tells us how to convert pixels to colors.
    """

    def __init__(self, instrument: SpectroscopyInstrument):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the calibrator with its instrument model.

        Parameters
        ----------
        instrument : `SpectroscopyInstrument`
            The math model for the camera.
        """
        self.instrument = instrument

    def calibrate(self, pixels: np.ndarray, offset_px: float) -> tuple[np.ndarray, np.ndarray]:
        """Figure out what color each pixel represents.

        Parameters
        ----------
        pixels : `np.ndarray`
            The brightness values for a line of pixels.
        offset_px : `float`
            Where this line of pixels starts, relative to the main star.

        Returns
        -------
        wavelengths : `np.ndarray`
            The calculated color (in nm) for each pixel.
        intensities : `np.ndarray`
            The original brightness values, untouched.
        """
        wavelengths = []
        for i in range(len(pixels)):
            # Calculate wavelength for each pixel
            wl = self.instrument.wavelength_at_pixel_offset(offset_px + i)
            wavelengths.append(wl)

        return np.array(wavelengths), pixels

    def apply_smoothing(self, intensities: np.ndarray, window: int = 5) -> np.ndarray:
        """Smooth out a jagged graph by averaging nearby points.

        Parameters
        ----------
        intensities : `np.ndarray`
            The jagged data to smooth.
        window : `int`, optional
            How many nearby points to average together (default is 5).

        Returns
        -------
        smoothed_intensities : `np.ndarray`
            The clean, smoothed-out data.

        Notes
        -----
        We use a smart smoothing tool here instead of a basic one. A basic
        tool pretends the data outside the edges is zero, which artificially
        drags the ends of the graph down. This tool extends the edge values
        outward, keeping the ends of the graph accurate. It also handles bad
        (dead) pixels safely.
        """
        if len(intensities) < window:
            return intensities
        return convolve(intensities, Box1DKernel(window), boundary="extend")
