"""Purpose: Unit tests for the stability of the extraction aperture width.

Description: The traced extraction methods read each column through a box
whose half-width comes from the trail width. The trail width is fitted
again in every column, so it is noisy. If the box half-width follows that
noise one whole pixel at a time, the box flips between sizes from column
to column and the extracted flux gets steps that have nothing to do with
the star. These tests extract a flat continuum from a synthetic frame with
realistic noise and check that the result is flat, that it does not follow
the per-column width estimate, and that the half-width changes smoothly.
"""

import numpy as np
import pytest
from scipy.ndimage import median_filter

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import (
    SpectrumExtractor,
)
from astrometricslib.test.synthetic import make_spectral_frame

# A cross-profile sigma of 1.78 px puts the fitted 2.5 sigma at 4.5 px, the
# rounding boundary between a 4 px and a 5 px half-width (the fit reads
# slightly wide), which is the worst case for per-column rounding.
TRACE_SIGMA_PX = 1.78
# Total trail flux in one column, in ADU. This is a bright star, so the
# per-column noise is about 0.6 percent.
CONTINUUM_ADU = 30000.0
FLARE_OFFSET_PX = 15.0
MAX_OFFSET_PX = 600.0
SEARCH_RADIUS_PX = 10
MEDIAN_SMOOTH_COLUMNS = 15
FLATNESS_TOLERANCE = 0.01
# A box that follows the noisy per-column width gives a correlation of
# 0.3 or more on this frame. Unrelated noise gives about 0.04.
MAXIMUM_ABS_CORRELATION = 0.15
# The half-width may change by at most this many pixels between
# neighbouring columns.
MAXIMUM_HALF_WIDTH_STEP_PX = 0.1


class _ArrayImage(AstrometricsImage):
    """An `AstrometricsImage` that wraps a NumPy array for the extractor."""

    def __init__(self, data: np.ndarray) -> None:
        """Wrap `data` so the extractor can read it like a real image."""
        self._data = data
        self._header = {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self) -> dict[str, object]:
        """Image headers dict."""
        return self._header


def _extract_flat_continuum(
    seed: int,
) -> tuple[SpectrumExtractor, np.ndarray, list[float]]:
    """Extract a flat continuum from a noisy synthetic frame.

    Parameters
    ----------
    seed : `int`
        Seed for the frame's noise.

    Returns
    -------
    extractor : `SpectrumExtractor`
        The extractor that read the frame, so a test can look at its
        diagnostics.
    profile : `numpy.ndarray`
        The extracted flux at each step along the trail, in ADU.
    trail_width_px : `list` [`float`]
        The per-column cross-profile sigma the extractor reported, in
        pixels.
    """
    frame = make_spectral_frame(
        trace_sigma_px=TRACE_SIGMA_PX, continuum_adu=CONTINUUM_ADU, lines=(), seed=seed
    )
    extractor = SpectrumExtractor()
    x0, y0 = frame.zero_order_xy
    profile, _, _, _, trail_width_px = extractor.extract_with_flare_mask_traced(
        _ArrayImage(frame.image),
        (x0, y0),
        FLARE_OFFSET_PX,
        MAX_OFFSET_PX,
        SEARCH_RADIUS_PX,
        "horizontal",
        frame.angle_deg,
    )
    return extractor, profile, trail_width_px


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_extracted_continuum_is_flat_after_a_median_smooth(seed: int) -> None:
    """A flat continuum extracts flat to 1 percent after a median smooth."""
    _, profile, _ = _extract_flat_continuum(seed)

    smoothed = median_filter(profile, size=MEDIAN_SMOOTH_COLUMNS, mode="nearest")
    interior = smoothed[MEDIAN_SMOOTH_COLUMNS:-MEDIAN_SMOOTH_COLUMNS]
    deviation = np.abs(interior / np.median(interior) - 1.0)

    assert deviation.max() < FLATNESS_TOLERANCE


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_extracted_flux_does_not_follow_the_per_column_width_estimate(seed: int) -> None:
    """The extracted flux is uncorrelated with the noisy per-column width."""
    _, profile, trail_width_px = _extract_flat_continuum(seed)

    widths = np.asarray(trail_width_px)
    fitted = widths > 0
    correlation = np.corrcoef(profile[fitted], widths[fitted])[0, 1]

    assert abs(correlation) < MAXIMUM_ABS_CORRELATION


def test_the_aperture_half_width_changes_smoothly_along_the_trail() -> None:
    """The box half-width does not jump from column to column."""
    extractor, _, _ = _extract_flat_continuum(0)

    half_widths = np.asarray(extractor.last_diagnostics.aperture_half_width_px)

    assert half_widths.size > 100
    assert np.abs(np.diff(half_widths)).max() < MAXIMUM_HALF_WIDTH_STEP_PX
