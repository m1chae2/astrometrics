"""Purpose: Unit tests for placing stack pictures on the sky.

Description: Checks the sky grid worked out from a stack header: the center
point lands on the reference sky position, the SIP distortion terms are
applied, the first grid row is the picture's top row (the FITS file's first
row), a header with no plate solution gives nothing, the picture is the
color picture when there is one, and a stack's header is read only once
until its file changes.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from backend.services.data import stack_sky_pictures
from backend.services.data.stack_sky_pictures import GRID_POINTS_PER_SIDE, StackSkyPictures, sky_grid

WIDTH = 400
HEIGHT = 300
SCALE_DEG = 1.0 / 3600.0


def _header(with_sip: bool) -> fits.Header:
    """Build a plate-solved header for a WIDTH by HEIGHT picture.

    Parameters
    ----------
    with_sip : `bool`
        Whether to add SIP distortion terms.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The header. Its reference pixel is the picture center.
    """
    header = fits.Header()
    header["NAXIS"] = 2
    header["NAXIS1"] = WIDTH
    header["NAXIS2"] = HEIGHT
    header["CTYPE1"] = "RA---TAN-SIP" if with_sip else "RA---TAN"
    header["CTYPE2"] = "DEC--TAN-SIP" if with_sip else "DEC--TAN"
    header["CRVAL1"] = 10.0
    header["CRVAL2"] = 40.0
    # The 1-based reference pixel is the picture center (0-based W/2 - 0.5).
    header["CRPIX1"] = WIDTH / 2 + 0.5
    header["CRPIX2"] = HEIGHT / 2 + 0.5
    header["CD1_1"] = -SCALE_DEG
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = SCALE_DEG
    if with_sip:
        header["A_ORDER"] = 2
        header["B_ORDER"] = 2
        header["A_2_0"] = 2e-4
        header["B_0_2"] = 0.0
    return header


def test_the_grid_center_is_the_reference_position() -> None:
    """The middle grid point is the reference pixel, so it lands on CRVAL."""
    grid = sky_grid(_header(with_sip=True))
    assert grid is not None
    assert len(grid) == GRID_POINTS_PER_SIDE**2
    middle = grid[(GRID_POINTS_PER_SIDE // 2) * GRID_POINTS_PER_SIDE + GRID_POINTS_PER_SIDE // 2]
    assert middle == pytest.approx([10.0, 40.0], abs=1e-9)


def test_the_first_grid_row_is_the_top_of_the_picture() -> None:
    """Grid row 0 is FITS y = -0.5, here the southern (low y) edge."""
    grid = np.asarray(sky_grid(_header(with_sip=False)))
    first_row = grid[:GRID_POINTS_PER_SIDE]
    last_row = grid[-GRID_POINTS_PER_SIDE:]
    assert first_row[:, 1].mean() == pytest.approx(40.0 - (HEIGHT / 2) * SCALE_DEG, abs=1e-4)
    assert last_row[:, 1].mean() == pytest.approx(40.0 + (HEIGHT / 2) * SCALE_DEG, abs=1e-4)
    # East is left (CD1_1 < 0), so the first column has the larger RA.
    assert grid[0, 0] > grid[GRID_POINTS_PER_SIDE - 1, 0]


def test_the_sip_terms_move_the_edges() -> None:
    """With A_2_0 the right edge moves by A_2_0 * u^2 pixels."""
    plain = np.asarray(sky_grid(_header(with_sip=False)))
    distorted = np.asarray(sky_grid(_header(with_sip=True)))
    u = WIDTH / 2  # distance in pixels from the reference pixel to the right edge
    expected_shift_deg = 2e-4 * u**2 * SCALE_DEG / np.cos(np.radians(40.0))
    right_edge_middle = (GRID_POINTS_PER_SIDE // 2) * GRID_POINTS_PER_SIDE + GRID_POINTS_PER_SIDE - 1
    # The shift is toward +x, which is west here, so the RA falls.
    shift = plain[right_edge_middle, 0] - distorted[right_edge_middle, 0]
    assert shift == pytest.approx(expected_shift_deg, rel=0.02)


def test_a_header_with_no_plate_solution_gives_nothing() -> None:
    """A stack that was never plate solved has no grid."""
    header = fits.Header()
    header["NAXIS1"] = WIDTH
    header["NAXIS2"] = HEIGHT
    assert sky_grid(header) is None


def test_a_target_without_files_or_solution_is_left_out(tmp_path: Path) -> None:
    """No stack, a missing picture, or an unsolved stack each give no entry."""
    pictures = StackSkyPictures(lambda path: path)
    assert pictures.entry_for(SimpleNamespace(id="none", stacking=None)) is None
    stacked = tmp_path / "M_1_Stacked.fits"
    unsolved = tmp_path / "M_1_Stacked_processed.fits"
    fits.PrimaryHDU(np.zeros((2, 2), dtype=np.float32)).writeto(unsolved)
    stacking = SimpleNamespace(
        processed_image=str(unsolved), stacked_image=str(stacked), color_preview_image="", preview_path=""
    )
    assert pictures.entry_for(SimpleNamespace(id="M 1", stacking=stacking)) is None
    preview = tmp_path / "M_1_Stacked_preview.jpg"
    preview.write_bytes(b"jpeg")
    stacking.preview_path = str(preview)
    assert pictures.entry_for(SimpleNamespace(id="M 1", stacking=stacking)) is None


def test_the_color_picture_is_used_and_the_header_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The color picture wins; a second ask reads no header."""
    processed = tmp_path / "M_31_Stacked_processed.fits"
    header = _header(with_sip=True)
    fits.PrimaryHDU(np.zeros((HEIGHT, WIDTH), dtype=np.float32), header=header).writeto(processed)
    preview = tmp_path / "M_31_Stacked_preview.jpg"
    color = tmp_path / "M_31_Stacked_color_preview.jpg"
    preview.write_bytes(b"jpeg")
    color.write_bytes(b"jpeg")
    stacking = SimpleNamespace(
        processed_image=str(processed),
        stacked_image=str(tmp_path / "M_31_Stacked.fits"),
        color_preview_image=str(color),
        preview_path=str(preview),
    )
    reads = []
    real_getheader = stack_sky_pictures.fits.getheader
    monkeypatch.setattr(
        stack_sky_pictures.fits,
        "getheader",
        lambda path, **kwargs: reads.append(path) or real_getheader(path, **kwargs),
    )
    pictures = StackSkyPictures(lambda path: path)
    target = SimpleNamespace(id="M 31", stacking=stacking)

    first = pictures.entry_for(target)
    second = pictures.entry_for(target)

    assert first is not None
    assert first["targetId"] == "M 31"
    assert first["picturePath"] == str(color)
    assert (first["width"], first["height"], first["gridSize"]) == (WIDTH, HEIGHT, GRID_POINTS_PER_SIDE)
    assert second == first
    assert reads == [str(processed)]
    # A newer stack is read again.
    os.utime(processed, (os.path.getmtime(processed) + 10, os.path.getmtime(processed) + 10))
    pictures.entry_for(target)
    assert len(reads) == 2
