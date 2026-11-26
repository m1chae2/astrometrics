"""Shared tools for keeping neighboring stars out of a star's measurement.

A star's sky level is read from pixels around it. A neighbor star that lies
in those pixels lifts the sky and makes the star read too faint. A neighbor
inside the star's circle adds light and makes it read too bright. This file
holds the pieces that the star-ring analysis, the star-profile fit, the
spectrum extractor and the aperture photometry share:

* `clipped_median_and_sigma`: a median and a noise taken after throwing out
  outlying pixels.
* `mask_radius_from_profile`, `moffat_profile` and `neighbor_mask`: how far
  around a bright neighbor its light still matters, and the pixels that
  covers.
* `NeighborField` and `nearest_neighbors`: a list of detected stars with a
  quick way to ask who lives near a point.

The functions here take their limits as arguments. Each caller passes its
own declared (tuned) values, so this file declares none.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from astrometricslib.utilities.robust_statistics import sigma_clipped_values

__all__ = [
    "NearestNeighbors",
    "NeighborField",
    "clipped_median_and_sigma",
    "mask_radius_from_profile",
    "moffat_profile",
    "nearest_neighbors",
    "neighbor_mask",
]

# The standard deviation of a Gaussian over its full width at half maximum:
# FWHM = 2 * sqrt(2 * ln 2) * sigma = 2.3548 * sigma.
_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))


def clipped_median_and_sigma(
    values: np.ndarray, *, minimum_count: int, maximum_passes: int, clip_sigma: float
) -> tuple[float, float, int] | None:
    """Find the median and noise of some pixels after clipping outliers.

    The clipping is the library's one loop, `sigma_clipped_values`: each
    pass drops every pixel farther than `clip_sigma` robust sigmas from the
    median, until nothing more is dropped. The noise is the standard
    deviation of the kept pixels.

    Parameters
    ----------
    values : `numpy.ndarray`
        The pixels, in ADU. Non-finite values are ignored.
    minimum_count : `int`
        The fewest finite pixels that give a result, and the fewest a pass
        may keep.
    maximum_passes : `int`
        The most clipping passes.
    clip_sigma : `float`
        How many robust sigmas from the median a pixel may lie and stay.

    Returns
    -------
    statistics : `tuple` [`float`, `float`, `int`] or `None`
        The median, the standard deviation of one kept pixel, and the number
        of pixels kept. `None` when fewer than `minimum_count` finite pixels
        exist.
    """
    pixels = sigma_clipped_values(
        values, minimum_count=minimum_count, maximum_passes=maximum_passes, clip_sigma=clip_sigma
    )
    if pixels is None:
        return None
    noise = float(np.std(pixels, ddof=1)) if pixels.size > 1 else 0.0
    return float(np.median(pixels)), noise, int(pixels.size)


def mask_radius_from_profile(
    envelope: np.ndarray, target_peak: float, neighbor_height: float, mask_level: float
) -> float:
    """Find how far from a neighbor its light still matters.

    The star's own profile tells how fast a star's light falls with
    distance. Scaled to the neighbor's height, it tells where the neighbor's
    light drops below `mask_level`. That distance is the mask radius.

    Parameters
    ----------
    envelope : `numpy.ndarray`
        The star's brightness above the sky at each whole-pixel distance
        from its center, never rising with distance.
    target_peak : `float`
        The star's peak above the sky, in ADU.
    neighbor_height : `float`
        The neighbor's height above the sky and above the star's own glow,
        in ADU. Must be positive.
    mask_level : `float`
        The brightness, in ADU, below which a neighbor's light is ignored.

    Returns
    -------
    radius : `float`
        The first distance, in pixels, where the scaled profile is below
        `mask_level`, or the largest distance in `envelope` when it never is.
    """
    threshold = mask_level * target_peak / neighbor_height
    below = np.flatnonzero(envelope < threshold)
    return float(below[0]) if below.size else float(len(envelope) - 1)


def moffat_profile(star_width_px: float, peak: float, length: int, beta: float) -> np.ndarray:
    """Give the brightness of a Moffat-shaped star at whole-pixel distances.

    A Moffat profile (a bell with wider wings than a Gaussian, the usual
    shape of a real star) is ``peak * (1 + (r / alpha)**2) ** -beta``. The
    width `alpha` is set so the full width at half maximum equals
    `star_width_px`.

    Parameters
    ----------
    star_width_px : `float`
        The star's full width at half maximum, in pixels.
    peak : `float`
        The star's peak above the sky, in ADU.
    length : `int`
        How many whole-pixel distances to give, starting at 0.
    beta : `float`
        The Moffat wing exponent. Larger means thinner wings.

    Returns
    -------
    envelope : `numpy.ndarray`
        The brightness at distances ``0 .. length - 1``, in ADU.
    """
    alpha = star_width_px / (2.0 * math.sqrt(2.0 ** (1.0 / beta) - 1.0))
    distance = np.arange(length, dtype=float)
    return peak * (1.0 + (distance / alpha) ** 2) ** -beta


@dataclass(frozen=True)
class NeighborField:
    """The detected stars of a frame, ready to be asked who is near a point.

    Parameters
    ----------
    positions : `numpy.ndarray`
        Shape ``(stars, 2)``: the 0-based column and row of each star.
    peaks : `numpy.ndarray`
        Each star's peak above the sky, in ADU.
    fluxes : `numpy.ndarray` or `None`, optional
        Each star's brightness from the finder, in any one unit. `None` when
        not known.
    """

    positions: np.ndarray
    peaks: np.ndarray
    fluxes: np.ndarray | None = None

    def __post_init__(self) -> None:
        """Build the search tree over the star positions."""
        object.__setattr__(self, "_tree", cKDTree(self.positions) if len(self.positions) else None)

    @classmethod
    def from_detections(cls, detections: Sequence[dict]) -> NeighborField:
        """Make a field from the finder's list of detections.

        Parameters
        ----------
        detections : `Sequence` [`dict`]
            Each entry holds ``x_centroid`` (or ``xcentroid``), ``y_centroid``
            (or ``ycentroid``), and, when known, ``peak`` and ``flux``. A
            missing peak is stored as `numpy.nan`, which keeps the star from
            masking anything.

        Returns
        -------
        field : `NeighborField`
            The stars, with 0-based positions as the finder gives them.
        """
        positions = np.array(
            [
                [
                    float(source.get("x_centroid", source.get("xcentroid"))),
                    float(source.get("y_centroid", source.get("ycentroid"))),
                ]
                for source in detections
            ],
            dtype=float,
        ).reshape(-1, 2)
        peaks = np.array([float(source.get("peak", math.nan)) for source in detections], dtype=float)
        fluxes = np.array([float(source.get("flux", math.nan)) for source in detections], dtype=float)
        return cls(positions, peaks, fluxes)

    def moved(self, new_positions: np.ndarray) -> NeighborField:
        """Give the same stars at new positions, such as in another frame.

        Parameters
        ----------
        new_positions : `numpy.ndarray`
            Shape ``(stars, 2)``, in the same order.

        Returns
        -------
        field : `NeighborField`
            A field with the same peaks and fluxes.
        """
        return NeighborField(new_positions, self.peaks, self.fluxes)

    def around(self, column: float, row: float, radius: float, exclude_radius: float = 0.0) -> np.ndarray:
        """Find the stars within a radius of a point.

        Parameters
        ----------
        column, row : `float`
            The point, 0-based.
        radius : `float`
            How far to look, in pixels.
        exclude_radius : `float`, optional
            Stars closer than this to the point are left out. They are the
            star at the point itself.

        Returns
        -------
        indices : `numpy.ndarray`
            The indices of the stars found, nearest first.
        """
        tree = getattr(self, "_tree")  # ruff: ignore[get-attr-with-constant] - set in __post_init__ on a frozen class
        if tree is None:
            return np.zeros(0, dtype=int)
        found = np.asarray(tree.query_ball_point([column, row], radius), dtype=int)
        if found.size == 0:
            return found
        distance = np.hypot(self.positions[found, 0] - column, self.positions[found, 1] - row)
        keep = distance >= exclude_radius
        found, distance = found[keep], distance[keep]
        return found[np.argsort(distance, kind="stable")]


@dataclass(frozen=True)
class NearestNeighbors:
    """For each star, its nearest other star and how many lie within a radius.

    Attributes
    ----------
    distance_px : `numpy.ndarray`
        The distance to the nearest other star within the radius, in pixels.
        `numpy.nan` when none lies within it.
    flux_ratio : `numpy.ndarray`
        That neighbor's flux divided by the star's own. `numpy.nan` when none
        lies within the radius or no fluxes were given.
    count : `numpy.ndarray`
        How many other stars lie within the radius.
    """

    distance_px: np.ndarray
    flux_ratio: np.ndarray
    count: np.ndarray


def nearest_neighbors(positions: np.ndarray, fluxes: np.ndarray | None, radius: float) -> NearestNeighbors:
    """Find each star's nearest other star within a radius.

    Parameters
    ----------
    positions : `numpy.ndarray`
        Shape ``(stars, 2)``, in pixels.
    fluxes : `numpy.ndarray` or `None`
        Each star's flux. `None` leaves the flux ratio as `numpy.nan`.
    radius : `float`
        How far to look, in pixels.

    Returns
    -------
    result : `NearestNeighbors`
        One entry per star.
    """
    count_stars = len(positions)
    distance = np.full(count_stars, np.nan)
    ratio = np.full(count_stars, np.nan)
    count = np.zeros(count_stars, dtype=int)
    if count_stars < 2:
        return NearestNeighbors(distance, ratio, count)
    tree = cKDTree(positions)
    count = np.asarray(tree.query_ball_point(positions, radius, return_length=True), dtype=int) - 1
    # The nearest other star is the second answer, the first being itself.
    second_distance, second_index = tree.query(positions, k=2)
    nearest_distance, nearest_index = second_distance[:, 1], second_index[:, 1]
    has_neighbor = nearest_distance <= radius
    distance[has_neighbor] = nearest_distance[has_neighbor]
    if fluxes is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio[has_neighbor] = fluxes[nearest_index[has_neighbor]] / fluxes[has_neighbor]
    return NearestNeighbors(distance, ratio, count)


def neighbor_mask(
    shape: tuple[int, int],
    center: tuple[float, float],
    neighbors: Sequence[tuple[float, float, float]],
    *,
    star_width_px: float,
    target_peak: float,
    sky_noise: float,
    minimum_signal_to_noise: float,
    mask_noise_multiple: float,
    beta: float,
    target_match_radius_px: float,
    offset: tuple[int, int] = (0, 0),
) -> tuple[np.ndarray, int]:
    """Mark the pixels lit by bright neighbors of a star.

    This is the rule of the star-ring analysis, taking its neighbors from a
    list of detections instead of running a finder. A neighbor counts when it
    stands at least `minimum_signal_to_noise` sky noise above the star's own
    glow at its position. Its mask radius is where the star's profile,
    scaled to the neighbor's height, falls below `mask_noise_multiple` sky
    noise. The star's profile is a Moffat shape of width `star_width_px`.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        The ``(rows, columns)`` of the window to mask.
    center : `tuple` [`float`, `float`]
        The star's 0-based ``(column, row)``, in image coordinates.
    neighbors : `Sequence` [`tuple` [`float`, `float`, `float`]]
        Each neighbor's 0-based ``(column, row)`` in image coordinates and its
        peak above the sky, in ADU. A neighbor with a non-finite peak is
        skipped.
    star_width_px : `float`
        The star's full width at half maximum, in pixels.
    target_peak : `float`
        The star's peak above the sky, in ADU.
    sky_noise : `float`
        The noise of one sky pixel, in ADU.
    minimum_signal_to_noise : `float`
        Smallest height above the star's glow, in sky noise, for a neighbor.
    mask_noise_multiple : `float`
        A neighbor is masked out to where its scaled profile falls below this
        many sky noise.
    beta : `float`
        The Moffat wing exponent of the star's profile.
    target_match_radius_px : `float`
        A detection this close to `center` is the star itself and is skipped.
    offset : `tuple` [`int`, `int`], optional
        The image ``(column, row)`` of the window's first pixel. Zero when
        the window is the whole image.

    Returns
    -------
    mask, count : `numpy.ndarray`, `int`
        `True` for each window pixel lit by a neighbor, and how many
        neighbors counted.
    """
    mask = np.zeros(shape, dtype=bool)
    if target_peak <= 0.0 or sky_noise <= 0.0 or not neighbors:
        return mask, 0
    center_column, center_row = center
    window_rows, window_columns = np.indices(shape, dtype=float)
    window_columns += offset[0]
    window_rows += offset[1]
    length = int(max(shape)) + 2
    envelope = moffat_profile(star_width_px, target_peak, length, beta)
    minimum_height = minimum_signal_to_noise * sky_noise
    mask_level = mask_noise_multiple * sky_noise
    counted = 0
    for column, row, peak in neighbors:
        if not math.isfinite(peak):
            continue
        distance = math.hypot(column - center_column, row - center_row)
        if distance <= target_match_radius_px:
            continue
        glow = float(np.interp(distance, np.arange(length), envelope))
        height = peak - glow
        if height < minimum_height:
            continue
        radius = mask_radius_from_profile(envelope, target_peak, height, mask_level)
        mask |= np.hypot(window_columns - column, window_rows - row) <= radius
        counted += 1
    return mask, counted
