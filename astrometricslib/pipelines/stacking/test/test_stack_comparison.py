"""Tests for measuring two stacks and comparing them.

The synthetic stacks have a known sky level, a known amount of pixel noise and
a known vignette, so each measurement can be checked against what was put in.
"""

from pathlib import Path

import numpy as np
import pytest

from astrometricslib.pipelines.stacking.post_processing import stack_comparison as sc

SIZE = 512
SKY = 1000.0


def make_stack(noise: float = 0.01, vignette: float = 0.0, seed: int = 0) -> np.ndarray:
    """Build a sky with noise and a radial vignette.

    Parameters
    ----------
    noise : `float`
        Pixel noise as a fraction of the sky.
    vignette : `float`
        Fractional dimming at the corners.
    seed : `int`
        Seed of the noise.

    Returns
    -------
    stack : `numpy.ndarray`
        A square image with the given sky level.
    """
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - SIZE / 2, x - SIZE / 2) / (SIZE / 2 * np.sqrt(2))
    sky = SKY * (1.0 - vignette * radius**2)
    return sky * (1.0 + noise * np.random.default_rng(seed).standard_normal((SIZE, SIZE)))


def test_the_sky_level_and_noise_are_measured() -> None:
    """The sky is the median; the noise comes from neighbour differences."""
    measured = sc.measure_stack_array(make_stack(noise=0.02))

    assert measured.sky_level == pytest.approx(SKY, rel=0.01)
    assert measured.noise_fraction == pytest.approx(0.02, rel=0.1)


def test_a_vignette_raises_the_flatness_numbers() -> None:
    """A stack with a 5% vignette is less flat than one without."""
    flat = sc.measure_stack_array(make_stack(vignette=0.0))
    vignetted = sc.measure_stack_array(make_stack(vignette=0.05))

    assert vignetted.flatness_rms > 5 * flat.flatness_rms
    # The darkest block sits a little inside the corner, so just under 5%.
    assert 0.03 < vignetted.flatness_peak_to_peak < 0.06


def test_a_bright_star_does_not_change_the_flatness() -> None:
    """Blocks holding a star are dropped, so the sky is what is measured."""
    plain = make_stack()
    starred = plain.copy()
    starred[200:204, 300:304] += 60000.0

    assert sc.measure_stack_array(starred).flatness_rms == pytest.approx(
        sc.measure_stack_array(plain).flatness_rms, rel=0.2
    )


def test_a_blank_stack_cannot_be_measured() -> None:
    """An all-zero stack has no sky, so measuring it raises."""
    with pytest.raises(ValueError, match="no measurable sky"):
        sc.measure_stack_array(np.zeros((SIZE, SIZE)))


def test_the_comparison_reports_the_change_of_each_measurement() -> None:
    """New flats show up as a large drop in flatness and none in noise."""
    before = sc.measure_stack_array(make_stack(vignette=0.05, seed=1), "before.fits")
    after = sc.measure_stack_array(make_stack(vignette=0.01, seed=2), "after.fits")

    comparison = sc.compare_measurements(before, after)

    assert comparison.before.path == "before.fits"
    assert comparison.changes["flatness_rms"] < -0.5
    assert abs(comparison.changes["noise_fraction"]) < 0.05
    assert any("flatness (rms)" in line and "down" in line for line in comparison.summary)
    assert any("pixel noise" in line and "unchanged" in line for line in comparison.summary)


def test_compare_stacks_reads_the_files(tmp_path: Path) -> None:
    """The file form measures both stacks from disk."""
    from astropy.io import fits

    fits.writeto(tmp_path / "a.fits", make_stack(seed=1).astype(np.float32))
    fits.writeto(tmp_path / "b.fits", make_stack(seed=2).astype(np.float32))

    comparison = sc.compare_stacks(str(tmp_path / "a.fits"), str(tmp_path / "b.fits"))

    assert comparison.before.sky_level == pytest.approx(SKY, rel=0.01)
    assert len(comparison.summary) == 5
