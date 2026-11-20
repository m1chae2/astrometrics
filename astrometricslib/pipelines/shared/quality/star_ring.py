"""Looking at the light in a ring around one star, for one-sided flares.

A bright star can have faint rays or a one-sided glow far outside its core.
This module measures them. It takes the pixels around a star, whose sky is
already known, and does four things:

1. It sizes a ring from the pixels themselves. The mean brightness is taken
   in one-pixel-wide annuli (rings) out from the star. The ring starts where
   that mean falls to a small fraction of the peak, so the saturated or
   noisy core is left out. It ends a margin beyond where the mean falls to a
   small multiple of the sky noise, so the ring reaches the faint glow.
2. It leaves out neighbor stars. A star finder (passed in by the caller, so
   this folder does not import a pipeline) lists the other stars near the
   ring. Each neighbor is masked out to the distance where the target's own
   profile, scaled to the neighbor's brightness, falls below the sky noise.
   A brighter neighbor gets a bigger mask.
3. It divides each ring pixel by the mean brightness at its distance, so the
   steep fall-off with distance does not hide a faint pattern, and fits a
   Fourier series in the direction angle by linear least squares.
   Least squares works when the mask leaves gaps. Each order ``m`` is a
   pattern with ``m`` lobes: 1 is one-sided, 2 is oval, 3 is three-fold.
4. It averages the unmasked pixels in 5 degree direction bins, for plotting
   with the same pixels that were fitted.

The direction angle is the one the star profile cut uses: measured from the
+y axis (increasing row) toward the +x axis (increasing column), so ``sin``
of the angle runs along x and ``cos`` along y. Inside the code, positions
are 0-based array indices; the neighbors are converted to 1-based pixels at
the end.

The module reads pixel data that is already loaded and changes nothing.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.star_profile import RingHarmonic, RingNeighbor
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.neighbor_masking import mask_radius_from_profile

__all__ = [
    "STAR_RING_BIN_DEGREES",
    "RingAnalysis",
    "analyze_star_ring",
]

# The largest distance from the star that the ring work reads. The ring's
# outer radius, the annulus means and the neighbor search all stay inside it.
# 60 px is 2.5 times the 24 px outer radius that a clipped bright star on the
# M 31 stack needed, and the pixel box stays small (121 x 121), so a click
# is answered in a fraction of a second.
STAR_RING_MAXIMUM_RADIUS_PX = TunedParameter(
    name="shared.star_ring_maximum_radius_px",
    default=60.0,
    unit="pixels",
    description="Largest distance from a star that the ring radii and annulus means reach.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# The ring starts where the mean brightness falls below this fraction of the
# star's peak. At 1% a Moffat star has left its core, where the center
# estimate and clipping dominate, but it is still far brighter than a faint
# flare. It was found on a clipped star in the M 31 stack, where it gives 4 px.
STAR_RING_INNER_PEAK_FRACTION = TunedParameter(
    name="shared.star_ring_inner_peak_fraction",
    default=0.01,
    unit="fraction",
    description="The ring starts where the mean brightness falls below this fraction of the star's peak.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-17",
)

# The ring's far end is set from where the mean brightness falls to this
# many times the sky noise. Below 2 sigma the star's glow can no longer be
# told from the sky in one annulus.
STAR_RING_OUTER_NOISE_MULTIPLE = TunedParameter(
    name="shared.star_ring_outer_noise_multiple",
    default=2.0,
    unit="× sky noise",
    description="Mean brightness, in sky noise, at which the glow is taken to end for the ring.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-17",
)

# The outer radius is this many times the radius where the glow ends, so the
# ring includes the faint outskirts where a flare is still above the noise
# while the mean is not.
STAR_RING_OUTER_MARGIN_FACTOR = TunedParameter(
    name="shared.star_ring_outer_margin_factor",
    default=1.5,
    unit="× radius",
    description="The ring's outer radius is the radius where the glow ends times this factor.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# The ring has its own sky: a tilted plane fitted to the pixels from this
# fraction of the largest radius out to the largest radius. The profile's sky
# annulus sits just outside the profile radius, inside the glow of a bright
# star, and reads about 1.2 sky noise too high on the M 31 star. That would
# pull the ring's outer radius in. At 0.5 the annulus is 30 to 60 px, where
# the same glow is below 0.3 sky noise. The tilt removes a smooth sky
# gradient, which would otherwise look like a one-sided pattern.
STAR_RING_SKY_INNER_FRACTION = TunedParameter(
    name="shared.star_ring_sky_inner_fraction",
    default=0.5,
    unit="fraction",
    description="Where the ring's own sky annulus starts, as a fraction of the largest ring radius.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# The ring is never narrower than this, so the fit always has enough pixels.
# A band 4 px wide at a radius of 6 px holds about 150 pixels, three times
# the 17 numbers fitted.
STAR_RING_MINIMUM_WIDTH_PX = TunedParameter(
    name="shared.star_ring_minimum_width_px",
    default=4.0,
    unit="pixels",
    description="Narrowest the ring may be, from its inner to its outer radius.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# The ring never starts closer to the star than this. Closer in, an annulus
# holds fewer than about 12 pixels and the center error dominates.
STAR_RING_MINIMUM_INNER_PX = TunedParameter(
    name="shared.star_ring_minimum_inner_px",
    default=2.0,
    unit="pixels",
    description="Smallest inner radius of the ring.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# A star finder detection this close to the center is the target itself, not
# a neighbor. 3 px is about 1.3 FWHM on the reference setup, which holds the
# difference between the finder's center and the refined center.
STAR_RING_TARGET_MATCH_RADIUS_PX = TunedParameter(
    name="shared.star_ring_target_match_radius_px",
    default=3.0,
    unit="pixels",
    description="A detection this close to the star's center is the star itself, not a neighbor.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# A neighbor is masked where the star's own profile, scaled to the neighbor's
# brightness, is still above this many times the sky noise. At 1 sigma the
# neighbor's light is no larger than the noise of one pixel.
STAR_RING_NEIGHBOR_MASK_NOISE_MULTIPLE = TunedParameter(
    name="shared.star_ring_neighbor_mask_noise_multiple",
    default=1.0,
    unit="× sky noise",
    description="A neighbor is masked out to where its scaled profile falls below this many sky noise.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-17",
)

# How far beyond the ring's outer radius the star finder looks for neighbors.
# A bright neighbor's mask can reach that far into the ring. The finder runs
# on this box only, which is what keeps a click fast.
STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX = TunedParameter(
    name="shared.star_ring_neighbor_search_margin_px",
    default=40.0,
    unit="pixels",
    description="How far beyond the ring's outer radius the star finder searches for neighbors.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# Each pixel is divided by the mean brightness at its distance. Where that
# mean is below this many times the sky noise, the sky noise is used
# instead, so a pixel is never divided by a number close to zero (or below
# it) in the faint outskirts.
STAR_RING_NORMALIZER_FLOOR_NOISE = TunedParameter(
    name="shared.star_ring_normalizer_floor_noise",
    default=2.0,
    unit="× sky noise",
    description="Smallest divisor, in sky noise, for the pixels of the ring before the Fourier fit.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-17",
)

# The highest order of the Fourier fit. Order 8 has 8 lobes, 45 degrees
# each, which a ring of at least a few pixels across still samples. Higher
# orders describe pixel-scale structure the ring cannot resolve.
STAR_RING_HARMONIC_COUNT = TunedParameter(
    name="shared.star_ring_harmonic_count",
    default=8,
    unit="orders",
    description="The highest Fourier order fitted to the brightness around the ring.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# A term counts as significant when its amplitude is this many times its
# uncertainty. For noise alone the amplitude over its uncertainty follows a
# Rayleigh law, which exceeds 3 with probability exp(-4.5) = 1.1% per order,
# so about 9% of round stars show at least one false term out of eight.
STAR_RING_SIGNIFICANCE_THRESHOLD = TunedParameter(
    name="shared.star_ring_significance_threshold",
    default=3.0,
    unit="σ",
    description="A Fourier term of the ring counts as real at this many times its uncertainty.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-17",
)

# The width of one direction bin of the ring plot. 5 degrees gives 72 bins;
# at a ring radius of 12 px a bin holds about 5 pixels.
STAR_RING_BIN_DEGREES = TunedParameter(
    name="shared.star_ring_bin_degrees",
    default=5.0,
    unit="degrees",
    description="Width of one direction bin of the ring plot.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# A neighbor must stand this many sky noise above the target's own glow at
# its position, so a bump in the glow of a bright star is not a neighbor.
# It is the same floor that `STAR_MINIMUM_SIGNAL_TO_NOISE` sets for a star.
STAR_RING_NEIGHBOR_MINIMUM_SIGNAL_TO_NOISE = TunedParameter(
    name="shared.star_ring_neighbor_minimum_signal_to_noise",
    default=5.0,
    unit="× sky noise",
    description="Smallest height above the target's own glow for a detection to count as a neighbor.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-17",
)

# A fit needs more pixels than numbers fitted. Three times as many keeps the
# uncertainty estimate from the residual scatter steady.
STAR_RING_MINIMUM_PIXELS_PER_FITTED_NUMBER = TunedParameter(
    name="shared.star_ring_minimum_pixels_per_fitted_number",
    default=3,
    unit="pixels per number",
    description="Fewest ring pixels per fitted number for the Fourier fit to be made.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-17",
)

# An annulus counts as inside the image when it holds at least this share of
# the pixels a whole annulus would hold. Beyond the first one that fails,
# the ring is cut off at the image edge.
STAR_RING_ANNULUS_MINIMUM_SHARE = TunedParameter(
    name="shared.star_ring_annulus_minimum_share",
    default=0.5,
    unit="fraction",
    description="Share of a whole annulus's pixels that must lie inside the image for the ring to reach it.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# A neighbor's brightest pixel is looked for within one pixel of the position
# the star finder gives, which is the finder's centroid accuracy.
STAR_RING_NEIGHBOR_PEAK_SEARCH_PX = TunedParameter(
    name="shared.star_ring_neighbor_peak_search_px",
    default=1,
    unit="pixels",
    description="How far from a detected neighbor's position its brightest pixel is looked for.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)


# Fewest pixels the ring's sky annulus needs, as in the profile's sky annulus.
_SKY_MINIMUM_PIXELS = 10

# The clipping of the sky plane fit: rounds, and the limit in robust sigmas.
_SKY_CLIP_ITERATIONS = 5
_SKY_CLIP_SIGMA = 3.0


@dataclass
class RingAnalysis:
    """What the ring analysis found; the fields match `StarProfileMeasurement`.

    Parameters
    ----------
    inner_px, outer_px : `float`
        The ring's radii.
    sky : `float`
        The sky level the ring's values are measured above.
    bin_angles, bin_values, bin_counts : `list`
        The brightness by direction in 5 degree bins.
    harmonics : `list` [`RingHarmonic`]
        The Fourier terms, empty when the ring could not be analyzed.
    constant, scale_value : `float` or `None`
        The fitted constant term, and the mean of the divisor over the
        fitted pixels (see `StarProfileMeasurement`).
    neighbors : `list` [`RingNeighbor`]
        The neighbors that removed at least one ring pixel.
    masked_fraction : `float`
        The fraction of ring pixels the masks removed.
    note : `str` or `None`
        Why the ring was not analyzed, or `None`.
    """

    inner_px: float
    outer_px: float
    sky: float
    bin_angles: list[float] = field(default_factory=list)
    bin_values: list[float] = field(default_factory=list)
    bin_counts: list[int] = field(default_factory=list)
    harmonics: list[RingHarmonic] = field(default_factory=list)
    constant: float | None = None
    scale_value: float | None = None
    neighbors: list[RingNeighbor] = field(default_factory=list)
    masked_fraction: float = 0.0
    note: str | None = None


def _parameter(declaration: TunedParameter) -> float:
    """Read the value of a declared parameter as a float.

    Returns
    -------
    value : `float`
        The resolved value.
    """
    return float(resolve(declaration).value)  # type: ignore[arg-type]


def _radial_means(
    distance: np.ndarray, values: np.ndarray, usable: np.ndarray, maximum_radius: int
) -> tuple[np.ndarray, np.ndarray]:
    """Average the values in one-pixel-wide annuli around the center.

    Annulus ``k`` holds the pixels whose distance rounds to ``k``, so its
    middle is at ``k`` pixels. Annulus 0 is the central pixel.

    Parameters
    ----------
    distance, values, usable : `numpy.ndarray`
        The distance of each pixel from the center, its value above the sky,
        and whether it may be used.
    maximum_radius : `int`
        The last annulus.

    Returns
    -------
    means, counts : `numpy.ndarray`
        The mean value and the pixel count of annuli 0 to ``maximum_radius``.
        The mean is NaN where an annulus holds no usable pixel.
    """
    keep = usable & (distance < maximum_radius + 0.5)
    index = np.rint(distance[keep]).astype(int)
    sums = np.bincount(index, weights=values[keep], minlength=maximum_radius + 1)
    counts = np.bincount(index, minlength=maximum_radius + 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    return means, counts


def _ring_background(
    distance: np.ndarray,
    window: np.ndarray,
    finite: np.ndarray,
    offsets: tuple[np.ndarray, np.ndarray],
    maximum_radius: int,
    fallback: float,
) -> tuple[float, np.ndarray]:
    """Measure the sky for the ring as a tilted plane, far from the star.

    A plane ``s0 + gx * dx + gy * dy`` (``dx`` and ``dy`` are the offsets
    from the star) is fitted by least squares to the pixels of an annulus
    from the declared fraction of the largest radius out to the largest
    radius, with 3 sigma clipping. The annulus lies outside the star's glow,
    and the tilt keeps a smooth sky gradient from showing up as a one-sided
    pattern in the ring. Stars in the annulus are removed by the clipping.

    Parameters
    ----------
    distance, window, finite : `numpy.ndarray`
        The distance of each pixel from the star, its raw value, and whether
        it is finite.
    offsets : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The column and row offset of each pixel from the star.
    maximum_radius : `int`
        The largest radius of the ring work.
    fallback : `float`
        The flat sky to use when too few pixels lie in the annulus.

    Returns
    -------
    sky, background : `float`, `numpy.ndarray`
        The sky at the star's center, and the plane over the window.
    """
    start = _parameter(STAR_RING_SKY_INNER_FRACTION) * maximum_radius
    annulus = finite & (distance >= start) & (distance <= maximum_radius)
    if int(annulus.sum()) < _SKY_MINIMUM_PIXELS:
        return fallback, np.full(window.shape, fallback)
    delta_x, delta_y = offsets
    design = np.stack([np.ones(int(annulus.sum())), delta_x[annulus], delta_y[annulus]], axis=1)
    target = window[annulus]
    keep = np.ones(target.shape, dtype=bool)
    coefficients = np.array([float(np.median(target)), 0.0, 0.0])
    for _ in range(_SKY_CLIP_ITERATIONS):
        coefficients = np.linalg.lstsq(design[keep], target[keep], rcond=None)[0]
        residual = target - design @ coefficients
        scatter = 1.4826 * float(np.median(np.abs(residual[keep] - np.median(residual[keep]))))
        keep = np.abs(residual) <= _SKY_CLIP_SIGMA * scatter
    background = coefficients[0] + coefficients[1] * delta_x + coefficients[2] * delta_y
    return float(coefficients[0]), background


def _reach_inside_image(counts: np.ndarray) -> int:
    """Find the last annulus before the image edge cuts the annuli short.

    Returns
    -------
    reach : `int`
        The largest ``k`` such that every annulus up to ``k`` holds at least
        half of the pixels a whole annulus would hold.
    """
    for radius in range(1, len(counts)):
        if counts[radius] < _parameter(STAR_RING_ANNULUS_MINIMUM_SHARE) * 2.0 * math.pi * radius:
            return radius - 1
    return len(counts) - 1


def _ring_radii(means: np.ndarray, reach: int, peak: float, sky_noise: float) -> tuple[float, float]:
    """Choose the ring's inner and outer radius from the annulus means.

    Parameters
    ----------
    means : `numpy.ndarray`
        The mean value of annuli 0 and up.
    reach : `int`
        The last annulus that may be used.
    peak, sky_noise : `float`
        The star's peak above the sky and the sky noise.

    Returns
    -------
    inner, outer : `float`
        The radii, in pixels.
    """
    minimum_inner = _parameter(STAR_RING_MINIMUM_INNER_PX)
    minimum_width = _parameter(STAR_RING_MINIMUM_WIDTH_PX)
    maximum = min(float(reach), _parameter(STAR_RING_MAXIMUM_RADIUS_PX))
    inner_level = _parameter(STAR_RING_INNER_PEAK_FRACTION) * peak
    outer_level = _parameter(STAR_RING_OUTER_NOISE_MULTIPLE) * sky_noise
    radii = np.arange(len(means))
    ends = (radii <= maximum) & np.isfinite(means)
    below_inner = np.flatnonzero(ends & (means < inner_level))
    inner_radius = float(below_inner[0]) if below_inner.size else maximum
    below_outer = np.flatnonzero(ends & (radii > inner_radius) & (means < outer_level))
    glow_end = float(below_outer[0]) if below_outer.size else maximum
    inner = min(max(inner_radius, minimum_inner), max(minimum_inner, maximum - minimum_width))
    outer = min(max(glow_end * _parameter(STAR_RING_OUTER_MARGIN_FACTOR), inner + minimum_width), maximum)
    inner = min(inner, max(minimum_inner, outer - minimum_width))
    return inner, outer


def _fit_harmonics(angle: np.ndarray, ratio: np.ndarray) -> tuple[list[RingHarmonic], float, str | None]:
    """Fit a constant and a Fourier series to the ring's relative brightness.

    The model is ``c0 + sum over m of (a_m cos(m t) + b_m sin(m t))`` by
    linear least squares. The covariance of the numbers is ``s^2 (X^T X)^-1``
    with ``s^2`` the residual sum of squares over the degrees of freedom. The
    fractional amplitude of order ``m`` is ``A / c0`` with ``A`` the length of
    ``(a_m, b_m)``; its uncertainty follows from the covariance by the usual
    first-order propagation. The amplitude is biased high when the signal is
    weak, since noise adds to its length. The debiased amplitude subtracts
    the noise variance of ``a_m`` and ``b_m`` from ``A^2``.

    Parameters
    ----------
    angle : `numpy.ndarray`
        The direction of each ring pixel, in radians from +y toward +x.
    ratio : `numpy.ndarray`
        Each pixel's value divided by the mean at its distance.

    Returns
    -------
    harmonics : `list` [`RingHarmonic`]
        The terms, empty when the fit could not be made.
    constant : `float`
        The constant term ``c0``.
    note : `str` or `None`
        A sentence that says why the fit could not be made.
    """
    orders = int(_parameter(STAR_RING_HARMONIC_COUNT))
    columns = [np.ones_like(angle)]
    for order in range(1, orders + 1):
        columns.append(np.cos(order * angle))
        columns.append(np.sin(order * angle))
    design = np.stack(columns, axis=1)
    count, unknowns = design.shape
    needed = round(_parameter(STAR_RING_MINIMUM_PIXELS_PER_FITTED_NUMBER)) * unknowns
    if count < needed:
        return [], 1.0, f"Too few ring pixels for the Fourier fit ({count} left; {needed} needed)."
    normal = design.T @ design
    if np.linalg.cond(normal) > 1e8:
        return [], 1.0, "The masked neighbors leave too large a gap in the ring for the Fourier fit."
    coefficients = np.linalg.solve(normal, design.T @ ratio)
    residual = ratio - design @ coefficients
    variance = float(residual @ residual) / (count - unknowns)
    covariance = variance * np.linalg.inv(normal)
    constant = float(coefficients[0])
    if constant <= 0:
        return (
            [],
            constant,
            "The ring's mean brightness is not above zero, so relative amplitudes are undefined.",
        )
    threshold = _parameter(STAR_RING_SIGNIFICANCE_THRESHOLD)
    harmonics = []
    for order in range(1, orders + 1):
        indices = [0, 2 * order - 1, 2 * order]
        c0, a, b = (float(coefficients[i]) for i in indices)
        block = covariance[np.ix_(indices, indices)]
        length = math.hypot(a, b)
        if length > 0:
            gradient = np.array([-length / c0**2, a / (length * c0), b / (length * c0)])
        else:
            gradient = np.array([0.0, 1.0 / c0 / math.sqrt(2.0), 1.0 / c0 / math.sqrt(2.0)])
        error = math.sqrt(max(float(gradient @ block @ gradient), 0.0))
        noise_square = float(block[1, 1] + block[2, 2])
        debiased = math.sqrt(max(length**2 - noise_square, 0.0)) / c0
        significance = (length / c0) / error if error > 0 else 0.0
        direction = math.degrees(math.atan2(b, a) / order) % (360.0 / order)
        harmonics.append(
            RingHarmonic(
                order=order,
                amplitude=float(length / c0),
                amplitude_error=float(error),
                amplitude_debiased=float(debiased),
                significance=float(significance),
                significant=bool(significance >= threshold),
                direction_degrees=float(direction),
            )
        )
    return harmonics, constant, None


def analyze_star_ring(
    data: np.ndarray,
    center_column: float,
    center_row: float,
    sky: float,
    sky_noise: float,
    peak: float,
    star_found: bool,
    fallback_outer_px: float,
    find_neighbors: Callable[[np.ndarray], list[tuple[float, float]]] | None,
) -> RingAnalysis:
    """Measure the brightness in a ring around a star, by direction.

    Parameters
    ----------
    data : `numpy.ndarray`
        The whole image, two-dimensional, with the sky not yet removed.
    center_column, center_row : `float`
        The star's center, as 0-based array positions.
    sky, sky_noise : `float`
        The sky level and its scatter, in image units.
    peak : `float`
        The star's brightest pixel above the sky.
    star_found : `bool`
        Whether the profile looks like a star. When it does not, only the
        unmasked bins are returned, with a note.
    fallback_outer_px : `float`
        The outer radius to use when there is no star.
    find_neighbors : callable or `None`
        A function that takes a small piece of the image (raw values) and
        returns the 0-based ``(column, row)`` of each star in it, the way the
        astrometry run's star finder does. `None` skips the masking.

    Returns
    -------
    analysis : `RingAnalysis`
        The radii, the bins, the Fourier terms, the masked neighbors and the
        fraction of pixels they removed.
    """
    height, width = data.shape
    maximum_radius = int(_parameter(STAR_RING_MAXIMUM_RADIUS_PX))
    x_low, x_high = (
        max(int(center_column) - maximum_radius - 1, 0),
        min(int(center_column) + maximum_radius + 3, width),
    )
    y_low, y_high = (
        max(int(center_row) - maximum_radius - 1, 0),
        min(int(center_row) + maximum_radius + 3, height),
    )
    rows, columns = np.mgrid[y_low:y_high, x_low:x_high]
    delta_x = columns - center_column
    delta_y = rows - center_row
    distance = np.hypot(delta_x, delta_y)
    window = data[y_low:y_high, x_low:x_high]
    finite = np.isfinite(window)
    sky, background = _ring_background(distance, window, finite, (delta_x, delta_y), maximum_radius, sky)
    values = np.where(finite, window - background, 0.0)

    means, counts = _radial_means(distance, values, finite, maximum_radius)
    reach = _reach_inside_image(counts)
    minimum_inner = _parameter(STAR_RING_MINIMUM_INNER_PX)
    if star_found and peak > 0 and sky_noise > 0:
        inner, outer = _ring_radii(means, reach, peak, sky_noise)
    else:
        outer = min(
            max(fallback_outer_px, minimum_inner + _parameter(STAR_RING_MINIMUM_WIDTH_PX)), float(reach)
        )
        inner = min(minimum_inner, outer)
    analysis = RingAnalysis(inner_px=float(inner), outer_px=float(outer), sky=float(sky))

    in_ring = finite & (distance >= inner) & (distance <= outer)
    masked = np.zeros_like(in_ring)
    if not star_found or peak <= 0 or sky_noise <= 0:
        analysis.note = "The ring is not analyzed because no star was found."
    else:
        if find_neighbors is not None:
            analysis.neighbors, masked = _mask_neighbors(
                find_neighbors,
                data,
                (center_column, center_row),
                (sky, sky_noise, peak),
                means,
                (inner, outer),
                (columns, rows, finite),
                in_ring,
            )
        analysis.masked_fraction = float((in_ring & masked).sum() / max(int(in_ring.sum()), 1))

    fitted = in_ring & ~masked
    angle = np.arctan2(delta_x, delta_y)
    _fill_bins(analysis, angle[fitted], values[fitted])

    if analysis.note is None:
        clean_means, _ = _radial_means(distance, values, finite & ~masked, maximum_radius)
        known = np.flatnonzero(np.isfinite(clean_means))
        # The mean falls steeply near the star, like a power law, so it is
        # interpolated between annuli in its logarithm. The floor keeps the
        # logarithm defined and is the smallest divisor.
        floor = _parameter(STAR_RING_NORMALIZER_FLOOR_NOISE) * sky_noise
        log_means = np.log(np.maximum(clean_means[known], floor))
        divisor = np.exp(np.interp(distance[fitted], known, log_means))
        analysis.harmonics, constant, analysis.note = _fit_harmonics(angle[fitted], values[fitted] / divisor)
        if analysis.harmonics:
            analysis.constant = constant
            analysis.scale_value = float(divisor.mean())
    return analysis


def _mask_neighbors(
    find_neighbors: Callable[[np.ndarray], list[tuple[float, float]]],
    data: np.ndarray,
    center: tuple[float, float],
    levels: tuple[float, float, float],
    means: np.ndarray,
    radii: tuple[float, float],
    window_parts: tuple[np.ndarray, np.ndarray, np.ndarray],
    in_ring: np.ndarray,
) -> tuple[list[RingNeighbor], np.ndarray]:
    """Find the neighbor stars and mask the pixels of the ring they light.

    For each detection other than the star itself, the neighbor's height is
    its brightest pixel, above the sky, minus the star's own mean at that
    distance. A neighbor lower than the declared signal-to-noise is not a
    neighbor (it is a bump in the glow). The mask radius is the first radius
    where the star's own profile (annulus means, never rising with radius),
    divided by the star's peak and scaled to the neighbor's height, falls
    below the declared multiple of the sky noise.

    Parameters
    ----------
    find_neighbors : callable
        The star finder passed to `analyze_star_ring`.
    data : `numpy.ndarray`
        The whole image.
    center : `tuple` [`float`, `float`]
        The star's center, as 0-based column and row.
    levels : `tuple` [`float`, `float`, `float`]
        The sky, the sky noise and the star's peak.
    means : `numpy.ndarray`
        The star's annulus means.
    radii : `tuple` [`float`, `float`]
        The ring's inner and outer radius (only the outer one is used, to
        size the search box).
    window_parts : `tuple`
        The window's column grid, its row grid and its finite-pixel mask.
    in_ring : `numpy.ndarray`
        Which window pixels are in the ring.

    Returns
    -------
    neighbors, masked : `list` [`RingNeighbor`], `numpy.ndarray`
        The neighbors that removed ring pixels, and the mask over the window.
    """
    center_column, center_row = center
    sky, sky_noise, peak = levels
    outer = radii[1]
    columns, rows, finite = window_parts
    height, width = data.shape
    margin = _parameter(STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX)
    box_half = outer + margin
    box_x0, box_x1 = max(int(center_column - box_half), 0), min(int(center_column + box_half) + 2, width)
    box_y0, box_y1 = max(int(center_row - box_half), 0), min(int(center_row + box_half) + 2, height)
    box = np.nan_to_num(data[box_y0:box_y1, box_x0:box_x1], nan=sky)
    found = find_neighbors(box)

    known = np.flatnonzero(np.isfinite(means))
    envelope = np.minimum.accumulate(np.interp(np.arange(len(means)), known, means[known]))
    target_match = _parameter(STAR_RING_TARGET_MATCH_RADIUS_PX)
    minimum_height = _parameter(STAR_RING_NEIGHBOR_MINIMUM_SIGNAL_TO_NOISE) * sky_noise
    mask_level = _parameter(STAR_RING_NEIGHBOR_MASK_NOISE_MULTIPLE) * sky_noise
    masked = np.zeros(in_ring.shape, dtype=bool)
    neighbors: list[RingNeighbor] = []
    half = round(_parameter(STAR_RING_NEIGHBOR_PEAK_SEARCH_PX))
    for found_column, found_row in found:
        column, row = box_x0 + found_column, box_y0 + found_row
        offset = math.hypot(column - center_column, row - center_row)
        if offset <= target_match:
            continue
        near = data[
            max(round(row) - half, 0) : min(round(row) + half + 1, height),
            max(round(column) - half, 0) : min(round(column) + half + 1, width),
        ]
        if not np.isfinite(near).any():
            continue
        glow = float(np.interp(offset, np.arange(len(envelope)), envelope))
        height_above_glow = float(np.nanmax(near)) - sky - glow
        if height_above_glow < minimum_height:
            continue
        # Annulus k of the star falls below the level where the star's peak
        # scaled to the neighbor's height reaches the mask level.
        mask_radius = mask_radius_from_profile(envelope, peak, height_above_glow, mask_level)
        disc = (np.hypot(columns - column, rows - row) <= mask_radius) & finite
        newly = disc & in_ring & ~masked
        masked |= disc
        removed = int(newly.sum())
        if removed > 0:
            neighbors.append(
                RingNeighbor(
                    x=float(column + 1.0),
                    y=float(row + 1.0),
                    mask_radius_px=mask_radius,
                    pixels_masked=removed,
                )
            )
    return neighbors, masked


def _fill_bins(analysis: RingAnalysis, angle: np.ndarray, values: np.ndarray) -> None:
    """Average the ring's pixels in direction bins and store them.

    Only bins that hold at least one pixel are stored. The angle is in
    radians from +y toward +x; the bins are in degrees from 0 up to 360.

    Parameters
    ----------
    analysis : `RingAnalysis`
        The result to fill.
    angle, values : `numpy.ndarray`
        The direction and the value above the sky of each pixel in the ring.
    """
    width = _parameter(STAR_RING_BIN_DEGREES)
    bin_count = round(360.0 / width)
    degrees = np.degrees(angle) % 360.0
    index = np.minimum((degrees // width).astype(int), bin_count - 1)
    sums = np.bincount(index, weights=values, minlength=bin_count)
    counts = np.bincount(index, minlength=bin_count)
    for bin_index in np.flatnonzero(counts):
        analysis.bin_angles.append(float((bin_index + 0.5) * width))
        analysis.bin_values.append(float(sums[bin_index] / counts[bin_index]))
        analysis.bin_counts.append(int(counts[bin_index]))
