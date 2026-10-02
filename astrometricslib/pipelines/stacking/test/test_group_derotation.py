"""Purpose: Unit tests for lining up exposure groups onto one trail tilt.

Description: Checks that rotating about a point leaves that point in place,
that a set of groups with different measured tilts is turned onto the
tilt of the one that measured most clearly, that a group whose tilt cannot
be trusted is left alone, and that nothing is rotated when no group's tilt
can be trusted.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.stacking.processing.group_derotation import (
    NEGLIGIBLE_ROTATION_DEGREES,
    derotate_groups_to_common_tilt,
    rotate_about_point,
)


def _straight_streak(size: int, star_row: float, star_column: float) -> np.ndarray:
    """Build an image with a star and a streak running straight down.

    Parameters
    ----------
    size : `int`
        The image's height and width.
    star_row : `float`
        The streak's starting row (the star's own position).
    star_column : `float`
        The streak's starting column.

    Returns
    -------
    image : `numpy.ndarray`
        The image, `float32`, with a bright dot at the star and a straight
        streak running down the same column below it.
    """
    rows, columns = np.mgrid[0:size, 0:size].astype(np.float64)
    image = np.full((size, size), 0.01, dtype=np.float64)
    image += 1.0 * np.exp(-((rows - star_row) ** 2 + (columns - star_column) ** 2) / (2 * 2.0**2))
    in_streak = (rows > star_row) & (np.abs(columns - star_column) < 0.5)
    image[in_streak] += 0.6
    return image.astype(np.float32)


def _tilted_streak(size: int, star_row: float, star_column: float, angle_degrees: float) -> np.ndarray:
    """Build a straight streak, then lean it by a known angle.

    Built by rotating a straight streak with `rotate_about_point` itself
    (rather than separate trigonometry), so a test that then rotates it
    back by `angle_degrees` is checking the function against its own
    stated contract: turning a trail by its own tilt brings it to 0.

    Parameters
    ----------
    size : `int`
        The image's height and width.
    star_row : `float`
        The streak's starting row (the star's own position).
    star_column : `float`
        The streak's starting column.
    angle_degrees : `float`
        The tilt the streak should measure as, in `rotate_about_point`'s own
        sense.

    Returns
    -------
    image : `numpy.ndarray`
        The image, `float32`, with the streak leaning at `angle_degrees`.
    """
    straight = _straight_streak(size, star_row, star_column)
    return rotate_about_point(straight, star_row, star_column, -angle_degrees)


def test_rotating_about_a_point_leaves_that_point_in_place() -> None:
    """The pixel a rotation turns about keeps its own brightness."""
    image = _tilted_streak(300, 150.0, 150.0, 8.0)

    rotated = rotate_about_point(image, 150.0, 150.0, 15.0)

    assert rotated[150, 150] == pytest.approx(image[150, 150], abs=0.05)


def test_a_negligible_angle_returns_the_image_unchanged() -> None:
    """An angle under the negligible threshold is not resampled at all."""
    image = _tilted_streak(300, 150.0, 150.0, 3.0)

    rotated = rotate_about_point(image, 150.0, 150.0, NEGLIGIBLE_ROTATION_DEGREES / 2.0)

    assert np.array_equal(rotated, image.astype(np.float32))


def test_rotating_by_the_measured_angle_straightens_the_streak() -> None:
    """A streak leaning by `angle` is vertical after rotating by `angle`."""
    image = _tilted_streak(300, 150.0, 150.0, 6.0)

    rotated = rotate_about_point(image, 150.0, 150.0, 6.0)

    # A vertical streak keeps every bright pixel in the same column as the
    # star; check that the streak's own column has not drifted far from it.
    far_row = 220
    brightest_column = int(np.argmax(rotated[far_row]))
    assert brightest_column == pytest.approx(150, abs=2)


def _stub_measurements(monkeypatch, angles_and_contrasts):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Make `measure_trail_angle_degrees` return canned answers in call order.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the real measurement with the stub.
    angles_and_contrasts : `list` [`tuple`]
        One `(angle_degrees, contrast_sigma)` per expected call, in order.
    """
    import astrometricslib.pipelines.stacking.processing.group_derotation as module

    answers = iter(angles_and_contrasts)
    monkeypatch.setattr(module, "measure_trail_angle_degrees", lambda *args, **kwargs: next(answers))


def test_groups_are_rotated_onto_the_most_clearly_measured_tilt(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """Three groups at different tilts end up at the clearest one's tilt."""
    images = [
        _tilted_streak(300, 150.0, 150.0, 1.0),
        _tilted_streak(300, 150.0, 150.0, 2.13),
        _tilted_streak(300, 150.0, 150.0, 4.7),
    ]
    paths = ["group_a.fits", "group_b.fits", "group_c.fits"]
    _stub_measurements(monkeypatch, [(1.0, 55.0), (2.13, 4000.0), (4.7, 4400.0)])

    rotated, angles = derotate_groups_to_common_tilt(images, paths, "ZWO ASI 533MM Pro")

    assert angles == pytest.approx([1.0, 2.13, 4.7])
    # The clearest group (4.7 degrees, contrast 4400) is the reference and
    # is left alone; the other two are turned to match it.
    for image in (rotated[0], rotated[1]):
        far_row = 220
        brightest_column = int(np.argmax(image[far_row]))
        assert brightest_column == pytest.approx(150, abs=6)


def test_a_group_below_the_contrast_floor_is_left_unrotated(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """A noisy group's own tilt is not trusted, and it is reported as None."""
    faint = _tilted_streak(300, 150.0, 150.0, 9.0)
    clear = _tilted_streak(300, 150.0, 150.0, 0.0)
    _stub_measurements(monkeypatch, [(9.0, 3.0), (0.0, 4000.0)])

    rotated, angles = derotate_groups_to_common_tilt(
        [faint, clear], ["a.fits", "b.fits"], "ZWO ASI 533MM Pro"
    )

    assert angles == [None, 0.0]
    assert np.array_equal(rotated[0], faint.astype(np.float32))


def test_nothing_is_rotated_when_no_group_can_be_trusted(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """Every group failing its tilt measurement leaves the images untouched."""
    images = [_tilted_streak(300, 150.0, 150.0, 2.0), _tilted_streak(300, 150.0, 150.0, 5.0)]
    _stub_measurements(monkeypatch, [(2.0, 1.0), (5.0, 2.0)])

    rotated, angles = derotate_groups_to_common_tilt(images, ["a.fits", "b.fits"], "ZWO ASI 533MM Pro")

    assert angles == [None, None]
    assert all(np.array_equal(r, i.astype(np.float32)) for r, i in zip(rotated, images, strict=True))


def test_mismatched_image_and_path_counts_are_rejected() -> None:
    """Each image needs exactly one path to re-measure its tilt from."""
    with pytest.raises(ValueError, match="one path"):
        derotate_groups_to_common_tilt([np.zeros((10, 10))], ["a.fits", "b.fits"], "ZWO ASI 533MM Pro")
