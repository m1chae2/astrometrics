"""Purpose: Unit tests for the bias of the sky estimate beside a spectrum.

Description: The extractor measures the sky in a band on each side of the
trail. Taking the lower of the two band medians reads low on average,
because the smaller of two noisy numbers sits below their true value. A
faint trace then loses too little sky and reads too bright. These tests
build synthetic frames with a known sky level and check that the recovered
sky is within 0.5 ADU (the camera's counts) of the truth, that a faint trace
is not read bright, and that a bright neighbour in one band makes the
extractor use the other band and say so.
"""

import numpy as np
import pytest

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import (
    SpectrumExtractor,
    measure_sky_level_per_pixel,
)
from astrometricslib.test.synthetic.spectral_frame import SyntheticSpectralFrame, make_spectral_frame

# The true sky, in ADU per pixel, and the noise on a frame that holds it.
TRUE_SKY_ADU = 60.0
READ_NOISE_ADU = 4.0
# A long trail, so many independent sky readings average the noise down.
FRAME_SHAPE = (256, 2200)
TRAIL_LENGTH_PX = 2000
TRAIL_ANGLE_DEG = 1.0
# The half-width, in pixels, of the reading box used to place the sky bands.
BOX_HALF_WIDTH_PX = 6
# The sky must be recovered to within this many ADU.
SKY_TOLERANCE_ADU = 0.5
# A faint trail's mean flux must match the noise-free value to this fraction.
FAINT_TRAIL_TOLERANCE = 0.01
FAINT_CONTINUUM_ADU = 600.0
# The neighbour's streak: its offset from the trail in rows (positive means
# a higher row index), its width, and its brightness per column.
NEIGHBOUR_OFFSET_PX = 17
NEIGHBOUR_SIGMA_PX = 2.0
NEIGHBOUR_COLUMN_FLUX_ADU = 20000.0
# The fraction of columns that must report the contaminated band.
MINIMUM_CONTAMINATED_FRACTION = 0.95


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


def _frame(
    add_noise: bool = True, seed: int = 0, continuum_adu: float = FAINT_CONTINUUM_ADU
) -> SyntheticSpectralFrame:
    """Make the long, tilted, line-free synthetic frame used by every test.

    Parameters
    ----------
    add_noise : `bool`, optional
        Whether to add photon and read noise (default `True`).
    seed : `int`, optional
        Noise seed (default 0).
    continuum_adu : `float`, optional
        Trail flux in one column, in ADU.

    Returns
    -------
    frame : `SyntheticSpectralFrame`
        The frame, with a sky of `TRUE_SKY_ADU` per pixel.
    """
    return make_spectral_frame(
        angle_deg=TRAIL_ANGLE_DEG,
        trail_length_px=TRAIL_LENGTH_PX,
        continuum_adu=continuum_adu,
        lines=(),
        sky_adu=TRUE_SKY_ADU,
        read_noise_adu=READ_NOISE_ADU,
        shape=FRAME_SHAPE,
        seed=seed,
        add_noise=add_noise,
    )


def _trail_columns(frame: SyntheticSpectralFrame) -> np.ndarray:
    """Give the columns that carry the trail, away from the zero order.

    Parameters
    ----------
    frame : `SyntheticSpectralFrame`
        The frame.

    Returns
    -------
    columns : `numpy.ndarray`
        Integer column indices.
    """
    x0 = int(frame.zero_order_xy[0])
    return np.arange(x0 + 100, x0 + TRAIL_LENGTH_PX)


def _add_neighbour(frame: SyntheticSpectralFrame, image: np.ndarray) -> np.ndarray:
    """Add a bright streak that follows the trail, above it in the image.

    Parameters
    ----------
    frame : `SyntheticSpectralFrame`
        The frame, used for the trail's true centre.
    image : `numpy.ndarray`
        The image to add the streak to.

    Returns
    -------
    contaminated : `numpy.ndarray`
        A copy of `image` with the streak added.
    """
    contaminated = image.copy()
    rows = np.arange(image.shape[0])
    for column in _trail_columns(frame):
        centre = float(frame.trace_center_y(column)) + NEIGHBOUR_OFFSET_PX
        profile = np.exp(-0.5 * ((rows - centre) / NEIGHBOUR_SIGMA_PX) ** 2)
        contaminated[:, column] += NEIGHBOUR_COLUMN_FLUX_ADU * profile / profile.sum()
    return contaminated


def _sky_per_column(frame: SyntheticSpectralFrame, image: np.ndarray) -> np.ndarray:
    """Measure the sky beside the trail in every trail column.

    Parameters
    ----------
    frame : `SyntheticSpectralFrame`
        The frame, used for the trail's true centre.
    image : `numpy.ndarray`
        The image to measure.

    Returns
    -------
    sky : `numpy.ndarray`
        The sky level per pixel at each trail column, in ADU.
    """
    return np.array([
        measure_sky_level_per_pixel(
            image[:, column], round(float(frame.trace_center_y(column))), BOX_HALF_WIDTH_PX
        )
        for column in _trail_columns(frame)
    ])


def test_the_recovered_sky_is_within_half_an_adu_of_the_truth() -> None:
    """The mean sky estimate matches the true sky to 0.5 ADU."""
    frame = _frame()

    sky = _sky_per_column(frame, frame.image)

    assert abs(sky.mean() - TRUE_SKY_ADU) < SKY_TOLERANCE_ADU


def test_a_faint_trail_is_not_read_bright_by_an_under_subtracted_sky() -> None:
    """A faint trail reads within 1 percent of its noise-free flux."""
    noisy = _frame()
    clean = _frame(add_noise=False)
    x0, y0 = noisy.zero_order_xy
    arguments = (1990.0, 10, "horizontal", TRAIL_ANGLE_DEG)

    noisy_profile, *_ = SpectrumExtractor().extract_with_flare_mask_traced(
        _ArrayImage(noisy.image), (x0, y0), 100.0, *arguments
    )
    clean_profile, *_ = SpectrumExtractor().extract_with_flare_mask_traced(
        _ArrayImage(clean.image), (x0, y0), 100.0, *arguments
    )

    assert noisy_profile.mean() / clean_profile.mean() - 1.0 == pytest.approx(0.0, abs=FAINT_TRAIL_TOLERANCE)


def test_a_bright_neighbour_in_the_upper_band_leaves_the_sky_at_the_truth() -> None:
    """A neighbour in the upper band leaves the lower band to set the sky."""
    frame = _frame()
    contaminated = _add_neighbour(frame, frame.image)

    sky = _sky_per_column(frame, contaminated)

    assert abs(sky.mean() - TRUE_SKY_ADU) < SKY_TOLERANCE_ADU


def test_the_extractor_records_that_it_used_the_lower_band() -> None:
    """The diagnostics name the upper band as contaminated."""
    frame = _frame()
    contaminated = _add_neighbour(frame, frame.image)
    x0, y0 = frame.zero_order_xy

    extractor = SpectrumExtractor()
    extractor.extract_with_flare_mask_traced(
        _ArrayImage(contaminated), (x0, y0), 100.0, 600.0, 10, "horizontal", TRAIL_ANGLE_DEG
    )
    diagnostics = extractor.last_diagnostics

    assert diagnostics.dominant_sky_mode == "upper_band_contaminated"
    assert diagnostics.contaminated_sky_fraction > MINIMUM_CONTAMINATED_FRACTION
    assert diagnostics.sky_mode_counts.get("lower_band_contaminated", 0) == 0


def test_a_clean_frame_uses_both_bands_for_most_readings() -> None:
    """Without a neighbour, almost every reading averages the two bands."""
    frame = _frame()
    x0, y0 = frame.zero_order_xy

    extractor = SpectrumExtractor()
    extractor.extract_with_flare_mask_traced(
        _ArrayImage(frame.image), (x0, y0), 100.0, 600.0, 10, "horizontal", TRAIL_ANGLE_DEG
    )

    assert extractor.last_diagnostics.dominant_sky_mode == "both_bands"
    assert extractor.last_diagnostics.contaminated_sky_fraction < 0.1
