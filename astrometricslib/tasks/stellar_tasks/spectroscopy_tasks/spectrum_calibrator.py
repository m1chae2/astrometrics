"""SpectrumCalibrator: wavelength mapping and spectral processing.

Maps extracted pixel indices to calibrated wavelengths and applies
smoothing to raw spectral intensity profiles.
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
    """Map pixel indices to wavelengths and perform calibration.

    Attributes
    ----------
    instrument : `SpectroscopyInstrument`
        The instrument model providing the pixel-to-wavelength
        conversion used by `calibrate`.
    """

    def __init__(self, instrument: SpectroscopyInstrument):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the calibrator with its instrument model.

        Parameters
        ----------
        instrument : `SpectroscopyInstrument`
            The instrument model to use for wavelength conversion.
        """
        self.instrument = instrument

    def calibrate(self, pixels: np.ndarray, offset_px: float) -> tuple[np.ndarray, np.ndarray]:
        """Return (wavelengths, intensities) for the extracted pixels.

        Parameters
        ----------
        pixels : `np.ndarray`
            Extracted 1D intensity profile.
        offset_px : `float`
            Starting pixel offset relative to the zero-order star.

        Returns
        -------
        wavelengths : `np.ndarray`
            Calibrated wavelength (nm) for each pixel in `pixels`.
        intensities : `np.ndarray`
            The input `pixels` array, returned unchanged.
        """
        wavelengths = []
        for i in range(len(pixels)):
            # Calculate wavelength for each pixel
            wl = self.instrument.wavelength_at_pixel_offset(offset_px + i)
            wavelengths.append(wl)

        return np.array(wavelengths), pixels

    def apply_smoothing(self, intensities: np.ndarray, window: int = 5) -> np.ndarray:
        """Apply a boxcar (moving-average) smoothing.

        Parameters
        ----------
        intensities : `np.ndarray`
            Raw spectral intensity profile to smooth.
        window : `int`, optional
            Width of the boxcar smoothing kernel, in samples (default
            5).

        Returns
        -------
        smoothed_intensities : `np.ndarray`
            The smoothed intensity profile, or `intensities` unchanged
            if it is shorter than `window`.

        Notes
        -----
        Box1DKernel(window) is the same moving-average kernel
        previously applied via np.convolve(mode="same"); astropy's
        convolve additionally interpolates NaN samples and, with
        boundary="extend", renormalizes at the array edges instead of
        zero-padding, so edge samples are no longer artificially
        damped.
        """
        if len(intensities) < window:
            return intensities
        return convolve(intensities, Box1DKernel(window), boundary="extend")
