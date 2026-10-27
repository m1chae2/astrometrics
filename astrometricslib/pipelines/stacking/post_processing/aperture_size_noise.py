"""Measures how a stack's empty-aperture noise grows with the aperture size.

Pixels of independent noise add in quadrature: an aperture holding N pixels has
a noise that grows as the square root of N. Resampling in registration and
fixed patterns correlate neighboring pixels, and then the noise grows faster
with the aperture, up to as N for fully correlated pixels. Surveys such as the
Hubble deep fields measure this by placing empty apertures of several sizes and
fitting the noise against the pixel count as a power law. This module does the
same at the positions of the target's stored empty apertures:

1. Each position is measured at four radii, 0.5, 1, 1.5 and 2 times the noise
   aperture's radius (`SIZE_RADIUS_MULTIPLES`). The sky ring of each is 3 px
   beyond the aperture and 5 px wide, the gap and width of the noise aperture's
   own ring, so the 1 times radius is exactly the stack's noise aperture.
2. An aperture is used only when it stays clear of the stack's faint-source
   mask (stars included) at the largest radius, and has a usable sum at every
   radius. The same apertures are used for all radii, so the radii differ in
   size only.
3. The noise at each radius is the robust spread of the sums, as for the
   stack's noise. The slope of log(noise) against log(pixels in the aperture)
   is the stored exponent: 0.5 for independent pixels, 1.0 for fully
   correlated pixels.

The prediction is not changed by the exponent (decision D20 of the imaged-
target suggestions plan). The estimator and the numbers are declared in
`pipelines/stacking/metrics.py`.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np

from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    MINIMUM_USABLE_APERTURES,
    faint_source_distance,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    APERTURE_RADIUS_PX,
    MAD_TO_SIGMA,
    RING_INNER_PX,
    RING_OUTER_PX,
    signed_aperture_sum,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SIZE_RADIUS_MULTIPLES",
    "ApertureSizeNoise",
    "ApertureSizeNoiseOutcome",
    "measure_aperture_size_noise",
]

# The radii as multiples of the noise aperture's radius. Four radii from half
# to double the stack's own aperture span a factor of 16 in pixel count, enough
# to separate a slope of 0.5 from one of 1.0 with 100 apertures, while the
# largest ring (outer radius 16 px) still fits between the 24 px that separate
# apertures. Design estimate: no data set chose them.
SIZE_RADIUS_MULTIPLES = (0.5, 1.0, 1.5, 2.0)

# The sky ring of each radius starts this far beyond the aperture and is this
# wide: the gap and width of the noise aperture's own ring (7 - 4 = 3 px and
# 12 - 7 = 5 px), so the ring keeps its relation to the aperture.
_RING_GAP_PX = RING_INNER_PX - APERTURE_RADIUS_PX
_RING_WIDTH_PX = RING_OUTER_PX - RING_INNER_PX

# An aperture must be at least this far from every pixel of the faint-source
# mask: the largest ring's outer radius plus 1 px, the same reasoning as
# `empty_apertures.FAINT_SOURCE_CLEARANCE_PX` (the center is rounded to a
# pixel, which moves it by up to 0.71 px).
_CLEARANCE_PX = max(SIZE_RADIUS_MULTIPLES) * APERTURE_RADIUS_PX + _RING_GAP_PX + _RING_WIDTH_PX + 1.0

# The apertures are resampled (with replacement, the same draw for every
# radius) this many times to get the error of the slope. The error of a
# standard error from B resamples is 1 / sqrt(2 (B - 1)) of itself: 2.2% for
# 1000. The fixed seed makes the number repeat exactly.
_BOOTSTRAP_COUNT = 1000
_BOOTSTRAP_SEED = 20261026


@dataclass(frozen=True)
class ApertureSizeNoise:
    """The noise at several aperture sizes and the exponent fitted to it.

    Parameters
    ----------
    radii_px : `tuple` [`float`, ...]
        The aperture radii used.
    pixel_counts : `tuple` [`float`, ...]
        The area of each aperture, pi r^2, in pixels.
    noises : `tuple` [`float`, ...]
        The robust spread of the sums at each radius, in the stack's units.
    exponent : `float`
        The slope of log(noise) against log(pixel count). 0.5 is independent
        pixels, 1.0 fully correlated pixels.
    exponent_error : `float`
        Its 1-sigma uncertainty, from resampling the apertures.
    aperture_count : `int`
        How many apertures were used at every radius.
    """

    radii_px: tuple[float, ...]
    pixel_counts: tuple[float, ...]
    noises: tuple[float, ...]
    exponent: float
    exponent_error: float
    aperture_count: int

    def describe(self) -> str:
        """Say the noise at each radius in one sentence.

        Returns
        -------
        text : `str`
            The noise at each radius and the number of apertures used.
        """
        parts = ", ".join(
            f"{radius:g} px: {noise:.4g}" for radius, noise in zip(self.radii_px, self.noises, strict=True)
        )
        return (
            f"Noise (stack units) at aperture radius {parts}, in {self.aperture_count} apertures clear "
            "of faint sources at the largest radius."
        )


@dataclass(frozen=True)
class ApertureSizeNoiseOutcome:
    """The size dependence of the noise, or the reason there is none.

    Parameters
    ----------
    result : `ApertureSizeNoise` or `None`
        The numbers, or `None` when they could not be worked out.
    reason : `str`
        Why there is no result. Empty when there is one.
    """

    result: ApertureSizeNoise | None
    reason: str = ""


def _fitted_slope(log_pixels: np.ndarray, log_noise: np.ndarray) -> np.ndarray:
    """Fit straight lines to log noise against log pixel count.

    Parameters
    ----------
    log_pixels : `numpy.ndarray`
        Shape ``(k,)``: the log of the pixel count at each radius.
    log_noise : `numpy.ndarray`
        Shape ``(..., k)``: the log of the noise at each radius, for one fit
        or for many resamples.

    Returns
    -------
    slope : `numpy.ndarray`
        The least-squares slope of each fit, shape ``(...)``.
    """
    centered = log_pixels - log_pixels.mean()
    return (log_noise * centered).sum(axis=-1) / (centered**2).sum()


def measure_aperture_size_noise(
    plane: np.ndarray,
    pixel_positions: np.ndarray,
    source_distance: np.ndarray | None = None,
) -> ApertureSizeNoiseOutcome:
    """Measure the noise at several aperture sizes and fit its exponent.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    pixel_positions : `numpy.ndarray`
        Shape ``(n, 2)``: the stored apertures' (column, row) positions on this
        stack. A position that is not finite is left out.
    source_distance : `numpy.ndarray`, optional
        The stack's `faint_source_distance`, when the caller already has it.
        Found here when left out.

    Returns
    -------
    outcome : `ApertureSizeNoiseOutcome`
        The noise at each radius and the exponent, or the reason there are too
        few apertures clear of the mask.
    """
    if source_distance is None:
        source_distance = faint_source_distance(plane)
    radii = tuple(multiple * APERTURE_RADIUS_PX for multiple in SIZE_RADIUS_MULTIPLES)
    height, width = plane.shape
    sums: list[list[float]] = []
    for column, row in pixel_positions:
        if not (math.isfinite(column) and math.isfinite(row)):
            continue
        round_row, round_column = round(row), round(column)
        if not (0 <= round_row < height and 0 <= round_column < width):
            continue
        if source_distance[round_row, round_column] <= _CLEARANCE_PX:
            continue
        values = [
            signed_aperture_sum(
                plane,
                float(column),
                float(row),
                radius=radius,
                ring_inner=radius + _RING_GAP_PX,
                ring_outer=radius + _RING_GAP_PX + _RING_WIDTH_PX,
            )
            for radius in radii
        ]
        if any(value is None for value in values):
            continue
        sums.append([float(value) for value in values])
    if len(sums) < MINIMUM_USABLE_APERTURES:
        return ApertureSizeNoiseOutcome(
            None,
            f"only {len(sums)} apertures stay clear of faint sources and the edge at the largest radius; "
            f"{MINIMUM_USABLE_APERTURES} are needed",
        )
    table = np.asarray(sums)  # shape (n, radii)
    centers = np.median(table, axis=0)
    spreads = MAD_TO_SIGMA * np.median(np.abs(table - centers), axis=0)
    if not np.all(spreads > 0):
        return ApertureSizeNoiseOutcome(None, "the sums at one radius have no spread")
    pixel_counts = np.array([math.pi * radius**2 for radius in radii])
    log_pixels = np.log(pixel_counts)
    exponent = float(_fitted_slope(log_pixels, np.log(spreads)))
    draws = np.random.default_rng(_BOOTSTRAP_SEED).integers(
        0, table.shape[0], size=(_BOOTSTRAP_COUNT, table.shape[0])
    )
    resampled = table[draws]  # shape (B, n, radii)
    resampled_centers = np.median(resampled, axis=1, keepdims=True)
    resampled_spreads = MAD_TO_SIGMA * np.median(np.abs(resampled - resampled_centers), axis=1)
    valid = np.all(resampled_spreads > 0, axis=1)
    slopes = _fitted_slope(log_pixels, np.log(resampled_spreads[valid]))
    error = float(np.std(slopes, ddof=1)) if slopes.size > 1 else float("nan")
    return ApertureSizeNoiseOutcome(
        ApertureSizeNoise(
            radii_px=radii,
            pixel_counts=tuple(float(count) for count in pixel_counts),
            noises=tuple(float(value) for value in spreads),
            exponent=exponent,
            exponent_error=error,
            aperture_count=int(table.shape[0]),
        )
    )
