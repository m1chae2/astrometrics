"""Purpose: Tests that the plate-scale hint uses the binned pixel size.

Description: Binning merges neighbouring camera pixels into one image pixel,
so a 2x2-binned image has pixels twice as wide on the sky. The plate scale in
arcseconds per pixel is ``206.265 * pixel size (micrometers) / focal length
(millimeters)``, and the pixel size must be the binned one. INDI/Ekos, NINA
and Siril write the binned size in ``XPIXSZ``, so it is used as it is. The
camera's own size is in ``PIXSIZE1``, which needs ``XBINNING``. These tests
build headers for the ZWO ASI533 camera (3.76 micrometer pixels) on a 405 mm
telescope.
"""

from unittest.mock import MagicMock

import pytest
from astropy.io import fits

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

# arcsec per pixel of an unbinned frame: 206.265 * 3.76 / 405
UNBINNED_SCALE = 206.265 * 3.76 / 405.0


def make_identifier() -> StarIdentifier:
    """Build an identifier whose configuration knows no focal length.

    Returns
    -------
    identifier : `StarIdentifier`
        An identifier that must take the focal length from the header.
    """
    config = MagicMock()
    config.get_value.return_value = None
    config.get_focal_length_mm.side_effect = ValueError("no focal length configured")
    return StarIdentifier(config=config)


def make_image(**cards: float) -> MagicMock:
    """Build an image stand-in with a camera and telescope header.

    The header starts with a focal length only. Each test adds the pixel
    size cards it needs.

    Parameters
    ----------
    **cards
        Header cards to add, such as ``XPIXSZ=7.52``.

    Returns
    -------
    image : `unittest.mock.MagicMock`
        An object that passes ``isinstance(image, AstrometricsImage)`` and
        has the header.
    """
    header = fits.Header({"FOCALLEN": 405.0})
    header.update(cards)
    image = MagicMock(spec=AstrometricsImage)
    image.header = header
    return image


def test_an_unbinned_frame_gets_the_plain_scale() -> None:
    """A 1x1 frame's hint brackets the unbinned scale by 5 percent."""
    image = make_image(XPIXSZ=3.76, YPIXSZ=3.76, XBINNING=1, YBINNING=1)

    lower, upper = make_identifier()._calculate_scale_hints(image)

    assert lower == pytest.approx(UNBINNED_SCALE * 0.95)
    assert upper == pytest.approx(UNBINNED_SCALE * 1.05)


def test_xpixsz_already_includes_binning_so_it_is_not_multiplied_again() -> None:
    """A 2x2 frame with XPIXSZ 7.52 (binned) gives twice the unbinned scale."""
    identifier = make_identifier()
    image = make_image(XPIXSZ=7.52, YPIXSZ=7.52, XBINNING=2, YBINNING=2)

    lower, upper = identifier._calculate_scale_hints(image)

    assert lower == pytest.approx(2 * UNBINNED_SCALE * 0.95)
    assert upper == pytest.approx(2 * UNBINNED_SCALE * 1.05)
    assert identifier.astrometry_flags == []


def test_pixsize1_is_multiplied_by_the_binning_factor() -> None:
    """With only the camera's own pixel size, 2x2 binning doubles the scale."""
    identifier = make_identifier()
    image = make_image(PIXSIZE1=3.76, PIXSIZE2=3.76, XBINNING=2, YBINNING=2)

    lower, upper = identifier._calculate_scale_hints(image)

    assert lower == pytest.approx(2 * UNBINNED_SCALE * 0.95)
    assert upper == pytest.approx(2 * UNBINNED_SCALE * 1.05)
    assert identifier.astrometry_flags == []


def test_pixsize1_without_binning_cards_is_treated_as_unbinned() -> None:
    """No ``XBINNING`` card means one camera pixel per image pixel."""
    lower, _ = make_identifier()._calculate_scale_hints(make_image(PIXSIZE1=3.76))

    assert lower == pytest.approx(UNBINNED_SCALE * 0.95)


def test_xpixsz_wins_over_pixsize1() -> None:
    """With both present, the binned XPIXSZ is used and binning ignored."""
    image = make_image(XPIXSZ=7.52, PIXSIZE1=3.76, XBINNING=2)

    lower, _ = make_identifier()._calculate_scale_hints(image)

    assert lower == pytest.approx(2 * UNBINNED_SCALE * 0.95)


def test_different_x_and_y_pixel_sizes_use_the_mean_and_raise_a_flag() -> None:
    """XPIXSZ 7.52 and YPIXSZ 3.76 give their mean and a flag."""
    identifier = make_identifier()

    lower, _ = identifier._calculate_scale_hints(make_image(XPIXSZ=7.52, YPIXSZ=3.76))

    assert lower == pytest.approx(1.5 * UNBINNED_SCALE * 0.95)
    assert identifier.astrometry_flags == [star_identifier_module.FLAG_SCALE_HINT_BINNING_MISMATCH]


def test_different_x_and_y_binning_with_pixsize1_uses_the_mean_and_raises_a_flag() -> None:
    """X binned 2 and Y binned 1 gives a factor of 1.5 and a flag."""
    identifier = make_identifier()
    image = make_image(PIXSIZE1=3.76, XBINNING=2, YBINNING=1)

    lower, upper = identifier._calculate_scale_hints(image)

    assert lower == pytest.approx(1.5 * UNBINNED_SCALE * 0.95)
    assert upper == pytest.approx(1.5 * UNBINNED_SCALE * 1.05)
    assert identifier.astrometry_flags == [star_identifier_module.FLAG_SCALE_HINT_BINNING_MISMATCH]


def test_a_header_scale_in_arcseconds_per_pixel_is_not_multiplied_by_binning() -> None:
    """``PIXSCAL`` already describes the image's pixels, binned or not."""
    image = make_image(PIXSCAL=3.8, XBINNING=2, YBINNING=2)

    lower, upper = make_identifier()._calculate_scale_hints(image)

    assert lower == pytest.approx(3.8 * 0.95)
    assert upper == pytest.approx(3.8 * 1.05)
