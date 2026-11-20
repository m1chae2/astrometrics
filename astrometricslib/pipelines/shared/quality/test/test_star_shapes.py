"""Tests of the per-star shape measurement of `measure_star_shapes`.

The images are synthetic: Moffat stars of known width, stretch and direction on
a noisy sky, drawn with a fixed random seed. The tests check the stretch value
(``1 - b / a``), the direction convention (from +y toward +x, like the star
profile cut), the 1-based positions, the frame summary and the plain note for
an image with no stars.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import Astrometrics
from astrometricslib.exceptions import NotFoundError
from astrometricslib.pipelines.shared.quality.star_shapes import measure_star_shapes_in_file

FRAME_SIZE = 700
TRUE_WIDTH_PX = 3.0
BETA = 3.0


def _write_stretched_stars(
    path: Path, elongation_ratio: float, angle_from_x_rad: float, star_count: int = 90
) -> list[tuple[float, float]]:
    """Write an image of stars with one stretch and one direction.

    Parameters
    ----------
    path : `pathlib.Path`
        The FITS file to write.
    elongation_ratio : `float`
        The long axis divided by the short axis of every star.
    angle_from_x_rad : `float`
        The long axis direction, from the x axis toward the y axis, in radians.
    star_count : `int`, optional
        The number of stars.

    Returns
    -------
    centers : `list` [`tuple` [`float`, `float`]]
        The 0-based ``(x, y)`` center of each star.
    """
    generator = np.random.default_rng(5)
    scale = 2.0 * math.sqrt(2.0 ** (1.0 / BETA) - 1.0)
    alpha_major = TRUE_WIDTH_PX * math.sqrt(elongation_ratio) / scale
    alpha_minor = TRUE_WIDTH_PX / math.sqrt(elongation_ratio) / scale
    image = generator.normal(1000.0, 4.0, size=(FRAME_SIZE, FRAME_SIZE))
    rows, columns = np.mgrid[0:FRAME_SIZE, 0:FRAME_SIZE]
    centers = []
    # Stars on a loose lattice, so none has a close neighbor.
    for index in range(star_count):
        cell_x, cell_y = index % 10, index // 10
        center_x = 40.0 + cell_x * 65.0 + generator.uniform(-8, 8)
        center_y = 40.0 + cell_y * 65.0 + generator.uniform(-8, 8)
        flux = 10 ** generator.uniform(4.4, 5.0)
        box = (
            slice(max(int(center_y) - 30, 0), int(center_y) + 31),
            slice(max(int(center_x) - 30, 0), int(center_x) + 31),
        )
        delta_x = columns[box] - center_x
        delta_y = rows[box] - center_y
        along = delta_x * math.cos(angle_from_x_rad) + delta_y * math.sin(angle_from_x_rad)
        across = -delta_x * math.sin(angle_from_x_rad) + delta_y * math.cos(angle_from_x_rad)
        profile = (1.0 + (along / alpha_major) ** 2 + (across / alpha_minor) ** 2) ** (-BETA)
        image[box] += profile * flux * (BETA - 1.0) / (math.pi * alpha_major * alpha_minor)
        centers.append((center_x, center_y))
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path, overwrite=True)
    return centers


def test_stretch_is_one_minus_short_over_long(tmp_path: Path) -> None:
    """An axis ratio of 1.5 reads a stretch of 1 - 1/1.5, about 0.33."""
    path = tmp_path / "stretched.fits"
    _write_stretched_stars(path, 1.5, 0.0)
    result = measure_star_shapes_in_file(str(path))
    assert result.star_count == len(result.shapes) > 20
    assert result.median_elongation == pytest.approx(1.0 - 1.0 / 1.5, abs=0.04)
    assert result.median_width_px == pytest.approx(TRUE_WIDTH_PX, rel=0.05)
    assert type(result.shapes[0].x) is float


@pytest.mark.parametrize(
    ("angle_from_x_degrees", "expected_degrees"),
    [(0.0, 90.0), (90.0, 0.0), (30.0, 60.0), (150.0, 120.0)],
)
def test_direction_runs_from_plus_y_toward_plus_x(
    tmp_path: Path, angle_from_x_degrees: float, expected_degrees: float
) -> None:
    """Stretch along +x reads 90 degrees and along +y reads 0 (the cut way)."""
    path = tmp_path / "direction.fits"
    _write_stretched_stars(path, 1.8, math.radians(angle_from_x_degrees))
    result = measure_star_shapes_in_file(str(path))
    assert result.direction_degrees is not None
    difference = (result.direction_degrees - expected_degrees + 90.0) % 180.0 - 90.0
    assert abs(difference) < 4.0
    # Lines at 1 and 179 degrees are 2 degrees apart, so wrap the difference.
    differences = np.abs(
        (np.array([star.angle_degrees for star in result.shapes]) - expected_degrees + 90.0) % 180.0 - 90.0
    )
    assert np.median(differences) < 6.0
    assert result.direction_coherence is not None and result.direction_coherence > 0.9
    assert all(0.0 <= shape.angle_degrees < 180.0 for shape in result.shapes)


def test_positions_are_one_based_and_summary_regions_filled(tmp_path: Path) -> None:
    """Each measured star lies within a pixel of a known 1-based position."""
    path = tmp_path / "positions.fits"
    centers = _write_stretched_stars(path, 1.3, 0.4)
    result = measure_star_shapes_in_file(str(path))
    assert result.width == FRAME_SIZE and result.height == FRAME_SIZE
    for shape in result.shapes:
        nearest = min(centers, key=lambda c: (c[0] + 1 - shape.x) ** 2 + (c[1] + 1 - shape.y) ** 2)
        assert math.hypot(nearest[0] + 1 - shape.x, nearest[1] + 1 - shape.y) < 1.0
    assert result.center_width_px is not None
    assert result.corner_width_px is not None
    assert result.corner_width_px == pytest.approx(result.center_width_px, rel=0.1)


def test_round_stars_have_no_stretch(tmp_path: Path) -> None:
    """Round stars read a stretch near 0."""
    path = tmp_path / "round.fits"
    _write_stretched_stars(path, 1.0, 0.0)
    result = measure_star_shapes_in_file(str(path))
    assert result.median_elongation is not None
    assert result.median_elongation < 0.05


def test_image_without_stars_is_a_note_not_an_error(tmp_path: Path) -> None:
    """An empty sky gives an empty list and a reason."""
    path = tmp_path / "empty.fits"
    fits.PrimaryHDU(np.random.default_rng(1).normal(100.0, 3.0, (200, 200)).astype(np.float32)).writeto(path)
    result = measure_star_shapes_in_file(str(path))
    assert result.shapes == [] and result.star_count == 0
    assert result.median_elongation is None
    assert result.reason


def test_public_method_returns_the_measurement(tmp_path: Path) -> None:
    """The public method gives a measurement, and a missing file raises."""
    path = tmp_path / "api.fits"
    _write_stretched_stars(path, 1.5, 0.0, star_count=60)
    result = Astrometrics().quality.measure_star_shapes(str(path))
    assert result.star_count > 10
    with pytest.raises(NotFoundError):
        Astrometrics().quality.measure_star_shapes(str(tmp_path / "missing.fits"))
