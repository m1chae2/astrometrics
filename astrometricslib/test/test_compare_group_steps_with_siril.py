"""Tests for the measuring helpers of the Siril comparison script.

Siril is not started. The helpers that measure leftover misalignment are
checked on synthetic star fields whose true offset is known.
"""

import numpy as np
import pytest
from scipy.ndimage import shift as shift_image

from astrometricslib.scripts.compare_group_steps_with_siril import (
    correlation_residual,
    find_manifests,
    star_centroids,
    star_offset,
)

SIZE = 400


def star_field(seed: int = 0, count: int = 60) -> np.ndarray:
    """Build a noisy sky with many small, unsaturated Gaussian stars.

    Parameters
    ----------
    seed : `int`
        Seed of the random generator.
    count : `int`
        The number of stars.

    Returns
    -------
    image : `numpy.ndarray`
        A square image with stars of different brightness.
    """
    generator = np.random.default_rng(seed)
    image = 0.01 + 0.0005 * generator.standard_normal((SIZE, SIZE))
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    for _ in range(count):
        row, column = generator.uniform(30, SIZE - 30, 2)
        brightness = generator.uniform(0.05, 0.3)
        image += brightness * np.exp(-((rows - row) ** 2 + (columns - column) ** 2) / (2 * 1.8**2))
    return image


def test_star_centres_are_found_to_a_fraction_of_a_pixel() -> None:
    """An isolated, moderately bright star is centred to 0.1 pixel."""
    image = np.full((SIZE, SIZE), 0.01)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image += 0.2 * np.exp(-((rows - 200.3) ** 2 + (columns - 150.7) ** 2) / (2 * 1.8**2))
    # A much brighter star elsewhere: stars above half the peak are skipped.
    image += 0.9 * np.exp(-((rows - 60.0) ** 2 + (columns - 300.0) ** 2) / (2 * 1.8**2))
    image += 0.0005 * np.random.default_rng(3).standard_normal(image.shape)
    centres = star_centroids(image)
    assert len(centres) == 1
    assert centres[0] == pytest.approx([200.3, 150.7], abs=0.1)


def test_the_star_offset_recovers_a_known_small_shift() -> None:
    """Two views of the same stars, one moved, give the shift."""
    image = star_field()
    moved = shift_image(image, (0.6, -0.4), order=3)
    offset = star_offset(image, moved)
    assert offset is not None
    assert offset[0] == pytest.approx(0.6, abs=0.15)
    assert offset[1] == pytest.approx(-0.4, abs=0.15)
    assert offset[2] >= 8


def test_too_few_stars_gives_no_offset() -> None:
    """A blank image has no stars to match."""
    assert star_offset(star_field(), np.full((SIZE, SIZE), 0.01)) is None


def test_the_phase_correlation_residual_matches_a_known_shift() -> None:
    """The leftover shift is measured to a few hundredths of a pixel."""
    image = star_field(seed=1)
    moved = shift_image(image, (2.0, -1.5), order=3)
    residual = correlation_residual(image, moved, None)
    assert residual[0] == pytest.approx(-2.0, abs=0.1)
    assert residual[1] == pytest.approx(1.5, abs=0.1)


def test_manifests_are_found_newest_first(tmp_path: pytest.TempPathFactory) -> None:
    """Group manifests are listed with the most recent first."""
    folder = tmp_path / "M 81" / "groups"
    folder.mkdir(parents=True)
    older, newer = folder / "a_manifest.json", folder / "b_manifest.json"
    older.write_text("{}")
    newer.write_text("{}")
    older.touch()
    newer.touch()
    import os

    os.utime(older, (1, 1))
    assert find_manifests(str(tmp_path), "M 81") == [str(newer), str(older)]
    assert find_manifests(str(tmp_path), "Nothing") == []
