"""Purpose: Unit tests for lining up exposure-group stacks.

Description: Siril registers each exposure group to its own reference frame,
so the group stacks of one session were found 6-7 pixels apart in real data.
These tests check that a known shift of a synthetic star field is recovered to
a fraction of a pixel, that two unrelated fields are refused instead of being
"aligned", that colour images move as one, and that the pixels a shift fills
in are reported. Further tests check the star-based refinement: that a known
rotation and scale between two stacks are recovered, that stars land closer
together than a shift alone puts them, and that a fit that is not believable is
refused so the plain shift is kept.
"""

import numpy as np
import pytest

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.stacking.processing.group_alignment import (
    MINIMUM_ALIGNMENT_CORRELATION,
    NEGLIGIBLE_SHIFT_PIXELS,
    REFINEMENT_MAX_ROTATION_DEGREES,
    AlignmentResult,
    align_images_to_reference,
    apply_alignment,
    apply_shift,
    detect_star_centroids,
    find_zero_order_position,
    measure_alignment,
    refine_alignment_with_stars,
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
    with pytest.raises(InvalidArgumentError, match="same shape"):
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


def make_spectrum_stack(
    star_row: float,
    star_column: float,
    star_brightness: float,
    trail_brightness: float,
    size: int = 1000,
    saturation: float = 1.0,
) -> np.ndarray:
    """Build a stack of a slitless spectrum: a star with a trail below it.

    The trail is a vertical streak that starts 350 px below the star, as the
    Star Analyzer 200's does. A star brighter than ``saturation`` becomes a
    flat plateau.

    Returns
    -------
    image : `numpy.ndarray`
        A ``size`` x ``size`` float32 image.
    """
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    star = star_brightness * np.exp(-((x - star_column) ** 2 + (y - star_row) ** 2) / (2 * 3.0**2))
    along = ((y > star_row + 330) & (y < star_row + 480)).astype(np.float32)
    trail = trail_brightness * along * np.exp(-((x - star_column) ** 2) / (2 * 4.0**2))
    return np.minimum(star + trail, saturation).astype(np.float32)


def test_the_zero_order_star_is_found_to_a_fraction_of_a_pixel() -> None:
    """The star's centre is recovered from a stack that has a trail too."""
    image = make_spectrum_stack(500.4, 507.6, 0.5, 0.2)

    row, column = find_zero_order_position(image)

    assert row == pytest.approx(500.4, abs=0.3)
    assert column == pytest.approx(507.6, abs=0.3)


def test_a_saturated_star_is_found_at_the_middle_of_its_plateau() -> None:
    """A flat-topped star is found at its centre."""
    image = make_spectrum_stack(497.0, 511.0, 5.0, 0.9)

    row, column = find_zero_order_position(image)

    assert row == pytest.approx(497.0, abs=0.5)
    assert column == pytest.approx(511.0, abs=0.5)


def test_a_trail_as_bright_as_the_star_is_not_mistaken_for_it() -> None:
    """The trail lies outside the search window, so it cannot be picked."""
    image = make_spectrum_stack(500.0, 500.0, 1.0, 1.0)

    row, _ = find_zero_order_position(image)

    assert row == pytest.approx(500.0, abs=0.5)


def test_two_equally_bright_stars_are_ambiguous() -> None:
    """A second equally bright star leaves no clear zero order."""
    image = make_spectrum_stack(500.0, 450.0, 0.5, 0.0) + make_spectrum_stack(500.0, 560.0, 0.5, 0.0)

    assert find_zero_order_position(image) is None


def test_an_empty_image_has_no_zero_order_star() -> None:
    """A blank image gives no position."""
    assert find_zero_order_position(np.zeros((400, 400), np.float32)) is None


def test_stacks_that_look_very_different_are_aligned_on_the_star() -> None:
    """A faint stack is lined up with a saturated one by the star."""
    reference = make_spectrum_stack(500.0, 500.0, 5.0, 0.9)
    image = make_spectrum_stack(519.0, 488.0, 0.08, 0.0)

    result = measure_alignment(reference, image, prefer_star_position=True)

    assert result.is_star_based
    assert result.trusted
    assert result.shift_rows_pixels == pytest.approx(-19.0, abs=0.5)
    assert result.shift_columns_pixels == pytest.approx(12.0, abs=0.5)


def test_without_a_clear_star_the_images_are_correlated_instead() -> None:
    """With no clear star, the images are correlated as before."""
    reference = make_star_field(1)
    image = np.roll(reference, (3, -2), axis=(0, 1))

    result = measure_alignment(reference, image, prefer_star_position=True)

    assert not result.is_star_based
    assert result.shift_rows_pixels == pytest.approx(-3.0, abs=0.3)
    assert result.shift_columns_pixels == pytest.approx(2.0, abs=0.3)


def test_align_images_can_use_the_star_position_for_every_group() -> None:
    """Each group is moved onto the reference by its star's position."""
    reference = make_spectrum_stack(500.0, 500.0, 5.0, 0.9)
    moved = make_spectrum_stack(512.0, 520.0, 0.3, 0.0)

    aligned, _, results = align_images_to_reference([reference, moved], 0, prefer_star_position=True)

    assert aligned[1] is not None
    assert results[1].is_star_based
    row, column = find_zero_order_position(aligned[1])
    assert row == pytest.approx(500.0, abs=0.6)
    assert column == pytest.approx(500.0, abs=0.6)


REFINEMENT_SIZE = 640


def render_stars(positions: np.ndarray, amplitudes: np.ndarray, seed: int, noise: float = 0.01) -> np.ndarray:
    """Draw Gaussian stars at the given (row, column) positions, with noise.

    Returns
    -------
    image : `numpy.ndarray`
        A ``REFINEMENT_SIZE`` square float32 image.
    """
    image = np.full((REFINEMENT_SIZE, REFINEMENT_SIZE), 0.05, dtype=np.float64)
    offsets = np.arange(-6, 7)
    for (row, column), amplitude in zip(positions, amplitudes, strict=True):
        row_index = round(row) + offsets
        column_index = round(column) + offsets
        if (
            row_index.min() < 0
            or column_index.min() < 0
            or row_index.max() >= REFINEMENT_SIZE
            or column_index.max() >= REFINEMENT_SIZE
        ):
            continue
        patch = np.exp(
            -((row_index[:, None] - row) ** 2 + (column_index[None, :] - column) ** 2) / (2 * 1.3**2)
        )
        image[np.ix_(row_index, column_index)] += amplitude * patch
    image += np.random.default_rng(seed).normal(0, noise, image.shape)
    return image.astype(np.float32)


def make_moved_pair(
    rotation_degrees: float, scale: float, shift_rows: float, shift_columns: float, seed: int = 11
) -> tuple[np.ndarray, np.ndarray, complex, complex]:
    """Draw one star field twice, the second moved by a known similarity.

    The reference shows the stars at ``a * z + b`` where ``z`` is a star's
    position in the moving image (``z = column + 1j * row``).

    Returns
    -------
    reference, moving : `numpy.ndarray`
        The two images.
    a, b : `complex`
        The transform from moving to reference.
    """
    rng = np.random.default_rng(seed)
    stars = 220
    moving_positions = rng.uniform(40, REFINEMENT_SIZE - 40, (stars, 2))
    amplitudes = rng.uniform(0.3, 1.0, stars)
    a = scale * np.exp(1j * np.radians(rotation_degrees))
    b = shift_columns + 1j * shift_rows
    moving_complex = moving_positions[:, 1] + 1j * moving_positions[:, 0]
    reference_complex = a * moving_complex + b
    reference_positions = np.column_stack([reference_complex.imag, reference_complex.real])
    reference = render_stars(reference_positions, amplitudes, seed=1)
    moving = render_stars(moving_positions, amplitudes, seed=2)
    return reference, moving, complex(a), complex(b)


def star_offsets_after(reference: np.ndarray, moved: np.ndarray) -> np.ndarray:
    """Measure how far each star of `moved` is from its partner in `reference`.

    Returns
    -------
    distances : `numpy.ndarray`
        One distance in pixels per matched star pair.
    """
    reference_stars = detect_star_centroids(reference)
    moved_stars = detect_star_centroids(moved)
    distances = []
    for star in moved_stars:
        nearest = np.hypot(*(reference_stars - star).T).min()
        if nearest < 3.0:
            distances.append(nearest)
    return np.array(distances)


def test_a_known_rotation_and_scale_are_recovered_from_the_stars() -> None:
    """A -0.04 degree turn and a 0.05% scale, as between nights, are found."""
    reference, moving, a, b = make_moved_pair(-0.04, 1.0005, 12.4, -7.3)
    initial = measure_alignment(reference, moving)

    refined = refine_alignment_with_stars(reference, moving, initial)

    centre = (REFINEMENT_SIZE - 1) / 2.0 * (1 + 1j)
    expected_centre_shift = a * centre + b - centre
    assert refined.star_pairs >= 100
    assert refined.rotation_degrees == pytest.approx(-0.04, abs=0.01)
    assert refined.scale == pytest.approx(1.0005, abs=0.0002)
    assert refined.shift_rows_pixels == pytest.approx(expected_centre_shift.imag, abs=0.1)
    assert refined.shift_columns_pixels == pytest.approx(expected_centre_shift.real, abs=0.1)
    assert refined.residual_pixels is not None
    assert refined.residual_pixels < 0.15
    assert refined.has_rotation_or_scale


def test_stars_land_closer_together_than_a_shift_alone_puts_them() -> None:
    """After the move, stars sit well within a pixel of their partners."""
    reference, moving, _, _ = make_moved_pair(0.1, 0.9990, -6.0, 9.5)
    initial = measure_alignment(reference, moving)
    refined = refine_alignment_with_stars(reference, moving, initial)

    shift_only, _ = apply_shift(moving, initial.shift_rows_pixels, initial.shift_columns_pixels)
    refined_image, covered = apply_alignment(moving, refined)

    shift_only_offsets = star_offsets_after(reference, shift_only)
    refined_offsets = star_offsets_after(reference, refined_image)
    assert np.sqrt(np.mean(refined_offsets**2)) < 0.2
    assert np.sqrt(np.mean(refined_offsets**2)) < 0.5 * np.sqrt(np.mean(shift_only_offsets**2))
    assert covered.mean() > 0.9


def test_a_pure_shift_is_not_given_a_rotation() -> None:
    """With no rotation or scale, the refinement keeps the plain shift."""
    reference, moving, _, _ = make_moved_pair(0.0, 1.0, 5.0, -4.0)
    initial = measure_alignment(reference, moving)

    refined = refine_alignment_with_stars(reference, moving, initial)

    assert abs(refined.rotation_degrees) < 0.01
    assert refined.scale == pytest.approx(1.0, abs=0.0003)


def test_too_few_stars_keep_the_initial_result() -> None:
    """A field with a handful of stars cannot support a fit."""
    rng = np.random.default_rng(5)
    positions = rng.uniform(100, 500, (8, 2))
    amplitudes = np.full(8, 0.8)
    reference = render_stars(positions + np.array([3.0, 2.0]), amplitudes, seed=1)
    moving = render_stars(positions, amplitudes, seed=2)
    initial = AlignmentResult(3.0, 2.0, 0.9)

    assert refine_alignment_with_stars(reference, moving, initial) is initial


def test_a_rotation_beyond_the_limit_is_refused() -> None:
    """A 1 degree turn is more than two stacks of one field differ by."""
    assert 1.0 > REFINEMENT_MAX_ROTATION_DEGREES
    reference, moving, _, _ = make_moved_pair(1.0, 1.0, 3.0, 2.0)
    initial = measure_alignment(reference, moving)

    refined = refine_alignment_with_stars(reference, moving, initial)

    assert refined is initial
    assert not refined.has_rotation_or_scale


def test_aligning_with_stars_gives_a_closer_match_than_without() -> None:
    """Aligning with stars records the rotation and lines the stars up."""
    reference, moving, _, _ = make_moved_pair(0.08, 1.0006, 7.0, -3.0)

    plain_images, _, plain_results = align_images_to_reference([reference, moving], 0)
    refined_images, covered, refined_results = align_images_to_reference(
        [reference, moving], 0, refine_with_stars=True
    )

    assert plain_results[1] is not None
    assert not plain_results[1].has_rotation_or_scale
    assert refined_results[1] is not None
    assert refined_results[1].rotation_degrees == pytest.approx(0.08, abs=0.02)
    assert refined_results[1].star_pairs > 50
    plain_offsets = star_offsets_after(reference, plain_images[1])
    refined_offsets = star_offsets_after(reference, refined_images[1])
    assert np.sqrt(np.mean(refined_offsets**2)) < np.sqrt(np.mean(plain_offsets**2))
    assert covered[1] is not None


def test_a_colour_image_is_turned_and_scaled_as_one() -> None:
    """Every channel moves by the same transform."""
    reference, moving, _, _ = make_moved_pair(0.1, 1.0004, 4.0, 3.0)
    initial = measure_alignment(reference, moving)
    refined = refine_alignment_with_stars(reference, moving, initial)
    colour = np.stack([moving, moving * 0.5, moving * 2.0])

    moved, covered = apply_alignment(colour, refined)

    assert moved.shape == colour.shape
    assert covered.shape == moving.shape
    assert np.allclose(moved[1] * 2.0, moved[0], atol=1e-3)
    assert np.allclose(moved[2] * 0.5, moved[0], atol=1e-3)


def test_apply_alignment_without_rotation_is_the_plain_shift() -> None:
    """With no rotation or scale, `apply_alignment` matches `apply_shift`."""
    field = make_star_field(4)
    plain, plain_covered = apply_shift(field, 2.5, -1.5)

    result, covered = apply_alignment(field, AlignmentResult(2.5, -1.5, 0.9))

    assert np.array_equal(result, plain)
    assert np.array_equal(covered, plain_covered)
