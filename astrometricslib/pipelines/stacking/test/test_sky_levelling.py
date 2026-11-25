"""Tests for the sky levelling of a stretched color preview.

Each test builds a small synthetic stretched picture: noisy sky with a tint
in one channel, plus a few bright stars. The step must make the three skies
equal with offsets only, leave stars' shape alone, and say why when it does
nothing.
"""

from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.pipelines.stacking.post_processing.sky_levelling import (
    level_sky_channels,
    level_sky_in_file,
)

SIZE = 400


def make_tinted_picture(sky: tuple[float, float, float]) -> np.ndarray:
    """Make a stretched color picture with the given sky levels.

    Parameters
    ----------
    sky : `tuple` [`float`, `float`, `float`]
        The red, green and blue sky levels.

    Returns
    -------
    image : `numpy.ndarray`
        Shape (3, SIZE, SIZE), values between 0 and 1.
    """
    generator = np.random.default_rng(3)
    image = np.empty((3, SIZE, SIZE), dtype=np.float32)
    for index, level in enumerate(sky):
        image[index] = level + 0.01 * generator.standard_normal((SIZE, SIZE))
    for row, column in ((100, 100), (250, 300), (330, 120)):
        image[:, row - 2 : row + 3, column - 2 : column + 3] = 0.9
    return np.clip(image, 0.0, 1.0)


def sky_of(image: np.ndarray) -> list[float]:
    """Measure the empty sky of each channel.

    Parameters
    ----------
    image : `numpy.ndarray`
        A color image, channels first.

    Returns
    -------
    levels : `list` [`float`]
        The three levels.
    """
    return [measure_sky_background(channel)[0] for channel in image]


def test_tinted_sky_is_made_equal() -> None:
    """A green-cyan sky has equal channel skies after the offsets."""
    image = make_tinted_picture((0.105, 0.118, 0.114))
    leveled, result = level_sky_channels(image)
    assert leveled is not None and result.applied
    after = sky_of(leveled)
    assert max(after) - min(after) < 0.001
    assert abs(result.red_offset - 0.013) < 0.002
    assert abs(result.blue_offset - 0.004) < 0.002
    assert np.array_equal(leveled[1], image[1])


def test_stars_are_shifted_not_scaled() -> None:
    """Only the level moves: a star's height above the sky is unchanged."""
    image = make_tinted_picture((0.105, 0.118, 0.114))
    leveled, _ = level_sky_channels(image)
    assert leveled is not None
    assert abs(float(leveled[0, 100, 100] - image[0, 100, 100]) - 0.013) < 0.002


def test_clipped_to_unit_range_and_border_kept() -> None:
    """Values stay in 0 to 1 and an all-zero border stays zero."""
    image = make_tinted_picture((0.105, 0.118, 0.114))
    image[:, :, :5] = 0.0
    image[:, 0, 200] = 1.0
    leveled, _ = level_sky_channels(image)
    assert leveled is not None
    assert leveled.min() >= 0.0 and leveled.max() <= 1.0
    assert not np.any(leveled[:, :, :5])


def test_single_channel_is_not_touched() -> None:
    """A mono picture gives no result image and says why."""
    leveled, result = level_sky_channels(np.full((SIZE, SIZE), 0.2, dtype=np.float32))
    assert leveled is None and not result.applied
    assert "not a color image" in result.reason


def test_too_large_offset_is_not_applied() -> None:
    """A difference above the limit leaves the picture alone."""
    image = make_tinted_picture((0.05, 0.20, 0.20))
    leveled, result = level_sky_channels(image)
    assert leveled is None and not result.applied
    assert "differ by more than" in result.reason
    assert len(result.sky_before) == 3


def test_unmeasurable_sky_is_skipped() -> None:
    """A flat picture has no spread to measure, so the step is skipped."""
    leveled, result = level_sky_channels(np.full((3, SIZE, SIZE), 0.2, dtype=np.float32))
    assert leveled is None and not result.applied
    assert "could not be measured" in result.reason


def test_file_round_trip(tmp_path: Path) -> None:
    """The file step writes the leveled picture and keeps the header."""
    source = tmp_path / "in.fits"
    target = tmp_path / "out.fits"
    header = fits.Header()
    header["OBJECT"] = "TEST"
    fits.PrimaryHDU(make_tinted_picture((0.105, 0.118, 0.114)), header=header).writeto(source)
    result = level_sky_in_file(str(source), str(target))
    assert result.applied and target.exists()
    with fits.open(target) as hdul:
        assert hdul[0].header["OBJECT"] == "TEST"
        after = sky_of(np.asarray(hdul[0].data, dtype=np.float32))
    assert max(after) - min(after) < 0.001
    assert result.record()["red_offset"] == result.red_offset


def test_file_not_written_when_skipped(tmp_path: Path) -> None:
    """An unreadable file gives a reason and writes nothing."""
    result = level_sky_in_file(str(tmp_path / "missing.fits"), str(tmp_path / "out.fits"))
    assert not result.applied and not (tmp_path / "out.fits").exists()
    assert "could not be read" in result.reason


def test_tint_in_the_dark_part_is_measured_not_the_whole_frame() -> None:
    """A nebula over most of the frame does not hide a tint in the dark sky.

    Over the whole frame the channels' sky levels come from the nebula, where
    red is far above green; the empty sky, the darkest half, has red low.
    """
    generator = np.random.default_rng(5)
    image = np.empty((3, SIZE, SIZE), dtype=np.float32)
    split = int(SIZE * 0.55)
    for index, (sky, nebula) in enumerate(((0.100, 0.45), (0.120, 0.30), (0.120, 0.30))):
        image[index, :, :split] = sky
        image[index, :, split:] = nebula
        image[index] += 0.01 * generator.standard_normal((SIZE, SIZE))
    image = np.clip(image, 0.0, 1.0)
    leveled, result = level_sky_channels(image)
    assert leveled is not None and result.applied
    assert abs(result.red_offset - 0.02) < 0.003
    medians = [float(np.median(channel[:, 10 : split - 10])) for channel in leveled]
    assert abs(medians[0] - medians[1]) < 0.002
