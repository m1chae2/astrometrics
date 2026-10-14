"""Purpose: Check the wavelength-dependent trail width of the synthetic frame.

Description: `make_spectral_frame` can give the trail a cross-profile width
that depends on wavelength, to imitate a spectrum whose red end is out of
focus. These tests check that the option changes nothing when it is not
used, that a constant table gives the same image as the plain width, and that
the width of the image at a column follows the table (measured from the
noise-free image).
"""

import numpy as np
import pytest

from astrometricslib.test.synthetic import make_spectral_frame

NOISE_FREE = {"add_noise": False, "lines": ()}
DISPERSION = 11.0


def _measured_sigma(image: np.ndarray, column: int, centre_row: float) -> float:
    """Measure the width of the trail in one column from the pixel values.

    Parameters
    ----------
    image : `numpy.ndarray`
        A noise-free frame, sky included.
    column : `int`
        The column to measure.
    centre_row : `float`
        The row of the trail centre in that column.

    Returns
    -------
    sigma : `float`
        The standard deviation of the light in the column around the trail
        centre, in pixels, with the sky removed.
    """
    rows = np.arange(image.shape[0])
    profile = image[:, column] - 150.0
    window = np.abs(rows - centre_row) < 15
    weights = profile[window]
    mean = np.sum(rows[window] * weights) / weights.sum()
    return float(np.sqrt(np.sum((rows[window] - mean) ** 2 * weights) / weights.sum()))


def test_without_the_option_the_frame_is_unchanged() -> None:
    """Leaving the option out, or `None`, gives the same image."""
    plain = make_spectral_frame(**NOISE_FREE)
    explicit = make_spectral_frame(trace_sigma_by_wavelength=None, **NOISE_FREE)

    assert np.array_equal(plain.image, explicit.image)
    assert plain.trace_sigma_by_wavelength is None
    assert plain.trace_sigma_at(5000.0) == plain.trace_sigma_px


def test_a_constant_table_matches_the_plain_width() -> None:
    """A table that never changes gives the image of the plain width."""
    plain = make_spectral_frame(trace_sigma_px=2.2, **NOISE_FREE)
    table = make_spectral_frame(
        trace_sigma_px=2.2, trace_sigma_by_wavelength=((3000.0, 2.2), (9000.0, 2.2)), **NOISE_FREE
    )

    assert np.allclose(plain.image, table.image, rtol=0, atol=1e-6)


def test_the_trail_width_follows_the_table() -> None:
    """The width in a column is the table value at that column's wavelength."""
    nodes = ((4200.0, 1.6), (8000.0, 3.0))
    frame = make_spectral_frame(
        dispersion_a_per_px=DISPERSION,
        trace_sigma_by_wavelength=nodes,
        trail_length_px=800,
        continuum_adu=20000.0,
        **NOISE_FREE,
    )
    x0 = frame.zero_order_xy[0]

    for wavelength in (4500.0, 6000.0, 7800.0):
        column = round(x0 + wavelength / DISPERSION)
        measured = _measured_sigma(frame.image, column, float(frame.trace_center_y(column)))
        assert measured == pytest.approx(frame.trace_sigma_at(wavelength), rel=0.02)


def test_the_width_is_held_outside_the_table_and_the_zero_order_keeps_its_own() -> None:
    """Outside the table the end values apply; the zero order keeps its own."""
    frame = make_spectral_frame(
        trace_sigma_px=2.5,
        trace_sigma_by_wavelength=((5000.0, 2.0), (6000.0, 3.0)),
        **NOISE_FREE,
    )

    assert frame.trace_sigma_at(100.0) == pytest.approx(2.0)
    assert frame.trace_sigma_at(9000.0) == pytest.approx(3.0)
    assert frame.trace_sigma_at(5500.0) == pytest.approx(2.5)
    assert frame.trace_sigma_px == pytest.approx(2.5)
    x0, y0 = frame.zero_order_xy
    assert _measured_sigma(frame.image, int(x0), y0) == pytest.approx(2.5, rel=0.05)
