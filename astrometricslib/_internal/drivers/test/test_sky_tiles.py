"""Purpose: Tests for the HEALPix sky-tile helpers.

Description: Checks the tile numbering against real Gaia stars, the Gaia
source ID rule (``source_id >> (35 + 2 * (12 - level))`` is the tile at
``level``), and that the circle and footprint helpers choose every tile that
holds a point inside the region, including near the poles and across RA 0/360.
"""

import numpy as np
import pytest

from astrometricslib.drivers import (
    healpix_pixels_of_points,
    pixel_source_id_range,
    pixels_near_circles,
    tile_of_source_id,
    tiles_for_cone,
    tiles_for_footprint,
)
from astrometricslib.exceptions import InvalidArgumentError


def test_pixel_source_id_ranges_tile_the_whole_id_space_without_gaps() -> None:
    """Neighbouring chunks meet exactly, so no star falls between two."""
    for level in (0, 3, 4):
        count = 12 * 4**level
        previous_high = 0
        for pixel in range(count):
            low, high = pixel_source_id_range(level, pixel)
            assert low == previous_high
            assert high > low
            previous_high = high
        # The end is 12 * 2**59, just above Gaia DR3's largest source ID.
        assert previous_high == 12 << 59
        assert previous_high > 6917528997577384320


def test_a_fine_pixel_lies_inside_its_coarse_parent() -> None:
    """A level 12 pixel p is inside level 4 pixel p >> 16."""
    fine_pixel = 123_456_789
    fine_low, fine_high = pixel_source_id_range(12, fine_pixel)
    coarse_low, coarse_high = pixel_source_id_range(4, fine_pixel >> 16)

    assert coarse_low <= fine_low < fine_high <= coarse_high
    assert fine_low == fine_pixel * 34359738368  # 2**35, as in Gaia's own documentation


@pytest.mark.parametrize(("level", "pixel"), [(-1, 0), (13, 0), (0, -1), (0, 12), (4, 3072)])
def test_pixel_source_id_range_rejects_out_of_range_arguments(level: int, pixel: int) -> None:
    """A typo must not silently ask for the wrong part of the sky."""
    with pytest.raises(InvalidArgumentError, match=r"HEALPix level|Pixel"):
        pixel_source_id_range(level, pixel)


# Real Gaia DR3 stars from the local Gaia cache, as (source_id, ra, dec).
# Every Gaia ID contains the star's HEALPix pixel at level 12 (ID // 2**35),
# so each one is a known right answer for `healpix_pixels_of_points`. They
# were picked to sit away from pixel edges and to be spread over the part of
# the sky the cache covers (declination -9 to +90 degrees; the cache holds
# nothing further south).
REAL_GAIA_STARS = [
    (3216076459047432576, 84.157318, -3.011855),
    (3017256345539703680, 83.658174, -5.685763),
    (3131979556282532352, 97.748858, 5.60661),
    (598964495444498816, 132.063126, 11.697245),
    (66521797809401472, 57.019427, 23.986438),
    (381249579555539072, 9.854079, 41.498679),
    (1328057871370922368, 250.399995, 36.451084),
    (1551089132139645696, 200.986347, 46.186764),
    (1609407709912024448, 211.284872, 54.967845),
    (1089550636544676224, 114.284482, 65.111841),
    (1066790131669416960, 149.999907, 67.918022),
    (576317201612828800, 0.068464, 89.11556),
    (576459622729603584, 82.846606, 89.94583),
    (1729338108943926656, 180.623905, 89.41876),
]


@pytest.mark.parametrize("level", [0, 2, 4, 8, 12])
def test_healpix_pixel_of_a_real_gaia_star_matches_the_pixel_in_its_id(level: int) -> None:
    """The pixel worked out from a star's position is the one in its ID."""
    ids = np.array([star[0] for star in REAL_GAIA_STARS], dtype=np.int64)
    ra = np.array([star[1] for star in REAL_GAIA_STARS])
    dec = np.array([star[2] for star in REAL_GAIA_STARS])

    pixels = healpix_pixels_of_points(level, ra, dec)

    assert list(pixels) == list(ids >> (35 + 2 * (12 - level)))


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_healpix_pixels_all_have_the_same_area_over_the_whole_sky(level: int) -> None:
    """Every pixel of a level gets its fair share of evenly spread points.

    The real stars above only cover the northern sky, so this is what
    checks the southern half: a mistake there would leave some pixels with
    no points at all and give others too many. Points spread evenly over
    a sphere have a uniformly random sine of the declination.
    """
    random_generator = np.random.default_rng(seed=11)
    point_count = 400_000
    ra = random_generator.uniform(0.0, 360.0, point_count)
    dec = np.degrees(np.arcsin(random_generator.uniform(-1.0, 1.0, point_count)))

    pixels = healpix_pixels_of_points(level, ra, dec)

    pixel_count = 12 * 4**level
    counts = np.bincount(pixels, minlength=pixel_count)
    expected = point_count / pixel_count
    # Five standard deviations of a count of about `expected` points.
    assert len(counts) == pixel_count
    assert np.all(np.abs(counts - expected) < 5 * np.sqrt(expected))


def test_healpix_pixels_of_points_rejects_a_level_that_does_not_exist() -> None:
    """A level outside 0..12 raises instead of returning nonsense."""
    with pytest.raises(InvalidArgumentError, match="level"):
        healpix_pixels_of_points(13, 10.0, 10.0)


CIRCLES = [
    (250.0, 36.0, 1.0),  # an ordinary spot
    (149.9, 68.0, 0.8),  # high declination
    (1.0, 10.0, 3.0),  # across the 0/360 degree seam
    (359.5, -20.0, 2.0),  # across the seam, in the south
    (30.0, 88.0, 4.0),  # around the north pole
    (200.0, -89.0, 3.0),  # around the south pole
    (100.0, -45.0, 10.0),  # a big southern circle
]


@pytest.mark.parametrize(("center_ra", "center_dec", "radius"), CIRCLES)
def test_every_point_inside_a_circle_is_in_a_chosen_pixel(
    center_ra: float, center_dec: float, radius: float
) -> None:
    """No star inside a circle can be left in a chunk that was not chosen."""
    random_generator = np.random.default_rng(seed=5)
    # Random distances and directions from the center, then turned into
    # positions with the standard spherical formulas.
    distance = np.radians(radius) * np.sqrt(random_generator.uniform(0.0, 1.0, 20_000))
    direction = random_generator.uniform(0.0, 2 * np.pi, 20_000)
    center_dec_radians = np.radians(center_dec)
    sin_dec = np.sin(center_dec_radians) * np.cos(distance) + np.cos(center_dec_radians) * np.sin(
        distance
    ) * np.cos(direction)
    dec = np.arcsin(np.clip(sin_dec, -1.0, 1.0))
    ra = np.radians(center_ra) + np.arctan2(
        np.sin(direction) * np.sin(distance) * np.cos(center_dec_radians),
        np.cos(distance) - np.sin(center_dec_radians) * sin_dec,
    )

    chosen = set(pixels_near_circles(4, [(center_ra, center_dec, radius)]))
    needed = set(healpix_pixels_of_points(4, np.degrees(ra), np.degrees(dec)).tolist())

    assert needed <= chosen


def test_a_small_circle_chooses_only_a_few_pixels() -> None:
    """A field-sized circle needs a few of the 3072 chunks, not thousands."""
    chosen = pixels_near_circles(4, [(250.0, 36.0, 0.8)])

    assert 1 <= len(chosen) <= 4
    assert chosen == sorted(set(chosen))


def test_the_pixel_at_a_circles_center_is_always_chosen() -> None:
    """The center's own chunk is in the list, including at the poles."""
    for center_ra, center_dec, radius in CIRCLES:
        center_pixel = int(healpix_pixels_of_points(4, center_ra, center_dec))
        assert center_pixel in pixels_near_circles(4, [(center_ra, center_dec, radius)])


def test_several_circles_choose_the_union_of_their_pixels() -> None:
    """Two far-apart circles give both circles' chunks, each listed once."""
    first = pixels_near_circles(4, [(250.0, 36.0, 0.8)])
    second = pixels_near_circles(4, [(10.0, -50.0, 0.8)])

    both = pixels_near_circles(4, [(250.0, 36.0, 0.8), (10.0, -50.0, 0.8), (250.0, 36.0, 0.8)])

    assert both == sorted(set(first) | set(second))


def test_a_circle_as_big_as_the_sky_chooses_every_pixel() -> None:
    """Whole-sky radius selects all chunks."""
    assert pixels_near_circles(1, [(0.0, 0.0, 180.0)]) == list(range(48))


@pytest.mark.parametrize("level", [6, 7, 8])
def test_tile_of_source_id_follows_the_level_12_rule(level: int) -> None:
    """A source ID's tile is its level-12 tile moved to a coarser level."""
    ids = np.array([star[0] for star in REAL_GAIA_STARS], dtype=np.int64)
    level_12 = ids // 2**35

    tiles = tile_of_source_id(ids, level)

    assert list(tiles) == list(level_12 >> (2 * (12 - level)))
    assert list(tiles) == list(ids >> (35 + 2 * (12 - level)))
    assert tile_of_source_id(int(ids[0]), level) == int(tiles[0])


@pytest.mark.parametrize("level", [6, 7, 8])
def test_a_source_id_range_round_trips_through_its_tile(level: int) -> None:
    """Every ID in a tile's range maps back to that tile; the top is open."""
    for tile in (0, 1, 12 * 4**level // 2, 12 * 4**level - 1):
        low, high = pixel_source_id_range(level, tile)
        assert tile_of_source_id(low, level) == tile
        assert tile_of_source_id(high - 1, level) == tile
        assert tile_of_source_id(high, level) == tile + 1


@pytest.mark.parametrize("level", [6, 7, 8])
def test_tile_of_a_real_star_matches_its_position(level: int) -> None:
    """The tile from a star's ID equals the tile from its position."""
    ids = np.array([star[0] for star in REAL_GAIA_STARS], dtype=np.int64)
    ra = np.array([star[1] for star in REAL_GAIA_STARS])
    dec = np.array([star[2] for star in REAL_GAIA_STARS])

    assert list(tile_of_source_id(ids, level)) == list(healpix_pixels_of_points(level, ra, dec))


def test_tile_of_source_id_rejects_a_level_that_does_not_exist() -> None:
    """A level outside 0..12 raises instead of returning nonsense."""
    with pytest.raises(InvalidArgumentError, match="level"):
        tile_of_source_id(123, 13)


def _random_points_in_circle(
    center_ra: float, center_dec: float, radius: float, count: int, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Draw points spread evenly over a circle on the sky.

    Returns
    -------
    ra, dec : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The points, in degrees.
    """
    random_generator = np.random.default_rng(seed=seed)
    distance = np.radians(radius) * np.sqrt(random_generator.uniform(0.0, 1.0, count))
    direction = random_generator.uniform(0.0, 2 * np.pi, count)
    center_dec_radians = np.radians(center_dec)
    sin_dec = np.sin(center_dec_radians) * np.cos(distance) + np.cos(center_dec_radians) * np.sin(
        distance
    ) * np.cos(direction)
    dec = np.arcsin(np.clip(sin_dec, -1.0, 1.0))
    ra = np.radians(center_ra) + np.arctan2(
        np.sin(direction) * np.sin(distance) * np.cos(center_dec_radians),
        np.cos(distance) - np.sin(center_dec_radians) * sin_dec,
    )
    return np.degrees(ra), np.degrees(dec)


@pytest.mark.parametrize("level", [6, 7, 8])
@pytest.mark.parametrize(("center_ra", "center_dec", "radius"), CIRCLES[:5])
def test_tiles_for_cone_cover_every_point_inside_the_cone(
    level: int, center_ra: float, center_dec: float, radius: float
) -> None:
    """No random point inside a cone falls in a tile that was not chosen."""
    radius = min(radius, 2.0)
    ra, dec = _random_points_in_circle(center_ra, center_dec, radius, 20_000, seed=3)

    chosen = set(tiles_for_cone(center_ra, center_dec, radius, level))
    needed = set(healpix_pixels_of_points(level, ra, dec).tolist())

    assert needed <= chosen


def _field_corners(
    center_ra: float, center_dec: float, width: float, height: float, rotation: float
) -> list[tuple[float, float]]:
    """Find the corners of a rotated field, in order around its edge.

    Returns
    -------
    corners : `list` [`tuple` [`float`, `float`]]
        Each corner as ``(ra, dec)`` in degrees.
    """
    half_width, half_height = np.radians(width) / 2, np.radians(height) / 2
    angle = np.radians(rotation)
    center_dec_radians = np.radians(center_dec)
    corners = []
    for sign_east, sign_north in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        east = sign_east * half_width * np.cos(angle) - sign_north * half_height * np.sin(angle)
        north = sign_east * half_width * np.sin(angle) + sign_north * half_height * np.cos(angle)
        # Inverse gnomonic projection about the field center.
        rho = np.hypot(east, north)
        angular_distance = np.arctan(rho)
        sin_dec = (
            np.cos(angular_distance) * np.sin(center_dec_radians)
            + north * np.sin(angular_distance) * np.cos(center_dec_radians) / rho
        )
        corner_dec = np.arcsin(sin_dec)
        corner_ra = np.radians(center_ra) + np.arctan2(
            east * np.sin(angular_distance),
            rho * np.cos(center_dec_radians) * np.cos(angular_distance)
            - north * np.sin(center_dec_radians) * np.sin(angular_distance),
        )
        corners.append((float(np.degrees(corner_ra) % 360.0), float(np.degrees(corner_dec))))
    return corners


def _random_points_in_field(
    center_ra: float, center_dec: float, width: float, height: float, rotation: float, count: int
) -> tuple[np.ndarray, np.ndarray]:
    """Draw points spread evenly over a rotated field.

    Returns
    -------
    ra, dec : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The points, in degrees.
    """
    random_generator = np.random.default_rng(seed=17)
    east = np.radians(random_generator.uniform(-width / 2, width / 2, count))
    north = np.radians(random_generator.uniform(-height / 2, height / 2, count))
    angle = np.radians(rotation)
    rotated_east = east * np.cos(angle) - north * np.sin(angle)
    rotated_north = east * np.sin(angle) + north * np.cos(angle)
    rho = np.hypot(rotated_east, rotated_north)
    angular_distance = np.arctan(rho)
    center_dec_radians = np.radians(center_dec)
    safe_rho = np.where(rho > 0, rho, 1.0)
    sin_dec = (
        np.cos(angular_distance) * np.sin(center_dec_radians)
        + rotated_north * np.sin(angular_distance) * np.cos(center_dec_radians) / safe_rho
    )
    dec = np.arcsin(sin_dec)
    ra = np.radians(center_ra) + np.arctan2(
        rotated_east * np.sin(angular_distance),
        rho * np.cos(center_dec_radians) * np.cos(angular_distance)
        - rotated_north * np.sin(center_dec_radians) * np.sin(angular_distance),
    )
    return np.degrees(ra), np.degrees(dec)


FIELDS = [
    (250.0, 36.0, 1.6, 1.6, 0.0),  # an ordinary 1.6 x 1.6 field
    (250.0, 36.0, 1.6, 1.6, 33.0),  # the same, rotated
    (120.0, -30.0, 3.0, 2.0, 25.0),  # a rotated 3 x 2 field in the south
    (0.2, 10.0, 3.0, 2.0, 70.0),  # across the RA 0/360 seam
    (359.9, -5.0, 1.6, 1.6, 15.0),  # across the seam from the other side
    (40.0, 88.5, 3.0, 2.0, 50.0),  # near the north pole
    (200.0, 89.6, 1.6, 1.6, 10.0),  # the pole is inside the field
    (10.0, -88.7, 3.0, 2.0, 120.0),  # near the south pole
]


@pytest.mark.parametrize("level", [6, 7, 8, 10])
@pytest.mark.parametrize(("center_ra", "center_dec", "width", "height", "rotation"), FIELDS)
def test_tiles_for_footprint_cover_every_point_inside_the_field(
    level: int, center_ra: float, center_dec: float, width: float, height: float, rotation: float
) -> None:
    """No random point in a rotated field is left in an unchosen tile."""
    corners = _field_corners(center_ra, center_dec, width, height, rotation)
    ra, dec = _random_points_in_field(center_ra, center_dec, width, height, rotation, 30_000)

    chosen = set(tiles_for_footprint(corners, 0.0, level))
    needed = set(healpix_pixels_of_points(level, ra, dec).tolist())

    assert needed <= chosen


def test_the_footprint_margin_adds_the_tiles_just_outside_the_field() -> None:
    """With a margin, every point within the margin is also covered."""
    center_ra, center_dec, width, height, rotation = 250.0, 36.0, 1.6, 1.6, 20.0
    corners = _field_corners(center_ra, center_dec, width, height, rotation)
    # A field 0.3 degrees bigger on every side lies wholly within 0.5
    # degrees of the first, even at its corners (0.3 * sqrt(2) = 0.42).
    big_ra, big_dec = _random_points_in_field(
        center_ra, center_dec, width + 0.6, height + 0.6, rotation, 30_000
    )

    with_margin = set(tiles_for_footprint(corners, 0.5, 7))
    without_margin = set(tiles_for_footprint(corners, 0.0, 7))
    needed = set(healpix_pixels_of_points(7, big_ra, big_dec).tolist())

    assert without_margin <= with_margin
    assert len(with_margin) > len(without_margin)
    assert needed <= with_margin


def test_a_footprint_is_inside_the_tiles_of_a_circle_that_holds_it() -> None:
    """A footprint chooses no tile that a circle holding it misses."""
    corners = _field_corners(250.0, 36.0, 1.6, 1.6, 0.0)
    footprint_tiles = set(tiles_for_footprint(corners, 0.0, 7))
    circle_tiles = set(tiles_for_cone(250.0, 36.0, 1.2, 7))

    assert footprint_tiles <= circle_tiles


def test_a_footprint_needs_three_corners_and_a_sensible_size() -> None:
    """Bad footprints raise instead of returning a wrong answer."""
    with pytest.raises(InvalidArgumentError, match="three corners"):
        tiles_for_footprint([(0.0, 0.0), (1.0, 1.0)], 0.0, 7)
    with pytest.raises(InvalidArgumentError, match="negative"):
        tiles_for_footprint([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], -1.0, 7)
    with pytest.raises(InvalidArgumentError, match="too large"):
        tiles_for_footprint([(0.0, 0.0), (120.0, 0.0), (240.0, 0.0)], 0.0, 4)
