"""Tests for showing a stack's saved JPEG in place of a drawn PNG.

A stretched view of a stack uses the picture that stacking saved beside it
when that picture is current. The tests use tiny stand-in files, so no Siril
is run.
"""

import base64
import os
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astrometricslib.pipelines.shared.image_conversions import render_data_url
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for

_JPEG_PREFIX = "data:image/jpeg;base64,"
_PNG_PREFIX = "data:image/png;base64,"


@pytest.fixture
def stack(tmp_path: Path) -> Path:
    """Make a small stand-in stacked FITS file.

    Returns
    -------
    stack : `pathlib.Path`
        The path of a 40 by 30 pixel FITS file.
    """
    path = tmp_path / "M_13_L_Stacked.fits"
    pixels = np.random.default_rng(1).normal(0.1, 0.01, (30, 40)).astype(np.float32)
    fits.writeto(path, pixels)
    return path


def _save_preview(stack: Path, size: tuple[int, int], age_seconds: float) -> Path:
    """Save a JPEG beside the stack, dated `age_seconds` after the stack.

    Returns
    -------
    preview : `pathlib.Path`
        The path of the saved JPEG.
    """
    preview = Path(preview_path_for(str(stack)))
    Image.new("L", size, 90).save(preview, format="JPEG")
    stack_time = stack.stat().st_mtime
    os.utime(preview, (stack_time + age_seconds, stack_time + age_seconds))
    return preview


def _decoded_size(image_data: str) -> tuple[int, int]:
    """Read the pixel size of a picture sent as a data URL.

    Returns
    -------
    size : `tuple` [`int`, `int`]
        Width and height in pixels.
    """
    payload = image_data.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(payload))) as picture:
        return picture.size


def test_a_stretched_view_uses_the_saved_jpeg(stack: Path) -> None:
    """Verify the viewer gets the JPEG and its 8-bit range."""
    _save_preview(stack, (40, 30), age_seconds=5)

    result = render_data_url(str(stack), stretch=True)

    assert result is not None
    assert result.image_data.startswith(_JPEG_PREFIX)
    assert (result.min, result.max) == (0.0, 255.0)
    assert result.headers


def test_a_linear_view_ignores_the_jpeg(stack: Path) -> None:
    """Verify an unstretched view is still drawn from the FITS data."""
    _save_preview(stack, (40, 30), age_seconds=5)

    result = render_data_url(str(stack), stretch=False)

    assert result is not None
    assert result.image_data.startswith(_PNG_PREFIX)


def test_a_stack_without_a_jpeg_is_drawn_as_before(stack: Path) -> None:
    """Verify a missing preview falls back to the PNG."""
    result = render_data_url(str(stack), stretch=True)

    assert result is not None
    assert result.image_data.startswith(_PNG_PREFIX)


def test_a_jpeg_older_than_the_stack_is_not_shown(stack: Path) -> None:
    """Verify a picture of an older stack is never shown."""
    _save_preview(stack, (40, 30), age_seconds=-60)

    result = render_data_url(str(stack), stretch=True)

    assert result is not None
    assert result.image_data.startswith(_PNG_PREFIX)


def test_a_large_jpeg_is_shrunk_to_the_size_limit(stack: Path) -> None:
    """Verify the picture fits the requested size, as the PNG does."""
    _save_preview(stack, (400, 300), age_seconds=5)

    result = render_data_url(str(stack), max_dimensions=100, stretch=True)

    assert result is not None
    assert _decoded_size(result.image_data) == (100, 75)


def test_an_unreadable_jpeg_falls_back_to_the_png(stack: Path) -> None:
    """Verify a damaged picture does not stop the stack from being shown."""
    preview = _save_preview(stack, (40, 30), age_seconds=5)
    preview.write_bytes(b"not a jpeg")
    os.utime(preview, (stack.stat().st_mtime + 5, stack.stat().st_mtime + 5))

    result = render_data_url(str(stack), stretch=True)

    assert result is not None
    assert result.image_data.startswith(_PNG_PREFIX)
