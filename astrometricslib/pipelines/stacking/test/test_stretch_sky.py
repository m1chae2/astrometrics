"""Tests for the guard that keeps the preview stretch from clipping the sky.

The images are small synthetic arrays: one is mostly empty sky, the other is
a nebula that fills the frame and leaves only a faint rim of sky at the edges.
Siril is not started.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.image_scaling import (
    AUTOSTRETCH_SHADOWS_CLIP_SIGMA,
    measure_sky_background,
)
from astrometricslib.pipelines.stacking.post_processing.stretch_sky import (
    CLIPPED_SHARE_LIMIT,
    measure_stretch_sky,
    sky_background_for_file,
)

SIZE = 400
SKY_LEVEL = 0.05
NOISE = 0.001


def make_image(nebula_amplitude: float, nebula_width: float, seed: int = 3) -> np.ndarray:
    """Make a sky with noise and a round, smooth nebula.

    Parameters
    ----------
    nebula_amplitude : `float`
        Height of the nebula at the center, above the sky.
    nebula_width : `float`
        The nebula's Gaussian width, in half-frames (0.5 fills the frame).

    Returns
    -------
    image : `numpy.ndarray`
        The image, `SIZE` by `SIZE`.
    """
    rows, columns = np.indices((SIZE, SIZE))
    radius = np.hypot((columns - SIZE / 2) / (SIZE / 2), (rows - SIZE / 2) / (SIZE / 2))
    nebula = nebula_amplitude * np.exp(-((radius / nebula_width) ** 2))
    noise = np.random.default_rng(seed).normal(0.0, NOISE, (SIZE, SIZE))
    return SKY_LEVEL + nebula + noise


def clipped_share(image: np.ndarray, black_point: float, region: np.ndarray | None = None) -> float:
    """Give the share of pixels below a black point.

    Returns
    -------
    share : `float`
        The share, over the whole image or over the pixels in `region`.
    """
    pixels = image if region is None else image[region]
    return float(np.mean(pixels < black_point))


def test_frame_filling_nebula_keeps_the_edges_above_black() -> None:
    """A nebula that fills the frame must not clip the outer sky to zero."""
    image = make_image(nebula_amplitude=0.03, nebula_width=1.3)
    old_level, old_noise = measure_sky_background(image)
    old_black_point = old_level + AUTOSTRETCH_SHADOWS_CLIP_SIGMA * old_noise
    rows, columns = np.indices(image.shape)
    rim = np.hypot((columns - SIZE / 2) / (SIZE / 2), (rows - SIZE / 2) / (SIZE / 2)) > 1.15
    assert clipped_share(image, old_black_point, rim) > 0.2

    sky = measure_stretch_sky(image)

    assert sky is not None
    assert sky.guard_lowered
    assert sky.black_point < old_black_point
    assert clipped_share(image, sky.black_point) <= resolve_limit() + 1e-3
    assert clipped_share(image, sky.black_point, rim) < 0.1
    assert np.median(image[rim]) > sky.black_point


def test_mostly_empty_field_keeps_its_black_point() -> None:
    """A mostly empty field gets the same black point as before."""
    image = make_image(nebula_amplitude=0.2, nebula_width=0.08)
    level, noise = measure_sky_background(image)
    old_black_point = level + AUTOSTRETCH_SHADOWS_CLIP_SIGMA * noise

    sky = measure_stretch_sky(image)

    assert sky is not None
    assert not sky.guard_lowered
    assert sky.black_point == pytest.approx(old_black_point, abs=1e-12)
    assert sky.as_background() == (level, noise)
    assert sky.clipped_share == pytest.approx(sky.first_clipped_share)


def test_colour_image_is_measured_on_the_channel_mean() -> None:
    """A colour stack gives the same measurement as its mean image."""
    image = make_image(nebula_amplitude=0.03, nebula_width=1.3)
    colour = np.stack([image, image, image])

    from_colour = measure_stretch_sky(colour)
    from_mono = measure_stretch_sky(image)

    assert from_colour is not None
    assert from_mono is not None
    assert from_colour.black_point == pytest.approx(from_mono.black_point)


def test_unmeasurable_image_gives_none() -> None:
    """An empty image, or one that is not an image, returns `None`."""
    assert measure_stretch_sky(np.zeros((64, 64))) is None
    assert measure_stretch_sky(np.zeros(10)) is None


def test_record_holds_black_point_and_shares() -> None:
    """The record carries the numbers the preview settings file shows."""
    sky = measure_stretch_sky(make_image(nebula_amplitude=0.03, nebula_width=1.3))

    assert sky is not None
    record = sky.record()
    assert record["black_point"] == sky.black_point
    assert record["clipped_share"] == sky.clipped_share
    assert record["guard_lowered_black_point"] is True
    assert record["first_clipped_share"] > record["clipped_share"]


def resolve_limit() -> float:
    """Give the guard's share limit.

    Returns
    -------
    limit : `float`
        The resolved value of `CLIPPED_SHARE_LIMIT`.
    """
    from astrometricslib.foundation.parameters import resolve

    return float(resolve(CLIPPED_SHARE_LIMIT).value)


def test_the_sky_of_a_single_channel_stack_is_measured_as_before(tmp_path: Path) -> None:
    """Verify a mono stack still gets a measured sky with no green step."""
    path = tmp_path / "mono.fits"
    rng = np.random.default_rng(3)
    fits.writeto(path, rng.normal(0.01, 0.0005, (200, 200)).astype(np.float32))

    sky = sky_background_for_file(str(path))

    assert sky is not None
    assert sky[0] == pytest.approx(0.01, abs=0.0005)
    assert sky[1] == pytest.approx(0.0005, rel=0.1)
