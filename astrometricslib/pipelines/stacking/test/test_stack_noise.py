"""Tests for the stack's noise in empty sky, on a fixed flux scale.

A synthetic stack has a known coordinate solution, known white noise and a few
bright stars. The tests check that the noise is recovered, that the apertures
chosen on the first stack are reused on the next, that a missing solution is
reported and not guessed, and that a reference frame's solution is used only
when it fits the stack.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    APERTURE_FILE_NAME,
    APERTURE_RADIUS_PX,
    NO_WCS_REASON,
    RING_INNER_PX,
    RING_OUTER_PX,
    load_apertures,
    measure_stack_noise,
    signed_aperture_sum,
)

SIZE = 900
PIXEL_SCALE_ARCSEC = 2.0
SKY_LEVEL = 100.0
STAR_POSITIONS = [(150 + 90 * column, 200 + 110 * row) for column in range(8) for row in range(6)]


def _sky_header(scale_arcsec: float = PIXEL_SCALE_ARCSEC) -> fits.Header:
    """Build a header with a plain tangent-plane sky solution.

    Parameters
    ----------
    scale_arcsec : `float`, optional
        The pixel scale.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A header whose solution puts (RA, Dec) = (150, 2) at the image center.
    """
    header = fits.Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRPIX1"] = SIZE / 2
    header["CRPIX2"] = SIZE / 2
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 2.0
    header["CDELT1"] = -scale_arcsec / 3600.0
    header["CDELT2"] = scale_arcsec / 3600.0
    return header


def _sky_image(noise_sigma: float, seed: int, star_amplitude: float = 2000.0) -> np.ndarray:
    """Build a flat sky with white noise and bright round stars.

    Parameters
    ----------
    noise_sigma : `float`
        The standard deviation of the white noise, per pixel.
    seed : `int`
        Seed of the noise.
    star_amplitude : `float`, optional
        The peak of each star above the sky.

    Returns
    -------
    image : `numpy.ndarray`
        The image, float32.
    """
    generator = np.random.default_rng(seed)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = SKY_LEVEL + generator.normal(0.0, noise_sigma, (SIZE, SIZE))
    # The amplitudes differ, because the star finder skips stars as bright as
    # the brightest pixels (it takes those for saturated).
    amplitudes = np.linspace(0.3, 1.0, len(STAR_POSITIONS)) * star_amplitude
    for (x, y), amplitude in zip(STAR_POSITIONS, amplitudes, strict=True):
        image += amplitude * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * 2.0**2))
    return image.astype(np.float32)


def _write(path: Path, image: np.ndarray, header: fits.Header | None) -> str:
    """Write an image to a FITS file.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write.
    image : `numpy.ndarray`
        The pixels.
    header : `astropy.io.fits.Header` or `None`
        The header, or `None` for a bare one.

    Returns
    -------
    path : `str`
        The file path.
    """
    fits.PrimaryHDU(image, header=header).writeto(path, overwrite=True)
    return str(path)


def test_noise_of_white_sky_is_recovered_within_its_uncertainty(tmp_path: Path) -> None:
    """The measured spread matches the one the aperture area predicts."""
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), _sky_header())
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is not None
    area = math.pi * APERTURE_RADIUS_PX**2
    ring_pixels = math.pi * (RING_OUTER_PX**2 - RING_INNER_PX**2)
    expected = 2.0 * math.sqrt(area + area**2 / ring_pixels)
    result = outcome.result
    assert result.aperture_count >= 90
    assert abs(result.noise_adu - expected) < 3 * result.noise_uncertainty_adu
    assert result.noise_uncertainty_adu / result.noise_adu == pytest.approx(
        1 / math.sqrt(2 * (result.aperture_count - 1))
    )
    assert result.reference_star_count == 10
    assert result.reference_flux_adu > 1000
    assert result.normalized_noise == pytest.approx(result.noise_adu / result.reference_flux_adu)
    assert result.wcs_source == "stack header"
    assert result.apertures_created is True


def test_apertures_are_reused_by_a_later_stack_of_the_target(tmp_path: Path) -> None:
    """A second stack reuses the apertures; noise follows its pixels."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1), _sky_header())
    second = _write(tmp_path / "second.fits", _sky_image(4.0, seed=2), _sky_header())
    first_outcome = measure_stack_noise(first, None, apertures)
    stored = Path(apertures).read_text()
    second_outcome = measure_stack_noise(second, None, apertures)
    assert Path(apertures).read_text() == stored
    assert first_outcome.result is not None and second_outcome.result is not None
    assert second_outcome.result.apertures_created is False
    ratio = second_outcome.result.noise_adu / first_outcome.result.noise_adu
    assert ratio == pytest.approx(2.0, rel=0.25)
    assert second_outcome.result.reference_flux_adu == pytest.approx(
        first_outcome.result.reference_flux_adu, rel=0.02
    )
    assert load_apertures(apertures).created_from == "first.fits"
    assert json.loads(stored)["aperture_radius_px"] == APERTURE_RADIUS_PX


def test_apertures_follow_the_sky_when_the_stack_is_shifted(tmp_path: Path) -> None:
    """A stack shifted by 30 pixels is measured at the same sky positions."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1), _sky_header())
    measure_stack_noise(first, None, apertures)
    shifted_header = _sky_header()
    shifted_header["CRPIX1"] = SIZE / 2 + 30
    shifted_image = np.roll(_sky_image(2.0, seed=3), 30, axis=1)
    shifted = _write(tmp_path / "shifted.fits", shifted_image, shifted_header)
    outcome = measure_stack_noise(shifted, None, apertures)
    assert outcome.result is not None
    assert outcome.result.reference_star_count >= 5


def test_a_stack_with_no_solution_and_no_reference_frame_is_not_measured(tmp_path: Path) -> None:
    """Without a WCS the reason says so and no apertures are made."""
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), None)
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is None
    assert outcome.reason == NO_WCS_REASON
    assert not (tmp_path / APERTURE_FILE_NAME).exists()


def test_the_reference_frames_solution_is_used_when_the_stars_line_up(tmp_path: Path) -> None:
    """A stack with no solution takes the reference frame's, trimmed."""
    reference = _write(tmp_path / "reference.fits", _sky_image(2.0, seed=5), _sky_header())
    trimmed = _sky_image(2.0, seed=6)[20:, 10:]
    stack = _write(tmp_path / "stack.fits", trimmed, None)
    outcome = measure_stack_noise(stack, reference, str(tmp_path / APERTURE_FILE_NAME), crop_offset=(10, 20))
    assert outcome.result is not None
    assert outcome.result.wcs_source == "registration reference frame"
    stored = load_apertures(str(tmp_path / APERTURE_FILE_NAME))
    assert stored is not None
    assert stored.wcs_source == "registration reference frame"


def test_a_reference_frame_that_does_not_fit_the_stack_is_refused(tmp_path: Path) -> None:
    """If the bright stars do not match, no noise is measured."""
    reference_image = _sky_image(2.0, seed=5)[::-1].copy()
    reference = _write(tmp_path / "reference.fits", reference_image, _sky_header())
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=6), None)
    outcome = measure_stack_noise(stack, reference, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is None
    assert "does not line up" in outcome.reason
    assert not (tmp_path / APERTURE_FILE_NAME).exists()


def test_a_stack_at_another_pixel_scale_is_refused(tmp_path: Path) -> None:
    """Apertures are refused at a very different pixel scale."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1), _sky_header())
    measure_stack_noise(first, None, apertures)
    other = _write(tmp_path / "other.fits", _sky_image(2.0, seed=2), _sky_header(scale_arcsec=3.0))
    outcome = measure_stack_noise(other, None, apertures)
    assert outcome.result is None
    assert "pixel scale" in outcome.reason


def test_an_aperture_that_reaches_empty_edge_pixels_is_dropped() -> None:
    """Ring pixels at exactly zero (an empty stack edge) spoil a sum."""
    image = np.full((100, 100), 50.0)
    assert signed_aperture_sum(image, 50.0, 50.0) == pytest.approx(0.0, abs=1e-9)
    image[45:55, 45:55] = 0.0
    assert signed_aperture_sum(image, 50.0, 50.0) is None
    assert signed_aperture_sum(np.full((100, 100), 50.0), 5.0, 50.0) is None
