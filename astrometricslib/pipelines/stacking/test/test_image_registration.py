"""Purpose: Unit tests for the shared image-registration engine.

Description: `image_registration` fits a shift, a rotation, a scale and an
optional quadratic lens-distortion correction to matched star positions, and
moves images with it. These tests check the fit on synthetic star lists (exact
recovery of a similarity, including a half turn; recovery of a quadratic
distortion; refusal with too few or badly spread pairs), the image warp (stars
land where the transform says, channels and footprint are handled, the output
may differ in size), and the way the exposure-group alignment uses it: a
half-turned group seen through distorted optics, which a similarity leaves
about 2 px off and the old code refused, is now accepted; a group that a
similarity already fits is unchanged; the crop's frame count and the
rejection-map merge use the same move as the image.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from scipy.ndimage import gaussian_filter

from astrometricslib.foundation.parameters import resolve
from astrometricslib.pipelines.stacking.post_processing import stack_crop
from astrometricslib.pipelines.stacking.processing import group_alignment
from astrometricslib.pipelines.stacking.processing.group_alignment import (
    PATTERN_MAX_RESIDUAL_PIXELS,
    AlignmentResult,
    align_images_to_reference,
    apply_alignment,
    detect_star_centroids,
    search_star_patterns,
)
from astrometricslib.pipelines.stacking.processing.group_file_merging import merge_rejection_maps
from astrometricslib.pipelines.stacking.processing.image_registration import (
    PolynomialDistortion,
    RegistrationTransform,
    fit_transform,
    warp,
)
from astrometricslib.pipelines.stacking.stack_runner import alignment_left_out_reason
from astrometricslib.pipelines.stacking.test.test_group_alignment_star_patterns import (
    FIELD_SIZE,
    make_pair,
    make_positions,
    render,
)

LIMITS = {"minimum_pairs": 30, "clip_sigmas": 3.0, "clip_passes": 3}
CENTER = (FIELD_SIZE - 1) / 2.0
DISTORTION_STRENGTH = 7.0


def make_distortion() -> PolynomialDistortion:
    """Build a smooth quadratic distortion of about 2 px rms.

    Returns
    -------
    distortion : `PolynomialDistortion`
        A correction over the whole test field.
    """
    c = DISTORTION_STRENGTH
    return PolynomialDistortion(
        origin_row_pixels=CENTER,
        origin_column_pixels=CENTER,
        half_size_pixels=FIELD_SIZE / 2.0,
        box=(0.0, FIELD_SIZE - 1.0, 0.0, FIELD_SIZE - 1.0),
        row_coefficients=(0.0, 0.0, 0.0, c, 0.0, -0.5 * c),
        column_coefficients=(0.0, 0.0, 0.0, -0.3 * c, 0.8 * c, 0.6 * c),
    )


def make_transform(rotation_degrees: float, offset: complex, distorted: bool) -> RegistrationTransform:
    """Build the move of a reference-from-image test pair.

    Returns
    -------
    transform : `RegistrationTransform`
        The move about the center of the field.
    """
    a = np.exp(1j * np.radians(rotation_degrees))
    center = complex(CENTER, CENTER)
    shift = a * center + offset - center
    return RegistrationTransform(
        shift_rows_pixels=float(shift.imag),
        shift_columns_pixels=float(shift.real),
        rotation_degrees=rotation_degrees,
        scale=1.0,
        origin_row_pixels=CENTER,
        origin_column_pixels=CENTER,
        distortion=make_distortion() if distorted else None,
    )


def make_distorted_pair(
    rotation_degrees: float = 180.0, offset: complex = 1199 + 1199j, seed: int = 1
) -> tuple[np.ndarray, np.ndarray, RegistrationTransform]:
    """Render a reference field and the same stars seen through distortion.

    Returns
    -------
    reference, image, transform : `tuple`
        The two rendered fields and the move that takes the image onto the
        reference.
    """
    positions = make_positions(seed, 150)
    amplitudes = np.random.default_rng(seed + 1).uniform(0.3, 1.0, 150)
    transform = make_transform(rotation_degrees, offset, distorted=True)
    rows, columns = transform.moving_positions(positions.imag, positions.real)
    return render(positions, amplitudes, seed=10), render(columns + 1j * rows, amplitudes, seed=11), transform


def random_pairs(
    transform: RegistrationTransform, count: int = 200, seed: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Draw moving-star positions and the reference positions the move gives.

    Returns
    -------
    reference_points, moving_points : `numpy.ndarray`
        Matched (row, column) positions.
    """
    rng = np.random.default_rng(seed)
    moving = rng.uniform(50, FIELD_SIZE - 50, (count, 2))
    rows, columns = transform.map_points(moving[:, 0], moving[:, 1])
    return np.column_stack([rows, columns]), moving


@pytest.mark.parametrize("rotation_degrees", [0.0, 1.3, -6.0, 90.0, 180.0, -179.6])
def test_a_similarity_is_recovered_exactly_at_any_rotation(rotation_degrees: float) -> None:
    """Noise-free pairs give back the move used to make them."""
    truth = make_transform(rotation_degrees, 300 - 200j, distorted=False)
    reference_points, moving_points = random_pairs(truth)

    fit = fit_transform(reference_points, moving_points, "similarity", (FIELD_SIZE, FIELD_SIZE), **LIMITS)

    assert fit is not None
    assert fit.model == "similarity"
    assert fit.residual_pixels < 1e-6
    assert fit.transform.shift_rows_pixels == pytest.approx(truth.shift_rows_pixels, abs=1e-6)
    assert fit.transform.shift_columns_pixels == pytest.approx(truth.shift_columns_pixels, abs=1e-6)
    assert np.cos(np.radians(fit.transform.rotation_degrees - rotation_degrees)) == pytest.approx(1.0)
    assert fit.transform.scale == pytest.approx(1.0)


def test_a_quadratic_distortion_is_recovered_by_the_polynomial_model() -> None:
    """The polynomial removes what a similarity cannot."""
    truth = make_transform(180.0, 1199 + 1199j, distorted=True)
    reference_points, moving_points = random_pairs(truth)

    fit = fit_transform(reference_points, moving_points, "polynomial", (FIELD_SIZE, FIELD_SIZE), **LIMITS)

    assert fit is not None
    assert fit.model == "polynomial"
    assert fit.similarity_residual_pixels > 1.5
    assert fit.residual_pixels < 1e-6
    held_out = np.random.default_rng(9).uniform(60, FIELD_SIZE - 60, (50, 2))
    expected = truth.map_points(held_out[:, 0], held_out[:, 1])
    found = fit.transform.map_points(held_out[:, 0], held_out[:, 1])
    assert np.abs(np.array(found) - np.array(expected)).max() < 1e-4


def test_the_polynomial_model_on_undistorted_stars_changes_nothing_that_matters() -> None:
    """Without distortion the fitted correction is zero."""
    truth = make_transform(1.0, 20 + 30j, distorted=False)
    reference_points, moving_points = random_pairs(truth)

    fit = fit_transform(reference_points, moving_points, "polynomial", (FIELD_SIZE, FIELD_SIZE), **LIMITS)

    assert fit is not None
    assert fit.residual_pixels < 1e-6
    assert np.abs(fit.transform.distortion.row_coefficients).max() < 1e-6


def test_too_few_pairs_are_refused() -> None:
    """Fewer than the minimum number of pairs gives no fit at all."""
    truth = make_transform(0.0, 5 + 5j, distorted=False)
    reference_points, moving_points = random_pairs(truth, count=10)

    assert (
        fit_transform(reference_points, moving_points, "similarity", (FIELD_SIZE, FIELD_SIZE), **LIMITS)
        is None
    )


def test_pairs_missing_from_a_quarter_of_the_image_get_no_polynomial() -> None:
    """Stars in only three quarters give the similarity and say why."""
    truth = make_transform(0.0, 5 + 5j, distorted=True)
    reference_points, moving_points = random_pairs(truth, count=400)
    keep = ~((moving_points[:, 0] > CENTER) & (moving_points[:, 1] > CENTER))

    fit = fit_transform(
        reference_points[keep], moving_points[keep], "polynomial", (FIELD_SIZE, FIELD_SIZE), **LIMITS
    )

    assert fit is not None
    assert fit.model == "similarity"
    assert fit.transform.distortion is None
    assert "quarter" in fit.polynomial_note


def test_an_unknown_model_name_is_an_error() -> None:
    """Only `similarity` and `polynomial` are models."""
    points = np.zeros((40, 2))
    with pytest.raises(ValueError, match="Unknown registration model"):
        fit_transform(points, points, "affine", (FIELD_SIZE, FIELD_SIZE), **LIMITS)


def test_the_correction_stays_at_its_edge_value_outside_the_stars() -> None:
    """Beyond the stars the correction is held, not extrapolated."""
    distortion = PolynomialDistortion(
        100.0, 100.0, 100.0, (50.0, 150.0, 50.0, 150.0), (0, 0, 0, 5, 0, 0), (0, 0, 0, 0, 0, 0)
    )

    edge = distortion.correction(np.array([100.0]), np.array([150.0]))
    far = distortion.correction(np.array([100.0]), np.array([5000.0]))

    assert far[0] == pytest.approx(edge[0])
    assert far[0][0] == pytest.approx(5 * 0.5**2)


def test_the_stored_record_round_trips_and_an_old_record_means_no_correction() -> None:
    """`to_record` and `from_record` agree; no record is no correction."""
    distortion = make_distortion()

    assert PolynomialDistortion.from_record(distortion.to_record()) == distortion
    assert PolynomialDistortion.from_record(None) is None


def test_tracing_a_position_back_undoes_the_move_with_distortion() -> None:
    """`moving_positions` is the inverse of `map_points`."""
    transform = make_transform(180.0, 1199 + 1199j, distorted=True)
    rows = np.array([10.0, 600.0, 1100.0])
    columns = np.array([1000.0, 20.0, 700.0])

    back = transform.moving_positions(*transform.map_points(rows, columns))

    assert np.abs(back[0] - rows).max() < 1e-3
    assert np.abs(back[1] - columns).max() < 1e-3


def star_image(row: float, column: float, shape: tuple[int, int] = (FIELD_SIZE, FIELD_SIZE)) -> np.ndarray:
    """Draw one Gaussian star.

    Returns
    -------
    image : `numpy.ndarray`
        A float32 image with a star of width 2 px at the position.
    """
    rows, columns = np.mgrid[0 : shape[0], 0 : shape[1]]
    return np.exp(-((rows - row) ** 2 + (columns - column) ** 2) / (2 * 2.0**2)).astype(np.float32)


def centroid(image: np.ndarray) -> tuple[float, float]:
    """Find the brightness-weighted center of an image.

    Returns
    -------
    row, column : `tuple` [`float`, `float`]
        The center in pixels.
    """
    rows, columns = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
    total = float(image.sum())
    return float((image * rows).sum() / total), float((image * columns).sum() / total)


@pytest.mark.parametrize("distorted", [False, True])
def test_a_warped_star_lands_where_the_transform_says(distorted: bool) -> None:
    """Moving an image puts a star at the position `map_points` gives."""
    transform = make_transform(180.0, 1199 + 1199j, distorted=distorted)
    row, column = 300.0, 800.0

    moved, covered = warp(star_image(row, column), transform, 3)

    expected = transform.map_points(np.array([row]), np.array([column]))
    found = centroid(np.clip(moved, 0, None))
    assert found[0] == pytest.approx(float(expected[0][0]), abs=0.05)
    assert found[1] == pytest.approx(float(expected[1][0]), abs=0.05)
    assert covered.shape == (FIELD_SIZE, FIELD_SIZE)


def test_every_channel_of_a_color_image_is_moved_the_same_way() -> None:
    """A three-channel image gives three identical warped planes."""
    transform = make_transform(1.0, 7 - 4j, distorted=True)
    plane = star_image(500.0, 500.0)
    color = np.stack([plane, 2 * plane, 3 * plane])

    moved, _ = warp(color, transform, 3)
    single, _ = warp(plane, transform, 3)

    assert moved.shape == color.shape
    assert np.allclose(moved[1], 2 * single, atol=1e-5)
    assert np.allclose(moved[2], 3 * single, atol=1e-5)


@pytest.mark.parametrize("distorted", [False, True])
def test_pixels_with_no_source_are_marked_and_zero(distorted: bool) -> None:
    """A shift of 100 rows leaves the first 100 rows with no data."""
    transform = RegistrationTransform(
        100.0,
        0.0,
        0.0,
        1.0,
        CENTER,
        CENTER,
        PolynomialDistortion(CENTER, CENTER, 600.0, (0, 1199, 0, 1199), (0,) * 6, (0,) * 6)
        if distorted
        else None,
    )

    moved, covered = warp(np.ones((FIELD_SIZE, FIELD_SIZE), dtype=np.float32), transform, 1)

    assert not covered[:99].any()
    assert covered[101:, 1:-1].all()
    assert np.abs(moved[:99]).max() == pytest.approx(0.0, abs=1e-9)
    assert moved[200:300, 100:200].min() == pytest.approx(1.0, abs=1e-4)


def test_the_output_may_be_a_different_size_from_the_input() -> None:
    """A reference grid of another size gets an image of that size."""
    transform = RegistrationTransform(0.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    moved, covered = warp(np.ones((100, 100), dtype=np.float32), transform, 1, output_shape=(150, 120))

    assert moved.shape == (150, 120)
    assert covered[:98, :98].all()
    assert not covered[110:].any()


def test_warping_forward_then_back_gives_the_image_again() -> None:
    """A distorted move can be read back at interior positions."""
    transform = make_transform(2.0, 15 + 20j, distorted=True)
    image = gaussian_filter(np.random.default_rng(5).normal(size=(FIELD_SIZE, FIELD_SIZE)), 4).astype(
        np.float32
    )

    moved, covered = warp(image, transform, 3)
    # The inverse is a transform whose forward map is `moving_positions`; use
    # the sample positions directly and compare at a few interior pixels.
    rows = np.array([300.0, 600.0, 900.0])
    columns = np.array([400.0, 700.0, 250.0])
    reference_rows, reference_columns = transform.map_points(rows, columns)
    from scipy.ndimage import map_coordinates

    sampled = map_coordinates(moved, [reference_rows, reference_columns], order=3)
    original = map_coordinates(image, [rows, columns], order=3)
    assert covered[600, 600]
    assert np.allclose(sampled, original, atol=0.02)


def test_a_distorted_half_turned_group_is_lined_up_by_the_polynomial() -> None:
    """A move the similarity leaves 2 px off, once refused, is accepted."""
    reference, image, _truth = make_distorted_pair()
    limit = resolve(PATTERN_MAX_RESIDUAL_PIXELS).value

    result, failure = search_star_patterns(reference, image)

    assert failure is None
    assert result is not None
    assert result.model == "polynomial"
    assert result.similarity_residual_pixels > limit
    assert result.residual_pixels < 0.2
    assert result.trusted
    assert abs(abs(result.rotation_degrees) - 180.0) < 0.1
    # The moved image's stars sit on the reference's stars.
    aligned, covered, results = align_images_to_reference([reference, image], 0, refine_with_stars=True)
    assert aligned[1] is not None
    assert results[1].distortion is not None
    reference_stars = detect_star_centroids(reference)
    aligned_stars = detect_star_centroids(aligned[1])
    paired_reference, paired_aligned = group_alignment._pair_nearest(
        reference_stars, aligned_stars, aligned_stars
    )
    assert len(paired_reference) > 40
    assert np.sqrt(np.mean(((paired_reference - paired_aligned) ** 2).sum(axis=1))) < 0.3
    assert covered[1].mean() > 0.95


def test_a_distorted_group_is_refused_when_its_stars_fill_only_half_the_frame() -> None:
    """With no stars in a half of the frame the search gives a reason."""
    reference, image, _ = make_distorted_pair()
    image = image.copy()
    image[: FIELD_SIZE // 2] = 0.1  # blank the top half: no stars to match there

    result, failure = search_star_patterns(reference, image)

    assert result is None
    assert failure is not None
    assert failure.reason  # names a cause with numbers


def test_a_group_the_similarity_fits_is_unchanged_and_has_no_correction() -> None:
    """An undistorted half-turned pair keeps the similarity result."""
    a, b = np.exp(1j * np.radians(180.0)), 1199 + 1199j
    reference, image = make_pair(a, b)

    result, failure = search_star_patterns(reference, image)

    assert failure is None
    assert result.distortion is None
    assert result.model == "similarity"
    assert result.residual_pixels == result.similarity_residual_pixels


def test_a_group_that_cannot_be_placed_reports_why() -> None:
    """The left-out reason names the failed search, not the correlation."""
    reference, _ = make_pair(1.0, 0j, seed=31)
    _, unrelated = make_pair(1.0, 0j, seed=32)

    aligned, _, results = align_images_to_reference([reference, unrelated], 0, refine_with_stars=True)

    assert aligned[1] is None
    assert results[1].failure_reason
    reason = alignment_left_out_reason(50.0, results[1])
    assert "could not be lined up with the 50 s group" in reason
    assert "correlation" not in reason
    assert results[1].failure_reason in reason


def test_a_group_with_no_search_reason_still_cites_its_correlation() -> None:
    """With no star-pattern search the correlation is the reason."""
    alignment = AlignmentResult(0.0, 0.0, 0.07)

    assert alignment_left_out_reason(60.0, alignment) == (
        "could not be lined up with the 60 s group (correlation 0.07)"
    )


def test_a_failed_search_is_logged_with_its_reason(caplog: pytest.LogCaptureFixture) -> None:
    """Nothing is silent when the star-pattern search finds no move."""
    reference, _ = make_pair(1.0, 0j, seed=31)
    _, unrelated = make_pair(1.0, 0j, seed=32)

    with caplog.at_level("INFO"):
        result, failure = search_star_patterns(reference, unrelated)

    assert result is None
    assert any("Star-pattern alignment failed" in record.getMessage() for record in caplog.records)
    assert failure.reason in caplog.text


def test_applying_an_alignment_with_distortion_matches_the_engine() -> None:
    """`apply_alignment` with a correction is `warp` of its transform."""
    transform = make_transform(180.0, 1199 + 1199j, distorted=True)
    alignment = AlignmentResult(
        transform.shift_rows_pixels,
        transform.shift_columns_pixels,
        0.0,
        rotation_degrees=180.0,
        distortion=transform.distortion,
    )
    image = star_image(400.0, 300.0)

    moved, covered = apply_alignment(image, alignment)

    expected, expected_covered = warp(image, transform, 3)
    assert np.array_equal(moved, expected)
    assert np.array_equal(covered, expected_covered)


def test_the_crop_counts_the_same_pixels_the_distorted_move_fills() -> None:
    """The coverage count follows the full move, correction included."""
    size = 300
    transform = RegistrationTransform(
        60.0,
        -35.0,
        20.0,
        1.0,
        (size - 1) / 2.0,
        (size - 1) / 2.0,
        PolynomialDistortion(
            (size - 1) / 2.0,
            (size - 1) / 2.0,
            size / 2.0,
            (0, size - 1, 0, size - 1),
            (4.0, 0, 0, 8.0, 0, -4.0),
            (-3.0, 0, 0, 0, 6.0, 5.0),
        ),
    )
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    homographies = [identity, identity]
    move = [60.0, -35.0, 20.0, 1.0]
    counts = stack_crop.frame_coverage_counts(
        homographies,
        [True, True],
        size,
        size,
        step=4,
        alignments=[None, move],
        distortions=[None, transform.distortion.to_record()],
    )
    alignment = AlignmentResult(
        60.0, -35.0, 1.0, rotation_degrees=20.0, scale=1.0, distortion=transform.distortion
    )
    _, filled = apply_alignment(np.ones((size, size), dtype=np.float32), alignment, order=1)

    counted = counts == 2
    sampled = filled[::4, ::4]
    assert np.mean(counted != sampled) < 0.02
    assert not np.any(counted & ~sampled)
    # Ignoring the correction would count a visibly different area.
    without = stack_crop.frame_coverage_counts(
        homographies, [True, True], size, size, step=4, alignments=[None, move]
    )
    assert np.any((without == 2) != counted)


def test_the_group_record_for_the_crop_reads_the_distortion() -> None:
    """The crop reads each group's stored correction; old records work."""
    size = 200
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    distortion = PolynomialDistortion(
        99.5, 99.5, 100.0, (0, 199, 0, 199), (6.0, 0, 0, 0, 0, 0), (0,) * 6
    ).to_record()
    diagnostics = {
        "registration_frames": [{"homography": identity}] * 4,
        "registration_included": [True] * 4,
        "exposure_group_alignments": [
            {"frame_count": 2, "alignment": None},
            {"frame_count": 2, "alignment": [0.0, 0.0, 0.0, 1.0], "distortion": distortion},
        ],
    }
    old_record = {
        **diagnostics,
        "exposure_group_alignments": [
            {"frame_count": 2, "alignment": None},
            {"frame_count": 2, "alignment": [0.0, 0.0, 0.0, 1.0]},
        ],
    }

    with_distortion = stack_crop._coverage_from_diagnostics(diagnostics, size, size)
    without = stack_crop._coverage_from_diagnostics(old_record, size, size)

    assert with_distortion is not None
    assert without is not None
    # Without the correction only the border margin is uncovered; the 6 px
    # correction pushes whole rows off the group's grid as well.
    assert int((with_distortion == 2).sum()) > int((without == 2).sum()) + 30


def test_the_rejection_map_gets_the_same_distorted_move(tmp_path: Path) -> None:
    """A marked block in a group's map lands where the transform puts it."""
    size = 301
    transform = RegistrationTransform(
        2.0,
        -3.0,
        180.0,
        1.0,
        150.0,
        150.0,
        PolynomialDistortion(
            150.0, 150.0, 150.0, (0, 300, 0, 300), (4.0, 0, 0, 6.0, 0, 0), (0.0, 0, 0, 0, 5.0, 0)
        ),
    )
    rejection_map = np.zeros((size, size), np.float32)
    rejection_map[40:46, 60:66] = 1.0
    path = tmp_path / "group_RejMap.fits"
    fits.writeto(path, rejection_map)
    alignment = AlignmentResult(2.0, -3.0, 1.0, rotation_degrees=180.0, distortion=transform.distortion)

    merge_rejection_maps([str(path)], [1], str(tmp_path / "merged.fits"), alignments=[alignment])

    merged = fits.getdata(tmp_path / "merged.fits")
    expected_row, expected_column = transform.map_points(np.array([42.5]), np.array([62.5]))
    found = centroid(np.clip(merged, 0, None))
    assert found[0] == pytest.approx(float(expected_row[0]), abs=0.3)
    assert found[1] == pytest.approx(float(expected_column[0]), abs=0.3)
    assert merged.sum() == pytest.approx(36.0, rel=0.05)
