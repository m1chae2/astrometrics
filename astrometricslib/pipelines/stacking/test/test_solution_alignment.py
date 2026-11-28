"""Tests for the alignment of one stack onto another through plate solutions.

The Siril solve is faked, so no Siril runs. The tests build small synthetic
stacks from Gaussian stars and two plate solutions (WCS) that differ in pixel
scale, turn and position, and in one case in a lens distortion (SIP terms).
The moving stack read onto the reference grid must match what the reference
camera would have seen, the reference grid must not be touched, a picture
cropped from its stack must find its place in the stack, and a stack that
Siril cannot solve must give a reason instead of an alignment.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS, Sip

from astrometricslib._internal.drivers.interfaces.plate_solve_driver import PlateSolveHeader
from astrometricslib.pipelines.stacking.post_processing import solution_alignment
from astrometricslib.pipelines.stacking.post_processing.solution_alignment import (
    GridPlacement,
    SkyAlignment,
    SolvedStack,
    combined_residual_pixels,
    placement_in_stack,
    solve_stack,
)
from astrometricslib.pipelines.stacking.test.test_color_from_other_stack import make_wcs, render, sky_stars


def make_distorted_wcs(shape: tuple[int, int], arcsec_per_pixel: float, rotation_degrees: float) -> WCS:
    """Make a plate solution with a small lens distortion (SIP terms).

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        Rows and columns of the picture.
    arcsec_per_pixel : `float`
        The pixel size on the sky.
    rotation_degrees : `float`
        The turn of the picture on the sky.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The plate solution. A star at the corner is moved about 2 pixels.
    """
    wcs = make_wcs(shape, arcsec_per_pixel, rotation_degrees)
    a_terms = np.zeros((3, 3))
    b_terms = np.zeros((3, 3))
    # About 2 pixels at 250 pixels from the center.
    a_terms[2, 0] = 3.0e-5
    b_terms[0, 2] = -2.0e-5
    wcs.sip = Sip(a_terms, b_terms, None, None, wcs.wcs.crpix)
    return wcs


def test_the_moving_stack_is_read_onto_the_reference_grid_through_the_sky() -> None:
    """A moving stack of another scale and turn matches the reference."""
    stars = sky_stars(150, seed=21, radius_degrees=0.06)
    reference_shape = (400, 400)
    reference_wcs = make_wcs(reference_shape, 1.5, 0.0)
    moving_shape = (330, 380)
    moving_wcs = make_wcs(moving_shape, 2.1, 12.0, shift=(8.0, -5.0))
    expected = render(reference_shape, reference_wcs, stars, 2.0, 0.0, seed=1)
    moving = render(moving_shape, moving_wcs, stars, 2.0 * 1.5 / 2.1, 0.0, seed=1)

    alignment = SkyAlignment(GridPlacement(reference_wcs), GridPlacement(moving_wcs), reference_shape)
    moved, covered = alignment.resample(moving, 3)

    assert moved.shape == reference_shape
    assert covered.mean() > 0.6
    inside = covered.copy()
    inside[:15] = inside[-15:] = False
    inside[:, :15] = inside[:, -15:] = False
    assert np.sqrt(np.mean((moved[inside] - expected[inside]) ** 2)) < 0.01


def test_a_lens_distortion_in_the_moving_solution_is_followed() -> None:
    """The SIP terms of the moving solution are inverted, not ignored."""
    stars = sky_stars(300, seed=22, radius_degrees=0.1)
    reference_shape = (400, 400)
    reference_wcs = make_wcs(reference_shape, 1.5, 0.0)
    moving_wcs = make_distorted_wcs((500, 500), 1.5, 3.0)
    expected = render(reference_shape, reference_wcs, stars, 2.0, 0.0, seed=1)
    moving = render((500, 500), moving_wcs, stars, 2.0, 0.0, seed=1)

    alignment = SkyAlignment(GridPlacement(reference_wcs), GridPlacement(moving_wcs), reference_shape)
    moved, covered = alignment.resample(moving, 3)

    inside = covered.copy()
    inside[:15] = inside[-15:] = False
    inside[:, :15] = inside[:, -15:] = False
    followed_error = np.sqrt(np.mean((moved[inside] - expected[inside]) ** 2))
    assert followed_error < 0.005
    # Without the distortion the read is about 2 pixels out at the corner.
    flat = SkyAlignment(
        GridPlacement(reference_wcs), GridPlacement(make_wcs((500, 500), 1.5, 3.0)), reference_shape
    )
    flat_moved, flat_covered = flat.resample(moving, 3)
    flat_inside = flat_covered & inside
    flat_error = np.sqrt(np.mean((flat_moved[flat_inside] - expected[flat_inside]) ** 2))
    assert flat_error > 3.0 * followed_error


def test_a_color_image_moves_by_channel_and_an_uncovered_part_is_marked() -> None:
    """Channels move alike, and a grid half off the data is not covered."""
    reference_shape = (200, 200)
    reference_wcs = make_wcs(reference_shape, 1.5, 0.0)
    moving_wcs = make_wcs((200, 200), 1.5, 0.0, shift=(100.0, 0.0))
    plane = np.random.default_rng(3).uniform(0.2, 0.8, (200, 200)).astype(np.float32)
    moving = np.stack([plane, 2.0 * plane, 3.0 * plane])

    alignment = SkyAlignment(GridPlacement(reference_wcs), GridPlacement(moving_wcs), reference_shape)
    moved, covered = alignment.resample(moving, 1)

    assert moved.shape == (3, *reference_shape)
    assert covered.mean() == pytest.approx(0.5, abs=0.02)
    assert np.count_nonzero(moved[:, ~covered]) == 0
    assert np.allclose(moved[2][covered], 3.0 * moved[0][covered])


def test_a_picture_offset_inside_its_stack_is_used() -> None:
    """The offset of a cropped picture moves where the solution is read."""
    reference_shape = (150, 150)
    stack_wcs = make_wcs((200, 200), 1.5, 0.0)
    moving_wcs = make_wcs((200, 200), 1.5, 0.0)
    plane = np.zeros((200, 200), dtype=np.float32)
    plane[60, 70] = 1.0
    alignment = SkyAlignment(GridPlacement(stack_wcs, 10, 20), GridPlacement(moving_wcs), reference_shape)

    moved, _ = alignment.resample(plane, 1)

    # Reference pixel (row, column) reads stack pixel (row + 10, column + 20).
    assert np.unravel_index(np.argmax(moved), moved.shape) == (50, 50)


def stack_and_picture_headers(offset: tuple[int, int], scale: float = 1.0) -> tuple[fits.Header, fits.Header]:
    """Make the stored headers of a stack and of a picture cut from it.

    Parameters
    ----------
    offset : `tuple` [`int`, `int`]
        The stack row and column of the picture's first pixel.
    scale : `float`, optional
        How much larger the picture's pixels are than the stack's.

    Returns
    -------
    stack_header, picture_header : `astropy.io.fits.Header`
        The two headers.
    """
    stack_wcs = make_wcs((300, 300), 1.5, 0.0)
    picture_wcs = make_wcs((300, 300), 1.5 * scale, 0.0)
    picture_wcs.wcs.crpix = [
        stack_wcs.wcs.crpix[0] - offset[1],
        stack_wcs.wcs.crpix[1] - offset[0],
    ]
    return stack_wcs.to_header(), picture_wcs.to_header()


def test_a_picture_cut_from_its_stack_finds_its_place() -> None:
    """The place of a picture comes back as the offset it was cut at."""
    stack_header, picture_header = stack_and_picture_headers((32, 16))

    assert placement_in_stack(stack_header, picture_header, (250, 260)) == (32, 16)


def test_a_picture_that_is_not_a_whole_pixel_cut_has_no_place() -> None:
    """A scaled picture, or a half-pixel offset, is refused."""
    stack_header, scaled = stack_and_picture_headers((10, 10), scale=1.01)
    assert placement_in_stack(stack_header, scaled, (250, 260)) is None
    stack_header, half = stack_and_picture_headers((10, 10))
    half["CRPIX1"] = float(half["CRPIX1"]) + 0.5
    assert placement_in_stack(stack_header, half, (250, 260)) is None
    assert placement_in_stack(stack_header, fits.Header(), (250, 260)) is None


def write_stack(path: Path, shape: tuple[int, int]) -> WCS:
    """Write a small stack with a stored plate solution.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to write.
    shape : `tuple` [`int`, `int`]
        Rows and columns.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The stored plate solution.
    """
    wcs = make_wcs(shape, 1.5, 0.0)
    fits.PrimaryHDU(np.zeros(shape, dtype=np.float32), header=wcs.to_header()).writeto(path)
    return wcs


def test_solve_stack_gives_the_new_solution_and_starts_from_the_stored_center(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Siril's answer is used, started from the stored solution's center."""
    path = tmp_path / "stack.fits"
    stored = write_stack(path, (200, 300))
    new_wcs = make_wcs((200, 300), 1.5, 0.0, shift=(1.0, 2.0))
    seen: dict[str, object] = {}

    def fake_solve(image_path: str, executable: str, hints: dict[str, float]) -> PlateSolveHeader:
        """Record what the caller asked and answer with the new solution.

        Returns
        -------
        header : `PlateSolveHeader`
            The new solution with its fit numbers.
        """
        seen.update(path=image_path, executable=executable, hints=hints)
        return PlateSolveHeader.from_header(
            new_wcs.to_header(), fit_residual_rms_arcsec=0.3, matched_star_count=700
        )

    monkeypatch.setattr(solution_alignment, "solve_image", fake_solve)

    solved, reason = solve_stack(str(path), "siril-cli")

    assert reason == ""
    assert solved is not None
    assert solved.matched_star_count == 700
    assert solved.fit_residual_pixels == pytest.approx(0.3 / 1.5, rel=1e-3)
    assert solved.wcs.wcs.crpix[0] == pytest.approx(new_wcs.wcs.crpix[0])
    assert seen["executable"] == "siril-cli"
    center_ra, center_dec = stored.all_pix2world([149.5], [99.5], 0)
    assert seen["hints"] == {
        "center_ra": pytest.approx(float(center_ra[0])),
        "center_dec": pytest.approx(float(center_dec[0])),
    }


def test_a_stack_siril_cannot_solve_gives_a_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No solution gives no aligned stack, and the stack is named."""
    path = tmp_path / "stack.fits"
    write_stack(path, (100, 100))
    monkeypatch.setattr(solution_alignment, "solve_image", lambda *arguments: None)

    solved, reason = solve_stack(str(path), "siril-cli")

    assert solved is None
    assert "Siril could not solve" in reason and "stack.fits" in reason


def test_the_combined_residual_adds_the_two_fits_in_reference_pixels() -> None:
    """The two residuals add in quadrature, or are unknown."""
    wcs = make_wcs((100, 100), 1.0, 0.0)
    reference = SolvedStack(wcs, 500, 0.3, 1.0)
    moving = SolvedStack(wcs, 500, 0.4, 2.0)

    assert combined_residual_pixels(reference, moving) == pytest.approx(0.5)
    assert combined_residual_pixels(reference, SolvedStack(wcs, None, None, 2.0)) is None
