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
2. *Refinement on the stars.* Each moved star is paired with the nearest
   reference star within a search radius. The shared registration engine
   (`image_registration.fit_transform`) fits a similarity (shift, turn and
   scale) plus a quadratic lens-distortion correction to the pairs and drops
   pairs that do not fit. The stars are then paired again with the fitted
   move and a much smaller radius, and the move is fitted once more.

Stars are found with the shared star finder (`star_finding`), after a coarse
sky background is taken away. The registration engine's own star finder is
built for linear frames; on a stretched picture, where the stars are
compressed, it found too few stars.

The result says how well the two grids agree (the root-mean-square distance
between the paired stars after the move) and how much of the reference
picture the moved picture covers.
"""

from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import median_filter
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

# The binning of the coarse sky background taken away before the star finder
# runs, and the width of its median filter in binned pixels: an 8 by 8 bin
# and a 9-bin median follow the glow of a nebula over about 70 pixels but not
# a star. Plumbing of the background estimate, not tuned.
_BACKGROUND_BIN = 8
_BACKGROUND_FILTER_BINS = 9

# Stars closer than this to the edge, in pixels, are not used: the stack's
# edge has fewer frames and its stars can be cut. Not tuned.
_EDGE_PIXELS = 30

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
    """

    transform: RegistrationTransform
    moving_stars: int
    pairs: int
    kept_pairs: int
    plate_solution_offset_pixels: float
    residual_pixels: float
    similarity_residual_pixels: float
    model: str

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
            "scale": round(float(self.transform.scale), 6),
            "rotation_degrees": round(float(self.transform.rotation_degrees), 4),
        }


def find_bright_stars(plane: np.ndarray, count: int, expected_fwhm_px: float) -> np.ndarray:
    """Find the brightest point sources on a picture.

    A coarse sky background (the median of 8 by 8 bins, smoothed over 9 bins)
    is taken away first, so the glow of a nebula is not counted as stars.
    The noise is the robust spread of what is left.

    Parameters
    ----------
    plane : `numpy.ndarray`
        One 2-D picture, linear or stretched.
    count : `int`
        The most stars to return.
    expected_fwhm_px : `float`
        The expected star width across its middle, in pixels.

    Returns
    -------
    stars : `numpy.ndarray`
        Shape ``(n, 3)``: row, column and flux of each star, brightest first.
        Stars within 30 pixels of the edge are left out. Empty when none was
        found.
    """
    plane = np.nan_to_num(np.asarray(plane, dtype=np.float32))
    binned = plane[::_BACKGROUND_BIN, ::_BACKGROUND_BIN]
    background = median_filter(binned, size=_BACKGROUND_FILTER_BINS)
    background = np.repeat(np.repeat(background, _BACKGROUND_BIN, 0), _BACKGROUND_BIN, 1)
    flat = plane - background[: plane.shape[0], : plane.shape[1]]
    noise = float(robust_sigma(flat[::4, ::4].ravel()))
    if not np.isfinite(noise) or noise <= 0.0:
        return np.empty((0, 3))
    sources = find_sources_above_background(
        flat,
        noise=noise,
        expected_fwhm_px=expected_fwhm_px,
        threshold_sigma=float(resolve(ALIGNMENT_DETECTION_SIGMAS).value),
        roundness_range=None,
    )
    if not sources:
        return np.empty((0, 3))
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
    return found[np.argsort(found[:, 2])[::-1][:count]]


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
        The expected star width on the moving picture, in its own pixels.
    star_count : `int`, optional
        The most stars to find on the moving picture.

    Returns
    -------
    fit : `AlignmentFit` or `None`
        The move and how well it fits, or `None` when too few stars could be
        paired. The caller decides whether the residual is good enough.
    """
    reference_points = np.asarray(reference_stars, dtype=np.float64)[:, :2]
    if len(reference_points) < _MINIMUM_PAIRS:
        return None
    moving_stars = find_bright_stars(moving_plane, star_count, expected_fwhm_px)[:, :2]
    if len(moving_stars) < _MINIMUM_PAIRS:
        return None
    first_radius, second_radius = (float(value) for value in resolve(ALIGNMENT_PAIRING_RADII_PIXELS).value)
    sky = moving_wcs.pixel_to_world(moving_stars[:, 1], moving_stars[:, 0])
    guess_columns, guess_rows = reference_wcs.world_to_pixel(sky)
    guess = np.column_stack([guess_rows, guess_columns])
    tree = cKDTree(reference_points)
    distance, index = tree.query(guess, distance_upper_bound=first_radius)
    paired = np.isfinite(distance)
    if paired.sum() < _MINIMUM_PAIRS:
        return None
    plate_solution_offset = float(np.median(distance[paired]))
    first = fit_transform(
        reference_points[index[paired]],
        moving_stars[paired],
        "polynomial",
        moving_plane.shape,
        minimum_pairs=_MINIMUM_PAIRS,
        clip_sigmas=_CLIP_SIGMAS,
        clip_passes=_CLIP_PASSES,
    )
    if first is None:
        return None
    mapped = np.column_stack(first.transform.map_points(moving_stars[:, 0], moving_stars[:, 1]))
    distance, index = tree.query(mapped, distance_upper_bound=second_radius)
    paired = np.isfinite(distance)
    if paired.sum() < _MINIMUM_PAIRS:
        return None
    second = fit_transform(
        reference_points[index[paired]],
        moving_stars[paired],
        "polynomial",
        moving_plane.shape,
        minimum_pairs=_MINIMUM_PAIRS,
        clip_sigmas=_CLIP_SIGMAS,
        clip_passes=_CLIP_PASSES,
    )
    if second is None:
        return None
    return AlignmentFit(
        transform=second.transform,
        moving_stars=len(moving_stars),
        pairs=int(paired.sum()),
        kept_pairs=int(np.sum(second.kept)),
        plate_solution_offset_pixels=plate_solution_offset,
        residual_pixels=float(second.residual_pixels),
        similarity_residual_pixels=float(second.similarity_residual_pixels),
        model=second.model,
    )
