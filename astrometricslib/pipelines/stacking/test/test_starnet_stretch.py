"""Tests for stretching the stars and the rest of a preview on their own.

StarNet itself is a neural network run by Siril, so these tests cover what
this code adds: the script that asks Siril for the split, the arcsinh stretch
of the stars, and the blend with the stretched starless image.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing import starnet_stretch

SIZE = 64


def star_image(peak: float = 1e-2, background: float = 0.0) -> np.ndarray:
    """Make a 2-D image with one bright and one faint star.

    Returns
    -------
    image : `numpy.ndarray`
        The image: a star of `peak` and one 1000 times fainter.
    """
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    star = peak * np.exp(-((x - 20) ** 2 + (y - 20) ** 2) / 4.0)
    faint = peak / 1000 * np.exp(-((x - 44) ** 2 + (y - 44) ** 2) / 4.0)
    return background + star + faint


def test_the_script_asks_siril_to_split_the_image_with_a_stretched_copy() -> None:
    """Siril loads the image and runs StarNet on a stretched copy."""
    commands = starnet_stretch.build_starnet_script("colour.fits")

    assert commands[1:3] == ['load "colour.fits"', "starnet -stretch"]
    assert commands[-1] == "close"


def test_siril_s_two_outputs_are_given_fixed_names(tmp_path: Path) -> None:
    """The files Siril names after the input are copied to fixed names."""
    (tmp_path / "starless_colour.fit").write_bytes(b"starless")
    (tmp_path / "starmask_colour.fit").write_bytes(b"stars")

    assert starnet_stretch.collect_starnet_outputs(str(tmp_path)) is True
    assert (tmp_path / starnet_stretch.STARLESS_NAME).read_bytes() == b"starless"
    assert (tmp_path / starnet_stretch.STARS_NAME).read_bytes() == b"stars"


def test_a_missing_output_is_reported(tmp_path: Path) -> None:
    """With only one of the two files StarNet is counted as failed."""
    (tmp_path / "starless_colour.fit").write_bytes(b"starless")

    assert starnet_stretch.collect_starnet_outputs(str(tmp_path)) is False


def test_a_stronger_stretch_lifts_faint_stars_more_than_bright_ones() -> None:
    """A stronger stretch lifts the faint star far more than the bright one."""
    stars = star_image()

    gentle = starnet_stretch.stretch_stars(stars, 10.0)
    strong = starnet_stretch.stretch_stars(stars, 1000.0)

    assert strong[44, 44] > 10 * gentle[44, 44]
    assert strong[20, 20] == pytest.approx(gentle[20, 20], abs=0.15)
    assert strong.max() <= 1.0
    assert strong.min() >= 0.0


def test_a_picture_without_stars_stays_dark() -> None:
    """An empty stars layer gives an empty result, not a division error."""
    assert not starnet_stretch.stretch_stars(np.zeros((SIZE, SIZE)), 300.0).any()


def test_the_blend_leaves_the_starless_image_where_there_are_no_stars() -> None:
    """Away from the stars the blended picture equals the starless picture."""
    starless = np.full((SIZE, SIZE), 0.2)

    blended = starnet_stretch.blend_stars(starless, star_image(), 300.0, 1.0)

    assert blended[5, 60] == pytest.approx(0.2, abs=1e-4)
    assert blended[20, 20] > 0.9
    assert blended.max() <= 1.0


def test_the_star_strength_scales_the_stars_without_touching_the_sky() -> None:
    """Half strength gives dimmer stars and the same sky."""
    starless = np.full((SIZE, SIZE), 0.2)
    stars = star_image()

    full = starnet_stretch.blend_stars(starless, stars, 300.0, 1.0)
    half = starnet_stretch.blend_stars(starless, stars, 300.0, 0.5)
    none = starnet_stretch.blend_stars(starless, stars, 300.0, 0.0)

    assert half[44, 44] < full[44, 44]
    assert half[5, 60] == pytest.approx(full[5, 60], abs=1e-4)
    assert none == pytest.approx(starless.astype(np.float32))


def test_a_colour_pair_is_blended_channel_by_channel() -> None:
    """Colour images keep their shape and each channel gets its own stars."""
    starless = np.full((3, SIZE, SIZE), 0.2)
    stars = np.stack([star_image(1e-2), star_image(5e-3), star_image(2e-3)])

    blended = starnet_stretch.blend_stars(starless, stars, 300.0, 1.0)

    assert blended.shape == starless.shape
    assert blended.dtype == np.float32
    assert blended[0, 20, 20] >= blended[2, 20, 20]


def test_files_are_blended_and_keep_the_starless_header(tmp_path: Path) -> None:
    """The blended file is written with the starless image's header."""
    header = fits.Header()
    header["OBJECT"] = "M 51"
    fits.writeto(tmp_path / "starless.fits", np.full((SIZE, SIZE), 0.2, dtype=np.float32), header)
    fits.writeto(tmp_path / "stars.fits", star_image().astype(np.float32))

    ok = starnet_stretch.blend_files(
        str(tmp_path / "starless.fits"), str(tmp_path / "stars.fits"), str(tmp_path / "out.fits"), 300.0, 0.8
    )

    assert ok is True
    with fits.open(tmp_path / "out.fits") as written:
        assert written[0].header["OBJECT"] == "M 51"
        assert written[0].data.shape == (SIZE, SIZE)


def test_images_of_different_shapes_are_refused(tmp_path: Path) -> None:
    """A starless and a stars image of different sizes are not blended."""
    fits.writeto(tmp_path / "starless.fits", np.zeros((SIZE, SIZE), dtype=np.float32))
    fits.writeto(tmp_path / "stars.fits", np.zeros((SIZE, SIZE + 4), dtype=np.float32))

    ok = starnet_stretch.blend_files(
        str(tmp_path / "starless.fits"), str(tmp_path / "stars.fits"), str(tmp_path / "out.fits"), 300.0, 0.8
    )

    assert ok is False
    assert not (tmp_path / "out.fits").exists()


def test_an_unreadable_file_is_refused(tmp_path: Path) -> None:
    """A file that is not FITS gives `False` and does not raise."""
    (tmp_path / "starless.fits").write_bytes(b"not fits")
    (tmp_path / "stars.fits").write_bytes(b"not fits")

    assert not starnet_stretch.blend_files(
        str(tmp_path / "starless.fits"), str(tmp_path / "stars.fits"), str(tmp_path / "out.fits"), 300.0, 0.8
    )
