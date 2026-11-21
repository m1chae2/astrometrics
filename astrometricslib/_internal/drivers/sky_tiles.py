"""Purpose: Cut the sky into numbered tiles and find the tiles a region needs.

Description: Gaia DR3 gives every star an ID number that already says where
the star is. This file turns that into a small toolkit: the number range
that holds all the stars of one sky tile, the tile a position falls in, the
tiles a circle or an image footprint touches, and the ADQL query (the Gaia
archive's SQL dialect) that asks for one tile.

How the tiles work
------------------
The tiles come from HEALPix, a way to cut a sphere into pieces of equal
area. First the sphere is cut into 12 big faces. Each face is then cut into
four equal pieces, each of those into four more, and so on. The number of
cuts is the "level": level L has 12 * 4**L tiles. "Nested" numbering means
the four children of a tile are numbered next to each other, so the parent's
number is the child's number with its last two binary digits removed.

Gaia builds each source_id so that ``source_id // 2**35`` is the star's
level-12 tile. The tile at a coarser level k is that number shifted right by
``2 * (12 - k)`` more binary digits, so a source_id shifted right by
``35 + 2 * (12 - k)`` is its level-k tile. All stars of one tile therefore
have source IDs in one unbroken range, which the archive answers quickly.
"""

import math
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from astrometricslib.exceptions import InvalidArgumentError

__all__ = [
    "build_pixel_query",
    "healpix_pixels_of_points",
    "pixel_source_id_range",
    "pixels_near_circles",
    "tile_of_source_id",
    "tiles_for_cone",
    "tiles_for_footprint",
]

# Gaia builds a star's ID from its position: ID // 2**35 is the star's
# HEALPix pixel at level 12 (nested numbering), so the pixel at a coarser
# level is that number with its last 2 binary digits per level removed.
# Gaia DR3 documentation, and consistent with the largest DR3 source ID
# (6917528997577384320 = 12 * 2**59).
_SOURCE_ID_PIXEL_SHIFT = 35
_GAIA_HEALPIX_LEVEL = 12


def pixel_source_id_range(healpix_level: int, pixel: int) -> tuple[int, int]:
    """Find the range of Gaia source IDs that lie in one chunk of the sky.

    Parameters
    ----------
    healpix_level : `int`
        How finely the sky is cut: level ``L`` has ``12 * 4**L`` pixels.
        Must be from 0 to 12.
    pixel : `int`
        The pixel's number at that level.

    Returns
    -------
    low, high : `tuple` [`int`, `int`]
        Every star in the pixel has ``low <= source_id < high``.

    Raises
    ------
    InvalidArgumentError
        If the level or pixel number is out of range.
    """
    if not 0 <= healpix_level <= _GAIA_HEALPIX_LEVEL:
        raise InvalidArgumentError(
            f"HEALPix level must be from 0 to {_GAIA_HEALPIX_LEVEL}, not {healpix_level}."
        )
    pixel_count = 12 * 4**healpix_level
    if not 0 <= pixel < pixel_count:
        raise InvalidArgumentError(f"Pixel {pixel} is outside 0..{pixel_count - 1} at level {healpix_level}.")
    shift = _SOURCE_ID_PIXEL_SHIFT + 2 * (_GAIA_HEALPIX_LEVEL - healpix_level)
    return pixel << shift, (pixel + 1) << shift


def _spread_bits(values: np.ndarray) -> np.ndarray:
    """Move every binary digit of each number to twice its place.

    Bit 0 goes to bit 0, bit 1 to bit 2, bit 2 to bit 4, and so on, leaving
    the odd places empty. HEALPix needs this to weave the two coordinates of
    a pixel inside its face into a single number.

    Parameters
    ----------
    values : `numpy.ndarray`
        Non-negative whole numbers below 2**32.

    Returns
    -------
    spread : `numpy.ndarray`
        The same numbers with their digits spread out, as 64-bit integers.
    """
    spread = values.astype(np.uint64)
    for shift, mask in (
        (16, 0x0000FFFF0000FFFF),
        (8, 0x00FF00FF00FF00FF),
        (4, 0x0F0F0F0F0F0F0F0F),
        (2, 0x3333333333333333),
        (1, 0x5555555555555555),
    ):
        spread = (spread | (spread << np.uint64(shift))) & np.uint64(mask)
    return spread


def healpix_pixels_of_points(healpix_level: int, ra_degrees: Any, dec_degrees: Any) -> np.ndarray:
    """Find which chunk of the sky each point is in.

    This is the standard HEALPix "nested" numbering that the Gaia archive
    uses, worked out here so no extra software is needed. The sphere is cut
    into 12 big faces, and each face is cut again and again into four equal
    pieces. All pixels at one level have exactly the same area.

    It was checked against 149,460 real Gaia stars: the pixel found here is
    the pixel in the star's own ID for 99.9993 percent of them at level 4.
    The rest sit within a few arcseconds of a pixel edge, where the ID was
    made from an earlier, slightly different position than the DR3 one.

    Parameters
    ----------
    healpix_level : `int`
        How finely the sky is cut: level ``L`` has ``12 * 4**L`` pixels.
    ra_degrees, dec_degrees : `float` or `numpy.ndarray`
        Position of each point, in degrees.

    Returns
    -------
    pixels : `numpy.ndarray`
        The pixel number of each point, as 64-bit integers.

    Raises
    ------
    InvalidArgumentError
        If the level is out of range.
    """
    if not 0 <= healpix_level <= _GAIA_HEALPIX_LEVEL:
        raise InvalidArgumentError(
            f"HEALPix level must be from 0 to {_GAIA_HEALPIX_LEVEL}, not {healpix_level}."
        )
    side = 2**healpix_level  # pixels along one edge of a face
    sin_dec = np.sin(np.radians(np.asarray(dec_degrees, dtype=float)))
    abs_sin_dec = np.abs(sin_dec)
    # How far around the sky the point is, in units of a quarter turn.
    quarter_turns = np.mod(np.radians(np.asarray(ra_degrees, dtype=float)), 2 * np.pi) / (np.pi / 2)

    # Near the equator (|sin(dec)| up to 2/3) the faces are four-sided
    # diamonds, and two families of diagonal lines pick out the pixel.
    rising = side * (0.5 + quarter_turns)
    falling = side * sin_dec * 0.75
    rising_line = np.floor(rising - falling).astype(np.int64)
    falling_line = np.floor(rising + falling).astype(np.int64)
    rising_face = rising_line // side
    falling_face = falling_line // side
    equator_face = np.where(
        rising_face == falling_face,
        rising_face | 4,
        np.where(rising_face < falling_face, rising_face, falling_face + 8),
    )
    equator_x = falling_line & (side - 1)
    equator_y = side - (rising_line & (side - 1)) - 1

    # Near the poles the four faces around the pole meet in a point.
    turn_number = np.minimum(np.floor(quarter_turns).astype(np.int64), 3)
    turn_fraction = quarter_turns - turn_number
    pole_scale = side * np.sqrt(3.0 * (1.0 - abs_sin_dec))
    line_a = np.minimum(np.floor(turn_fraction * pole_scale).astype(np.int64), side - 1)
    line_b = np.minimum(np.floor((1.0 - turn_fraction) * pole_scale).astype(np.int64), side - 1)
    is_north = sin_dec >= 0
    pole_face = np.where(is_north, turn_number, turn_number + 8)
    pole_x = np.where(is_north, side - line_b - 1, line_a)
    pole_y = np.where(is_north, side - line_a - 1, line_b)

    is_equatorial = abs_sin_dec <= 2.0 / 3.0
    face = np.where(is_equatorial, equator_face, pole_face)
    x_in_face = np.where(is_equatorial, equator_x, pole_x)
    y_in_face = np.where(is_equatorial, equator_y, pole_y)
    within_face = _spread_bits(x_in_face) | (_spread_bits(y_in_face) << np.uint64(1))
    return (face.astype(np.uint64) * np.uint64(side * side) + within_face).astype(np.int64)


# How many sample points are laid across the width of one pixel when working
# out which pixels a circle touches. The circle is padded by one spacing and
# sampled at this spacing, so only a pixel that grazes the circle by less than
# about a spacing can be missed. At level 4 a pixel is about 3.7 degrees
# wide, so this is about 0.46 degrees. Chosen by reasoning, not measured; the
# tests check that no point inside a circle is left in a pixel that is not
# chosen.
_SAMPLES_PER_PIXEL_WIDTH = 8

# The same, for a footprint. A footprint covers an area, not just a rim, so a
# tile corner can poke into its inside; the grid is twice as fine as for a
# circle so such a tip is caught unless it is under a sixteenth of a tile
# wide. Chosen by reasoning, not measured; the tests check that no random
# point inside a field is left in an unchosen tile.
_FOOTPRINT_SAMPLES_PER_PIXEL_WIDTH = 16

# The most grid points worked on at once, to keep memory use small.
_MAXIMUM_POINTS_PER_BLOCK = 500_000


def pixels_near_circles(healpix_level: int, circles: Iterable[tuple[float, float, float]]) -> list[int]:
    """List the chunks of sky that hold any part of some circles.

    This lets the catalog be built only where it is needed (for example
    around the fields that have been imaged) instead of for the whole sky.
    It lays a fine grid of points over each circle and looks up the pixel
    of each one.

    Parameters
    ----------
    healpix_level : `int`
        How finely the sky is cut into chunks.
    circles : `iterable` [`tuple` [`float`, `float`, `float`]]
        Each circle as ``(ra, dec, radius)``, all in degrees.

    Returns
    -------
    pixels : `list` [`int`]
        The chunk numbers, smallest first, without repeats. A chunk that
        only just grazes a circle (by less than about half a degree at
        level 4) may be left out.
    """
    pixel_width_degrees = math.degrees(math.sqrt(4 * math.pi / (12 * 4**healpix_level)))
    spacing = pixel_width_degrees / _SAMPLES_PER_PIXEL_WIDTH
    chosen: set[int] = set()
    for center_ra, center_dec, radius in circles:
        search_radius = min(radius + spacing, 180.0)
        ring_radii = np.append(np.arange(0.0, search_radius, spacing), search_radius)
        center_dec_radians = math.radians(center_dec)
        sample_ras: list[np.ndarray] = []
        sample_decs: list[np.ndarray] = []
        for ring_radius in ring_radii:
            ring_radius_radians = math.radians(ring_radius)
            # Points on a ring get further apart as it grows, so bigger
            # rings need more points to keep the same spacing.
            # A ring of radius zero is just the center: one point.
            points_on_ring = math.ceil(360.0 * math.sin(ring_radius_radians) / spacing) + 1
            position_angles = np.linspace(0.0, 2 * np.pi, points_on_ring, endpoint=False)
            sin_dec = math.sin(center_dec_radians) * math.cos(ring_radius_radians) + math.cos(
                center_dec_radians
            ) * math.sin(ring_radius_radians) * np.cos(position_angles)
            sample_dec_radians = np.arcsin(np.clip(sin_dec, -1.0, 1.0))
            ra_offset = np.arctan2(
                np.sin(position_angles) * math.sin(ring_radius_radians) * math.cos(center_dec_radians),
                math.cos(ring_radius_radians) - math.sin(center_dec_radians) * np.sin(sample_dec_radians),
            )
            sample_ras.append(center_ra + np.degrees(ra_offset))
            sample_decs.append(np.degrees(sample_dec_radians))
        pixels = healpix_pixels_of_points(
            healpix_level, np.concatenate(sample_ras), np.concatenate(sample_decs)
        )
        chosen.update(int(pixel) for pixel in np.unique(pixels))
    return sorted(chosen)


def build_pixel_query(low: int, high: int, magnitude_limit: float, count_only: bool = False) -> str:
    """Write the ADQL query that asks the archive for one chunk of sky.

    Parameters
    ----------
    low, high : `int`
        The range of source IDs wanted, from `pixel_source_id_range`.
    magnitude_limit : `float`
        Only stars brighter than this Gaia G magnitude are wanted.
    count_only : `bool`, optional
        If true, ask only how many stars there are, not for the stars.

    Returns
    -------
    query : `str`
        The ADQL text to send.
    """
    columns = "COUNT(*) AS star_count" if count_only else "source_id, ra, dec, phot_g_mean_mag"
    # This is ADQL for the remote archive, not SQL for a local database. The
    # only values put into the text are whole numbers and a magnitude limit
    # (all numbers), and the column names come from the fixed strings above.
    return (
        f"SELECT {columns} FROM gaiadr3.gaia_source "  # ruff: ignore[hardcoded-sql-expression]
        f"WHERE source_id >= {low} AND source_id < {high} "
        f"AND phot_g_mean_mag < {magnitude_limit}"
    )


def tile_of_source_id(source_id: Any, healpix_level: int) -> Any:
    """Find the sky tile a Gaia source ID belongs to.

    Parameters
    ----------
    source_id : `int` or `numpy.ndarray`
        One Gaia DR3 source ID, or an array of them.
    healpix_level : `int`
        How finely the sky is cut: level ``L`` has ``12 * 4**L`` tiles.
        Must be from 0 to 12.

    Returns
    -------
    tile : `int` or `numpy.ndarray`
        The tile number at that level, the same shape as the input
        (a plain `int` for one ID).

    Raises
    ------
    InvalidArgumentError
        If the level is out of range.
    """
    if not 0 <= healpix_level <= _GAIA_HEALPIX_LEVEL:
        raise InvalidArgumentError(
            f"HEALPix level must be from 0 to {_GAIA_HEALPIX_LEVEL}, not {healpix_level}."
        )
    shift = _SOURCE_ID_PIXEL_SHIFT + 2 * (_GAIA_HEALPIX_LEVEL - healpix_level)
    tiles = np.asarray(source_id, dtype=np.int64) >> shift
    return int(tiles) if tiles.ndim == 0 else tiles


def tiles_for_cone(ra_deg: float, dec_deg: float, radius_deg: float, healpix_level: int) -> list[int]:
    """List the sky tiles that hold any part of one circle.

    This is `pixels_near_circles` for a single circle.

    Parameters
    ----------
    ra_deg, dec_deg : `float`
        Center of the circle, in degrees.
    radius_deg : `float`
        Radius of the circle, in degrees.
    healpix_level : `int`
        How finely the sky is cut into tiles.

    Returns
    -------
    tiles : `list` [`int`]
        The tile numbers, smallest first, without repeats.
    """
    return pixels_near_circles(healpix_level, [(ra_deg, dec_deg, radius_deg)])


def tiles_for_footprint(
    corners_ra_dec: Sequence[tuple[float, float]], margin_deg: float, healpix_level: int
) -> list[int]:
    """List the sky tiles that hold any part of an image footprint.

    The footprint is the four-sided (or more) shape on the sky that an image
    covers, given by its corners in order around the edge.

    Method: the corners are flattened onto a plane that touches the sky at
    the footprint's middle (a gnomonic projection, where straight lines on
    the plane are great circles on the sky). A grid of points is laid over
    the plane, spaced one sixteenth of a tile width apart. Every grid point
    that is inside the footprint, or within the margin of its edge, is
    mapped back to the sky and its tile is looked up. One extra grid spacing
    is added to the margin, so a tile is missed only if it is touched by
    less than about one sixteenth of its width. The corners themselves are
    always included.

    Parameters
    ----------
    corners_ra_dec : `sequence` [`tuple` [`float`, `float`]]
        The footprint corners as ``(ra, dec)`` in degrees, in order around
        the edge. At least three. The footprint must be smaller than about
        60 degrees across.
    margin_deg : `float`
        How far beyond the footprint edge to include, in degrees.
    healpix_level : `int`
        How finely the sky is cut into tiles.

    Returns
    -------
    tiles : `list` [`int`]
        The tile numbers, smallest first, without repeats.

    Raises
    ------
    InvalidArgumentError
        If there are fewer than three corners, the margin is negative, or
        the footprint is too large to flatten.
    """
    if len(corners_ra_dec) < 3:
        raise InvalidArgumentError("A footprint needs at least three corners.")
    if margin_deg < 0:
        raise InvalidArgumentError(f"The margin must not be negative, not {margin_deg}.")
    corner_ras = np.radians([float(corner[0]) for corner in corners_ra_dec])
    corner_decs = np.radians([float(corner[1]) for corner in corners_ra_dec])
    corner_vectors = np.stack(
        [
            np.cos(corner_decs) * np.cos(corner_ras),
            np.cos(corner_decs) * np.sin(corner_ras),
            np.sin(corner_decs),
        ],
        axis=1,
    )
    middle = corner_vectors.sum(axis=0)
    if np.linalg.norm(middle) < 1e-9:
        raise InvalidArgumentError("The footprint is too large to flatten.")
    middle /= np.linalg.norm(middle)
    middle_dec = math.asin(float(np.clip(middle[2], -1.0, 1.0)))
    middle_ra = math.atan2(float(middle[1]), float(middle[0]))
    if np.any(corner_vectors @ middle < math.cos(math.radians(30.0))):
        raise InvalidArgumentError("The footprint is too large: it must be smaller than about 60 degrees.")

    def _flatten(ra_radians: np.ndarray, dec_radians: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Project sky positions onto the plane touching the sky at the middle.

        Parameters
        ----------
        ra_radians, dec_radians : `numpy.ndarray`
            Sky positions, in radians.

        Returns
        -------
        east, north : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
            Plane coordinates, in radians of tangent.
        """
        ra_offset = ra_radians - middle_ra
        cos_distance = math.sin(middle_dec) * np.sin(dec_radians) + math.cos(middle_dec) * np.cos(
            dec_radians
        ) * np.cos(ra_offset)
        east = np.cos(dec_radians) * np.sin(ra_offset) / cos_distance
        north = (
            math.cos(middle_dec) * np.sin(dec_radians)
            - math.sin(middle_dec) * np.cos(dec_radians) * np.cos(ra_offset)
        ) / cos_distance
        return east, north

    def _unflatten(east: np.ndarray, north: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Map plane points back onto the sky (the inverse of `_flatten`).

        Parameters
        ----------
        east, north : `numpy.ndarray`
            Plane coordinates, in radians of tangent.

        Returns
        -------
        ra, dec : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
            Sky positions, in degrees.
        """
        rho = np.hypot(east, north)
        angle = np.arctan(rho)
        safe_rho = np.where(rho > 0, rho, 1.0)
        sin_dec = np.cos(angle) * math.sin(middle_dec) + np.where(
            rho > 0, north * np.sin(angle) * math.cos(middle_dec) / safe_rho, 0.0
        )
        sky_ra = middle_ra + np.arctan2(
            east * np.sin(angle),
            rho * math.cos(middle_dec) * np.cos(angle) - north * math.sin(middle_dec) * np.sin(angle),
        )
        return np.degrees(sky_ra), np.degrees(np.arcsin(np.clip(sin_dec, -1.0, 1.0)))

    corner_east, corner_north = _flatten(corner_ras, corner_decs)
    pixel_width_degrees = math.degrees(math.sqrt(4 * math.pi / (12 * 4**healpix_level)))
    spacing = math.radians(pixel_width_degrees / _FOOTPRINT_SAMPLES_PER_PIXEL_WIDTH)
    reach = math.radians(margin_deg) + spacing

    east_values = np.arange(corner_east.min() - reach, corner_east.max() + reach + spacing, spacing)
    north_values = np.arange(corner_north.min() - reach, corner_north.max() + reach + spacing, spacing)

    def _tiles_of_rows(row_norths: np.ndarray) -> np.ndarray:
        """Find the tiles of the wanted grid points in some rows of the grid.

        Parameters
        ----------
        row_norths : `numpy.ndarray`
            The north coordinates of the grid rows to look at.

        Returns
        -------
        tiles : `numpy.ndarray`
            The distinct tile numbers found.
        """
        grid_east, grid_north = np.meshgrid(east_values, row_norths)
        grid_east = grid_east.ravel()
        grid_north = grid_north.ravel()

        # A grid point is wanted if it is inside the polygon (even-odd rule) or
        # within `reach` of one of its edges.
        inside = np.zeros(grid_east.shape, dtype=bool)
        near_edge = np.zeros(grid_east.shape, dtype=bool)
        for index in range(len(corner_east)):
            start_east, start_north = corner_east[index], corner_north[index]
            end_east = corner_east[(index + 1) % len(corner_east)]
            end_north = corner_north[(index + 1) % len(corner_north)]
            crosses = (start_north > grid_north) != (end_north > grid_north)
            if end_north != start_north:
                crossing_east = start_east + (grid_north - start_north) * (end_east - start_east) / (
                    end_north - start_north
                )
                inside ^= crosses & (grid_east < crossing_east)
            edge_east = end_east - start_east
            edge_north = end_north - start_north
            edge_length_squared = edge_east**2 + edge_north**2
            if edge_length_squared > 0:
                along = np.clip(
                    ((grid_east - start_east) * edge_east + (grid_north - start_north) * edge_north)
                    / edge_length_squared,
                    0.0,
                    1.0,
                )
            else:
                along = np.zeros(grid_east.shape)
            distance = np.hypot(
                grid_east - (start_east + along * edge_east), grid_north - (start_north + along * edge_north)
            )
            near_edge |= distance <= reach
        wanted = inside | near_edge
        wanted_east = grid_east[wanted]
        wanted_north = grid_north[wanted]

        sample_ras, sample_decs = _unflatten(wanted_east, wanted_north)
        return np.unique(healpix_pixels_of_points(healpix_level, sample_ras, sample_decs))

    # Work a block of rows at a time so a big field at a fine level does not
    # need millions of points in memory at once.
    rows_per_block = max(1, _MAXIMUM_POINTS_PER_BLOCK // len(east_values))
    corner_sky_ra, corner_sky_dec = _unflatten(corner_east, corner_north)
    tiles = {int(tile) for tile in healpix_pixels_of_points(healpix_level, corner_sky_ra, corner_sky_dec)}
    for first in range(0, len(north_values), rows_per_block):
        tiles.update(int(tile) for tile in _tiles_of_rows(north_values[first : first + rows_per_block]))
    return sorted(tiles)
