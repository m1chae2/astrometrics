"""Purpose: Unit tests for placing a stack picture on the sky.

Description: Checks the sky grid worked out from a stack header: the center
point lands on the reference sky position, the SIP distortion terms are
applied, the first grid row is the picture's top row (the FITS file's first
row), and a header with no plate solution, or with only the mount's pointing,
gives nothing. Also checks which files are used: the color picture when there
is one, and the stretched stack named after the stack; and that
`get_sky_picture` leaves out a target without files and keeps each answer
until a file changes.
"""

import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.target import Target, TargetStackingResult
from astrometricslib.visualization import sky_placement
from astrometricslib.visualization.sky_placement import (
    GRID_POINTS_PER_SIDE,
    get_sky_picture,
    place_picture_on_sky,
    sky_grid,
    sky_picture_files,
)

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
    header["PLTSOLVD"] = True
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


@pytest.mark.parametrize("flag", [None, False, "F"])
def test_a_pointing_only_sky_map_gives_nothing(flag: object) -> None:
    """A sky map not marked as plate solved, and with no SIP terms, is skipped.

    Parameters
    ----------
    flag : `object`
        The ``PLTSOLVD`` card to write, or `None` for no card.
    """
    header = _header(with_sip=False)
    del header["PLTSOLVD"]
    if flag is not None:
        header["PLTSOLVD"] = flag
    assert sky_grid(header) is None


def test_the_color_picture_wins_and_the_stretched_stack_is_named_after_the_stack(tmp_path: Path) -> None:
    """The color picture is used when there is one, else the preview."""
    stacking = TargetStackingResult(stacked_image=str(tmp_path / "M_31_Stacked.fits"))

    processed, picture = sky_picture_files(stacking)
    assert processed == str(tmp_path / "M_31_Stacked_processed.fits")
    assert picture == str(tmp_path / "M_31_Stacked_preview.jpg")

    stacking.color_preview_image = str(tmp_path / "M_31_Stacked_color_preview.jpg")
    assert sky_picture_files(stacking)[1] == stacking.color_preview_image
    assert sky_picture_files(TargetStackingResult()) == ("", "")


def test_a_solved_stretched_stack_places_its_picture(tmp_path: Path) -> None:
    """A solved header places the picture; an unsolved one gives nothing."""
    processed = tmp_path / "M_31_Stacked_processed.fits"
    fits.PrimaryHDU(np.zeros((HEIGHT, WIDTH), dtype=np.float32), header=_header(with_sip=True)).writeto(
        processed
    )
    unsolved = tmp_path / "M_1_Stacked_processed.fits"
    fits.PrimaryHDU(np.zeros((2, 2), dtype=np.float32)).writeto(unsolved)

    placement = place_picture_on_sky(str(processed), "/pictures/M_31.jpg")

    assert placement is not None
    assert placement.picture_path == "/pictures/M_31.jpg"
    assert (placement.width, placement.height, placement.grid_size) == (WIDTH, HEIGHT, GRID_POINTS_PER_SIDE)
    assert len(placement.grid) == GRID_POINTS_PER_SIDE**2
    assert place_picture_on_sky(str(unsolved), "/pictures/M_1.jpg") is None
    assert place_picture_on_sky(str(tmp_path / "missing.fits"), "/pictures/M_1.jpg") is None


def _target_with_pictures(folder: Path, name: str) -> tuple[Target, Path, Path]:
    """Make a target whose stack has a stretched stack and a color picture.

    Parameters
    ----------
    folder : `Path`
        Where to write the files.
    name : `str`
        The file name stem of the stack.

    Returns
    -------
    target, processed, picture : `tuple` [`Target`, `Path`, `Path`]
        The target, its plate-solved stretched stack and its picture.
    """
    processed = folder / f"{name}_Stacked_processed.fits"
    fits.PrimaryHDU(np.zeros((HEIGHT, WIDTH), dtype=np.float32), header=_header(with_sip=False)).writeto(
        processed
    )
    picture = folder / f"{name}_Stacked_color_preview.jpg"
    picture.write_bytes(b"jpeg")
    stacking = TargetStackingResult(
        stacked_image=str(folder / f"{name}_Stacked.fits"), color_preview_image=str(picture)
    )
    return Target(id=name, stacking=stacking), processed, picture


def test_a_target_without_files_or_solution_has_no_sky_picture(tmp_path: Path) -> None:
    """No files, a missing picture, or an unsolved stack each give nothing."""
    assert get_sky_picture(Target(id="M 1")) is None
    target, processed, picture = _target_with_pictures(tmp_path, "M_1")
    picture.unlink()
    assert get_sky_picture(target) is None
    picture.write_bytes(b"jpeg")
    fits.PrimaryHDU(np.zeros((2, 2), dtype=np.float32)).writeto(processed, overwrite=True)
    assert get_sky_picture(target) is None


def test_the_sky_picture_is_kept_until_a_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second ask reads no header; a newer stack is read again."""
    target, processed, picture = _target_with_pictures(tmp_path, "M_31")
    calls: list[str] = []
    place = sky_placement.place_picture_on_sky

    def counted(processed_path: str, picture_path: str) -> object:
        """Count each header read, then place the picture.

        Returns
        -------
        placement : `SkyPicturePlacement` or `None`
            The real placement.
        """
        calls.append(processed_path)
        return place(processed_path, picture_path)

    monkeypatch.setattr(sky_placement, "place_picture_on_sky", counted)

    first = get_sky_picture(target)
    second = get_sky_picture(target)

    assert first is not None
    assert first.picture_path == str(picture)
    assert second == first
    assert calls == [str(processed)]
    later = os.path.getmtime(processed) + 10
    os.utime(processed, (later, later))
    get_sky_picture(target)
    assert len(calls) == 2
