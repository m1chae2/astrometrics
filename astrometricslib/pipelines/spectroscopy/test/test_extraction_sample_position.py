"""Purpose: Unit tests for where along a spectrum each extracted sample sits.

Description: Every sample of an extracted spectrum is given the wavelength of
one exact distance from the zero-order star, so the light read for that sample
has to come from that exact distance. The extractor used to read the whole
pixel that contains the position (`int()` rounds down), which put the light
up to a pixel too close to the star: about half a pixel on average, or about
5 A of wavelength, changing with where the star's centre fell within its pixel.
These tests place a narrow bump on a synthetic trail at a known distance and
check the extracted profile puts it at that distance for any start position.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.spectrum_extractor import SpectrumExtractor


class _Image:
    """A stand-in image holding only the array the extractor reads."""

    def __init__(self, data: np.ndarray) -> None:
        """Keep the array.

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        """
        self.data = data


BUMP_ROW = 120.0
TRAIL_COLUMN = 40


def _trail_with_a_bump() -> np.ndarray:
    """Build a vertical trail with a narrow bump exactly on row 120.

    Returns
    -------
    data : `numpy.ndarray`
        A 200 x 80 image: a Gaussian trail (sigma 2 px across) whose
        brightness along it is 100 plus a bump (sigma 1.5 px) centred on row
        `BUMP_ROW`, on a background of 10.
    """
    rows, columns = np.mgrid[0:200, 0:80].astype(float)
    across = np.exp(-0.5 * ((columns - TRAIL_COLUMN) / 2.0) ** 2)
    along = 100.0 + 400.0 * np.exp(-0.5 * ((rows - BUMP_ROW) / 1.5) ** 2)
    return 10.0 + across * along


def _bump_position(profile: np.ndarray) -> float:
    """Find the bump as the centre of mass above the floor.

    Returns
    -------
    position : `float`
        The bump's centre, in samples.
    """
    excess = np.clip(profile - np.median(profile), 0.0, None)
    return float((excess * np.arange(profile.size)).sum() / excess.sum())


@pytest.mark.parametrize("start_row", [100.0, 100.3, 100.5, 100.8])
def test_the_bump_is_read_at_its_true_distance(start_row: float) -> None:
    """The bump is 120 - start samples from the start, for any fraction."""
    extractor = SpectrumExtractor(radius=6, subtract_sky_background=False)

    profile = extractor.extract_line(
        _Image(_trail_with_a_bump()), (float(TRAIL_COLUMN), start_row), np.array([0.0, 1.0]), 50
    )

    assert _bump_position(profile) == pytest.approx(BUMP_ROW - start_row, abs=0.12)


@pytest.mark.parametrize("start_row", [100.0, 100.3, 100.5, 100.8])
def test_the_traced_extraction_reads_the_bump_at_its_true_distance(start_row: float) -> None:
    """The traced extraction, used by the pipeline, agrees."""
    extractor = SpectrumExtractor(radius=6, subtract_sky_background=False)

    profile, _, _ = extractor.extract_line_traced(
        _Image(_trail_with_a_bump()), (float(TRAIL_COLUMN), start_row), np.array([0.0, 1.0]), 50
    )

    assert _bump_position(profile) == pytest.approx(BUMP_ROW - start_row, abs=0.12)


def test_a_position_on_a_pixel_centre_reads_that_pixel_alone() -> None:
    """A whole-number start still reads whole pixels, unchanged."""
    extractor = SpectrumExtractor(radius=6, subtract_sky_background=False)
    data = _trail_with_a_bump()

    profile = extractor.extract_line(_Image(data), (float(TRAIL_COLUMN), 100.0), np.array([0.0, 1.0]), 30)

    assert profile[0] == pytest.approx(float(data[100, TRAIL_COLUMN - 6 : TRAIL_COLUMN + 7].sum()))


def test_a_position_past_the_last_row_still_reads_the_last_row() -> None:
    """The last sample, whose upper neighbour is off the image, is not lost."""
    extractor = SpectrumExtractor(radius=6, subtract_sky_background=False)

    profile = extractor.extract_line(
        _Image(_trail_with_a_bump()), (float(TRAIL_COLUMN), 180.4), np.array([0.0, 1.0]), 19
    )

    assert np.isfinite(profile[-1])
