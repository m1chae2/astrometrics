"""Tests for the flat-frame checks and the smoothed master flat.

A flat frame's noise is copied into every light frame, so a flat set with
few or faint frames leaves a fixed noise pattern in the stack. These tests
check that the noise is measured correctly (with and without a vignette),
that the smoothing width is the one that reaches the noise limit, that good
flat sets are left alone, and that the smoothed master keeps the vignette.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking import flat_calibration as fc

SIZE = 160


def vignette(size: int = SIZE, depth: float = 0.08) -> np.ndarray:
    """Make a smooth radial falloff, 1.0 at the centre.

    Returns
    -------
    shape : `numpy.ndarray`
        The relative response across a square frame.
    """
    y, x = np.mgrid[0:size, 0:size]
    radius = np.hypot(y - size / 2, x - size / 2) / (size / 2)
    return 1.0 - depth * radius**2


def write_flat(path: Path, level: float, noise: float, seed: int, pattern: np.ndarray | None = None) -> str:
    """Write a 16-bit flat frame with a vignette and white noise.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write.
    level : `float`
        Mean brightness as a fraction of full scale (65535).
    noise : `float`
        Relative noise of each pixel.
    seed : `int`
        Seed of the noise.
    pattern : `numpy.ndarray`, optional
        A fixed multiplicative pattern shared by every frame of a set.

    Returns
    -------
    path : `str`
        The file written.
    """
    generator = np.random.default_rng(seed)
    base = level * 65535.0 * vignette()
    if pattern is not None:
        base = base * pattern
    data = base * (1.0 + noise * generator.standard_normal(base.shape))
    fits.writeto(path, np.clip(data, 0, 65535).astype(np.uint16), overwrite=True)
    return str(path)


def test_noise_of_a_frame_is_measured_through_a_vignette() -> None:
    """The neighbour-difference estimate ignores the smooth vignette."""
    generator = np.random.default_rng(1)
    frame = 20000.0 * vignette() * (1.0 + 0.04 * generator.standard_normal((SIZE, SIZE)))
    assert fc.measure_frame_noise_fraction(frame) == pytest.approx(0.04, rel=0.1)


def test_a_frame_with_no_positive_median_has_no_noise_estimate() -> None:
    """An all-zero frame cannot be given a relative noise."""
    assert fc.measure_frame_noise_fraction(np.zeros((8, 8))) is None


def test_smoothing_width_is_none_when_noise_is_within_the_limit() -> None:
    """A quiet master flat is not smoothed."""
    assert fc.smoothing_sigma_for_noise(fc.MAXIMUM_FLAT_NOISE_FRACTION) is None
    assert fc.smoothing_sigma_for_noise(0.001) is None


def test_smoothing_width_follows_the_noise_and_is_clipped() -> None:
    """The width grows with the noise, between the two limits."""
    moderate = fc.smoothing_sigma_for_noise(0.02)
    assert moderate is not None
    assert fc.MINIMUM_SMOOTHING_SIGMA_PIXELS < moderate < fc.MAXIMUM_SMOOTHING_SIGMA_PIXELS
    assert fc.smoothing_sigma_for_noise(0.0051) == fc.MINIMUM_SMOOTHING_SIGMA_PIXELS
    assert fc.smoothing_sigma_for_noise(0.9) == fc.MAXIMUM_SMOOTHING_SIGMA_PIXELS


def test_the_chosen_width_brings_white_noise_down_to_the_limit(tmp_path: Path) -> None:
    """Smoothing a noisy flat by the chosen width reaches the noise limit."""
    path = write_flat(tmp_path / "flat.fits", level=0.2, noise=0.045, seed=2)
    assessment = fc.assess_flats([path])
    assert assessment.smoothing_sigma_pixels is not None
    master = fc.build_smoothed_master_flat([path], [], assessment.smoothing_sigma_pixels)
    assert master is not None
    shape = vignette()
    # What is left after removing the known vignette is the master's noise.
    leftover = (master / master.mean()) / (shape / shape.mean()) - 1.0
    assert float(np.std(leftover[20:-20, 20:-20])) < 1.6 * fc.MAXIMUM_FLAT_NOISE_FRACTION


def test_a_single_faint_noisy_flat_is_reported_and_smoothed(tmp_path: Path) -> None:
    """One flat at 1% of full scale has far too much noise."""
    path = write_flat(tmp_path / "flat.fits", level=0.01, noise=0.045, seed=3)
    assessment = fc.assess_flats([path])
    assert assessment.frame_count == 1
    assert assessment.level_fraction == pytest.approx(0.01, rel=0.1)
    assert assessment.noise_fraction == pytest.approx(0.045, rel=0.15)
    assert assessment.needs_smoothing
    assert any("faint" in issue for issue in assessment.issues)
    assert any("take more flats" in issue for issue in assessment.issues)


def test_a_good_set_of_flats_has_no_issues(tmp_path: Path) -> None:
    """Forty bright, quiet frames leave a master flat below the noise limit."""
    paths = [write_flat(tmp_path / f"flat_{i}.fits", level=0.3, noise=0.01, seed=10 + i) for i in range(40)]
    assessment = fc.assess_flats(paths)
    assert assessment.issues == []
    assert not assessment.needs_smoothing
    assert assessment.noise_fraction == pytest.approx(0.01 / np.sqrt(fc.MAXIMUM_FRAMES_AVERAGED), rel=0.2)


def test_noise_from_two_frames_ignores_a_shared_pixel_pattern(tmp_path: Path) -> None:
    """Pixel sensitivity differences cancel when two frames are subtracted."""
    pattern = 1.0 + 0.03 * np.random.default_rng(4).standard_normal((SIZE, SIZE))
    paths = [write_flat(tmp_path / f"flat_{i}.fits", 0.3, 0.004, 20 + i, pattern) for i in range(2)]
    one_frame = fc.measure_frame_noise_fraction(np.asarray(fits.getdata(paths[0]), dtype=np.float64))
    assert one_frame is not None
    assert one_frame > 0.025  # the single-frame estimate counts the pattern as noise
    assessment = fc.assess_flats(paths)
    assert assessment.noise_fraction == pytest.approx(0.004 / np.sqrt(2), rel=0.2)


def test_a_flat_near_saturation_is_reported(tmp_path: Path) -> None:
    """Flats at the top of the range may not respond linearly."""
    path = write_flat(tmp_path / "flat.fits", level=0.97, noise=0.002, seed=5)
    assessment = fc.assess_flats([path])
    assert any("bright" in issue for issue in assessment.issues)


def test_an_unreadable_set_reports_one_issue(tmp_path: Path) -> None:
    """A path that is not a FITS file gives a clear issue and no numbers."""
    bad = tmp_path / "bad.fits"
    bad.write_text("not a fits file")
    assessment = fc.assess_flats([str(bad)])
    assert assessment.noise_fraction is None
    assert assessment.issues == ["no flat frame could be read"]
    assert fc.assess_flats([]).issues == ["no flat frame could be read"]


def test_the_smoothed_master_keeps_the_vignette_and_has_mean_one_half(tmp_path: Path) -> None:
    """Smoothing removes pixel noise, not the corner-to-centre falloff."""
    path = write_flat(tmp_path / "flat.fits", level=0.2, noise=0.045, seed=6)
    master = fc.build_smoothed_master_flat([path], [], 3.0)
    assert master is not None
    assert master.dtype == np.float32
    assert float(master.mean()) == pytest.approx(0.5, abs=1e-4)
    centre = float(master[SIZE // 2 - 5 : SIZE // 2 + 5, SIZE // 2 - 5 : SIZE // 2 + 5].mean())
    corner = float(master[:8, :8].mean())
    expected = vignette()[:8, :8].mean() / vignette()[75:85, 75:85].mean()
    assert corner / centre == pytest.approx(expected, rel=0.03)


def test_the_bias_is_subtracted_before_the_flat_is_normalised(tmp_path: Path) -> None:
    """Removing the bias gives the same master as a flat without offset."""
    plain = write_flat(tmp_path / "plain.fits", level=0.2, noise=0.0, seed=7)
    offset_data = np.asarray(fits.getdata(plain), dtype=np.float64) + 2000.0
    offset = tmp_path / "offset.fits"
    fits.writeto(offset, offset_data.astype(np.uint16), overwrite=True)
    bias = tmp_path / "bias.fits"
    fits.writeto(bias, np.full((SIZE, SIZE), 2000, dtype=np.uint16), overwrite=True)
    without_bias = fc.build_smoothed_master_flat([str(offset)], [], 2.0)
    with_bias = fc.build_smoothed_master_flat([str(offset)], [str(bias)], 2.0)
    reference = fc.build_smoothed_master_flat([plain], [], 2.0)
    assert with_bias is not None
    assert reference is not None
    assert without_bias is not None
    np.testing.assert_allclose(with_bias, reference, rtol=1e-3)
    assert not np.allclose(without_bias, reference, rtol=1e-3)


def test_the_master_flat_is_written_with_a_clean_header(tmp_path: Path) -> None:
    """The saved file is a float image with the template's camera keywords."""
    template = write_flat(tmp_path / "flat.fits", level=0.2, noise=0.01, seed=8)
    with fits.open(template, mode="update", memmap=False) as hdul:
        hdul[0].header["INSTRUME"] = "ZWO CCD ASI533MM Pro"
    master = np.full((SIZE, SIZE), 0.5, dtype=np.float32)
    destination = tmp_path / "out" / "flat_stacked.fits"
    fc.write_master_flat(str(destination), master, template)
    with fits.open(destination, memmap=False) as hdul:
        header = hdul[0].header
        assert header["INSTRUME"] == "ZWO CCD ASI533MM Pro"
        assert header["IMAGETYP"] == "Master Flat"
        assert "BZERO" not in header
        assert hdul[0].data.dtype == np.dtype(">f4")


def test_a_flat_listed_twice_counts_once_and_is_not_noiseless(tmp_path: Path) -> None:
    """The same file listed twice is one frame, not a noiseless flat."""
    path = write_flat(tmp_path / "flat.fits", level=0.01, noise=0.045, seed=30)
    link = tmp_path / "link.fits"
    link.symlink_to(path)
    assessment = fc.assess_flats([path, path, str(link)])
    assert assessment.frame_count == 1
    assert assessment.noise_fraction == pytest.approx(0.045, rel=0.15)
    assert assessment.needs_smoothing


def test_two_identical_copies_are_not_read_as_a_noiseless_flat(tmp_path: Path) -> None:
    """Two files with the same pixels use the one-frame noise estimate."""
    path = write_flat(tmp_path / "a.fits", level=0.01, noise=0.045, seed=31)
    copy = tmp_path / "b.fits"
    copy.write_bytes(Path(path).read_bytes())
    assessment = fc.assess_flats([path, str(copy)])
    assert assessment.noise_fraction is not None
    assert assessment.noise_fraction > 0.01
    assert assessment.needs_smoothing
