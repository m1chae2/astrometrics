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
from scipy.ndimage import gaussian_filter

from astrometricslib.pipelines.stacking.pre_processing import flat_calibration as fc

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
    """Blurring a noisy flat by the chosen width reaches the noise limit."""
    path = write_flat(tmp_path / "flat.fits", level=0.2, noise=0.045, seed=2)
    assessment = fc.assess_flats([path])
    assert assessment.smoothing_sigma_pixels is not None
    # Siril's `gauss` is SciPy's Gaussian filter in mirror mode, to 4e-7.
    blurred = gaussian_filter(
        np.asarray(fits.getdata(path), dtype=np.float64), assessment.smoothing_sigma_pixels, mode="mirror"
    )
    shape = vignette()
    leftover = (blurred / blurred.mean()) / (shape / shape.mean()) - 1.0
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


# ------------------------------------------------ full scale from the camera


def write_counts_flat(path: Path, mean_adu: float, seed: int, noise: float = 0.01) -> str:
    """Write a flat frame whose mean is a chosen number of counts.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write.
    mean_adu : `float`
        Mean pixel value in counts, stored unscaled in a 16-bit integer.
    seed : `int`
        Seed of the noise.
    noise : `float`, optional
        Relative noise of each pixel.

    Returns
    -------
    path : `str`
        The file written.
    """
    generator = np.random.default_rng(seed)
    data = mean_adu * (1.0 + noise * generator.standard_normal((SIZE, SIZE)))
    fits.writeto(path, np.clip(data, 0, 65535).astype(np.uint16), overwrite=True)
    return str(path)


def test_a_14_bit_flat_is_judged_against_the_camera_clip_ceiling(tmp_path: Path) -> None:
    """A D5300 flat at 4000 counts is 24% of 16383, not 6% of 65535."""
    path = write_counts_flat(tmp_path / "flat.fits", 4000.0, seed=40)
    guessed = fc.assess_flats([path])
    profiled = fc.assess_flats([path], camera="Nikon D5300")
    assert guessed.level_fraction == pytest.approx(4000.0 / 65535.0, rel=0.01)
    assert any(issue.startswith(fc.FAINT_FLAT_ISSUE_PREFIX) for issue in guessed.issues)
    assert profiled.level_fraction == pytest.approx(4000.0 / 16383.0, rel=0.01)
    assert not any(issue.startswith(fc.FAINT_FLAT_ISSUE_PREFIX) for issue in profiled.issues)
    assert not any(issue.startswith(fc.BRIGHT_FLAT_ISSUE_PREFIX) for issue in profiled.issues)


def test_a_14_bit_flat_at_8000_counts_passes_the_level_check(tmp_path: Path) -> None:
    """8000 counts is 49% of the D5300's 16383 and passes the 10-90% window."""
    path = write_counts_flat(tmp_path / "flat.fits", 8000.0, seed=41)
    assessment = fc.assess_flats([path], camera="Nikon D5300")
    assert assessment.level_fraction == pytest.approx(8000.0 / 16383.0, rel=0.01)
    assert fc.MINIMUM_FLAT_LEVEL_FRACTION < assessment.level_fraction < fc.MAXIMUM_FLAT_LEVEL_FRACTION
    assert assessment.full_scale == pytest.approx(16383.0)
    assert not any(issue.startswith(fc.FAINT_FLAT_ISSUE_PREFIX) for issue in assessment.issues)
    assert not any(issue.startswith(fc.BRIGHT_FLAT_ISSUE_PREFIX) for issue in assessment.issues)


def test_a_14_bit_flat_near_the_clip_ceiling_is_reported_as_bright(tmp_path: Path) -> None:
    """At 16000 counts a D5300 flat is nearly clipped. The guess missed it."""
    path = write_counts_flat(tmp_path / "flat.fits", 16000.0, seed=42, noise=0.002)
    guessed = fc.assess_flats([path])
    profiled = fc.assess_flats([path], camera="Nikon D5300")
    assert not any(issue.startswith(fc.BRIGHT_FLAT_ISSUE_PREFIX) for issue in guessed.issues)
    assert any(issue.startswith(fc.BRIGHT_FLAT_ISSUE_PREFIX) for issue in profiled.issues)


def test_the_assessment_records_where_the_full_scale_came_from(tmp_path: Path) -> None:
    """The diagnostics name the camera profile, or say the scale is a guess."""
    path = write_counts_flat(tmp_path / "flat.fits", 8000.0, seed=43)
    profiled = fc.assess_flats([path], camera="Nikon D5300").as_diagnostics()
    assert profiled["full_scale"] == pytest.approx(16383.0)
    assert str(profiled["full_scale_source"]).startswith(fc.FULL_SCALE_SOURCE_CAMERA_PROFILE)
    assert "Nikon D5300" in str(profiled["full_scale_source"])
    guessed = fc.assess_flats([path]).as_diagnostics()
    assert guessed["full_scale"] == pytest.approx(65535.0)
    assert guessed["full_scale_source"] == fc.FULL_SCALE_SOURCE_16_BIT_GUESS


def test_a_camera_without_a_profile_keeps_the_16_bit_guess(tmp_path: Path) -> None:
    """An unlisted camera gives the same result as no camera at all."""
    path = write_counts_flat(tmp_path / "flat.fits", 4000.0, seed=44)
    assessment = fc.assess_flats([path], camera="Some Unlisted Camera 9000")
    assert assessment.full_scale == pytest.approx(65535.0)
    assert assessment.full_scale_source == fc.FULL_SCALE_SOURCE_16_BIT_GUESS
    assert assessment.level_fraction == fc.assess_flats([path]).level_fraction


def test_an_asi533_flat_is_unchanged_by_the_camera_profile(tmp_path: Path) -> None:
    """The ASI533's 65532 ceiling is within 0.005% of the 16-bit guess."""
    path = write_counts_flat(tmp_path / "flat.fits", 30000.0, seed=45)
    guessed = fc.assess_flats([path])
    profiled = fc.assess_flats([path], camera="ZWO ASI533MM Pro")
    assert profiled.full_scale == pytest.approx(65532.0)
    assert profiled.level_fraction == pytest.approx(guessed.level_fraction, rel=1e-4)
    assert profiled.issues == guessed.issues
    assert profiled.noise_fraction == guessed.noise_fraction
    assert str(profiled.full_scale_source).startswith(fc.FULL_SCALE_SOURCE_CAMERA_PROFILE)


def test_a_floating_point_flat_keeps_a_full_scale_of_one_for_any_camera(tmp_path: Path) -> None:
    """Values within 0-1 are a normalised image, whatever the camera."""
    generator = np.random.default_rng(46)
    data = (0.4 * (1.0 + 0.01 * generator.standard_normal((SIZE, SIZE)))).astype(np.float32)
    path = tmp_path / "flat.fits"
    fits.writeto(path, data)
    assessment = fc.assess_flats([str(path)], camera="Nikon D5300")
    assert assessment.full_scale == pytest.approx(1.0)
    assert assessment.full_scale_source == fc.FULL_SCALE_SOURCE_FLOAT_RANGE
    assert assessment.level_fraction == pytest.approx(0.4, rel=0.01)
