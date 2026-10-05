"""Tests for the picture tool that returns an image to the client.

`Visualization.render_fits` must return PNG bytes plus a description, and the
MCP registry must wrap that as image content, not as text.
"""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astrometricslib.api.visualization import Visualization
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import ViewableImage


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


def test_render_fits_returns_a_png_and_a_description(frame_path: str) -> None:
    """The picture is a PNG no larger than asked, with its brightness range."""
    picture = Visualization(None, None).render_fits(frame_path, max_dimensions=150)
    assert isinstance(picture, ViewableImage)
    decoded = Image.open(BytesIO(picture.png_bytes))
    assert max(decoded.size) == 150
    assert picture.description["full_frame_size"] == {"width": 300, "height": 200}
    assert picture.description["crop"] is None


def test_a_crop_is_zoomed_and_reported(frame_path: str) -> None:
    """A crop around the star is cut at full size and enlarged to view."""
    picture = Visualization(None, None).render_fits(
        frame_path, crop_center_x=150, crop_center_y=100, crop_size=40, max_dimensions=400
    )
    assert picture.description["crop"] == {"left": 130, "top": 80, "width": 40, "height": 40}
    assert max(Image.open(BytesIO(picture.png_bytes)).size) == 200


def test_half_given_options_are_refused(frame_path: str) -> None:
    """One of center/width, or part of a crop, is an error."""
    with pytest.raises(InvalidArgumentError):
        Visualization(None, None).render_fits(frame_path, center=1000.0)
    with pytest.raises(InvalidArgumentError):
        Visualization(None, None).render_fits(frame_path, crop_size=40)


def test_the_registry_returns_image_content(frame_path: str) -> None:
    """A tool that returns a `ViewableImage` reaches the client as an image."""
    from astrometricslib.mcp.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("view", "View a frame.")(lambda: Visualization(None, None).render_fits(frame_path))
    content = asyncio.run(registry.execute("view", {}))
    assert content[0].type == "image"
    assert content[0].mimeType == "image/png"
    assert Image.open(BytesIO(base64.b64decode(content[0].data))).size[0] > 0
    assert "brightness_range_shown" in json.loads(content[1].text)


def test_a_target_frame_is_found_by_its_number(frame_path: str) -> None:
    """A frame can be named by the number at the end of its file name."""
    from astrometricslib.models.target import FrameRecord, Target

    target = Target(
        id="T 1",
        frames=[
            FrameRecord(path=frame_path, role="LIGHT", timestamp=1.0),
            FrameRecord(path="/other/T_1_014.fits", role="LIGHT", timestamp=2.0),
        ],
    )
    # frame_path ends in "frame.fits": give it a number-free full name.
    picture = Visualization(None, None).render_fits(target=target, file_name="frame.fits")
    assert picture.description["path"] == frame_path
    with pytest.raises(NotFoundError):
        Visualization(None, None).render_fits(target=target, file_name="999")
    with pytest.raises(InvalidArgumentError):
        Visualization(None, None).render_fits()


def test_a_processed_fits_is_drawn_without_a_second_stretch(tmp_path: Path) -> None:
    """A processed FITS is already stretched, so it is not stretched again."""
    generator = np.random.default_rng(2)
    data = np.clip(generator.normal(0.2, 0.01, (200, 300)), 0.0, 1.0).astype(np.float32)
    processed = tmp_path / "M_13_L_Stacked_processed.fits"
    plain = tmp_path / "M_13_L_Stacked.fits"
    for path in (processed, plain):
        fits.PrimaryHDU(data).writeto(path)

    drawn_processed = Visualization(None, None).render_fits(str(processed), max_dimensions=150)
    drawn_plain = Visualization(None, None).render_fits(str(plain), max_dimensions=150)

    assert drawn_processed.description["stretched"] is False
    assert drawn_plain.description["stretched"] is True
    assert drawn_processed.png_bytes != drawn_plain.png_bytes
