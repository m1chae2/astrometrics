"""Tests for drawing one image: `Visualization.render` and `plot_image`.

`render` returns the bytes of a PNG (or of the saved JPEG) with how the
picture was drawn; `plot_image` returns a Matplotlib figure whose axes count
pixels from 1. The MCP server's tests check that a rendered picture reaches
the client as an image.
"""

from io import BytesIO
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astrometricslib.api.visualization import Visualization
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.target import FrameRecord, ImageCrop, RenderedImage


@pytest.fixture
def frame_path(tmp_path: Path) -> str:
    """Write a small noisy frame with one bright star.

    Returns
    -------
    path : `str`
        The FITS file.
    """
    generator = np.random.default_rng(1)
    data = generator.normal(1000.0, 20.0, (200, 300)).astype(np.float32)
    data[100, 150] = 50000.0
    path = tmp_path / "frame.fits"
    fits.PrimaryHDU(data).writeto(path)
    return str(path)


def test_render_returns_a_png_and_how_it_was_drawn(frame_path: str) -> None:
    """The picture is a PNG no larger than asked, with its brightness range."""
    picture = Visualization(None, None).render(frame_path, max_dimensions=150)
    assert isinstance(picture, RenderedImage)
    assert picture.media_type == "image/png"
    decoded = Image.open(BytesIO(picture.picture))
    assert max(decoded.size) == 150
    assert (picture.width_px, picture.height_px) == decoded.size
    assert (picture.full_width_px, picture.full_height_px) == (300, 200)
    assert picture.crop is None
    assert picture.black < picture.white


def test_render_takes_a_frame_record_or_an_array(frame_path: str) -> None:
    """A frame record draws its file; an array draws its pixels."""
    visualization = Visualization(None, None)
    from_record = visualization.render(FrameRecord(path=frame_path), max_dimensions=150)
    assert from_record.path == frame_path
    from_array = visualization.render(np.ones((20, 30)) * np.arange(30), max_dimensions=150)
    assert from_array.path is None
    assert (from_array.full_width_px, from_array.full_height_px) == (30, 20)
    with pytest.raises(InvalidArgumentError):
        visualization.render(42)


def test_a_crop_is_zoomed_and_reported_from_1(frame_path: str) -> None:
    """A crop around the star is cut at full size and enlarged to view."""
    picture = Visualization(None, None).render(frame_path, crop=(151, 101, 40), max_dimensions=400)
    assert picture.crop == ImageCrop(left_px=131, top_px=81, width_px=40, height_px=40)
    assert max(Image.open(BytesIO(picture.picture)).size) == 200


def test_bad_brightness_and_crop_values_are_refused(frame_path: str) -> None:
    """A black value above the white one, or a short crop, is an error."""
    with pytest.raises(InvalidArgumentError):
        Visualization(None, None).render(frame_path, brightness=(1000.0, 900.0))
    with pytest.raises(InvalidArgumentError):
        Visualization(None, None).render(frame_path, crop=(10, 10))


def test_a_manual_brightness_range_is_drawn_as_asked(frame_path: str) -> None:
    """The range given is the range shown, with no automatic stretch."""
    picture = Visualization(None, None).render(frame_path, brightness=(950.0, 1050.0))
    assert (picture.black, picture.white) == (950.0, 1050.0)
    assert picture.stretch_parameters is None
    assert picture.stretched is False


def test_a_rendered_image_shows_itself_in_jupyter(frame_path: str) -> None:
    """The Jupyter bundle holds the picture under its media type."""
    picture = Visualization(None, None).render(frame_path, max_dimensions=150)
    assert picture._repr_mimebundle_() == {"image/png": picture.picture}
    assert "frame.fits" in repr(picture)


def test_plot_image_counts_pixels_from_1(frame_path: str) -> None:
    """The axes cover the image with pixel 1 at the first pixel's center."""
    figure = Visualization(None, None).plot_image(frame_path)
    try:
        axes = figure.axes[0]
        assert axes.get_xlim() == (0.5, 300.5)
        assert axes.get_ylim() == (200.5, 0.5)
    finally:
        plt.close(figure)


def test_plot_image_refuses_overlays_on_an_array() -> None:
    """An array has no saved detections, so overlays are refused."""
    with pytest.raises(InvalidArgumentError):
        Visualization(None, None).plot_image(np.ones((10, 10)), overlays=["detections"])
    with pytest.raises(InvalidArgumentError):
        Visualization(None, None).plot_image(np.ones((10, 10)), overlays=["nothing"])


def test_a_processed_fits_is_plotted_without_a_second_stretch(tmp_path: Path) -> None:
    """A processed FITS is already stretched, so it is not stretched again."""
    generator = np.random.default_rng(2)
    data = np.clip(generator.normal(0.2, 0.01, (200, 300)), 0.0, 1.0).astype(np.float32)
    processed = tmp_path / "M_13_L_Stacked_processed.fits"
    plain = tmp_path / "M_13_L_Stacked.fits"
    for path in (processed, plain):
        fits.PrimaryHDU(data).writeto(path)

    drawn_processed = Visualization(None, None).plot_image(str(processed), crop=(150, 100, 50))
    drawn_plain = Visualization(None, None).plot_image(str(plain), crop=(150, 100, 50))
    try:
        processed_pixels = drawn_processed.axes[0].images[0].get_array()
        plain_pixels = drawn_plain.axes[0].images[0].get_array()
        assert not np.array_equal(processed_pixels, plain_pixels)
    finally:
        plt.close(drawn_processed)
        plt.close(drawn_plain)
