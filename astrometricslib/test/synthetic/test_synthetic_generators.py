"""Purpose: Check that the synthetic frame generators give their stated truth.

Description: The generators in this folder are the foundation for later
truth-known tests, so they need their own tests. These tests check that a
star's flux and position come out as requested, that a drifting sequence
follows its documented rule, that the spectral frame puts its lines and
its trail where it says, and that the random noise repeats for one seed
and changes for another.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import SpectrumExtractor
from astrometricslib.test.synthetic import (
    SyntheticSpectralFrame,
    SyntheticStar,
    drifted_stars,
    make_drifted_sequence,
    make_photometry_fits,
    make_photometry_frame,
    make_spectral_frame,
)

NOISE_FREE = {"sky_adu": 0.0, "add_noise": False}


def _centroid(image: np.ndarray) -> tuple[float, float]:
    """Return the intensity-weighted centre (x, y) of an image.

    Parameters
    ----------
    image : `numpy.ndarray`
        A sky-free image.

    Returns
    -------
    centroid : `tuple` [`float`, `float`]
        Column and row of the centre of light.
    """
    rows, columns = np.indices(image.shape)
    total = image.sum()
    return float((columns * image).sum() / total), float((rows * image).sum() / total)


def test_noise_free_star_flux_matches_requested_flux() -> None:
    """The pixels of a noise-free star on zero sky add up to its flux."""
    star = SyntheticStar(x=100.3, y=90.7, flux_adu=50_000.0, fwhm_px=3.0)
    image = make_photometry_frame([star], **NOISE_FREE)
    assert image.sum() == pytest.approx(50_000.0, rel=1e-3)


def test_pixel_integration_is_not_a_point_sample() -> None:
    """A narrow star between pixels still keeps all of its flux."""
    star = SyntheticStar(x=64.5, y=64.5, flux_adu=10_000.0, fwhm_px=1.0)
    image = make_photometry_frame([star], **NOISE_FREE)
    assert image.sum() == pytest.approx(10_000.0, rel=1e-3)


def test_half_pixel_shift_moves_centroid_by_half_pixel() -> None:
    """Moving a star by 0.5 px in x moves its centroid by 0.5 px."""
    base = make_photometry_frame([SyntheticStar(100.0, 80.0, 30_000.0, 3.0)], **NOISE_FREE)
    shifted = make_photometry_frame([SyntheticStar(100.5, 80.0, 30_000.0, 3.0)], **NOISE_FREE)
    base_x, base_y = _centroid(base)
    shifted_x, shifted_y = _centroid(shifted)
    assert shifted_x - base_x == pytest.approx(0.5, abs=1e-3)
    assert shifted_y - base_y == pytest.approx(0.0, abs=1e-3)
    assert base_x == pytest.approx(100.0, abs=1e-3)


def test_noisy_frame_has_expected_sky_and_noise() -> None:
    """Sky level and the Poisson-plus-read noise match the stated model."""
    image = make_photometry_frame([], shape=(300, 300), sky_adu=400.0, read_noise_adu=5.0, seed=3)
    assert image.mean() == pytest.approx(400.0, abs=0.5)
    # Poisson variance 400 (gain 1) plus read variance 25.
    assert image.std() == pytest.approx(np.sqrt(425.0), rel=0.03)


def test_gain_reduces_poisson_noise_in_adu() -> None:
    """A gain of 4 e-/ADU cuts the Poisson noise in ADU by a factor of 2."""
    image = make_photometry_frame([], shape=(300, 300), sky_adu=400.0, read_noise_adu=0.0, gain_e_per_adu=4.0)
    assert image.std() == pytest.approx(np.sqrt(400.0 / 4.0), rel=0.03)


def test_frame_clips_at_saturation() -> None:
    """No pixel exceeds the saturation level."""
    star = SyntheticStar(50.0, 50.0, 1e7, 3.0)
    image = make_photometry_frame([star], saturation_adu=30_000.0)
    assert image.max() == pytest.approx(30_000.0)
    assert image.dtype == np.float64


def test_fits_file_round_trip(tmp_path: Path) -> None:
    """The FITS file holds the returned array and the requested cards."""
    path = tmp_path / "synthetic.fits"
    stars = [SyntheticStar(40.0, 40.0, 5000.0, 3.0)]
    written = make_photometry_fits(
        path,
        stars,
        date_obs="2026-05-01T00:00:00",
        exptime_s=30.0,
        extra_header={"FILTER": "L"},
        shape=(80, 80),
        seed=5,
    )
    with fits.open(path, memmap=False) as hdul:
        header = hdul[0].header
        data = np.array(hdul[0].data)
    assert header["DATE-OBS"] == "2026-05-01T00:00:00"
    assert header["EXPTIME"] == pytest.approx(30.0)
    assert header["FILTER"] == "L"
    np.testing.assert_array_equal(data, written)


def test_sequence_drift_follows_documented_rule() -> None:
    """Frame k centroid sits at start + k * drift (no rotation)."""
    star = SyntheticStar(100.0, 100.0, 40_000.0, 3.0)
    frames = make_drifted_sequence([star], 5, drift_px_per_frame=(0.3, -0.2), **NOISE_FREE)
    assert len(frames) == 5
    for k, frame in enumerate(frames):
        x, y = _centroid(frame)
        assert x == pytest.approx(100.0 + 0.3 * k, abs=1e-3)
        assert y == pytest.approx(100.0 - 0.2 * k, abs=1e-3)


def test_sequence_rotation_follows_documented_rule() -> None:
    """Rotation turns +x toward +y about the frame centre, then drifts."""
    shape = (201, 201)
    star = SyntheticStar(150.0, 100.0, 40_000.0, 3.0)  # 50 px right of centre
    moved = drifted_stars([star], 3, (0.0, 0.0), 30.0, shape)[0]
    # After 90 degrees the star is 50 px above the centre in +y.
    assert moved.x == pytest.approx(100.0, abs=1e-9)
    assert moved.y == pytest.approx(150.0, abs=1e-9)
    # Frame 0 is the input, unchanged.
    first = drifted_stars([star], 0, (5.0, 5.0), 30.0, shape)[0]
    assert (first.x, first.y) == (150.0, 100.0)


def test_flux_scale_callable_applies_to_every_star() -> None:
    """A callable multiplier scales the flux of all stars together."""
    stars = [SyntheticStar(60.0, 60.0, 10_000.0, 3.0), SyntheticStar(160.0, 160.0, 20_000.0, 3.0)]
    frames = make_drifted_sequence(stars, 3, (0.0, 0.0), flux_scale=lambda k: 1.0 - 0.1 * k, **NOISE_FREE)
    for k, frame in enumerate(frames):
        assert frame.sum() == pytest.approx(30_000.0 * (1.0 - 0.1 * k), rel=1e-3)


def test_flux_scale_dict_applies_to_chosen_star_only() -> None:
    """A dict multiplier scales only the star it names."""
    stars = [SyntheticStar(60.0, 60.0, 10_000.0, 3.0), SyntheticStar(160.0, 160.0, 20_000.0, 3.0)]
    frames = make_drifted_sequence(
        stars, 3, (0.0, 0.0), flux_scale={0: lambda k: 1.0 if k != 1 else 0.5}, **NOISE_FREE
    )
    first_star_flux = [f[:110, :110].sum() for f in frames]
    second_star_flux = [f[110:, 110:].sum() for f in frames]
    assert first_star_flux == pytest.approx([10_000.0, 5_000.0, 10_000.0], rel=1e-3)
    assert second_star_flux == pytest.approx([20_000.0] * 3, rel=1e-3)


def test_sequence_frames_use_seed_plus_frame_index() -> None:
    """Frame k of a sequence equals a single frame made with seed + k."""
    stars = [SyntheticStar(100.0, 100.0, 10_000.0, 3.0)]
    frames = make_drifted_sequence(stars, 3, (0.0, 0.0), seed=10)
    for k, frame in enumerate(frames):
        np.testing.assert_array_equal(frame, make_photometry_frame(stars, seed=10 + k))
    assert not np.array_equal(frames[0], frames[1])


def _line_depth(frame_image: np.ndarray, truth: SyntheticSpectralFrame, line_index: int) -> float:
    """Measure the depth of one line in a noise-free, sky-free frame.

    Sums the light in a +/-12 row window around the trail centre of each
    column. Compares the lowest column within 4 pixels of the expected line
    column with the median of nearby columns that are away from the line.

    Parameters
    ----------
    frame_image : `numpy.ndarray`
        The image to measure.
    truth : `SyntheticSpectralFrame`
        The frame that holds the expected line columns.
    line_index : `int`
        Which line to measure.

    Returns
    -------
    depth : `float`
        Fractional depth of the dip relative to the local continuum.
    """
    expected = truth.line_columns_px[line_index]
    spectrum = np.empty(frame_image.shape[1])
    for x in range(frame_image.shape[1]):
        row = round(float(truth.trace_center_y(x)))
        spectrum[x] = frame_image[max(row - 12, 0) : row + 13, x].sum()
    centre = round(expected)
    window = spectrum[centre - 4 : centre + 5]
    continuum = np.median(np.r_[spectrum[centre - 25 : centre - 10], spectrum[centre + 11 : centre + 26]])
    assert abs((centre - 4 + int(np.argmin(window))) - expected) <= 1.0
    return float(1.0 - window.min() / continuum)


@pytest.mark.parametrize(("line_index", "depth"), [(0, 0.5), (1, 0.6)])
def test_spectral_line_depth_matches_request(line_index: int, depth: float) -> None:
    """The dip in the summed trail has the requested depth within 10%."""
    truth = make_spectral_frame(sky_adu=0.0, add_noise=False)
    measured = _line_depth(truth.image, truth, line_index)
    assert measured == pytest.approx(depth, rel=0.10)


def test_spectral_line_columns_follow_straight_line_model() -> None:
    """The expected columns equal x0 + wavelength / dispersion."""
    truth = make_spectral_frame()
    assert truth.line_columns_px == pytest.approx((60.0 + 4861.0 / 11.0, 60.0 + 6563.0 / 11.0))
    assert truth.line_wavelengths_a == (4861.0, 6563.0)


def test_noisy_spectrum_minimum_lands_on_expected_column() -> None:
    """With noise on, the deepest column is still next to the line."""
    truth = make_spectral_frame(seed=1)
    for expected in truth.line_columns_px:
        spectrum = np.empty(truth.image.shape[1])
        for x in range(truth.image.shape[1]):
            row = round(float(truth.trace_center_y(x)))
            window = truth.image[row - 12 : row + 13, x]
            spectrum[x] = window.sum() - 25 * 150.0
        centre = round(expected)
        found = centre - 6 + int(np.argmin(spectrum[centre - 6 : centre + 7]))
        assert abs(found - expected) <= 1.5


def test_spectral_zero_order_and_trail_geometry() -> None:
    """The zero order and trail centre are where the docstring says."""
    truth = make_spectral_frame(sky_adu=0.0, add_noise=False)
    image = truth.image
    peak_row, peak_col = np.unravel_index(np.argmax(image), image.shape)
    assert (peak_col, peak_row) == (60, 128)
    for x in (200, 400, 700):
        rows = np.arange(image.shape[0])
        column = image[:, x]
        centre = float((rows * column).sum() / column.sum())
        expected = 128.0 - np.tan(np.radians(2.0)) * (x - 60.0)
        assert centre == pytest.approx(expected, abs=0.05)
        assert truth.trace_center_y(x) == pytest.approx(expected)
    assert image[:, 762:].max() < 1e-6  # nothing beyond the trail end (x = 760)


def test_extractor_follows_the_generated_trail() -> None:
    """The real extractor's tilt rule tracks the trail only for this sign."""
    truth = make_spectral_frame(sky_adu=0.0, add_noise=False)
    fake_image = SimpleNamespace(data=truth.image)
    extractor = SpectrumExtractor(radius=8, subtract_sky_background=False)
    kwargs = {
        "flare_offset_pixels": 40.0,
        "max_offset_pixels": 600.0,
        "radius": 8,
        "orientation": "horizontal",
    }
    right, _, _ = extractor.extract_with_flare_mask(fake_image, (60.0, 128.0), angle_degrees=2.0, **kwargs)
    wrong, _, _ = extractor.extract_with_flare_mask(fake_image, (60.0, 128.0), angle_degrees=-2.0, **kwargs)
    assert right.sum() > 0.9 * 560 * 3000.0
    assert wrong.sum() < 0.5 * right.sum()


def test_same_seed_repeats_and_different_seed_differs() -> None:
    """Equal seeds give identical arrays. Different seeds do not."""
    stars = [SyntheticStar(100.0, 100.0, 10_000.0, 3.0)]
    np.testing.assert_array_equal(make_photometry_frame(stars, seed=4), make_photometry_frame(stars, seed=4))
    assert not np.array_equal(make_photometry_frame(stars, seed=4), make_photometry_frame(stars, seed=5))
    first = make_spectral_frame(seed=2).image
    np.testing.assert_array_equal(first, make_spectral_frame(seed=2).image)
    assert not np.array_equal(first, make_spectral_frame(seed=3).image)


def test_spectral_line_outside_trail_raises() -> None:
    """A line past the end of the trail is rejected."""
    with pytest.raises(ValueError, match="outside the trail"):
        make_spectral_frame(lines=((20000.0, 0.5),))
