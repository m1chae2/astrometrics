"""Puts a picture of one stack onto the pixel grid of another stack.

The color picture of a monochrome stack (see `color_from_other_stack`) needs
the color stack's pixels to lie on the monochrome stack's pixels. The two
stacks come from different cameras or optics, so they differ in pixel size,
turn, framing and lens distortion. This module finds the move in two steps:

1. *First guess from the plate solutions.* Each stack carries a plate
   solution (WCS, the map from pixel to sky position). Every star found on
   the moving picture is turned into a sky position with its own plate
   solution, and that position into a pixel on the reference grid with the
   reference's plate solution. Two plate solutions agree to a few pixels, so
   each star lands near its partner.
2. *The shared shift.* Two plate solutions of a long lens can disagree by
   ten pixels or more, more than the pairing radius below. Every moved star
   votes for the shift to each reference star within a wide search radius;
   the true partners all vote for nearly the same shift, and chance
   neighbors spread their votes out. The most-voted shift is added to the
   guess.
3. *Refinement on the stars.* Each moved star is paired with the nearest
   reference star within a search radius. Only the moving picture's stars
   that land on the reference picture are used, and the fit is made about
   the middle of the shared area: a wide-field color picture often covers
   the reference picture off its own center, and the distortion correction
   needs pairs in all four quarters about the point it is made around. The
   shared registration engine (`image_registration.fit_transform`) fits a
   similarity (shift, turn and scale) plus a quadratic lens-distortion
   correction to the pairs and drops pairs that do not fit. The stars are
   then paired again with the fitted move and a much smaller radius, and the
   move is fitted once more.

Stars are found with the shared star finder (`star_finding`), after a coarse
sky background is taken away. The finder's star-shaped template is set to the
star width measured on the picture itself, because the same camera gives
stars twice as wide on a night of poor seeing, and a template half the
stars' width rejects them as not star-like. The registration engine's own
star finder is built for linear frames; on a stretched picture, where the
stars are compressed, it found too few stars.

The result says how well the two grids agree (the root-mean-square distance
between the paired stars after the move) and how much of the reference
picture the moved picture covers.
"""

from dataclasses import dataclass, replace

import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import gaussian_filter, maximum_filter, median_filter
from scipy.spatial import cKDTree

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.star_finding import find_sources_above_background
from astrometricslib.pipelines.stacking.post_processing.stack_image import robust_sigma
from astrometricslib.pipelines.stacking.processing.image_registration import (
    RegistrationTransform,
    fit_transform,
)

__all__ = [
    "ALIGNMENT_DETECTION_SIGMAS",
    "ALIGNMENT_PAIRING_RADII_PIXELS",
    "ALIGNMENT_SHIFT_SEARCH_PIXELS",
    "AlignmentFit",
    "align_with_stars",
    "find_bright_stars",
]

# How far above the sky noise a source must stand to be used for the
# alignment, in noise widths. Six keeps noise peaks and the knots of a nebula
# out and still found about 3,000 stars on the M 27 monochrome picture (ZWO
# ASI533MM, 405 mm) and 2,000 on the Nikon D5300 pictures (STK-62).
ALIGNMENT_DETECTION_SIGMAS = TunedParameter(
    name="stacking.color_preview_detection_sigmas",
    default=6.0,
    unit="noise widths",
    description="How far above the sky noise a star must stand to be used to align two stacks.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-62",
)

# The search radius for a star's partner, in reference pixels: first after the
# plate-solution guess, then after the first fitted move. The two plate
# solutions of M 27 put the Nikon stars a median of about 1 to 2 pixels from
# their ZWO partners, so 8 pixels finds every partner and few wrong ones. The
# fitted move leaves 0.4 to 0.5 pixels, so 2 pixels keeps every right pair
# and almost no wrong ones (STK-62).
ALIGNMENT_PAIRING_RADII_PIXELS = TunedParameter(
    name="stacking.color_preview_pairing_radii_pixels",
    default=(8.0, 2.0),
    unit="pixels",
    description="Search radius for a star's partner after the plate-solution guess and after the first fit.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# How far from its plate-solution guess a moved star looks for reference
# stars when it votes for the shared shift, in reference pixels. The plate
# solutions of the Nikon D5300 at 300 mm and the ZWO ASI533MM at 405 mm put
# the stars of M 45 about 10 pixels (dy -5, dx -9) from their partners, and
# those of M 81, M 101 and M 13 about 3 to 4 pixels; 40 pixels holds four
# times the worst shift seen, and the vote is not upset by the chance
# neighbors a wider circle takes in (STK-62).
ALIGNMENT_SHIFT_SEARCH_PIXELS = TunedParameter(
    name="stacking.color_preview_shift_search_pixels",
    default=40.0,
    unit="pixels",
    description="How far from its plate-solution guess a star looks for partners to vote on the shift.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The binning of the coarse sky background taken away before the star finder
# runs, and the width of its median filter in binned pixels: an 8 by 8 bin
# and a 9-bin median follow the glow of a nebula over about 70 pixels but not
# a star. Plumbing of the background estimate, not tuned.
_BACKGROUND_BIN = 8
_BACKGROUND_FILTER_BINS = 9

# Stars closer than this to the edge, in pixels, are not used: the stack's
# edge has fewer frames and its stars can be cut. Not tuned.
_EDGE_PIXELS = 30

# The star-width measurement: the smoothing before the peaks are found, the
# half size of the box around a peak, the most peaks measured, and the share
# of the picture's top value above which a peak counts as a burnt-out core
# and is not measured. Plumbing of a half-maximum width, not tuned.
_WIDTH_SMOOTHING_PIXELS = 1.0
_WIDTH_HALF_BOX = 10
_WIDTH_PEAKS = 300
_WIDTH_SATURATED_SHARE = 0.95

# The measured star width is held between these multiples of the expected
# width, so a picture with almost no stars (where the measurement would be
# of nebula knots) cannot pick an absurd template. A sanity guard, not tuned.
_WIDTH_LIMITS = (0.5, 3.0)

# The fewest pairs a fit may use, and the clipping of the registration
# engine: the same choices as the frame registration (see
# `image_registration`). Not tuned here.
_MINIMUM_PAIRS = 20
_CLIP_SIGMAS = 3.0
_CLIP_PASSES = 5


@dataclass(frozen=True)
class AlignmentFit:
    """How one picture was put onto another picture's grid.

    Attributes
    ----------
    transform : `RegistrationTransform`
        The move from the moving picture to the reference grid.
    moving_stars : `int`
        How many stars were found on the moving picture.
    pairs : `int`
        How many star pairs the last fit was given.
    kept_pairs : `int`
        How many of them the last fit used.
    plate_solution_offset_pixels : `float`
        The median distance between a star placed by the two plate solutions
        alone and its partner, in reference pixels.
    residual_pixels : `float`
        The root-mean-square distance between the kept pairs after the move.
    similarity_residual_pixels : `float`
        The same, after the similarity alone (no distortion correction).
    model : `str`
        ``"polynomial"`` or ``"similarity"``: what the move holds.
    moving_star_width_pixels : `float`
        The star width the finder's template used on the moving picture.
    plate_solution_shift_pixels : `tuple` [`float`, `float`]
        The shared shift, row and column, added to the plate-solution guess.
    """

    transform: RegistrationTransform
    moving_stars: int
    pairs: int
    kept_pairs: int
    plate_solution_offset_pixels: float
    residual_pixels: float
    similarity_residual_pixels: float
    model: str
    moving_star_width_pixels: float = 0.0
    plate_solution_shift_pixels: tuple[float, float] = (0.0, 0.0)

    def record(self) -> dict[str, float | int | str]:
        """Give the fit as plain numbers for a JSON record.

        Returns
        -------
        record : `dict`
            The counts, the residuals, the model, the scale and the turn.
        """
        return {
            "moving_stars": self.moving_stars,
            "pairs": self.pairs,
            "kept_pairs": self.kept_pairs,
            "plate_solution_offset_pixels": round(self.plate_solution_offset_pixels, 3),
            "residual_pixels": round(self.residual_pixels, 3),
            "similarity_residual_pixels": round(self.similarity_residual_pixels, 3),
            "model": self.model,
            "moving_star_width_pixels": round(self.moving_star_width_pixels, 2),
            "plate_solution_shift_pixels": [round(value, 2) for value in self.plate_solution_shift_pixels],
            "scale": round(float(self.transform.scale), 6),
            "rotation_degrees": round(float(self.transform.rotation_degrees), 4),
        }


def _remove_background(plane: np.ndarray) -> tuple[np.ndarray, float]:
    """Take a coarse sky background away from a picture.

    The background is the median of 8 by 8 bins, smoothed over 9 bins, so the
    glow of a nebula is not counted as stars.

    Parameters
    ----------
    plane : `numpy.ndarray`
        One 2-D picture, linear or stretched.

    Returns
    -------
    flat : `numpy.ndarray`
        The picture with the background taken away.
    noise : `float`
        The robust spread of what is left. Not finite or zero when the
        picture is blank.
    """
    plane = np.nan_to_num(np.asarray(plane, dtype=np.float32))
    binned = plane[::_BACKGROUND_BIN, ::_BACKGROUND_BIN]
    background = median_filter(binned, size=_BACKGROUND_FILTER_BINS)
    background = np.repeat(np.repeat(background, _BACKGROUND_BIN, 0), _BACKGROUND_BIN, 1)
    flat = plane - background[: plane.shape[0], : plane.shape[1]]
    return flat, float(robust_sigma(flat[::4, ::4].ravel()))


def measure_star_width(flat: np.ndarray, level: float, expected_fwhm_px: float) -> float:
    """Measure the typical star width on a picture with its background removed.

    The width is taken on the brightest peaks that are not burnt out. For
    each one, the pixels above half the peak's height inside a 21 by 21
    pixel box are counted, and the width is the diameter of a circle of that
    area (the full width at half maximum of a round star). The median over
    the peaks is the picture's star width. A stretched picture squeezes a
    star's core, so this is the width the star finder sees, not the
    optical width.

    Parameters
    ----------
    flat : `numpy.ndarray`
        The 2-D picture with its sky background taken away.
    level : `float`
        The least height of a peak, in the picture's units.
    expected_fwhm_px : `float`
        The expected star width, in pixels. It is returned when no peak can
        be measured, and the measured width is held between half and three
        times it.

    Returns
    -------
    width : `float`
        The star width to use for the finder's template, in pixels.
    """
    half = _WIDTH_HALF_BOX
    smoothed = gaussian_filter(flat, _WIDTH_SMOOTHING_PIXELS)
    is_peak = (smoothed == maximum_filter(smoothed, size=2 * half + 1)) & (smoothed > level)
    is_peak[: half + 1, :] = False
    is_peak[-half - 1 :, :] = False
    is_peak[:, : half + 1] = False
    is_peak[:, -half - 1 :] = False
    rows, columns = np.nonzero(is_peak)
    heights = flat[rows, columns]
    top = float(np.max(flat))
    unsaturated = heights < _WIDTH_SATURATED_SHARE * top
    rows, columns, heights = rows[unsaturated], columns[unsaturated], heights[unsaturated]
    order = np.argsort(heights)[::-1][:_WIDTH_PEAKS]
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1]
    inside_circle = yy**2 + xx**2 <= half**2
    widths = []
    for row, column in zip(rows[order], columns[order], strict=True):
        cut = flat[row - half : row + half + 1, column - half : column + half + 1]
        sky = float(np.median(cut[~inside_circle]))
        height = float(cut[half, half]) - sky
        if height <= 0.0:
            continue
        area = int(np.count_nonzero((cut - sky > 0.5 * height) & inside_circle))
        widths.append(2.0 * np.sqrt(area / np.pi))
    if not widths:
        return float(expected_fwhm_px)
    low, high = (factor * expected_fwhm_px for factor in _WIDTH_LIMITS)
    return float(np.clip(np.median(widths), low, high))


def _find_stars(plane: np.ndarray, count: int, expected_fwhm_px: float) -> tuple[np.ndarray, float]:
    """Find the brightest point sources on a picture, and the width used.

    Parameters
    ----------
    plane : `numpy.ndarray`
        One 2-D picture, linear or stretched.
    count : `int`
        The most stars to return.
    expected_fwhm_px : `float`
        The expected star width across its middle, in pixels: the starting
        guess for the measured width.

    Returns
    -------
    stars : `numpy.ndarray`
        Shape ``(n, 3)``: row, column and flux of each star, brightest first.
    width : `float`
        The star width the finder's template used, in pixels.
    """
    flat, noise = _remove_background(plane)
    if not np.isfinite(noise) or noise <= 0.0:
        return np.empty((0, 3)), float(expected_fwhm_px)
    threshold = float(resolve(ALIGNMENT_DETECTION_SIGMAS).value)
    width = measure_star_width(flat, threshold * noise, expected_fwhm_px)
    sources = find_sources_above_background(
        flat,
        noise=noise,
        expected_fwhm_px=width,
        threshold_sigma=threshold,
        roundness_range=None,
    )
    if not sources:
        return np.empty((0, 3)), width
    found = np.array([
        [float(source["y_centroid"]), float(source["x_centroid"]), float(source["flux"])]
        for source in sources
    ])
    rows, columns = found[:, 0], found[:, 1]
    inside = (
        (rows > _EDGE_PIXELS)
        & (rows < plane.shape[0] - _EDGE_PIXELS)
        & (columns > _EDGE_PIXELS)
        & (columns < plane.shape[1] - _EDGE_PIXELS)
    )
    found = found[inside]
    return found[np.argsort(found[:, 2])[::-1][:count]], width


def find_bright_stars(plane: np.ndarray, count: int, expected_fwhm_px: float) -> np.ndarray:
    """Find the brightest point sources on a picture.

    A coarse sky background (the median of 8 by 8 bins, smoothed over 9 bins)
    is taken away first, so the glow of a nebula is not counted as stars.
    The noise is the robust spread of what is left. The star finder's
    template is set to the star width measured on the picture (see
    `measure_star_width`).

    Parameters
    ----------
    plane : `numpy.ndarray`
        One 2-D picture, linear or stretched.
    count : `int`
        The most stars to return.
    expected_fwhm_px : `float`
        The expected star width across its middle, in pixels: the starting
        guess for the measured width.

    Returns
    -------
    stars : `numpy.ndarray`
        Shape ``(n, 3)``: row, column and flux of each star, brightest first.
        Stars within 30 pixels of the edge are left out. Empty when none was
        found.
    """
    return _find_stars(plane, count, expected_fwhm_px)[0]


def _plate_solution_shift(guess: np.ndarray, reference_points: np.ndarray, tree: cKDTree) -> np.ndarray:
    """Find the shift shared by the plate-solution guesses and their partners.

    Each guessed position votes for the shift to every reference star within
    the search radius (`ALIGNMENT_SHIFT_SEARCH_PIXELS`). The votes go into
    square cells half a pairing radius wide; the true partners pile up in
    one cell, and chance neighbors spread thinly over all of them. The shift
    is the median of the votes near the fullest cell.

    Parameters
    ----------
    guess : `numpy.ndarray`
        The moved stars on the reference grid, by the plate solutions alone,
        shape ``(n, 2)``: row and column.
    reference_points : `numpy.ndarray`
        The reference stars, shape ``(m, 2)``: row and column.
    tree : `scipy.spatial.cKDTree`
        A search tree over ``reference_points``.

    Returns
    -------
    shift : `numpy.ndarray`
        The row and column shift to add to the guesses. Zero when fewer than
        the fewest pairs a fit may use agree on a shift.
    """
    search_radius = float(resolve(ALIGNMENT_SHIFT_SEARCH_PIXELS).value)
    first_radius = float(resolve(ALIGNMENT_PAIRING_RADII_PIXELS).value[0])
    neighbors = tree.query_ball_point(guess, search_radius)
    votes = [
        reference_points[indices] - point for point, indices in zip(guess, neighbors, strict=True) if indices
    ]
    if not votes:
        return np.zeros(2)
    votes = np.concatenate(votes)
    cell = first_radius / 2.0
    edges = np.arange(-search_radius, search_radius + cell, cell)
    counts, row_edges, column_edges = np.histogram2d(votes[:, 0], votes[:, 1], bins=(edges, edges))
    peak_row, peak_column = np.unravel_index(np.argmax(counts), counts.shape)
    center = np.array([
        0.5 * (row_edges[peak_row] + row_edges[peak_row + 1]),
        0.5 * (column_edges[peak_column] + column_edges[peak_column + 1]),
    ])
    near = np.all(np.abs(votes - center) <= cell, axis=1)
    if near.sum() < _MINIMUM_PAIRS:
        return np.zeros(2)
    return np.median(votes[near], axis=0)


def _fit_about_center(
    reference_points: np.ndarray, moving_points: np.ndarray
) -> tuple[RegistrationTransform, np.ndarray, float, float, str] | None:
    """Fit the move about the middle of the paired moving stars.

    The registration engine turns and scales about the moving picture's
    center and wants pairs in all four quarters about it. When the shared
    area lies off that center, the stars are first given in a frame whose
    middle is the middle of the paired stars, and the fitted move is then
    written back in the moving picture's own pixels.

    Parameters
    ----------
    reference_points, moving_points : `numpy.ndarray`
        The paired positions, row and column, one row per pair.

    Returns
    -------
    fit : `tuple` or `None`
        The move in the moving picture's pixels, which pairs were kept, the
        residual after the move and after the similarity alone, and the
        model name; `None` when the fit failed.
    """
    corner = np.floor(moving_points.min(axis=0))
    extent = np.ceil(moving_points.max(axis=0) - corner) + 1.0
    fitted = fit_transform(
        reference_points,
        moving_points - corner,
        "polynomial",
        (int(extent[0]), int(extent[1])),
        minimum_pairs=_MINIMUM_PAIRS,
        clip_sigmas=_CLIP_SIGMAS,
        clip_passes=_CLIP_PASSES,
    )
    if fitted is None:
        return None
    local = fitted.transform
    distortion = local.distortion
    if distortion is not None:
        first_row, last_row, first_column, last_column = distortion.box
        distortion = replace(
            distortion,
            origin_row_pixels=distortion.origin_row_pixels + corner[0],
            origin_column_pixels=distortion.origin_column_pixels + corner[1],
            box=(
                first_row + corner[0],
                last_row + corner[0],
                first_column + corner[1],
                last_column + corner[1],
            ),
        )
    transform = replace(
        local,
        origin_row_pixels=local.origin_row_pixels + corner[0],
        origin_column_pixels=local.origin_column_pixels + corner[1],
        shift_rows_pixels=local.shift_rows_pixels - corner[0],
        shift_columns_pixels=local.shift_columns_pixels - corner[1],
        distortion=distortion,
    )
    return (
        transform,
        fitted.kept,
        float(fitted.residual_pixels),
        float(fitted.similarity_residual_pixels),
        fitted.model,
    )


def align_with_stars(
    reference_stars: np.ndarray,
    reference_wcs: WCS,
    moving_plane: np.ndarray,
    moving_wcs: WCS,
    expected_fwhm_px: float,
    star_count: int = 2000,
) -> AlignmentFit | None:
    """Fit the move from a picture onto the reference grid.

    Parameters
    ----------
    reference_stars : `numpy.ndarray`
        The reference picture's stars, shape ``(n, 2)`` or more columns: row
        and column first (see `find_bright_stars`).
    reference_wcs : `astropy.wcs.WCS`
        The reference picture's plate solution (celestial axes only).
    moving_plane : `numpy.ndarray`
        One 2-D plane of the picture to move, such as the green channel.
    moving_wcs : `astropy.wcs.WCS`
        The moving picture's plate solution (celestial axes only).
    expected_fwhm_px : `float`
        The expected star width on the moving picture, in its own pixels:
        the starting guess for the measured width.
    star_count : `int`, optional
        The most stars of the moving picture to use: the brightest of those
        that land on the reference picture.

    Returns
    -------
    fit : `AlignmentFit` or `None`
        The move and how well it fits, or `None` when too few stars could be
        paired. The caller decides whether the residual is good enough.
    """
    reference_points = np.asarray(reference_stars, dtype=np.float64)[:, :2]
    if len(reference_points) < _MINIMUM_PAIRS:
        return None
    # Every star is found first; the brightest of those that land on the
    # reference picture are used.
    moving_stars, moving_width = _find_stars(moving_plane, len(moving_plane.ravel()), expected_fwhm_px)
    moving_stars = moving_stars[:, :2]
    if len(moving_stars) < _MINIMUM_PAIRS:
        return None
    first_radius, second_radius = (float(value) for value in resolve(ALIGNMENT_PAIRING_RADII_PIXELS).value)
    sky = moving_wcs.pixel_to_world(moving_stars[:, 1], moving_stars[:, 0])
    guess_columns, guess_rows = reference_wcs.world_to_pixel(sky)
    guess = np.column_stack([guess_rows, guess_columns])
    margin = float(resolve(ALIGNMENT_SHIFT_SEARCH_PIXELS).value)
    low, high = reference_points.min(axis=0) - margin, reference_points.max(axis=0) + margin
    on_reference = np.all((guess >= low) & (guess <= high), axis=1)
    moving_stars, guess = moving_stars[on_reference][:star_count], guess[on_reference][:star_count]
    if len(moving_stars) < _MINIMUM_PAIRS:
        return None
    tree = cKDTree(reference_points)
    shift = _plate_solution_shift(guess, reference_points, tree)
    distance, index = tree.query(guess + shift, distance_upper_bound=first_radius)
    paired = np.isfinite(distance)
    if paired.sum() < _MINIMUM_PAIRS:
        return None
    plate_solution_offset = float(np.median(np.hypot(*(reference_points[index[paired]] - guess[paired]).T)))
    first = _fit_about_center(reference_points[index[paired]], moving_stars[paired])
    if first is None:
        return None
    mapped = np.column_stack(first[0].map_points(moving_stars[:, 0], moving_stars[:, 1]))
    distance, index = tree.query(mapped, distance_upper_bound=second_radius)
    paired = np.isfinite(distance)
    if paired.sum() < _MINIMUM_PAIRS:
        return None
    second = _fit_about_center(reference_points[index[paired]], moving_stars[paired])
    if second is None:
        return None
    transform, kept, residual, similarity_residual, model = second
    return AlignmentFit(
        transform=transform,
        moving_stars=len(moving_stars),
        pairs=int(paired.sum()),
        kept_pairs=int(np.sum(kept)),
        plate_solution_offset_pixels=plate_solution_offset,
        residual_pixels=residual,
        similarity_residual_pixels=similarity_residual,
        model=model,
        moving_star_width_pixels=moving_width,
        plate_solution_shift_pixels=(float(shift[0]), float(shift[1])),
    )
