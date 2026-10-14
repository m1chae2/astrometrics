"""Purpose: Unit tests for lining up exposure-group stacks by star patterns.

Description: Phase correlation fails when two group stacks are turned by a few
degrees or by 180 degrees (a mount flip at the meridian), as the Nikon D5300
stacks of M 106, NGC 7023, M 51, NGC 2024 and NGC 6992 were on 2026-10-13.
These tests build synthetic star fields, move them by a known shift, rotation
and scale, and check that `find_alignment_from_star_patterns` recovers the
move with no first guess: for a small turn, a half turn, a large offset, and
with stars missing or added. They also check that too few stars, or two
unrelated fields, are refused, that `align_images_to_reference` falls back to
star patterns only for imaging stacks, and that the spectral path is unchanged.
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from astrometricslib.pipelines.stacking.processing import group_alignment
from astrometricslib.pipelines.stacking.processing.group_alignment import (
    REFINEMENT_MINIMUM_PAIRS,
    align_images_to_reference,
    find_alignment_from_star_patterns,
)
from astrometricslib.pipelines.stacking.processing.star_pattern_candidates import (
    candidate_similarity_transforms,
)

FIELD_SIZE = 1200


def make_positions(seed: int, count: int = 150) -> np.ndarray:
    """Draw random star positions as complex numbers ``column + 1j * row``.

    Returns
    -------
    positions : `numpy.ndarray`
        Complex positions, away from the frame edges.
    """
    rng = np.random.default_rng(seed)
    return rng.uniform(100, FIELD_SIZE - 100, count) + 1j * rng.uniform(100, FIELD_SIZE - 100, count)


def render(positions: np.ndarray, amplitudes: np.ndarray, seed: int = 0) -> np.ndarray:
    """Draw stars at the given positions onto a noisy flat sky.

    Returns
    -------
    image : `numpy.ndarray`
        A ``FIELD_SIZE`` square float32 image. Stars off the frame are
        skipped.
    """
    image = np.zeros((FIELD_SIZE, FIELD_SIZE), dtype=np.float64)
    for position, amplitude in zip(positions, amplitudes, strict=True):
        row, column = position.imag, position.real
        if not (3 <= row < FIELD_SIZE - 4 and 3 <= column < FIELD_SIZE - 4):
            continue
        base_row, base_column = int(row), int(column)
        rows, columns = np.mgrid[base_row - 3 : base_row + 4, base_column - 3 : base_column + 4]
        image[rows, columns] += amplitude * np.exp(
            -((rows - row) ** 2 + (columns - column) ** 2) / (2 * 1.5**2)
        )
    noise = np.random.default_rng(seed).normal(0, 0.01, image.shape)
    return (0.1 + image + gaussian_filter(noise, 0.5)).astype(np.float32)


def make_pair(a: complex, b: complex, seed: int = 1, count: int = 150) -> tuple[np.ndarray, np.ndarray]:
    """Make a reference field and the same stars seen by an image.

    The image's positions are ``(reference - b) / a``, so the move that puts
    the image onto the reference is ``reference = a * image + b``.

    Returns
    -------
    reference, image : `numpy.ndarray`
        The two rendered fields.
    """
    positions = make_positions(seed, count)
    amplitudes = np.random.default_rng(seed + 1).uniform(0.3, 1.0, count)
    return render(positions, amplitudes, seed=10), render((positions - b) / a, amplitudes, seed=11)


def expected_centre_shift(a: complex, b: complex) -> complex:
    """Give where the image centre moves, minus where it started.

    Returns
    -------
    shift : `complex`
        ``column + 1j * row`` displacement of the image centre.
    """
    centre = (FIELD_SIZE - 1) / 2.0 * (1 + 1j)
    return a * centre + b - centre


@pytest.mark.parametrize(
    ("rotation_degrees", "scale", "offset"),
    [
        (0.0, 1.0, 40 + 25j),
        (-6.3, 1.0014, -200 + 150j),
        (0.4, 0.9996, 300 - 280j),
        (179.6, 1.0, 1100 + 900j),
    ],
)
def test_a_known_move_is_recovered_with_no_first_guess(
    rotation_degrees: float, scale: float, offset: complex
) -> None:
    """Big shifts, small turns, and a half turn are all found."""
    a = scale * np.exp(1j * np.radians(rotation_degrees))
    reference, image = make_pair(a, offset)

    result = find_alignment_from_star_patterns(reference, image)

    assert result is not None
    assert result.is_star_pattern
    assert result.trusted
    assert result.rotation_degrees == pytest.approx(rotation_degrees, abs=0.01)
    assert result.scale == pytest.approx(scale, abs=2e-4)
    shift = expected_centre_shift(a, offset)
    assert result.shift_columns_pixels == pytest.approx(shift.real, abs=0.3)
    assert result.shift_rows_pixels == pytest.approx(shift.imag, abs=0.3)
    assert result.star_pairs >= REFINEMENT_MINIMUM_PAIRS
    assert result.residual_pixels < 0.3


def test_stars_missing_from_one_image_and_extras_in_the_other_are_tolerated() -> None:
    """A third of the stars missing and 60 extra ones do not stop it."""
    a, b = 1.001 * np.exp(1j * np.radians(2.0)), 80 - 60j
    positions = make_positions(3, 150)
    amplitudes = np.random.default_rng(4).uniform(0.3, 1.0, 150)
    reference = render(positions, amplitudes)
    extra = make_positions(5, 60)
    kept = np.arange(150) % 3 != 0
    image = render(
        np.concatenate([((positions - b) / a)[kept], extra]),
        np.concatenate([amplitudes[kept], np.full(60, 0.9)]),
        seed=12,
    )

    result = find_alignment_from_star_patterns(reference, image)

    assert result is not None
    assert result.rotation_degrees == pytest.approx(2.0, abs=0.02)
    assert result.star_pairs >= REFINEMENT_MINIMUM_PAIRS


def test_too_few_stars_are_refused() -> None:
    """Fewer matched stars than the minimum are refused, not guessed at."""
    reference, image = make_pair(np.exp(1j * np.radians(1.0)), 30 + 10j, count=12)

    assert find_alignment_from_star_patterns(reference, image) is None


def test_two_unrelated_fields_are_refused() -> None:
    """Chance coincidences between different fields do not pass."""
    reference, _ = make_pair(1.0, 0j, seed=21)
    _, unrelated = make_pair(1.0, 0j, seed=22)

    assert find_alignment_from_star_patterns(reference, unrelated) is None


def test_a_change_of_scale_beyond_the_limit_is_refused() -> None:
    """A 2% scale change is not the same telescope, so the match is refused."""
    reference, image = make_pair(1.02, 20 + 20j)

    assert find_alignment_from_star_patterns(reference, image) is None


def test_the_true_move_is_among_the_candidates() -> None:
    """The triangle matcher proposes the true move."""
    a, b = 1.001 * np.exp(1j * np.radians(-3.0)), -120 + 90j
    reference, image = make_pair(a, b)
    reference_stars = group_alignment.detect_star_centroids(reference)
    image_stars = group_alignment.detect_star_centroids(image)

    candidates = candidate_similarity_transforms(reference_stars, image_stars)

    errors = np.abs(candidates[:, 0] - a) + np.abs(candidates[:, 1] - b) / FIELD_SIZE
    assert errors.min() < 0.01


def test_the_alignment_loop_falls_back_to_star_patterns_for_a_flipped_stack() -> None:
    """A half-turned stack that correlation misses is lined up by stars."""
    a, b = np.exp(1j * np.radians(180.0)), 1199 + 1199j
    reference, image = make_pair(a, b)

    aligned, covered, results = align_images_to_reference([reference, image], 0, refine_with_stars=True)

    assert aligned[1] is not None
    assert covered[1] is not None
    assert results[1].is_star_pattern
    assert abs(abs(results[1].rotation_degrees) - 180.0) < 0.01


def test_the_alignment_loop_still_leaves_out_a_stack_that_cannot_be_placed() -> None:
    """With no common stars the group is left out."""
    reference, _ = make_pair(1.0, 0j, seed=31)
    _, unrelated = make_pair(1.0, 0j, seed=32)

    aligned, _, results = align_images_to_reference([reference, unrelated], 0, refine_with_stars=True)

    assert aligned[1] is None
    assert not results[1].trusted


def test_the_spectral_path_never_tries_star_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `refine_with_stars` the star-pattern search is not called."""

    def fail(*args: object, **kwargs: object) -> None:
        """Fail the test if called.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("star patterns must not be tried on this path")

    monkeypatch.setattr(group_alignment, "find_alignment_from_star_patterns", fail)
    reference, _ = make_pair(1.0, 0j, seed=41)
    _, unrelated = make_pair(1.0, 0j, seed=42)

    aligned, _, results = align_images_to_reference([reference, unrelated], 0)

    assert aligned[1] is None
    assert not results[1].is_star_pattern


def test_a_shift_the_stars_agree_with_is_not_second_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack that already lines up by stars skips the search."""

    def fail(*args: object, **kwargs: object) -> None:
        """Fail the test if called.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("star patterns must not be tried here")

    monkeypatch.setattr(group_alignment, "find_alignment_from_star_patterns", fail)
    reference, image = make_pair(1.0, 6 + 4j, seed=51)

    aligned, _, results = align_images_to_reference([reference, image], 0, refine_with_stars=True)

    assert aligned[1] is not None
    assert not results[1].is_star_pattern
