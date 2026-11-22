"""Tests for the masked second stretch of small bright smooth objects.

The tests build small synthetic stacks: a smooth ring on a dark sky with a few
stars (like M 57), a dense field of stars (like a globular cluster), and a
star field with no bright region. They check that the step is used only for
a nebula or a galaxy with a smooth bright region, that the sky and the faint
stars stay exactly as the normal picture made them, and that the ring and the
hole inside it are told apart afterwards.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.pipelines.stacking.post_processing import masked_stretch
from astrometricslib.pipelines.stacking.post_processing.masked_stretch import (
    MASKED_STRETCH_CATEGORIES,
    MaskedStretchPlan,
    TargetAnchor,
    apply_masked_stretch,
    apply_masked_stretch_in_file,
    plan_masked_stretch,
    plan_masked_stretch_for_file,
)
from astrometricslib.pipelines.stacking.post_processing.star_tone import tone_stars

SIZE = 320
SKY = 0.006
SKY_BRIGHTNESS = 0.13


def _stars(image: np.ndarray, positions: list[tuple[int, int]], amplitude: float, width: float = 1.2) -> None:
    """Add Gaussian stars to an image, in place.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image to change.
    positions : `list` [`tuple` [`int`, `int`]]
        The (row, column) of each star.
    amplitude : `float`
        The peak of each star.
    width : `float`, optional
        The Gaussian sigma, in pixels.
    """
    y, x = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
    for row, column in positions:
        image += amplitude * np.exp(-((y - row) ** 2 + (x - column) ** 2) / (2 * width**2))


def ring_stack(seed: int = 0) -> np.ndarray:
    """Build a dark sky with a smooth ring nebula and a few stars.

    Returns
    -------
    image : `numpy.ndarray`
        The sky is 0.006 with a little noise. The ring has an outer radius of
        26 pixels, a hole of 8 pixels, and sits about 3 times the sky. The
        hole is a little fainter. Six faint stars lie well outside it.
    """
    generator = np.random.default_rng(seed)
    image = SKY + 6e-5 * generator.standard_normal((SIZE, SIZE))
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - 160, x - 160)
    image += (
        np.where(radius < 26, 0.012, 0.0)
        + np.where(radius < 8, -0.004, 0.0)
        + np.where((radius >= 14) & (radius < 22), 0.006, 0.0)
    )
    _stars(image, [(30, 40), (60, 270), (260, 60), (280, 250), (40, 160), (250, 170)], 0.03)
    return image


def normal_picture(image: np.ndarray) -> np.ndarray:
    """Stretch a stack the way the normal preview does, without Siril.

    Parameters
    ----------
    image : `numpy.ndarray`
        The linear stack.

    Returns
    -------
    picture : `numpy.ndarray`
        The sky lands on `SKY_BRIGHTNESS`; everything much brighter than
        about twice the sky is nearly white.
    """
    scaled = np.clip((image - SKY * 0.95) / (1.0 - SKY * 0.95), 0.0, 1.0)
    midtones = 0.0012
    stretched = ((midtones - 1.0) * scaled) / ((2.0 * midtones - 1.0) * scaled - midtones)
    return tone_stars(np.where(scaled <= 0, 0.0, stretched))


def star_pile_stack(seed: int = 1) -> np.ndarray:
    """Build a patch of bright stars with faint halos, like a loose cluster.

    Returns
    -------
    image : `numpy.ndarray`
        The sky is 0.006. Sixty bright stars (peak 0.4) lie in a disc 60
        pixels across, each with a faint wide halo. The halos together lift
        the smoothed picture above twice the sky, but about half of the
        region's light sits in the stars themselves.
    """
    generator = np.random.default_rng(seed)
    image = SKY + 6e-5 * generator.standard_normal((SIZE, SIZE))
    positions = []
    for _ in range(60):
        radius = 30 * np.sqrt(generator.uniform())
        angle = generator.uniform(0, 2 * np.pi)
        positions.append((round(160 + radius * np.sin(angle)), round(160 + radius * np.cos(angle))))
    _stars(image, positions, 0.4, 1.2)
    _stars(image, positions, 0.006, 8.0)
    return image


def test_only_a_nebula_or_a_galaxy_can_get_the_step() -> None:
    """The step lists exactly the two kinds the design allows."""
    assert frozenset({"nebula", "galaxy"}) == MASKED_STRETCH_CATEGORIES


@pytest.mark.parametrize("category", [None, "cluster", "star", "solar_system"])
def test_other_kinds_of_target_never_get_the_step(category: str | None) -> None:
    """A ring in the data does not matter: the kind decides first."""
    plan = plan_masked_stretch(ring_stack(), category, SKY_BRIGHTNESS)

    assert plan.used is False
    assert plan.weights is None
    assert "not a nebula or a galaxy" in plan.reason
    assert plan.record()["used"] is False
    assert plan.step_sentence().startswith("Masked stretch not used")


def test_a_kind_that_does_not_qualify_is_answered_without_reading_the_file(tmp_path: Path) -> None:
    """An unreadable path is fine for a cluster: the file is never opened."""
    plan = plan_masked_stretch_for_file(str(tmp_path / "missing.fits"), "cluster", SKY_BRIGHTNESS)

    assert plan.used is False
    assert "not a nebula or a galaxy" in plan.reason


def test_an_unreadable_stack_gives_an_unused_plan_with_a_reason(tmp_path: Path) -> None:
    """A nebula whose stack cannot be read is left alone, and says why."""
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a fits file")

    plan = plan_masked_stretch_for_file(str(broken), "nebula", SKY_BRIGHTNESS)

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_UNREADABLE


def test_a_ring_nebula_gets_a_second_stretch() -> None:
    """The ring is one region and the white point is its own."""
    plan = plan_masked_stretch(ring_stack(), "nebula", SKY_BRIGHTNESS)

    assert plan.used is True
    assert plan.region_count == 1
    assert 1500 < plan.region_pixels < 2500
    assert plan.white_point is not None
    # The ring is 0.018 to 0.024; a star would give 0.03 or more.
    assert 0.015 < plan.white_point < 0.03
    assert plan.black_point == pytest.approx(SKY, rel=0.05)
    record = plan.record()
    assert record["used"] is True
    assert record["reason"] == ""
    assert record["region_count"] == 1
    assert "1 bright smooth region" in plan.step_sentence()


def test_the_ring_and_the_hole_are_told_apart_and_the_sky_is_unchanged() -> None:
    """Outside the mask the picture is exactly the normal one."""
    image = ring_stack()
    plan = plan_masked_stretch(image, "galaxy", SKY_BRIGHTNESS)
    assert plan.weights is not None
    before = normal_picture(image)
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - 160, x - 160)
    # The normal stretch whites the whole ring and the hole out.
    assert np.median(before[radius < 6]) > 0.8
    assert np.median(before[(radius >= 16) & (radius < 20)]) > 0.8

    after = apply_masked_stretch(before, image, plan)

    assert after is not None
    hole = float(np.median(after[radius < 5]))
    ring = float(np.median(after[(radius >= 16) & (radius < 20)]))
    assert ring - hole > 0.15
    assert hole < 0.6
    assert np.array_equal(after[plan.weights == 0], before[plan.weights == 0])
    assert np.median(after[radius > 100]) == pytest.approx(np.median(before[radius > 100]))
    assert float(after.max()) <= 1.0
    assert float(after.min()) >= 0.0


def test_the_sky_next_to_the_region_does_not_get_a_dark_moat() -> None:
    """The second stretch gives the sky the normal picture's brightness."""
    image = ring_stack()
    plan = plan_masked_stretch(image, "nebula", SKY_BRIGHTNESS)
    before = normal_picture(image)
    after = apply_masked_stretch(before, image, plan)
    assert after is not None and plan.weights is not None
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - 160, x - 160)
    edge_sky = (radius > 30) & (radius < 36) & (plan.weights > 0)
    assert edge_sky.any()
    assert float(np.median(after[edge_sky])) > 0.8 * SKY_BRIGHTNESS


def test_a_pile_of_stars_is_not_a_smooth_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """A region with half its light in peaks is dropped by the limit."""
    image = star_pile_stack()

    plan = plan_masked_stretch(image, "nebula", SKY_BRIGHTNESS)

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_REGION
    assert plan.record()["used"] is False
    # The limit does it: with the limit lifted the region is found.
    lifted = masked_stretch.MASKED_STRETCH_STAR_PEAK_LIMIT.model_copy(update={"default": 0.99})
    monkeypatch.setattr(masked_stretch, "MASKED_STRETCH_STAR_PEAK_LIMIT", lifted)
    assert plan_masked_stretch(image, "nebula", SKY_BRIGHTNESS).used is True


def test_a_star_field_with_no_bright_region_is_left_alone() -> None:
    """A few stars on a flat sky give no region at all."""
    generator = np.random.default_rng(2)
    image = SKY + 6e-5 * generator.standard_normal((SIZE, SIZE))
    _stars(image, [(50, 60), (200, 100), (120, 250)], 0.05)

    plan = plan_masked_stretch(image, "galaxy", SKY_BRIGHTNESS)

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_REGION


def test_a_colour_pixel_keeps_its_colour() -> None:
    """Each colour channel of a blended pixel is scaled by the same factor."""
    image = ring_stack()
    plan = plan_masked_stretch(image, "nebula", SKY_BRIGHTNESS)
    base = normal_picture(image)
    picture = np.stack([base * 0.9, base * 0.7, base * 0.5])
    colour_stack = np.stack([image, image, image])

    blended = apply_masked_stretch(picture, colour_stack, plan)

    assert blended is not None
    assert blended.shape == picture.shape
    centre = (slice(None), 160, 160)
    ratios = blended[centre] / blended[centre][0]
    expected = picture[centre] / picture[centre][0]
    assert ratios == pytest.approx(expected, rel=1e-3)
    assert blended[0, 160, 160] != pytest.approx(picture[0, 160, 160], abs=0.01)


def test_a_picture_of_another_size_is_refused() -> None:
    """The blend needs the picture and the stack to match pixel for pixel."""
    image = ring_stack()
    plan = plan_masked_stretch(image, "nebula", SKY_BRIGHTNESS)

    assert apply_masked_stretch(np.zeros((10, 10)), image, plan) is None
    assert apply_masked_stretch(normal_picture(image), image[:100, :100], plan) is None
    assert apply_masked_stretch(normal_picture(image), image, MaskedStretchPlan(False, "x", None)) is None


def test_the_step_works_on_files(tmp_path: Path) -> None:
    """The file form reads the stack and the picture and keeps the header."""
    image = ring_stack().astype(np.float32)
    stack_path, picture_path, output_path = (
        tmp_path / name for name in ("stack.fits", "pic.fits", "out.fits")
    )
    fits.writeto(stack_path, image)
    header = fits.Header()
    header["OBJECT"] = "M 57"
    fits.writeto(picture_path, normal_picture(image).astype(np.float32), header)

    plan = plan_masked_stretch_for_file(str(stack_path), "nebula", SKY_BRIGHTNESS)
    assert plan.used is True
    assert apply_masked_stretch_in_file(str(stack_path), str(picture_path), str(output_path), plan) is True

    with fits.open(output_path) as written:
        assert written[0].header["OBJECT"] == "M 57"
        assert written[0].data.shape == image.shape
    assert (
        apply_masked_stretch_in_file(str(stack_path), str(tmp_path / "none.fits"), str(output_path), plan)
        is False
    )


def test_the_step_is_deterministic() -> None:
    """The same stack gives the same plan and the same picture."""
    image = ring_stack()
    first = plan_masked_stretch(image, "nebula", SKY_BRIGHTNESS)
    second = plan_masked_stretch(image, "nebula", SKY_BRIGHTNESS)

    assert first.record() == second.record()
    assert first.weights is not None and second.weights is not None
    assert np.array_equal(first.weights, second.weights)


def solved_header(ra_deg: float = 10.0, dec_deg: float = 20.0) -> fits.Header:
    """Build a header that puts a sky point at pixel (160, 160).

    Parameters
    ----------
    ra_deg, dec_deg : `float`
        The sky point at the center of the ring, in degrees.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A tangent-plane solution of 1.9 arcseconds per pixel.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra_deg, dec_deg]
    wcs.wcs.crpix = [161.0, 161.0]
    wcs.wcs.cdelt = [-1.9 / 3600.0, 1.9 / 3600.0]
    return wcs.to_header()


def off_center_ring_stack() -> np.ndarray:
    """Build the ring stack with the ring moved far from the image center.

    Returns
    -------
    image : `numpy.ndarray`
        The ring of `ring_stack`, now centered on row 60 and column 60.
    """
    moved = np.roll(ring_stack(), (-100, -100), axis=(0, 1))
    return moved


def test_a_ring_away_from_the_image_center_is_not_the_target() -> None:
    """With no plate solution the region must be near the center."""
    plan = plan_masked_stretch(off_center_ring_stack(), "nebula", SKY_BRIGHTNESS)

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_TARGET_REGION
    assert plan.position_source == masked_stretch.SOURCE_CENTER


def test_without_a_solution_the_center_stands_for_the_target() -> None:
    """The record says the center was used; a missing size is not tested."""
    plan = plan_masked_stretch(ring_stack(), "galaxy", SKY_BRIGHTNESS, TargetAnchor(None, None, None))

    assert plan.used is True
    assert plan.position_source == masked_stretch.SOURCE_CENTER
    assert plan.size_ratio is None
    assert plan.record()["position_source"] == masked_stretch.SOURCE_CENTER


def test_a_plate_solution_places_the_target_and_the_size_is_checked() -> None:
    """The region holding the catalog position, of a fitting size, is used."""
    anchor = TargetAnchor(10.0, 20.0, 1.5)
    plan = plan_masked_stretch(ring_stack(), "nebula", SKY_BRIGHTNESS, anchor, solved_header())

    assert plan.used is True
    assert plan.position_source == masked_stretch.SOURCE_WCS
    assert plan.size_ratio is not None
    assert 0.5 < plan.size_ratio < 1.5


def test_a_region_that_does_not_hold_the_catalog_position_is_not_used() -> None:
    """A smooth region elsewhere, such as a star halo, is left alone."""
    elsewhere = TargetAnchor(10.0 + 60 * 1.9 / 3600.0, 20.0, 1.5)
    plan = plan_masked_stretch(ring_stack(), "nebula", SKY_BRIGHTNESS, elsewhere, solved_header())

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_TARGET_REGION
    assert plan.position_source == masked_stretch.SOURCE_WCS


def test_a_target_outside_the_frame_is_not_used() -> None:
    """A catalog position off the picture gives that reason."""
    plan = plan_masked_stretch(
        ring_stack(), "nebula", SKY_BRIGHTNESS, TargetAnchor(11.0, 20.0, 1.5), solved_header()
    )

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_TARGET_OUTSIDE


@pytest.mark.parametrize("major_axis_arcmin", [0.3, 600.0])
def test_a_region_that_does_not_fit_the_catalog_size_is_not_used(major_axis_arcmin: float) -> None:
    """A region far larger or far smaller than the catalog size is refused."""
    anchor = TargetAnchor(10.0, 20.0, major_axis_arcmin)
    plan = plan_masked_stretch(ring_stack(), "nebula", SKY_BRIGHTNESS, anchor, solved_header())

    assert plan.used is False
    assert plan.reason.startswith(masked_stretch.REASON_SIZE_MISMATCH)
    assert plan.size_ratio is not None
