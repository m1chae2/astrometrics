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
from scipy import ndimage

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
from astrometricslib.pipelines.stacking.post_processing.star_tone import soft_highlights, tone_stars
from astrometricslib.test.synthetic.sky_map import tan_sky_map

SIZE = 320
SKY = 0.006


CRUSHED_SHARE = masked_stretch.MASKED_STRETCH_CRUSHED_SHARE


@pytest.fixture(autouse=True)
def _guard_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch the crushed-region guard off for the synthetic pictures.

    Their regions are not mostly white, so without this the curve would
    never run.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to set the guard's share to zero for the test.
    """
    monkeypatch.setattr(
        masked_stretch,
        "MASKED_STRETCH_CRUSHED_SHARE",
        CRUSHED_SHARE.model_copy(update={"default": -1.0}),
    )


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
        The sky lands near 0.13; everything much brighter than
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
    plan = plan_masked_stretch(ring_stack(), category)

    assert plan.used is False
    assert plan.weights is None
    assert "not a nebula or a galaxy" in plan.reason
    assert plan.record()["used"] is False
    assert plan.step_sentence().startswith("Masked stretch not used")


def test_a_kind_that_does_not_qualify_is_answered_without_reading_the_file(tmp_path: Path) -> None:
    """An unreadable path is fine for a cluster: the file is never opened."""
    plan = plan_masked_stretch_for_file(str(tmp_path / "missing.fits"), "cluster")

    assert plan.used is False
    assert "not a nebula or a galaxy" in plan.reason


def test_an_unreadable_stack_gives_an_unused_plan_with_a_reason(tmp_path: Path) -> None:
    """A nebula whose stack cannot be read is left alone, and says why."""
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a fits file")

    plan = plan_masked_stretch_for_file(str(broken), "nebula")

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_UNREADABLE


def test_a_ring_nebula_gets_a_second_stretch() -> None:
    """The ring is one region and the white point is its own."""
    plan = plan_masked_stretch(ring_stack(), "nebula")

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


def bulge_stack(seed: int = 3) -> np.ndarray:
    """Build a galaxy-like bulge: a smooth peak whose wings fade into the sky.

    Returns
    -------
    image : `numpy.ndarray`
        The sky is 0.006. The bulge is a Gaussian 22 pixels wide (sigma) with
        a peak of 0.06, so the normal stretch passes the knee well outside
        the bulge's half-light radius and crushes the core near white.
    """
    generator = np.random.default_rng(seed)
    image = SKY + 6e-5 * generator.standard_normal((SIZE, SIZE))
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    image += 0.06 * np.exp(-((y - 160) ** 2 + (x - 160) ** 2) / (2 * 22.0**2))
    return image


def gentle_picture(image: np.ndarray, midtones: float = 0.01) -> np.ndarray:
    """Stretch a stack with a smooth curve of the linear value alone.

    Parameters
    ----------
    image : `numpy.ndarray`
        The linear stack.
    midtones : `float`, optional
        The midtones balance; the smaller, the harder the stretch.

    Returns
    -------
    picture : `numpy.ndarray`
        A midtones stretch followed by the highlight compression of the star
        toning, so the picture is one rising function of the stack and crushes
        the bright part toward 0.9, as the normal preview does.
    """
    scaled = np.clip((image - SKY * 0.95) / (1.0 - SKY * 0.95), 0.0, 1.0)
    stretched = np.where(
        scaled <= 0, 0.0, ((midtones - 1.0) * scaled) / ((2.0 * midtones - 1.0) * scaled - midtones)
    )
    return soft_highlights(stretched)


def test_the_ring_and_the_hole_are_told_apart_and_the_sky_is_unchanged() -> None:
    """The ring is spread above the hole; the sky is the normal one."""
    image = ndimage.gaussian_filter(ring_stack(), 3.0)
    plan = plan_masked_stretch(image, "galaxy")
    assert plan.weights is not None
    before = gentle_picture(image, 0.003)
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - 160, x - 160)
    ring_zone = (radius >= 16) & (radius < 20)
    # The normal stretch crushes the ring and the hole close together.
    crushed = np.median(before[ring_zone]) - np.median(before[radius < 5])

    after = apply_masked_stretch(before, image, plan)

    assert after is not None
    spread = np.median(after[ring_zone]) - np.median(after[radius < 5])
    assert spread > 2.0 * crushed
    assert spread > 0.1
    assert np.allclose(after[radius > 40], before[radius > 40], atol=1e-6)
    assert float(after.max()) <= 1.0
    assert float(after.min()) >= 0.0


def test_below_the_knee_the_picture_is_exactly_the_normal_one() -> None:
    """A pixel at or below the knee level is unchanged."""
    image = bulge_stack()
    plan = plan_masked_stretch(image, "galaxy")
    before = gentle_picture(image)

    after = apply_masked_stretch(before, image, plan)

    assert after is not None
    knee = masked_stretch.resolve(masked_stretch.MASKED_STRETCH_KNEE).value
    unchanged = after == before.astype(np.float32)
    assert unchanged[before < knee - 0.05].all()
    assert (~unchanged).any()


def test_the_core_of_a_bulge_is_the_brightest_part_and_the_order_is_kept() -> None:
    """A brighter pixel is never darker than a dimmer one, anywhere."""
    image = bulge_stack()
    plan = plan_masked_stretch(image, "galaxy")
    before = gentle_picture(image)

    after = apply_masked_stretch(before, image, plan)

    assert after is not None and plan.white_point is not None
    assert after[160, 160] > before[160, 160]
    kept = image.ravel() <= plan.white_point
    sorted_output = after.ravel()[kept][np.argsort(image.ravel()[kept], kind="stable")]
    # Pixel noise is far below the picture's own steps; allow one noise width.
    assert np.all(np.diff(sorted_output) >= -2e-3)
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - 160, x - 160)
    profile = [float(np.median(after[(radius >= r) & (radius < r + 3)])) for r in range(0, 90, 3)]
    assert np.all(np.diff(profile) <= 1e-3)


def test_the_curve_is_continuous_in_value_and_slope_at_the_knee() -> None:
    """The log curve starts at the knee with the picture's own slope."""
    fraction = np.linspace(0.0, 1e-8, 5)
    for slope in (1.0, 30.0, 4000.0):
        curved = masked_stretch._log_curve(fraction, slope)
        assert curved[0] == pytest.approx(0.0)
        assert (curved[-1] - curved[0]) / (fraction[-1] - fraction[0]) == pytest.approx(slope, rel=0.02)
        assert masked_stretch._log_curve(np.array([1.0]), slope)[0] == pytest.approx(1.0)
        ramp = masked_stretch._log_curve(np.linspace(0.0, 1.0, 200), slope)
        assert np.all(np.diff(ramp) >= 0.0)


def test_no_pixel_above_the_knee_is_left_outside_the_applied_region() -> None:
    """The grown region holds every connected pixel above the knee."""
    image = bulge_stack()
    plan = plan_masked_stretch(image, "galaxy")
    assert plan.smoothed is not None and plan.weights is not None
    before = gentle_picture(image)
    knee = masked_stretch.resolve(masked_stretch.MASKED_STRETCH_KNEE).value
    extended = masked_stretch._extended_pixels(image.astype(np.float64), plan.smoothed.astype(np.float64))
    point = masked_stretch._level_near(image.astype(np.float64), before, extended, knee)
    assert point is not None

    grown = masked_stretch._grown_region(plan.smoothed.astype(np.float64), plan.weights >= 1.0, point[1])

    above = plan.smoothed > point[1]
    assert grown[above].all()


def test_a_region_the_normal_picture_already_shows_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With less than half of the region above 0.85 nothing changes."""
    monkeypatch.setattr(masked_stretch, "MASKED_STRETCH_CRUSHED_SHARE", CRUSHED_SHARE)
    image = bulge_stack()
    plan = plan_masked_stretch(image, "galaxy")
    gentle = np.clip(gentle_picture(image, 0.01) * 0.5, 0.0, 0.8)

    after = apply_masked_stretch(gentle, image, plan)

    assert after is not None
    assert np.array_equal(after, gentle.astype(np.float32))


def test_a_stack_whose_knee_cannot_be_found_is_not_changed() -> None:
    """With too few extended pixels the blend refuses."""
    image = ring_stack()
    plan = plan_masked_stretch(image, "nebula")
    assert plan.smoothed is not None
    plan.smoothed[:] = 1.0

    assert apply_masked_stretch(gentle_picture(image), image, plan) is None


def test_a_pile_of_stars_is_not_a_smooth_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """A region with half its light in peaks is dropped by the limit."""
    image = star_pile_stack()

    plan = plan_masked_stretch(image, "nebula")

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_REGION
    assert plan.record()["used"] is False
    # The limit does it: with the limit lifted the region is found.
    lifted = masked_stretch.MASKED_STRETCH_STAR_PEAK_LIMIT.model_copy(update={"default": 0.99})
    monkeypatch.setattr(masked_stretch, "MASKED_STRETCH_STAR_PEAK_LIMIT", lifted)
    assert plan_masked_stretch(image, "nebula").used is True


def test_a_star_field_with_no_bright_region_is_left_alone() -> None:
    """A few stars on a flat sky give no region at all."""
    generator = np.random.default_rng(2)
    image = SKY + 6e-5 * generator.standard_normal((SIZE, SIZE))
    _stars(image, [(50, 60), (200, 100), (120, 250)], 0.05)

    plan = plan_masked_stretch(image, "galaxy")

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_REGION


def test_a_colour_pixel_keeps_its_colour() -> None:
    """Each colour channel of a blended pixel is scaled by the same factor."""
    image = ring_stack()
    plan = plan_masked_stretch(image, "nebula")
    base = normal_picture(image)
    picture = np.stack([base * 0.5, base * 0.4, base * 0.3])
    colour_stack = np.stack([image, image, image])

    blended = apply_masked_stretch(picture, colour_stack, plan)

    assert blended is not None
    assert blended.shape == picture.shape
    centre = (slice(None), 160, 160)
    ratios = blended[centre] / blended[centre][0]
    expected = picture[centre] / picture[centre][0]
    assert ratios == pytest.approx(expected, rel=1e-3)
    assert blended[0, 160, 160] >= picture[0, 160, 160] - 1e-6


def test_a_picture_of_another_size_is_refused() -> None:
    """The blend needs the picture and the stack to match pixel for pixel."""
    image = ring_stack()
    plan = plan_masked_stretch(image, "nebula")

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

    plan = plan_masked_stretch_for_file(str(stack_path), "nebula")
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
    first = plan_masked_stretch(image, "nebula")
    second = plan_masked_stretch(image, "nebula")

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
    wcs = tan_sky_map(ra_deg, dec_deg, (161.0, 161.0), degrees_per_pixel=1.9 / 3600.0)
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
    plan = plan_masked_stretch(off_center_ring_stack(), "nebula")

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_TARGET_REGION
    assert plan.position_source == masked_stretch.SOURCE_CENTER


def test_without_a_solution_the_center_stands_for_the_target() -> None:
    """The record says the center was used; a missing size is not tested."""
    plan = plan_masked_stretch(ring_stack(), "galaxy", TargetAnchor(None, None, None))

    assert plan.used is True
    assert plan.position_source == masked_stretch.SOURCE_CENTER
    assert plan.size_ratio is None
    assert plan.record()["position_source"] == masked_stretch.SOURCE_CENTER


def test_a_plate_solution_places_the_target_and_the_size_is_checked() -> None:
    """The region holding the catalog position, of a fitting size, is used."""
    anchor = TargetAnchor(10.0, 20.0, 1.5)
    plan = plan_masked_stretch(ring_stack(), "nebula", anchor, solved_header())

    assert plan.used is True
    assert plan.position_source == masked_stretch.SOURCE_WCS
    assert plan.size_ratio is not None
    assert 0.5 < plan.size_ratio < 1.5


def test_a_region_that_does_not_hold_the_catalog_position_is_not_used() -> None:
    """A smooth region elsewhere, such as a star halo, is left alone."""
    elsewhere = TargetAnchor(10.0 + 60 * 1.9 / 3600.0, 20.0, 1.5)
    plan = plan_masked_stretch(ring_stack(), "nebula", elsewhere, solved_header())

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_TARGET_REGION
    assert plan.position_source == masked_stretch.SOURCE_WCS


def test_a_target_outside_the_frame_is_not_used() -> None:
    """A catalog position off the picture gives that reason."""
    plan = plan_masked_stretch(ring_stack(), "nebula", TargetAnchor(11.0, 20.0, 1.5), solved_header())

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_TARGET_OUTSIDE


@pytest.mark.parametrize("major_axis_arcmin", [0.3, 600.0])
def test_a_region_that_does_not_fit_the_catalog_size_is_not_used(major_axis_arcmin: float) -> None:
    """A region far larger or far smaller than the catalog size is refused."""
    anchor = TargetAnchor(10.0, 20.0, major_axis_arcmin)
    plan = plan_masked_stretch(ring_stack(), "nebula", anchor, solved_header())

    assert plan.used is False
    assert plan.reason.startswith(masked_stretch.REASON_SIZE_MISMATCH)
    assert plan.size_ratio is not None


def test_a_chosen_object_gets_the_step_for_a_cluster_target() -> None:
    """An explicit choice skips the kind check and is named in the record."""
    anchor = TargetAnchor(10.0, 20.0, 1.5, mode="object", object_id="NGC  7635")
    plan = plan_masked_stretch(ring_stack(), "cluster", anchor, solved_header())

    assert plan.used is True
    assert plan.mode == "object"
    assert plan.record()["mode"] == "object"
    assert plan.record()["object"] == "NGC  7635"
    assert plan.record()["category"] == "cluster"


def test_a_chosen_object_still_needs_a_smooth_region_at_its_place() -> None:
    """The position check still applies to an explicit choice."""
    elsewhere = TargetAnchor(10.0 + 60 * 1.9 / 3600.0, 20.0, 1.5, mode="object", object_id="Elsewhere")
    plan = plan_masked_stretch(ring_stack(), "cluster", elsewhere, solved_header())

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_NO_TARGET_REGION
    assert plan.object_id == "Elsewhere"


def test_a_chosen_object_still_has_its_size_checked() -> None:
    """The size check still applies to an explicit choice."""
    anchor = TargetAnchor(10.0, 20.0, 600.0, mode="object", object_id="Huge cloud")
    plan = plan_masked_stretch(ring_stack(), "cluster", anchor, solved_header())

    assert plan.used is False
    assert plan.reason.startswith(masked_stretch.REASON_SIZE_MISMATCH)


def test_off_never_uses_the_step_even_for_a_nebula() -> None:
    """Off skips the step for a target that would qualify."""
    plan = plan_masked_stretch(ring_stack(), "nebula", TargetAnchor(mode="off"))

    assert plan.used is False
    assert plan.reason == masked_stretch.REASON_TURNED_OFF
    assert plan.record()["mode"] == "off"


def test_off_is_answered_without_reading_the_file(tmp_path: Path) -> None:
    """The file planner stops at Off before it opens the stack."""
    plan = plan_masked_stretch_for_file(str(tmp_path / "missing.fits"), "nebula", TargetAnchor(mode="off"))

    assert plan.reason == masked_stretch.REASON_TURNED_OFF


def test_automatic_is_the_rule_used_before_the_choice_existed() -> None:
    """The default anchor gives the same plan as no anchor mode at all."""
    anchor = TargetAnchor(10.0, 20.0, 1.5)
    automatic = plan_masked_stretch(ring_stack(), "nebula", anchor, solved_header())

    assert anchor.mode == "auto"
    assert automatic.used is True
    assert automatic.record()["mode"] == "auto"
    assert plan_masked_stretch(ring_stack(), "cluster", anchor, solved_header()).used is False


def test_the_anchor_reads_the_targets_choice() -> None:
    """`anchor_for_target` turns the saved choice into an anchor."""
    from astrometricslib.models.target import PreviewBrightObject, Target
    from astrometricslib.pipelines.stacking.post_processing.masked_stretch import anchor_for_target

    target = Target(id="M 52", ra="23h 24m 48s", dec="+61° 35′ 36′′", major_axis_arcmin=13.0)

    assert anchor_for_target(target).mode == "auto"
    target.preview_bright_object = PreviewBrightObject(mode="off")
    assert anchor_for_target(target).mode == "off"
    target.preview_bright_object = PreviewBrightObject(
        mode="object", simbad_id="NGC  7635", ra_deg=350.2, dec_deg=61.2, major_axis_arcmin=9.0
    )
    chosen = anchor_for_target(target)
    assert (chosen.mode, chosen.ra_deg, chosen.dec_deg, chosen.major_axis_arcmin) == (
        "object",
        350.2,
        61.2,
        9.0,
    )
    assert chosen.object_id == "NGC  7635"
