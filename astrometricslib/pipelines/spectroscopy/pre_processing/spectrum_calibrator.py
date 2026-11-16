"""Tools for mapping pixels to colors (wavelengths).

Converts a row of pixels from the image into actual light wavelengths
(like red, green, blue) based on the physics of the camera's grating.
It also provides tools to smooth out the jagged data into clean curves.
"""

import logging
from typing import TYPE_CHECKING

import numpy as np
from astropy.convolution import Box1DKernel, convolve

from astrometricslib.foundation.parameter_context import run_basis_value
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES, factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)

if TYPE_CHECKING:
    from astrometricslib.pipelines.spectroscopy.pre_processing.spectroscopy_instrument import (
        SpectroscopyInstrument,
    )

logger = logging.getLogger(__name__)

# The smoothing window of the calibration tuner's dip search is 5 samples on
# the reference setup: wide enough to take the noise off single pixels and
# narrow enough that a Balmer line, which spans about one resolution element
# (43.3 Å, nearly 4 samples of 11.39 Å), keeps its depth. It was chosen by
# judgment and checked only on the synthetic frames of SPE-12. It is written
# here as a width in Angstroms that scales with the spectral resolution the
# run measured (5 samples of the reference dispersion, so 1.32 resolution
# elements), and `smoothing_window_samples` turns it into samples with the
# run's own dispersion. Its reference value, 5 reference samples, gives
# back a window of 5 on the reference setup.
SPECTRUM_SMOOTHING_WINDOW_ANGSTROM = TunedParameter(
    name="spectroscopy.spectrum_smoothing_window_angstrom",
    default=factor_for(
        5 * REFERENCE_SETUP_BASES[RunBasis.DISPERSION_ANGSTROM_PER_PX], RunBasis.SPECTRAL_RESOLUTION_ANGSTROM
    ),
    unit="× spectral resolution",
    description="The width of the running average that smooths a spectrum before its dips are searched.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING}),
    register_entry="SPE-12",
    basis=RunBasis.SPECTRAL_RESOLUTION_ANGSTROM,
    reference_value=5 * REFERENCE_SETUP_BASES[RunBasis.DISPERSION_ANGSTROM_PER_PX],
)


def smoothing_window_samples() -> int:
    """Give the smoothing window of a spectrum, in samples.

    The window is a fixed number of resolution elements (see
    `SPECTRUM_SMOOTHING_WINDOW_ANGSTROM`), so it is wider in samples when the
    dispersion is finer and narrower when it is coarser.

    Returns
    -------
    window : `int`
        The width in samples: the window in Angstroms over the run's
        dispersion, rounded to the nearest odd whole number, at least 1.
        It is 5 on the reference setup.
    """
    window_angstrom = float(resolve(SPECTRUM_SMOOTHING_WINDOW_ANGSTROM).value)
    dispersion = run_basis_value(RunBasis.DISPERSION_ANGSTROM_PER_PX)
    if dispersion is None:
        dispersion = REFERENCE_SETUP_BASES[RunBasis.DISPERSION_ANGSTROM_PER_PX]
    window = max(1, round(window_angstrom / dispersion))
    return window if window % 2 == 1 else window + 1


class SpectrumCalibrator:
    """Turns pixel numbers into actual colors (wavelengths).

    Attributes
    ----------
    instrument : `SpectroscopyInstrument`
        The math model that tells us how to convert pixels to colors.
    """

    def __init__(self, instrument: SpectroscopyInstrument) -> None:
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
        return self.calibrate_at_distances(pixels, offset_px + np.arange(len(pixels), dtype=float))

    def calibrate_at_distances(
        self, pixels: np.ndarray, distances_px: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Figure out what color each sample represents, given its distance.

        Use this when the samples are not exactly one pixel apart, for
        example when the first sample sits a fraction of a pixel past the
        requested start.

        Parameters
        ----------
        pixels : `np.ndarray`
            The brightness value of each sample.
        distances_px : `np.ndarray`
            The distance of each sample from the main star, in pixels,
            measured along the dispersion direction. It has one value per
            sample.

        Returns
        -------
        wavelengths : `np.ndarray`
            The calculated color (in nm) for each sample.
        intensities : `np.ndarray`
            The original brightness values, untouched.
        """
        wavelengths = [self.instrument.wavelength_at_pixel_offset(float(d)) for d in distances_px]
        return np.array(wavelengths), pixels

    def apply_smoothing(self, intensities: np.ndarray, window: int | None = None) -> np.ndarray:
        """Smooth out a jagged graph by averaging nearby points.

        Parameters
        ----------
        intensities : `np.ndarray`
            The jagged data to smooth.
        window : `int`, optional
            How many nearby points to average together. The default `None`
            takes `smoothing_window_samples()`, which is 5 on the reference
            setup and follows the run's resolution and dispersion.

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
        if window is None:
            window = smoothing_window_samples()
        if len(intensities) < window:
            return intensities
        return convolve(intensities, Box1DKernel(window), boundary="extend")
