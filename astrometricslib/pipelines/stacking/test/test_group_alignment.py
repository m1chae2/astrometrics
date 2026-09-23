"""Purpose: Unit tests for lining up exposure-group stacks.

Description: Siril registers each exposure group to its own reference frame,
so the group stacks of one session were found 6-7 pixels apart in real data.
These tests check that a known shift of a synthetic star field is recovered to
a fraction of a pixel, that two unrelated fields are refused instead of being
"aligned", that colour images move as one, and that the pixels a shift fills
in are reported.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.stacking.group_alignment import (
    MINIMUM_ALIGNMENT_CORRELATION,
    NEGLIGIBLE_SHIFT_PIXELS,
    align_images_to_reference,
    apply_shift,
    measure_alignment,
)


def make_star_field(seed: int, size: int = 256, stars: int = 60, noise: float = 0.02) -> np.ndarray:
    """Build a noisy field of Gaussian stars on a sky gradient.

    Returns
    -------
    field : `numpy.ndarray`
        A ``size`` x ``size`` float32 image.
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    image = 0.05 + 0.0002 * x + 0.0001 * y
    for _ in range(stars):
        cx, cy = rng.uniform(20, size - 20, 2)
        amplitude = rng.uniform(0.2, 1.0)
        image = image + amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 1.3**2))
    return (image + rng.normal(0, noise, image.shape)).astype(np.float32)


def test_a_known_shift_is_recovered_to_a_fraction_of_a_pixel() -> None:
    """A known shift of (3.25, -2.5) px is recovered, with high correlation."""
    field = make_star_field(1)
    moved, _ = apply_shift(field, 3.25, -2.5)

    result = measure_alignment(field, moved)

    # The shift that undoes the move is the opposite one.
    assert result.shift_rows_pixels == pytest.approx(-3.25, abs=0.1)
    assert result.shift_columns_pixels == pytest.approx(2.5, abs=0.1)
    assert result.correlation > 0.8
    assert result.trusted


def test_two_different_fields_are_not_trusted() -> None:
    """Unrelated fields have no true offset and fall below the floor."""
    result = measure_alignment(make_star_field(1), make_star_field(2))

    assert result.correlation < MINIMUM_ALIGNMENT_CORRELATION
    assert not result.trusted


def test_a_field_with_different_noise_still_lines_up() -> None:
    """The same stars with different noise (short and long stacks) align."""
    reference = make_star_field(3, noise=0.01)
    noisy, _ = apply_shift(make_star_field(3, noise=0.05), 1.5, 4.0)

    result = measure_alignment(reference, noisy)

    assert result.trusted
    assert result.shift_columns_pixels == pytest.approx(-4.0, abs=0.15)


def test_images_of_different_shape_are_rejected() -> None:
    """Aligning stacks that are not the same size is a caller error."""
    with pytest.raises(ValueError, match="same shape"):
        measure_alignment(np.zeros((10, 10)), np.zeros((12, 10)))


def test_a_colour_image_is_shifted_as_one() -> None:
    """Each colour plane moves by the same amount, and the mask is 2-D."""
    plane = make_star_field(4)
    colour = np.stack([plane, plane * 0.5, plane * 2.0])

    shifted, covered = apply_shift(colour, 2.0, 3.0)

    assert shifted.shape == colour.shape
    assert covered.shape == plane.shape
    np.testing.assert_allclose(shifted[1], shifted[0] * 0.5, atol=1e-4)


def test_a_colour_image_is_measured_from_its_mean_plane() -> None:
    """A colour stack lines up with a shifted copy of itself."""
    plane = make_star_field(5)
    colour = np.stack([plane, plane, plane])
    moved, _ = apply_shift(colour, -2.0, 1.0)

    result = measure_alignment(colour, moved)

    assert result.shift_rows_pixels == pytest.approx(2.0, abs=0.1)
    assert result.shift_columns_pixels == pytest.approx(-1.0, abs=0.1)


def test_pixels_a_shift_brings_in_are_reported_as_not_covered() -> None:
    """Shifting by 4 rows leaves the first 4 rows empty and not covered."""
    field = make_star_field(6)

    shifted, covered = apply_shift(field, 4.0, 0.0)

    assert not covered[:4].any()
    assert covered[6:-2].all()
    assert np.all(shifted[:3] == 0)


def test_a_negligible_shift_leaves_the_image_untouched() -> None:
    """A shift under the threshold is not applied, so nothing is smoothed."""
    field = make_star_field(7)

    shifted, covered = apply_shift(field, NEGLIGIBLE_SHIFT_PIXELS / 2, 0.0)

    np.testing.assert_array_equal(shifted, field)
    assert covered.all()


def make_center_confined_star_field(seed: int, size: int = 256, stars: int = 60) -> np.ndarray:
    """Build a star field with every star kept within the central half.

    This stands in for a spectral group stack, where the target star and its
    dispersed trail sit near the centre of the field and the rest of the
    frame is empty sky (see `SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION`).

    Returns
    -------
    field : `numpy.ndarray`
        A ``size`` x ``size`` float32 image.
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    image = np.zeros((size, size), dtype=np.float32)
    quarter = size // 4
    for _ in range(stars):
        cx, cy = rng.uniform(quarter, size - quarter, 2)
        amplitude = rng.uniform(0.2, 1.0)
        image = image + amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 1.3**2))
    return image.astype(np.float32)


def make_unrelated_clutter(seed: int, size: int = 256, count: int = 300) -> np.ndarray:
    """Build faint, independently-random points spread over the whole frame.

    Two frames built from different seeds share no true offset. This stands
    in for the empty-sky pixels of a spectral frame, whose pattern (read
    noise, dark residuals) is unrelated between two group stacks and would
    otherwise be free to dominate a whole-frame correlation, since a
    spectral target's real signal covers only a small share of the frame.

    Returns
    -------
    clutter : `numpy.ndarray`
        A ``size`` x ``size`` float32 image of faint, scattered points.
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    image = np.zeros((size, size), dtype=np.float32)
    for _ in range(count):
        cx, cy = rng.uniform(0, size, 2)
        amplitude = rng.uniform(0.1, 0.5)
        image = image + amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 1.3**2))
    return image.astype(np.float32)


def test_a_center_crop_still_recovers_a_known_shift() -> None:
    """Cropping to the centre does not change the offset that is found."""
    field = make_center_confined_star_field(10)
    moved, _ = apply_shift(field, 2.0, -1.5)

    result = measure_alignment(field, moved, crop_fraction=0.5)

    assert result.shift_rows_pixels == pytest.approx(-2.0, abs=0.1)
    assert result.shift_columns_pixels == pytest.approx(1.5, abs=0.1)
    assert result.trusted


def test_a_center_crop_ignores_clutter_that_defeats_whole_frame_alignment() -> None:
    """Clutter with no true offset can spoil whole-frame alignment.

    A centre crop that excludes most of it still finds the true shift.
    """
    center_field = make_center_confined_star_field(11)
    moved_center, _ = apply_shift(center_field, 3.0, 2.0)
    reference = center_field + make_unrelated_clutter(101)
    moving = moved_center + make_unrelated_clutter(202)

    whole_frame = measure_alignment(reference, moving)
    cropped = measure_alignment(reference, moving, crop_fraction=0.5)

    assert not whole_frame.trusted
    assert cropped.trusted
    assert cropped.shift_rows_pixels == pytest.approx(-3.0, abs=0.15)
    assert cropped.shift_columns_pixels == pytest.approx(-2.0, abs=0.15)


def test_align_images_to_reference_passes_the_crop_fraction_through() -> None:
    """`align_images_to_reference` measures each pair with the same crop."""
    center_field = make_center_confined_star_field(12)
    moved_center, _ = apply_shift(center_field, -1.0, 4.0)
    reference = center_field + make_unrelated_clutter(303)
    moving = moved_center + make_unrelated_clutter(404)

    aligned, _, results = align_images_to_reference([reference, moving], 0, crop_fraction=0.5)

    assert results[1].trusted
    assert aligned[1] is not None


def test_align_images_moves_each_group_onto_the_reference_and_flags_a_stranger() -> None:
    """The reference stays, a shifted group is lined up, a stranger is out."""
    reference = make_star_field(8)
    shifted_group, _ = apply_shift(make_star_field(8, noise=0.04), 5.0, -3.0)
    stranger = make_star_field(9)

    aligned, covered, results = align_images_to_reference([shifted_group, reference, stranger], 1)

    np.testing.assert_array_equal(aligned[1], reference)
    assert results[1] is None
    assert aligned[0] is not None
    assert results[0].shift_rows_pixels == pytest.approx(-5.0, abs=0.1)
    inner = (slice(20, -20), slice(20, -20))
    assert np.corrcoef(aligned[0][inner].ravel(), reference[inner].ravel())[0, 1] > 0.7
    assert aligned[2] is None
    assert covered[2] is None
