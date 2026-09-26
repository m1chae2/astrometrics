"""Tests for the local deep-star catalog database.

The tile grid decides which stars a lookup opens, so a mistake there means
stars silently missing from the sky map. The main test therefore compares
the tiled lookup against a brute-force check of every star, for many random
circles including ones over the poles and across the zero-hours seam.
"""

import math
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.drivers.deep_star_store import (
    BRIGHT_TIER_MAX_MAGNITUDE,
    COARSE_TILE_HEIGHT_DEGREES,
    FINE_TILE_HEIGHT_DEGREES,
    TileGrid,
    count_stars_by_grid,
    find_deep_stars,
    get_deep_catalog_path,
    get_deep_catalog_status,
    get_downloaded_pixels,
    record_downloaded_pixel,
    set_deep_catalog_plan,
)


class _LibraryConfig:
    """A stand-in for `AppConfiguration` that points at a temporary folder."""

    def __init__(self, library_path: Path) -> None:
        self._library_path = library_path

    def get_library_path(self) -> Path:
        """Return the sandboxed library root.

        Returns
        -------
        path : `pathlib.Path`
            The temporary directory standing in for the library.
        """
        return self._library_path


def _separation(ra: float, dec: float, star_ra: np.ndarray, star_dec: np.ndarray) -> np.ndarray:
    """Measure the angle from a point to many stars, without the store's code.

    Returns
    -------
    separation : `numpy.ndarray`
        The angle to each star, in degrees, from the spherical law of cosines.
    """
    cosine = np.sin(np.radians(dec)) * np.sin(np.radians(star_dec)) + np.cos(np.radians(dec)) * np.cos(
        np.radians(star_dec)
    ) * np.cos(np.radians(star_ra - ra))
    return np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))


def _random_sky(count: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Scatter fake stars evenly over the whole sphere.

    Returns
    -------
    source_ids, ra, dec, magnitude : `numpy.ndarray`
        Each star's ID, position in degrees, and G magnitude (5 to 16).
    """
    generator = np.random.default_rng(seed)
    source_ids = np.arange(1, count + 1, dtype=np.int64) * 1_000_003
    ra = generator.uniform(0.0, 360.0, count)
    # Uniform on a sphere means the sine of the declination is uniform.
    dec = np.degrees(np.arcsin(generator.uniform(-1.0, 1.0, count)))
    magnitude = np.round(generator.uniform(5.0, 16.0, count), 3)
    return source_ids, ra, dec, magnitude


def test_tile_grid_rejects_a_height_that_does_not_divide_the_sky():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Rows of unequal height would leave a gap at the pole."""
    with pytest.raises(ValueError, match="divide"):
        TileGrid(7.0)


def test_scalar_and_vector_tile_numbers_agree():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """One-at-a-time and many-at-once versions file stars identically."""
    grid = TileGrid(1.0)
    _, ra, dec, _ = _random_sky(2000, seed=1)
    ra = np.concatenate([ra, [0.0, 359.9999999, 360.0, 0.5], [10.0] * 4])
    dec = np.concatenate([dec, [0.0, 0.0, 0.0, 90.0], [-90.0, 90.0, 89.9999, -89.9999]])

    vector = grid.tile_numbers(ra, dec)
    scalar = np.array([grid.tile_number(a, d) for a, d in zip(ra, dec, strict=True)])

    assert np.array_equal(vector, scalar)


def test_every_tile_is_about_the_same_size_on_the_sky():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Rows near the pole must be cut into fewer, wider columns."""
    grid = TileGrid(1.0)

    equator_columns = grid._column_counts[grid.row_count // 2]
    polar_columns = grid._column_counts[0]

    assert 355 <= equator_columns <= 360
    assert polar_columns < 10


@pytest.mark.parametrize("seed", [11, 12])
def test_tiled_lookup_matches_a_brute_force_check_of_every_star(tmp_path, seed):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Random circles anywhere on the sky find exactly the stars inside."""
    config = _LibraryConfig(tmp_path)
    source_ids, ra, dec, magnitude = _random_sky(30000, seed=seed)
    record_downloaded_pixel(config, 0, source_ids, ra, dec, magnitude)
    generator = np.random.default_rng(seed + 100)

    # Hand-picked hard cases first: over each pole, across the RA seam,
    # on a tile edge, tiny, and huge.
    circles = [
        (10.0, 89.5, 3.0),
        (200.0, -89.5, 3.0),
        (0.0, 20.0, 5.0),
        (359.9, -30.0, 8.0),
        (180.0, 0.0, 0.05),
        (45.0, 45.0, 60.0),
        (45.0, 45.0, 100.0),
        (300.0, 10.0, 179.0),
    ]
    for _ in range(40):
        circles.append((
            float(generator.uniform(0.0, 360.0)),
            float(np.degrees(np.arcsin(generator.uniform(-1.0, 1.0)))),
            float(10 ** generator.uniform(-1.0, 1.7)),
        ))

    for circle_ra, circle_dec, radius in circles:
        limit = float(generator.uniform(6.0, 16.0))
        found = find_deep_stars(config, circle_ra, circle_dec, radius, limit)
        found_ids = {star[0] for star in found}

        separation = _separation(circle_ra, circle_dec, ra, dec)
        # Positions are saved to a millionth of a degree, so a star within
        # that of the edge could go either way; allow a hair of slack.
        slack = 1e-4
        must_find = set(source_ids[(separation <= radius - slack) & (magnitude <= limit - 1e-3)].tolist())
        may_find = set(source_ids[(separation <= radius + slack) & (magnitude <= limit + 1e-3)].tolist())
        assert must_find <= found_ids <= may_find, (circle_ra, circle_dec, radius, limit)


def test_stars_come_back_brightest_first_with_their_saved_values(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Order, IDs, positions and magnitudes survive the round trip."""
    config = _LibraryConfig(tmp_path)
    record_downloaded_pixel(
        config,
        0,
        np.array([300, 100, 200], dtype=np.int64),
        np.array([10.5, 10.0, 10.25]),
        np.array([20.5, 20.0, 20.25]),
        np.array([11.5, 9.25, 14.0]),
    )

    stars = find_deep_stars(config, 10.25, 20.25, 2.0, 16.0)

    assert [star[0] for star in stars] == [100, 300, 200]
    assert stars[0][1] == pytest.approx(10.0, abs=1e-6)
    assert stars[0][2] == pytest.approx(20.0, abs=1e-6)
    assert [star[3] for star in stars] == pytest.approx([9.25, 11.5, 14.0])


def test_faint_and_bright_stars_are_split_at_the_tier_boundary(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A star at the boundary is bright-tier; just past it, faint-tier."""
    config = _LibraryConfig(tmp_path)
    boundary = BRIGHT_TIER_MAX_MAGNITUDE
    record_downloaded_pixel(
        config,
        0,
        np.array([1, 2, 3], dtype=np.int64),
        np.array([50.0, 50.001, 50.002]),
        np.array([10.0, 10.0, 10.0]),
        np.array([boundary - 1.0, boundary, boundary + 0.001]),
    )

    only_bright = find_deep_stars(config, 50.0, 10.0, 1.0, boundary)
    with_faint = find_deep_stars(config, 50.0, 10.0, 1.0, boundary + 1.0)

    assert [star[0] for star in only_bright] == [1, 2]
    assert [star[0] for star in with_faint] == [1, 2, 3]


def test_maximum_stars_keeps_the_brightest(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A cap on the count drops the faintest stars, not random ones."""
    config = _LibraryConfig(tmp_path)
    record_downloaded_pixel(
        config,
        0,
        np.arange(1, 6, dtype=np.int64),
        np.linspace(100.0, 100.4, 5),
        np.full(5, 5.0),
        np.array([15.0, 8.0, 12.0, 10.0, 14.0]),
    )

    stars = find_deep_stars(config, 100.2, 5.0, 2.0, 16.0, maximum_stars=3)

    assert [star[3] for star in stars] == pytest.approx([8.0, 10.0, 12.0])


def test_negative_magnitudes_are_kept(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The brightest stars in the sky have negative magnitudes."""
    config = _LibraryConfig(tmp_path)
    record_downloaded_pixel(
        config, 0, np.array([1], dtype=np.int64), np.array([101.0]), np.array([-16.0]), np.array([-1.46])
    )

    stars = find_deep_stars(config, 101.0, -16.0, 1.0, 16.0)

    assert [star[0] for star in stars] == [1]
    assert stars[0][3] == pytest.approx(-1.46)


def test_lookup_without_a_catalog_returns_none_and_creates_nothing(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A fresh install has no catalog; looking must not create one."""
    config = _LibraryConfig(tmp_path)

    assert find_deep_stars(config, 10.0, 10.0, 1.0, 16.0) is None
    assert get_downloaded_pixels(config) == set()
    assert not get_deep_catalog_path(config).exists()


def test_empty_area_gives_an_empty_list_not_none(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """An empty area is different from no catalog being installed."""
    config = _LibraryConfig(tmp_path)
    record_downloaded_pixel(
        config, 0, np.array([1], dtype=np.int64), np.array([10.0]), np.array([10.0]), np.array([9.0])
    )

    assert find_deep_stars(config, 200.0, -50.0, 1.0, 16.0) == []


def test_recording_a_pixel_twice_does_not_duplicate_its_stars(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Re-running a chunk (say after a crash) must not double the stars."""
    config = _LibraryConfig(tmp_path)
    arguments = (
        np.array([1, 2], dtype=np.int64),
        np.array([10.0, 10.1]),
        np.array([5.0, 5.1]),
        np.array([9.0, 10.0]),
    )

    record_downloaded_pixel(config, 7, *arguments)
    record_downloaded_pixel(config, 7, *arguments)

    assert len(find_deep_stars(config, 10.05, 5.05, 2.0, 16.0)) == 2
    assert get_downloaded_pixels(config) == {7}
    assert get_deep_catalog_status(config)["star_count"] == 2


def test_status_reports_progress_and_completeness(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Status says how much of the sky is downloaded, and when all of it is."""
    config = _LibraryConfig(tmp_path)
    assert get_deep_catalog_status(config)["installed"] is False

    set_deep_catalog_plan(config, healpix_level=0, magnitude_limit=16.0)
    status = get_deep_catalog_status(config)
    assert status["installed"] is False  # planned, but nothing downloaded yet
    assert status["pixels_total"] == 12

    record_downloaded_pixel(
        config, 0, np.array([1], dtype=np.int64), np.array([10.0]), np.array([10.0]), np.array([9.0])
    )
    status = get_deep_catalog_status(config)
    assert status["installed"] is True
    assert status["complete"] is False
    assert status["pixels_downloaded"] == 1
    assert status["magnitude_limit"] == pytest.approx(16.0)

    for pixel in range(1, 12):
        record_downloaded_pixel(
            config,
            pixel,
            np.array([pixel + 1], dtype=np.int64),
            np.array([10.0]),
            np.array([10.0]),
            np.array([9.0]),
        )
    assert get_deep_catalog_status(config)["complete"] is True


def test_resuming_with_different_settings_is_refused(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Chunks made at a different depth or chunk size cannot be mixed in."""
    config = _LibraryConfig(tmp_path)
    set_deep_catalog_plan(config, healpix_level=4, magnitude_limit=16.0)

    set_deep_catalog_plan(config, healpix_level=4, magnitude_limit=16.0)  # same again: fine
    with pytest.raises(ValueError, match="healpix_level"):
        set_deep_catalog_plan(config, healpix_level=5, magnitude_limit=16.0)
    with pytest.raises(ValueError, match="magnitude_limit"):
        set_deep_catalog_plan(config, healpix_level=4, magnitude_limit=15.0)


def test_tile_sizes_are_the_documented_ones():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Guard the constants the derivation comments describe."""
    assert COARSE_TILE_HEIGHT_DEGREES == pytest.approx(5.0)
    assert FINE_TILE_HEIGHT_DEGREES == pytest.approx(1.0)
    assert math.isclose(BRIGHT_TIER_MAX_MAGNITUDE, 12.0)


def test_stars_are_counted_by_grid(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The end-of-download report can say how many stars are in each grid."""
    config = _LibraryConfig(tmp_path)
    assert count_stars_by_grid(config) == {"bright": 0, "faint": 0}

    record_downloaded_pixel(
        config,
        0,
        np.array([1, 2, 3], dtype=np.int64),
        np.array([10.0, 20.0, 30.0]),
        np.array([5.0, 5.0, 5.0]),
        np.array([8.0, BRIGHT_TIER_MAX_MAGNITUDE, 14.0]),
    )

    assert count_stars_by_grid(config) == {"bright": 2, "faint": 1}
