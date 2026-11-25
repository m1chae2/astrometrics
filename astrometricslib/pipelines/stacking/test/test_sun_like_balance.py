"""Tests for the white-balance check against stars like the Sun.

Each test builds a small synthetic sky: Gaussian stars on a grid, seen through
channels of known sensitivity, with a plate solution and a table of Gaia
colors that match the stars. The check must find the sensitivities back from
the sun-like stars alone, leave the sky alone, and say why when it does
nothing.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.pipelines.stacking.post_processing.sun_like_balance import (
    SunLikeBalance,
    balance_sun_like_stars,
    balance_sun_like_stars_in_file,
    select_sun_like_stars,
)

SIZE = 600
GRID_SPACING = 50
# The red and blue light a sun-like star puts in the camera for one unit of
# green: a camera that sees too much red and a little too little blue.
SUN_LIKE_SENSITIVITY = (1.15, 1.0, 0.95)
SKY_LEVEL = 0.01
SUN_LIKE_COLOUR = 0.82


def make_wcs() -> WCS:
    """Make a plate solution for the synthetic image.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A tangent-plane solution, 2 arcseconds per pixel, near declination 30.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [300.0, 30.0]
    wcs.wcs.crpix = [SIZE / 2.0, SIZE / 2.0]
    wcs.wcs.cdelt = [-2.0 / 3600.0, 2.0 / 3600.0]
    return wcs


def make_sky(
    colours: list[float], sensitivities: list[tuple[float, float, float]], seed: int = 0
) -> tuple[np.ndarray, Table]:
    """Make a color image of Gaussian stars and the matching Gaia table.

    Star ``i`` sits on grid point ``i``, has Gaia color ``colours[i]`` and is
    seen through ``sensitivities[i]`` (red, green and blue weights).

    Returns
    -------
    image : `numpy.ndarray`
        The image, shape (3, SIZE, SIZE), with the same sky level in the
        three channels.
    stars : `astropy.table.Table`
        The Gaia stars: ``ra``, ``dec``, ``g`` and ``bp_rp``.
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    image = np.full((3, SIZE, SIZE), SKY_LEVEL) + rng.normal(0.0, 0.0002, (3, SIZE, SIZE))
    grid = np.arange(GRID_SPACING, SIZE - GRID_SPACING / 2, GRID_SPACING)
    points = [(px, py) for py in grid for px in grid][: len(colours)]
    wcs = make_wcs()
    for (px, py), weights in zip(points, sensitivities, strict=True):
        shape = np.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * 2.5**2))
        image += 0.3 * shape * np.asarray(weights)[:, None, None]
    ra, dec = wcs.all_pix2world([p[0] for p in points], [p[1] for p in points], 0)
    stars = Table({"ra": ra, "dec": dec, "g": np.full(len(points), 12.0), "bp_rp": colours})
    return image, stars


def sun_like_sky(count: int = 40) -> tuple[np.ndarray, Table]:
    """Make a sky of `count` sun-like stars seen through the usual camera.

    Returns
    -------
    image : `numpy.ndarray`
        The image.
    stars : `astropy.table.Table`
        The Gaia stars.
    """
    return make_sky([SUN_LIKE_COLOUR] * count, [SUN_LIKE_SENSITIVITY] * count)


def test_the_gains_make_the_sun_like_stars_white() -> None:
    """The red and blue gains undo the camera's weights for sun-like stars."""
    image, stars = sun_like_sky()

    corrected, result = balance_sun_like_stars(image, make_wcs(), stars)

    assert corrected is not None
    assert result.applied
    assert result.red_gain == pytest.approx(1 / 1.15, rel=0.01)
    assert result.blue_gain == pytest.approx(1 / 0.95, rel=0.01)
    assert result.red_to_green_before == pytest.approx(1.15, rel=0.01)
    assert result.red_to_green_after == pytest.approx(1.0, abs=0.005)
    assert result.blue_to_green_after == pytest.approx(1.0, abs=0.005)
    assert result.star_count == 40
    assert result.colour_range == (0.75, 0.90)


def test_the_skies_of_the_three_channels_end_up_equal() -> None:
    """The empty-sky levels are made equal, so a neutral sky stays neutral."""
    image, stars = sun_like_sky()

    corrected, _ = balance_sun_like_stars(image, make_wcs(), stars)

    assert corrected is not None
    after = [measure_sky_background(channel)[0] for channel in corrected]
    assert after == pytest.approx([SKY_LEVEL] * 3, abs=1e-5)


def test_a_small_difference_between_the_channel_skies_is_removed() -> None:
    """A sky tint of a fraction of the noise is levelled to the green sky."""
    image, stars = sun_like_sky()
    image[0] -= 0.0001
    image[2] += 0.0001

    corrected, result = balance_sun_like_stars(image, make_wcs(), stars)

    assert corrected is not None
    after = [measure_sky_background(channel)[0] for channel in corrected]
    assert after[0] == pytest.approx(after[1], abs=3e-6)
    assert after[2] == pytest.approx(after[1], abs=3e-6)
    assert result.red_sky_shift == pytest.approx(0.0001, abs=3e-6)
    assert result.blue_sky_shift == pytest.approx(-0.0001, abs=3e-6)


def test_a_nebula_does_not_move_the_sky_the_correction_is_made_about() -> None:
    """A bright region that lifts the median leaves the empty sky alone."""
    image, stars = sun_like_sky()
    image[:, : SIZE // 2, :] += 0.01
    before = [measure_sky_background(channel)[0] for channel in image]

    corrected, _ = balance_sun_like_stars(image, make_wcs(), stars)

    assert corrected is not None
    after = [measure_sky_background(channel)[0] for channel in corrected]
    assert after == pytest.approx(before, abs=2e-5)


def test_stars_of_other_colors_do_not_set_the_gains() -> None:
    """Red stars in the field are not measured, whatever their flux ratios."""
    colours = [SUN_LIKE_COLOUR] * 30 + [1.8] * 30
    sensitivities = [SUN_LIKE_SENSITIVITY] * 30 + [(1.6, 1.0, 0.5)] * 30
    image, stars = make_sky(colours, sensitivities)

    _, result = balance_sun_like_stars(image, make_wcs(), stars)

    assert result.applied
    assert result.star_count == 30
    assert result.red_gain == pytest.approx(1 / 1.15, rel=0.01)


def test_too_few_stars_skip_the_correction_and_say_why() -> None:
    """Fewer stars than the minimum change nothing and give a reason."""
    image, stars = sun_like_sky(count=5)

    corrected, result = balance_sun_like_stars(image, make_wcs(), stars)

    assert corrected is None
    assert not result.applied
    assert "only 5" in result.reason
    assert result.red_gain is None


def test_the_band_is_widened_once_when_the_narrow_band_is_short() -> None:
    """Stars just outside the narrow band count when the wide band is used."""
    image, stars = make_sky([0.72] * 30, [SUN_LIKE_SENSITIVITY] * 30)

    _, result = balance_sun_like_stars(image, make_wcs(), stars)

    assert result.applied
    assert result.colour_range == (0.70, 1.00)


def test_a_correction_above_the_limit_is_not_applied_and_is_flagged() -> None:
    """A change of more than 20 percent is reported but not made."""
    image, stars = make_sky([SUN_LIKE_COLOUR] * 30, [(1.5, 1.0, 1.0)] * 30)

    corrected, result = balance_sun_like_stars(image, make_wcs(), stars)

    assert corrected is None
    assert not result.applied
    assert "more than 20 percent" in result.reason
    assert result.red_gain == pytest.approx(1 / 1.5, rel=0.01)
    assert result.red_to_green_after is None


def test_a_star_with_a_close_neighbor_is_left_out() -> None:
    """A sun-like star with another catalog star 10 pixels away is not used."""
    _, stars = sun_like_sky(count=30)
    wcs = make_wcs()
    first_x, first_y = wcs.all_world2pix(stars["ra"][0], stars["dec"][0], 0)
    near_ra, near_dec = wcs.all_pix2world(first_x + 10.0, first_y, 0)
    stars.add_row((float(near_ra), float(near_dec), 12.0, 1.5))

    chosen = select_sun_like_stars(stars, wcs, (SIZE, SIZE), (0.75, 0.90))

    assert len(chosen) == 29
    assert not np.any(np.hypot(chosen[:, 0] - first_x, chosen[:, 1] - first_y) < 1.0)


def test_stars_outside_the_magnitude_range_are_left_out() -> None:
    """A sun-like star too bright or too faint is not measured."""
    _, stars = sun_like_sky(count=30)
    stars["g"][:5] = 9.0
    stars["g"][5:8] = 14.5

    chosen = select_sun_like_stars(stars, make_wcs(), (SIZE, SIZE), (0.75, 0.90))

    assert len(chosen) == 22


def write_solved_image(path: Path, image: np.ndarray) -> None:
    """Save a synthetic image with its plate solution.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write.
    image : `numpy.ndarray`
        The image, channels first.
    """
    fits.writeto(path, image.astype(np.float32), make_wcs().to_header(), overwrite=True)


def test_the_file_step_writes_a_corrected_copy(tmp_path: Path) -> None:
    """The saved copy has white sun-like stars and the input is untouched."""
    image, stars = sun_like_sky()
    source, target = tmp_path / "colour.fits", tmp_path / "balanced.fits"
    write_solved_image(source, image)
    before = source.read_bytes()

    result = balance_sun_like_stars_in_file(str(source), str(target), lambda corners, margin: stars)

    assert result.applied
    assert source.read_bytes() == before
    saved = fits.getdata(target)
    _, check = balance_sun_like_stars(saved.astype(np.float64), make_wcs(), stars)
    assert check.red_gain == pytest.approx(1.0, abs=0.01)
    assert check.blue_gain == pytest.approx(1.0, abs=0.01)
    assert "40 sun-like stars" in result.step_sentence()


def test_the_file_step_asks_for_the_footprint_of_the_whole_image(tmp_path: Path) -> None:
    """The catalog question names the image's four corners and a margin."""
    image, stars = sun_like_sky()
    source = tmp_path / "colour.fits"
    write_solved_image(source, image)
    asked: list[tuple[list[tuple[float, float]], float]] = []

    def load(corners: list[tuple[float, float]], margin: float) -> Table:
        """Remember the footprint and give the stars.

        Returns
        -------
        stars : `astropy.table.Table`
            The synthetic stars.
        """
        asked.append((corners, margin))
        return stars

    balance_sun_like_stars_in_file(str(source), str(tmp_path / "out.fits"), load)

    corners, margin = asked[0]
    ras = [corner[0] for corner in corners]
    decs = [corner[1] for corner in corners]
    half_width_degrees = (SIZE / 2.0) * 2.0 / 3600.0
    assert len(corners) == 4
    assert max(decs) - min(decs) == pytest.approx(2 * half_width_degrees, rel=0.01)
    assert max(ras) - min(ras) > 2 * half_width_degrees * 0.99
    assert margin > 0.0


def test_an_image_without_a_plate_solution_is_skipped(tmp_path: Path) -> None:
    """Without a solution the stars cannot be placed, so nothing changes."""
    image, stars = sun_like_sky()
    source, target = tmp_path / "colour.fits", tmp_path / "balanced.fits"
    fits.writeto(source, image.astype(np.float32))

    result = balance_sun_like_stars_in_file(str(source), str(target), lambda corners, margin: stars)

    assert not result.applied
    assert "no plate solution" in result.reason
    assert not target.exists()


def test_an_unreachable_catalog_is_a_reason_and_not_an_error(tmp_path: Path) -> None:
    """A catalog that cannot be reached skips the check with a reason."""
    image, _ = sun_like_sky()
    source, target = tmp_path / "colour.fits", tmp_path / "balanced.fits"
    write_solved_image(source, image)

    def unreachable(corners: list[tuple[float, float]], margin: float) -> Table:
        """Fail like a Gaia archive that does not answer.

        Raises
        ------
        ExternalServiceError
            Always.
        """
        raise ExternalServiceError("no answer")

    result = balance_sun_like_stars_in_file(str(source), str(target), unreachable)

    assert not result.applied
    assert "not available" in result.reason
    assert not target.exists()


def test_a_field_with_no_catalog_stars_is_skipped(tmp_path: Path) -> None:
    """An empty catalog answer skips the check with a reason."""
    image, _ = sun_like_sky()
    source, target = tmp_path / "colour.fits", tmp_path / "balanced.fits"
    write_solved_image(source, image)

    result = balance_sun_like_stars_in_file(str(source), str(target), lambda corners, margin: None)

    assert not result.applied
    assert "no Gaia star colors" in result.reason


def test_a_single_channel_image_is_skipped(tmp_path: Path) -> None:
    """A mono image has no colors to balance."""
    source, target = tmp_path / "mono.fits", tmp_path / "balanced.fits"
    fits.writeto(source, np.zeros((SIZE, SIZE), dtype=np.float32), make_wcs().to_header())

    result = balance_sun_like_stars_in_file(str(source), str(target), lambda corners, margin: None)

    assert not result.applied
    assert "not a color image" in result.reason


def test_the_record_holds_every_number_as_plain_data() -> None:
    """The record is ready to save as JSON, and the sentence reads well."""
    applied = SunLikeBalance(
        applied=True,
        reason="done",
        star_count=41,
        colour_range=(0.75, 0.9),
        red_gain=0.912,
        blue_gain=1.02,
    )
    skipped = SunLikeBalance(applied=False, reason="only 3 stars")

    assert applied.record()["colour_range"] == [0.75, 0.9]
    assert applied.step_sentence() == "White balance check on 41 sun-like stars: red x0.912, blue x1.020"
    assert skipped.step_sentence() == "White balance check not applied (only 3 stars)"
    assert set(skipped.record()) == set(applied.record())
