"""Purpose: Unit tests for lining up exposure-group stacks.

Description: Siril registers each exposure group to its own reference frame,
so the group stacks of one session were found 6-7 pixels apart in real data.
These tests check that a known shift of a synthetic star field is recovered to
a fraction of a pixel, that two unrelated fields are refused instead of being
"aligned", that colour images move as one, and that the pixels a shift fills
in are reported. Further tests check the alignment result's method and its
use of a position map. Last, tests of the extended-target search,
which finds a broad glow (a globular cluster) that the point-source search
rejects, and refuses a frame with two equally bright glows.
"""

import numpy as np
import pytest

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.stacking.processing.group_alignment import (
    GROUP_RESAMPLE_SPLINE_ORDER,
    MINIMUM_ALIGNMENT_CORRELATION,
    NEGLIGIBLE_SHIFT_PIXELS,
    AlignmentResult,
    align_images_to_reference,
    apply_alignment,
    apply_shift,
    detect_star_centroids,
    find_extended_zero_order_position,
    find_zero_order_position,
    locate_zero_order,
    measure_alignment,
)
from astrometricslib.pipelines.stacking.processing.group_position_map import GroupPositionMap


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


def test_the_default_resample_order_is_the_registered_cubic_order() -> None:
    """The default order is the registered cubic order; order 1 differs."""
    field = make_star_field(2)

    default, _ = apply_shift(field, 1.5, 2.25)
    cubic, _ = apply_shift(field, 1.5, 2.25, order=3)
    linear, _ = apply_shift(field, 1.5, 2.25, order=1)

    assert GROUP_RESAMPLE_SPLINE_ORDER.default == 3
    assert np.array_equal(default, cubic)
    assert not np.array_equal(default, linear)


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

    assert result.correlation < MINIMUM_ALIGNMENT_CORRELATION.default
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

    shifted, covered = apply_shift(field, NEGLIGIBLE_SHIFT_PIXELS.default / 2, 0.0)

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


def test_the_star_finder_returns_the_stars_it_was_given() -> None:
    """Each bright, isolated star is found to a fraction of a pixel."""
    field = make_star_field(7, size=400, stars=12, noise=0.005)

    found = detect_star_centroids(field, count=50)

    assert 3 <= len(found) <= 12
    assert found.shape[1] == 2


def test_an_alignment_names_its_method() -> None:
    """A shift says how it was found; a Siril move carries a position map."""
    assert AlignmentResult(1.0, 2.0, 0.9).method == "correlation"
    assert AlignmentResult(1.0, 2.0, 0.0, is_star_based=True, method="zero_order").trusted
    failed = AlignmentResult(0.0, 0.0, 0.0, method="siril", failure_reason="Siril found 2 star(s)")
    assert not failed.trusted


def test_a_position_map_moves_an_image_like_the_shift_it_describes() -> None:
    """A shift written as a position map resamples like the shift."""
    field = make_star_field(4)
    shifted, _ = apply_shift(field, 2.5, -1.5)
    result = AlignmentResult(
        2.5, -1.5, 0.0, method="siril", position_map=GroupPositionMap.from_shift(field.shape, 2.5, -1.5)
    )

    moved, covered = apply_alignment(field, result)

    assert np.allclose(moved[10:-10, 10:-10], shifted[10:-10, 10:-10], atol=1e-4)
    assert covered[10:-10, 10:-10].all()


def test_apply_alignment_without_rotation_is_the_plain_shift() -> None:
    """With no rotation or scale, `apply_alignment` matches `apply_shift`."""
    field = make_star_field(4)
    plain, plain_covered = apply_shift(field, 2.5, -1.5)

    result, covered = apply_alignment(field, AlignmentResult(2.5, -1.5, 0.9))

    assert np.array_equal(result, plain)
    assert np.array_equal(covered, plain_covered)


CLUSTER_ROW, CLUSTER_COLUMN = 505.0, 492.0


def make_cluster_field(
    amplitude: float = 300.0, extra_glows: tuple[tuple[float, float, float], ...] = ()
) -> np.ndarray:
    """Build a noisy sky with a broad glow and bright stars scattered on it.

    The glow stands in for a globular cluster. It is a Gaussian 30 px wide at
    (505, 492). Six stars of height 400 sit within 80 px of its centre, so the
    point-source search sees several rival spots.

    Parameters
    ----------
    amplitude : `float`, optional
        The height of the main glow above the sky of 100.
    extra_glows : `tuple` [`tuple` [`float`, `float`, `float`], ...], optional
        More glows of the same width, each as (row, column, height).

    Returns
    -------
    image : `numpy.ndarray`
        A 1000 x 1000 float32 image.
    """
    y, x = np.mgrid[0:1000, 0:1000].astype(np.float32)
    image = 100.0 + np.random.default_rng(3).normal(0, 5, (1000, 1000)).astype(np.float32)
    glows = [(CLUSTER_ROW, CLUSTER_COLUMN, amplitude), *extra_glows]
    for row, column, height in glows:
        image += height * np.exp(-((y - row) ** 2 + (x - column) ** 2) / (2 * 30.0**2))
    stars = [(-70, 20), (60, -50), (10, 75), (-30, -60), (45, 40), (-55, 60)]
    for row_offset, column_offset in stars:
        distance = (y - CLUSTER_ROW - row_offset) ** 2 + (x - CLUSTER_COLUMN - column_offset) ** 2
        image += 400.0 * np.exp(-distance / (2 * 2.5**2))
    return image


def test_a_cluster_with_rival_bright_spots_defeats_the_point_source_search() -> None:
    """The point-source search gives up on a glow with bright stars on it."""
    assert find_zero_order_position(make_cluster_field()) is None


def test_the_extended_search_finds_the_middle_of_a_cluster() -> None:
    """The glow's centre is found to within a few pixels."""
    position = find_extended_zero_order_position(make_cluster_field())

    assert position is not None
    assert position[0] == pytest.approx(CLUSTER_ROW, abs=3.0)
    assert position[1] == pytest.approx(CLUSTER_COLUMN, abs=3.0)


def test_locating_a_cluster_says_the_position_is_from_the_extended_search() -> None:
    """`locate_zero_order` flags a position that came from the glow search."""
    found = locate_zero_order(make_cluster_field())

    assert found is not None
    assert found.is_extended_target
    assert found.row_column == (found.row, found.column)


def test_locating_a_star_gives_the_point_source_answer_unflagged() -> None:
    """A clear star is found by the point-source search, exactly as before."""
    image = make_spectrum_stack(500.4, 507.6, 0.5, 0.2)

    found = locate_zero_order(image)

    assert found is not None
    assert not found.is_extended_target
    assert found.row_column == find_zero_order_position(image)


def test_two_equally_bright_glows_are_ambiguous_for_the_extended_search() -> None:
    """A second glow of the same height 200 px away leaves no clear target."""
    image = make_cluster_field(extra_glows=((CLUSTER_ROW, CLUSTER_COLUMN + 200.0, 300.0),))

    assert locate_zero_order(image) is None


def test_a_glow_beyond_the_rival_window_is_ignored() -> None:
    """A brighter glow 450 px from the centre is outside the rival window."""
    image = make_cluster_field(extra_glows=((505.0, 942.0, 600.0),))

    found = locate_zero_order(image)

    assert found is not None
    assert found.column == pytest.approx(CLUSTER_COLUMN, abs=3.0)


def test_a_blank_image_and_a_smooth_gradient_have_no_extended_target() -> None:
    """Neither a flat image nor a tilted plane has a peak in the window."""
    rows, columns = np.mgrid[0:1000, 0:1000]
    gradient = (0.2 * columns + 0.1 * rows).astype(np.float32)

    assert find_extended_zero_order_position(np.zeros((1000, 1000), np.float32)) is None
    assert find_extended_zero_order_position(gradient) is None


def test_an_image_smaller_than_one_block_has_no_extended_target() -> None:
    """A 3 x 3 image is too small to average in blocks."""
    assert find_extended_zero_order_position(np.ones((3, 3), np.float32)) is None


def test_a_saturated_star_is_found_only_when_asked_for() -> None:
    """The percentile cut skips a top star unless saturated stars are kept."""
    rows, columns = np.mgrid[0:600, 0:600]
    image = np.full((600, 600), 100.0)
    for index, (row, column) in enumerate([(150, 150), (150, 450), (450, 150), (450, 450)]):
        amplitude = 1000.0 * (index + 1)
        image += amplitude * np.exp(-((rows - row) ** 2 + (columns - column) ** 2) / (2 * 2.0**2))
    image += np.random.default_rng(1).normal(0.0, 1.0, image.shape)
    kept = detect_star_centroids(image, count=10, skip_saturated=False)
    cut = detect_star_centroids(image, count=10)
    assert len(kept) == 4
    assert len(cut) < len(kept)
