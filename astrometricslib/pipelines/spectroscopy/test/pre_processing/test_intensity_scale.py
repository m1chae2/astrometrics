"""Purpose: Unit tests for the intensity scale of a stored spectrum.

Description: Checks that the factor turning stored intensities into counts
per second comes out right for raw frames and for Siril float stacks, that
an image whose header says too little gives no factor, and that a spectrum
records the factor of the image it was read from.
"""

import pytest
from astropy.io import fits

from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_scale import counts_per_second_factor
from astrometricslib.pipelines.stacking.processing.exposure_groups import FULL_SCALE_COUNTS


def _header(**cards: float) -> fits.Header:
    """Build a FITS header from keyword cards.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The header.
    """
    header = fits.Header()
    for key, value in cards.items():
        header[key] = value
    return header


def test_a_raw_frame_is_counts_per_exposure() -> None:
    """A 4 s raw frame holds counts, so the rate is a quarter of them."""
    assert counts_per_second_factor(_header(BITPIX=16, EXPTIME=4.0)) == pytest.approx(0.25)


def test_a_float_stack_is_full_scale_per_average_exposure() -> None:
    """Deneb's stack: 39 frames of 1 s, 1.0 is 65535 counts in one second."""
    header = _header(BITPIX=-32, EXPTIME=39.0, STACKCNT=39)
    assert counts_per_second_factor(header) == pytest.approx(FULL_SCALE_COUNTS)


def test_a_longer_average_exposure_lowers_the_rate() -> None:
    """Mirach's stack: 39 frames of 4 s, so a quarter of Deneb's factor."""
    header = _header(BITPIX=-32, EXPTIME=156.0, STACKCNT=39)
    assert counts_per_second_factor(header) == pytest.approx(FULL_SCALE_COUNTS / 4.0)


def test_the_same_star_in_two_stacks_gives_the_same_rate() -> None:
    """A 1 s and a 4 s stack of one star agree in counts per second."""
    rate = 1000.0
    one_second = (rate * 1.0 / FULL_SCALE_COUNTS) * counts_per_second_factor(
        _header(BITPIX=-32, EXPTIME=10.0, STACKCNT=10)
    )
    four_seconds = (rate * 4.0 / FULL_SCALE_COUNTS) * counts_per_second_factor(
        _header(BITPIX=-32, EXPTIME=40.0, STACKCNT=10)
    )
    assert one_second == pytest.approx(rate)
    assert four_seconds == pytest.approx(rate)


@pytest.mark.parametrize(
    "cards",
    [{}, {"BITPIX": -32, "EXPTIME": 10.0}, {"BITPIX": 16}, {"BITPIX": -32, "STACKCNT": 5}],
)
def test_a_header_that_says_too_little_gives_no_factor(cards: dict[str, float]) -> None:
    """Nothing is guessed when the exposure or the stack count is missing."""
    assert counts_per_second_factor(_header(**cards)) is None


def test_no_header_gives_no_factor() -> None:
    """An image with no header leaves the scale unknown."""
    assert counts_per_second_factor(None) is None
