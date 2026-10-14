"""Guess how two star lists are related by matching small triangles of stars.

Two stacks of the same field can be offset by thousands of pixels, turned by a
few degrees, or turned by 180 degrees (a German equatorial mount flips the
camera when the telescope crosses the meridian). Phase correlation cannot
find such a move, and star pairing needs a first guess that is already close.
This module makes the first guess without one.

The idea is the one used by asterism matchers such as `astroalign`. Every
triangle of three nearby stars has a shape that does not change when the
picture is shifted, turned, or enlarged: the ratios of its sides. A triangle
in one star list whose ratios equal those of a triangle in the other is a
likely match, and its three corners give a shift, a rotation and a scale.
Many wrong guesses come out too; the caller keeps the guess that lines up the
most stars (see `group_alignment.find_alignment_from_star_patterns`).

Everything here works on arrays of star positions and returns arrays.
"""

import numpy as np
from scipy.spatial import cKDTree

# Only the brightest stars of each list are used to build triangles. Stacks of
# different exposure times do not agree on which stars are brightest (a long
# exposure saturates its brightest stars, which are then skipped), so a short
# list misses the overlap. On the Nikon D5300 group pairs of M 106, NGC 7023,
# M 51 and NGC 6992 (2026-10-13), 60 stars found a correct match for none of
# the four, while 150 found one for all four (197 to 250 stars lined up).
PATTERN_STAR_COUNT = 150

# Each star forms triangles with pairs chosen from its nearest neighbours.
# Eight neighbours give 28 triangles per star (about 4,200 triangles for 150
# stars), plenty for the same triangle to appear in both lists even when a few
# stars are missing from one of them.
PATTERN_NEIGHBOUR_COUNT = 8

# Two triangles match when both side ratios agree to this much. Star centres
# are good to about 0.1 px; on a triangle whose shortest side is 20 px that is
# a ratio error of about 0.005 to 0.01. Looser than this gives many false
# matches; the caller checks every guess against all the stars, so a few
# false ones cost time and nothing else.
PATTERN_RATIO_TOLERANCE = 0.01

# Triangles with a longest side under this many pixels are skipped: their
# shape is dominated by the error in the star centres.
PATTERN_MINIMUM_LONGEST_SIDE_PIXELS = 20.0

# A guess that changes the scale by more than this fraction is dropped before
# it is checked. Both stacks come from the same telescope, and the largest
# scale change between nights seen so far is 0.14% (M 51, 2026-10-13); 10% is
# a very wide net for the noisy three-star estimate, and the caller applies a
# much tighter limit to the final fit.
PATTERN_MAXIMUM_SCALE_DEVIATION = 0.1


def _triangle_shapes(stars: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """List the triangles of nearby stars and the shape of each.

    Parameters
    ----------
    stars : `numpy.ndarray`
        Star positions, shape ``(n, 2)`` as (row, column), brightest first.

    Returns
    -------
    shapes : `numpy.ndarray`
        Shape ``(m, 2)``: the shortest and the middle side divided by the
        longest side of each triangle.
    corners : `numpy.ndarray`
        Shape ``(m, 3)``: the star index of each corner, ordered by the length
        of the side opposite it (shortest first), so corners of two triangles
        with the same shape pair up in the same order.
    positions : `numpy.ndarray`
        The star positions as complex numbers ``column + 1j * row``.
    """
    used = stars[:PATTERN_STAR_COUNT]
    positions = used[:, 1] + 1j * used[:, 0]
    neighbour_count = min(PATTERN_NEIGHBOUR_COUNT, len(used) - 1)
    if neighbour_count < 2:
        return np.empty((0, 2)), np.empty((0, 3), dtype=int), positions
    _, neighbours = cKDTree(used).query(used, k=neighbour_count + 1)
    shapes = []
    corners = []
    for star_index in range(len(used)):
        near = neighbours[star_index, 1:]
        for first in range(neighbour_count):
            for second in range(first + 1, neighbour_count):
                triple = (star_index, int(near[first]), int(near[second]))
                points = positions[list(triple)]
                # Side i is the one opposite corner i.
                sides = np.array([
                    abs(points[1] - points[2]),
                    abs(points[0] - points[2]),
                    abs(points[0] - points[1]),
                ])
                order = np.argsort(sides)
                shortest, middle, longest = sides[order]
                if longest < PATTERN_MINIMUM_LONGEST_SIDE_PIXELS or shortest <= 0.0:
                    continue
                shapes.append((shortest / longest, middle / longest))
                corners.append([triple[corner] for corner in order])
    return np.array(shapes).reshape(-1, 2), np.array(corners, dtype=int).reshape(-1, 3), positions


def candidate_similarity_transforms(reference_stars: np.ndarray, image_stars: np.ndarray) -> np.ndarray:
    """Propose moves that could put the image's stars onto the reference's.

    Parameters
    ----------
    reference_stars : `numpy.ndarray`
        The reference's star positions, shape ``(n, 2)`` as (row, column),
        brightest first.
    image_stars : `numpy.ndarray`
        The image's star positions, in the same form.

    Returns
    -------
    candidates : `numpy.ndarray`
        Shape ``(k, 2)`` and complex: each row is ``(a, b)`` such that
        ``reference = a * image + b`` for positions written as
        ``column + 1j * row``.
        ``angle(a)`` is the rotation and ``abs(a)`` the scale. Empty when
        there are too few stars to form triangles. Most candidates are wrong;
        every one must be checked against the full star lists.
    """
    reference_shapes, reference_corners, reference_positions = _triangle_shapes(reference_stars)
    image_shapes, image_corners, image_positions = _triangle_shapes(image_stars)
    if len(reference_shapes) == 0 or len(image_shapes) == 0:
        return np.empty((0, 2), dtype=complex)
    distances, partners = cKDTree(reference_shapes).query(
        image_shapes, distance_upper_bound=PATTERN_RATIO_TOLERANCE
    )
    candidates = []
    for image_triangle in np.nonzero(np.isfinite(distances))[0]:
        source = image_positions[image_corners[image_triangle]]
        target = reference_positions[reference_corners[partners[image_triangle]]]
        # Solve target = a * source + b for the three corner pairs.
        design = np.vstack([source, np.ones(3, dtype=complex)]).T
        (a, b), *_ = np.linalg.lstsq(design, target, rcond=None)
        if abs(abs(a) - 1.0) <= PATTERN_MAXIMUM_SCALE_DEVIATION:
            candidates.append((a, b))
    return np.array(candidates, dtype=complex).reshape(-1, 2)
