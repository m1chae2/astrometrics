"""Tests for the choice between a fitted sky plane and GraXpert.

The images are small synthetic arrays: a flat sky with a tilt, a bright blob
standing for a nebula and a few stars. GraXpert is not started.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing.sky_plane import (
    SKY_PLANE_GRADIENT_LIMIT,
    choose_background_method,
    choose_background_method_for_file,
    select_sky_bins,
    subtract_sky_plane,
    subtract_sky_plane_in_file,
)

SIZE = 256
SKY_LEVEL = 0.05


def make_sky(tilt_fraction: float, blob_amplitude: float = 0.0, seed: int = 1) -> np.ndarray:
    """Make a tilted sky with a round blob and a little noise.

    Parameters
    ----------
    tilt_fraction : `float`
        The change of the sky from the left edge to the right edge, as a
        fraction of the sky level.
    blob_amplitude : `float`, optional
        The height of a Gaussian blob at the center, in the sky's units.
    seed : `int`, optional
        Seed of the noise.

    Returns
    -------
    image : `numpy.ndarray`
        A 256 by 256 float32 image.
    """
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    tilt = SKY_LEVEL * tilt_fraction * (columns / (SIZE - 1) - 0.5)
    blob = blob_amplitude * np.exp(-((rows - SIZE / 2) ** 2 + (columns - SIZE / 2) ** 2) / (2 * 15.0**2))
    noise = np.random.default_rng(seed).normal(0.0, 0.0002, (SIZE, SIZE))
    return (SKY_LEVEL + tilt + blob + noise).astype(np.float32)


def ring_medians(image: np.ndarray) -> list[float]:
    """Give the median of the image in rings around the center.

    Returns
    -------
    medians : `list` [`float`]
        The median in each ring from 25 to 125 pixels out, 20 pixels wide.
    """
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(rows - (SIZE - 1) / 2, columns - (SIZE - 1) / 2)
    return [float(np.median(image[(radius >= low) & (radius < low + 20)])) for low in range(25, 125, 20)]


def test_a_flat_tilted_sky_with_a_blob_takes_the_plane_path() -> None:
    """The measured gradient is the tilt, the blob does not change it."""
    decision = choose_background_method(make_sky(0.01, blob_amplitude=0.05))

    assert decision.method == "plane"
    assert decision.gradient_fraction == pytest.approx(0.01, abs=0.003)
    assert decision.limit_fraction == SKY_PLANE_GRADIENT_LIMIT.default
    assert len(decision.plane_coefficients) == 1
    assert "Background: plane fit (gradient 1.0%, limit 3.0%)" == decision.step_sentence()


def test_the_plane_leaves_no_ring_and_keeps_the_blob() -> None:
    """After the plane the sky is level in every ring; the blob stays."""
    image = make_sky(0.02, blob_amplitude=0.05)
    decision = choose_background_method(image)

    flattened = subtract_sky_plane(image, decision.plane_coefficients)

    # Rings beyond the blob read the sky level, with no moat and no rise.
    for median in ring_medians(flattened)[1:]:
        assert median == pytest.approx(SKY_LEVEL, rel=0.003)
    # The sky tilt is gone, so the left and right edges agree.
    assert np.median(flattened[:, :16]) == pytest.approx(np.median(flattened[:, -16:]), rel=0.003)
    # The blob keeps its height.
    assert flattened[SIZE // 2, SIZE // 2] == pytest.approx(image[SIZE // 2, SIZE // 2], rel=0.01)
    assert flattened.dtype == image.dtype and flattened.shape == image.shape


def test_the_blob_does_not_pull_the_plane_when_it_is_off_center() -> None:
    """A bright object near one edge is left out of the sky bins."""
    image = make_sky(0.0)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image += (0.08 * np.exp(-((rows - 128) ** 2 + (columns - 200) ** 2) / (2 * 20.0**2))).astype(np.float32)

    decision = choose_background_method(image)

    assert decision.method == "plane"
    assert decision.gradient_fraction is not None and decision.gradient_fraction < 0.01


def test_a_strong_gradient_takes_the_graxpert_path() -> None:
    """A tilt above the limit is left to GraXpert, and no plane is made."""
    decision = choose_background_method(make_sky(0.10))

    assert decision.method == "graxpert"
    assert decision.gradient_fraction == pytest.approx(0.10, abs=0.01)
    assert decision.plane_coefficients == []
    assert decision.step_sentence() == ""
    assert "at or above" in decision.reason


def test_a_colour_stack_takes_its_largest_tilt() -> None:
    """One strongly tilted channel is enough to send the stack to GraXpert."""
    flat = make_sky(0.005, seed=1)
    tilted = make_sky(0.08, seed=2)
    decision = choose_background_method(np.stack([flat, flat, tilted]))

    assert decision.method == "graxpert"
    assert decision.gradient_fraction == pytest.approx(0.08, abs=0.01)


def test_a_colour_stack_with_a_flat_sky_gets_one_plane_for_each_channel() -> None:
    """Each channel is flattened by its own plane and keeps its level."""
    stack = np.stack([make_sky(0.01, seed=1), 1.2 * make_sky(0.015, seed=2), 0.8 * make_sky(0.005, seed=3)])
    decision = choose_background_method(stack)

    assert decision.method == "plane"
    assert len(decision.plane_coefficients) == 3
    flattened = subtract_sky_plane(stack, decision.plane_coefficients)
    for channel, original in zip(flattened, stack, strict=True):
        assert np.median(channel) == pytest.approx(np.median(original), rel=0.002)
        assert np.median(channel[:, :16]) == pytest.approx(np.median(channel[:, -16:]), rel=0.003)


def test_empty_border_pixels_stay_empty() -> None:
    """Zero pixels (a blank border) are not given a negative value."""
    image = make_sky(0.01)
    image[:, :8] = 0.0
    decision = choose_background_method(image)

    flattened = subtract_sky_plane(image, decision.plane_coefficients)

    assert decision.method == "plane"
    assert not np.any(flattened[:, :8])


@pytest.mark.parametrize(
    "image",
    [np.zeros((SIZE, SIZE), dtype=np.float32), np.full((32, 32), 0.05, dtype=np.float32), np.zeros(10)],
    ids=["no pixel above zero", "smaller than the grid", "not an image"],
)
def test_a_stack_that_cannot_be_measured_keeps_graxpert(image: np.ndarray) -> None:
    """When the tilt cannot be judged, the method used before is kept."""
    decision = choose_background_method(image)

    assert decision.method == "graxpert"
    assert decision.gradient_fraction is None
    assert decision.reason


def test_the_sky_bins_leave_out_a_bright_object() -> None:
    """Bins under a bright blob are not taken as sky; the rest are."""
    from astrometricslib.pipelines.stacking.post_processing.sky_plane import _bin_medians

    image = make_sky(0.0, blob_amplitude=0.1)
    sky = select_sky_bins(_bin_medians(image.astype(np.float64)))

    assert not sky[32, 32]
    assert sky[2, 2] and sky[60, 60]
    assert sky.sum() > 0.8 * sky.size


def test_a_file_that_cannot_be_read_keeps_graxpert(tmp_path: Path) -> None:
    """A missing or broken file gives the GraXpert path with a reason."""
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a fits file")

    decision = choose_background_method_for_file(str(broken))

    assert decision.method == "graxpert"
    assert "could not be read" in decision.reason


def test_the_plane_file_keeps_the_header_and_the_input_is_unchanged(tmp_path: Path) -> None:
    """The flattened file is a new file with the same header."""
    image = make_sky(0.02)
    source = tmp_path / "stack.fits"
    header = fits.Header()
    header["OBJECT"] = "TEST"
    fits.PrimaryHDU(image, header).writeto(source)
    decision = choose_background_method_for_file(str(source))
    target = tmp_path / "flat.fits"

    assert subtract_sky_plane_in_file(str(source), str(target), decision.plane_coefficients)

    with fits.open(target) as written:
        assert written[0].header["OBJECT"] == "TEST"
        assert written[0].data.shape == image.shape
    with fits.open(source) as untouched:
        assert np.array_equal(untouched[0].data, image)


def test_the_plane_file_reports_a_mismatch_instead_of_raising(tmp_path: Path) -> None:
    """Planes that do not match the file are a failed step, not an error."""
    source = tmp_path / "stack.fits"
    fits.PrimaryHDU(make_sky(0.01)).writeto(source)

    assert not subtract_sky_plane_in_file(str(source), str(tmp_path / "flat.fits"), [(0.0, 0.0), (0.0, 0.0)])


def make_vignetted_sky(corner_falloff: float, galaxy_amplitude: float = 0.0, seed: int = 4) -> np.ndarray:
    """Make a flat sky that darkens toward the corners, with a small galaxy.

    Parameters
    ----------
    corner_falloff : `float`
        The fall of the sky at the frame corners, as a fraction of the sky
        level, following ``r^2`` with ``r`` in half-widths.
    galaxy_amplitude : `float`, optional
        The height of a small Gaussian galaxy off the center, in the sky's
        units.
    seed : `int`, optional
        Seed of the noise.

    Returns
    -------
    image : `numpy.ndarray`
        A 256 by 256 float32 image.
    """
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    x = (columns + 0.5) / SIZE * 2.0 - 1.0
    y = (rows + 0.5) / SIZE * 2.0 - 1.0
    falloff = -SKY_LEVEL * corner_falloff / 2.0 * (x**2 + y**2)
    galaxy = galaxy_amplitude * np.exp(-((rows - 90) ** 2 + (columns - 170) ** 2) / (2 * 10.0**2))
    noise = np.random.default_rng(seed).normal(0.0, 0.0002, (SIZE, SIZE))
    return (SKY_LEVEL + falloff + galaxy + noise).astype(np.float32)


def corner_and_middle_medians(image: np.ndarray) -> tuple[float, float]:
    """Give the median of the four corners and of the middle ring.

    Returns
    -------
    corner, middle : `float`
        The median within 40 pixels of the corners (about 1.2 to 1.4
        half-widths) and the median of the ring 60 to 90 pixels from the
        center.
    """
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(rows - (SIZE - 1) / 2, columns - (SIZE - 1) / 2)
    return float(np.median(image[radius > 150])), float(np.median(image[(radius > 60) & (radius < 90)]))


def test_a_radial_falloff_is_flattened_and_a_small_galaxy_is_kept() -> None:
    """The corners come up to the middle's level; the galaxy stays."""
    image = make_vignetted_sky(0.006, galaxy_amplitude=0.04)
    decision = choose_background_method(image)

    assert decision.method == "plane"
    assert decision.radial_used
    assert decision.radial_fraction == pytest.approx(-0.006, abs=0.0015)
    assert "radial falloff" in decision.step_sentence()
    assert decision.record()["radial_used"] is True
    flattened = subtract_sky_plane(image, decision.plane_coefficients, decision.radial_coefficients)

    before_corner, before_middle = corner_and_middle_medians(image)
    corner, middle = corner_and_middle_medians(flattened)
    assert before_middle - before_corner > 0.004 * SKY_LEVEL
    assert corner == pytest.approx(middle, abs=0.0015 * SKY_LEVEL)
    assert flattened[90, 170] == pytest.approx(image[90, 170], rel=0.02)
    assert flattened[SIZE // 2, SIZE // 2] == pytest.approx(image[SIZE // 2, SIZE // 2], rel=0.001)


def test_a_flat_sky_gets_the_plane_alone() -> None:
    """With no falloff the radial part is left out; the plane is unchanged."""
    image = make_sky(0.01, blob_amplitude=0.05)
    decision = choose_background_method(image)

    assert decision.method == "plane"
    assert not decision.radial_used
    assert decision.radial_coefficients == []
    assert decision.record()["radial_used"] is False
    assert decision.step_sentence() == "Background: plane fit (gradient 1.0%, limit 3.0%)"
    assert np.array_equal(
        subtract_sky_plane(image, decision.plane_coefficients, decision.radial_coefficients),
        subtract_sky_plane(image, decision.plane_coefficients),
    )


def test_a_frame_filling_blob_does_not_get_a_radial_part() -> None:
    """A glow that fades toward the edges is not taken for vignetting.

    The blob is wide enough that most bins pass as sky, as on NGC 2244. Its
    fall toward the corners is far above the limit, so nothing radial is
    subtracted and no ring can be carved.
    """
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    glow = 0.03 * np.exp(-((rows - 128) ** 2 + (columns - 128) ** 2) / (2 * 85.0**2))
    image = (make_sky(0.0) + glow).astype(np.float32)
    decision = choose_background_method(image)

    assert decision.method == "plane"
    assert not decision.radial_used
    assert decision.radial_reason
    flattened = subtract_sky_plane(image, decision.plane_coefficients, decision.radial_coefficients)
    assert np.allclose(flattened, subtract_sky_plane(image, decision.plane_coefficients))


def test_a_sky_that_brightens_toward_the_corners_gets_no_radial_part() -> None:
    """Only the darkening of vignetting is removed, never a rise."""
    decision = choose_background_method(make_vignetted_sky(-0.006))

    assert decision.method == "plane"
    assert not decision.radial_used


def test_a_falloff_above_the_limit_is_not_cut_back() -> None:
    """A falloff beyond what vignetting makes is left alone, not clipped."""
    decision = choose_background_method(make_vignetted_sky(0.03))

    assert not decision.radial_used
    assert "limit" in decision.radial_reason


def test_the_radial_part_is_subtracted_for_every_colour_channel() -> None:
    """A colour stack gets one radial pair for each channel, or none."""
    stack = np.stack([make_vignetted_sky(0.006, seed=1), make_vignetted_sky(0.006, seed=2)])
    decision = choose_background_method(stack)

    assert decision.radial_used
    assert len(decision.radial_coefficients) == 2
    with pytest.raises(ValueError, match="radial"):
        subtract_sky_plane(stack, decision.plane_coefficients, [(0.0, 0.0)])


def test_a_colour_stack_whose_channels_change_differently_toward_the_corners_takes_graxpert() -> None:
    """One channel that brightens while another darkens is not vignetting."""
    stack = np.stack([
        make_vignetted_sky(-0.2, seed=1),
        make_vignetted_sky(0.06, seed=2),
        make_vignetted_sky(0.02, seed=3),
    ])
    decision = choose_background_method(stack)

    assert decision.method == "graxpert"
    assert "color channels" in decision.reason
    assert decision.plane_coefficients == []


def test_a_colour_stack_with_the_same_vignetting_in_every_channel_keeps_the_plane_path() -> None:
    """Vignetting shared by all channels is not a color gradient."""
    stack = np.stack([make_vignetted_sky(0.03, seed=seed) for seed in (1, 2, 3)])
    decision = choose_background_method(stack)

    assert decision.method == "plane"
