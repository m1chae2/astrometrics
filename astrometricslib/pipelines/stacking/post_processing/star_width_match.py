"""Matches the star widths of a color stack's channels for its picture.

A camera lens does not bring every color to the same focus. With the Nikon
D5300 behind the 300 mm lens, a star's red light is spread over a wider
patch than its green and blue light: on the M 45 stack the red star image is
about 35% wider than the green one, measured by the spread of its light.
Each star then has a green-white core inside a red skirt, and the stretch
turns the skirt into an orange-red ring around every star. Where many faint
skirts overlap, as over a galaxy, they add a red haze. Behind the 405 mm
telescope the three widths are close, and red is a little narrower than
green.

This step runs on the linear data of a color stack, after the background
step and before the color calibration, so that the calibration and the
white-balance check measure stars whose three colors have the same shape.

1. *Stars.* Stars are found on the green channel. Only bright, unsaturated
   stars with no neighbor nearby are used, so each one shows the shape of a
   single star (see `STAR_WIDTH_MATCH_STAR_SELECTION`).
2. *Widths.* In each channel the spread of each star's light around its
   center (the second moment, given as an equivalent full width at half
   maximum) is measured inside a circle, with the sky taken from a ring
   around it (see `STAR_WIDTH_MATCH_RADII_PIXELS`). The median over the
   stars is the channel's star width. Green is the reference: it has twice
   as many pixels on the sensor as red or blue, so it is the sharpest and
   least noisy channel.
3. *Decision.* A channel whose stars are wider than green's by more than the
   first number of `STAR_WIDTH_MATCH_RATIO_LIMITS` is sharpened. A channel
   that is narrower than green, or close to it, is left alone: making green
   and blue wider would throw away detail. A ratio above the second number
   is not trusted (a bad stack or a wrong star list) and nothing is changed.
4. *Sharpening.* The average star of each channel is built from the stars,
   made round by averaging over each ring of equal distance. A small filter
   is then worked out that turns the wide channel's average star into
   green's (a Wiener filter: it divides out the wide star's shape and puts
   green's shape in, with `STAR_WIDTH_MATCH_REGULARIZATION` keeping it from
   boosting the noise at fine scales). The filter's values add up to one, so
   every star keeps its total light and an even sky keeps its level. The
   filter is applied to the whole channel. The star widths are measured
   again on the same stars and recorded.

The filter is the same over the whole frame. On the 300 mm stacks the extra
width of red changed by about 15% between the middle and the corners, so one
filter leaves a small difference near the corners.

The stack itself is never changed. This works on the scratch copy that the
preview is made from.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import fft, ndimage, signal

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.pipelines.shared.star_finding import (
    find_sources_above_background,
    source_positions,
    unsaturated_boxes,
)

logger = logging.getLogger(__name__)

__all__ = [
    "STAR_WIDTH_MATCH_BRIGHT_STAR_GUARD_PIXELS",
    "STAR_WIDTH_MATCH_GRID",
    "STAR_WIDTH_MATCH_MINIMUM_STARS",
    "STAR_WIDTH_MATCH_RADII_PIXELS",
    "STAR_WIDTH_MATCH_RATIO_LIMITS",
    "STAR_WIDTH_MATCH_REGULARIZATION",
    "STAR_WIDTH_MATCH_STAR_SELECTION",
    "StarWidthMatch",
    "find_measurable_stars",
    "match_star_widths",
    "match_star_widths_in_file",
    "measure_channel_widths",
]

# The ratio of a channel's star width to green's above which the channel is
# sharpened, and the ratio above which nothing is changed. Measured with the
# second moment inside 10 px on the 24 Nikon D5300 color stacks in the
# library that have measurable stars (2026-11-25): behind the 405 mm
# telescope (11 stacks) red read 0.92 to 0.97 of green except M 31 (1.07),
# and blue 0.99 to 1.05, none above 1.073. Behind the 300 mm lens (13
# stacks), 12 have a channel from 1.12 (red, NGC 7023) to 1.38 (red, M 42);
# blue reaches 1.22 (M 13). M 27 at 300 mm reads 0.91 and 1.05 and is left
# alone. 1.10 lies between the two groups, so the 405 mm pictures are left
# as they were. Above 2.0 the widths are not a lens effect but a broken
# measurement or stack.
STAR_WIDTH_MATCH_RATIO_LIMITS = TunedParameter(
    name="stacking.star_width_match_ratio_limits",
    default=(1.10, 2.0),
    unit="ratio to the green channel's star width",
    description=(
        "Width ratio to green above which a color channel is sharpened, and the ratio above which "
        "nothing is changed."
    ),
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-63",
)

# How the stars that are measured are chosen: the star finder's expected
# width (pixels), how far above the sky noise a star must peak (in noise
# widths), how far from any other found star it must be (pixels) and the
# share of the smallest channel's brightest value it must stay under. On the
# 24 Nikon stacks these kept 38 (M 51) to the cap of 4,000 stars (2026-11-25).
# With a peak of 20 noise widths, the red-to-green ratio of the median widths
# scattered by 0.6% (NGC 7023) to 1.5% (M 45) over random sets of 100 stars.
# 25 px keeps a neighbor's light out of the 15 px sky ring. Half of the
# brightest value keeps the stars away from saturation, where the core is
# cut flat and the width is wrong.
STAR_WIDTH_MATCH_STAR_SELECTION = TunedParameter(
    name="stacking.star_width_match_star_selection",
    default=(4.0, 20.0, 25.0, 0.5),
    unit="pixels, noise widths, pixels, fraction",
    description=(
        "Star finder width, peak above the sky noise, distance to the nearest other star and "
        "share of the brightest value for a star whose width is measured."
    ),
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-63",
)

# The radius of the circle in which a star's spread is measured, and the
# inner and outer radii of the sky ring, in pixels. The red light of a 300 mm
# Nikon star reaches about 11 px from its center before it falls to the sky
# (M 45 average star, 2026-11-25), so the 10 px circle holds the wing that
# makes the red ring, and the ring from 12 to 15 px lies beyond it. The
# average star is cut at the ring's inner radius.
STAR_WIDTH_MATCH_RADII_PIXELS = TunedParameter(
    name="stacking.star_width_match_radii_pixels",
    default=(10.0, 12.0, 15.0),
    unit="pixels",
    description="Measuring circle and sky ring radii for the star widths of a color stack's channels.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-63",
)

# The fewest stars needed to measure the widths and build the average star.
# Over random sets of 30 stars the red-to-green ratio of the median widths
# scattered by 1.2% (NGC 7023) to 3.0% (M 45, where part of it is the change
# across the field), 2026-11-25; with fewer the 1.10 limit could flip on
# noise.
STAR_WIDTH_MATCH_MINIMUM_STARS = TunedParameter(
    name="stacking.star_width_match_minimum_stars",
    default=30,
    unit="stars",
    description="Fewest measurable stars for the star width matching of a color preview.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-63",
)

# The Wiener filter's noise term, as a share of the average star's power at
# the largest scale (which is 1). A smaller value matches the widths more
# closely and raises the noise: on the M 45 300 mm stack 1e-4, 3e-4 and 1e-3
# gave red-to-green width ratios of 1.047, 1.047 and 1.055 with the red sky
# noise up 27%, 19% and 6%; on NGC 7023, 1.017, 1.015 and 1.018 with +4%,
# -2% and -10% (2026-11-25, half the stars built the filter and the other
# half tested it). 1e-3 keeps the noise rise small for nearly the same match.
STAR_WIDTH_MATCH_REGULARIZATION = TunedParameter(
    name="stacking.star_width_match_regularization",
    default=1e-3,
    unit="share of the average star's power",
    description="Noise term of the Wiener filter that sharpens a wide color channel to green's star width.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-63",
)

# The number of rows and columns of the grid of regions that each get their
# own filter. On the M 45 300 mm stack the red-to-green ratio ran from 1.25
# (left) to 1.49 (lower right) over a 3 by 3 grid, and one filter for the
# whole frame left 0.75 to 1.14 in the regions; with a filter per region
# the regions read 0.93 to 1.06 (2026-11-25). A region with fewer than
# `STAR_WIDTH_MATCH_MINIMUM_STARS` stars uses the average star of the whole
# frame. The filters are blended smoothly between the regions' centers.
STAR_WIDTH_MATCH_GRID = TunedParameter(
    name="stacking.star_width_match_grid",
    default=3,
    unit="regions per side",
    description="Rows and columns of the grid of regions that each get their own star width filter.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-63",
)

# Around a bright star the filter is not used: a saturated core is cut flat,
# and the filter rings around a flat core, which shows as a cyan ring after
# the stretch (M 45, 2026-11-25). Within the first distance of a pixel at or
# above the saturation share (the last number of
# `STAR_WIDTH_MATCH_STAR_SELECTION`) the channel takes green's shape with the
# typical star's color instead (see `_guard_bright_stars`), and the filtered
# values take over evenly by the second distance, in pixels. On three
# saturated M 45 stars the red-to-green ratio in rings 3 px wide out to 30 px
# from the center ran 0.5 to 2.1 before (the red skirt peaks 9 px out). With
# 4 and 10 px it ran 0.7 to 1.9 with a dip to -0.2 at 18 px (the ringing
# reaches past 10 px); with 8 and 20 px, 0.3 to 1.1; with 12 and 25 px,
# 0.67 to 1.15 with no ring.
STAR_WIDTH_MATCH_BRIGHT_STAR_GUARD_PIXELS = TunedParameter(
    name="stacking.star_width_match_bright_star_guard_pixels",
    default=(12.0, 25.0),
    unit="pixels",
    description="Distances from a bright star's core within which the star width filter is faded out.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-63",
)

# The most stars measured. More add time and change the medians by less
# than their scatter. A cap for speed, not tuning.
_MAXIMUM_STARS = 4000

# The side of the square on which the filter is worked out, and the half
# width of the filter that is kept, in pixels. The filter of the M 45 stack
# falls below 1e-4 of its peak within 15 px; 20 px keeps all of it. Plumbing.
_FILTER_GRID_PIXELS = 64
_FILTER_HALF_WIDTH_PIXELS = 20

# A star's center must be this far inside the image so its stamp fits, in
# pixels beyond the sky ring. Plumbing.
_EDGE_MARGIN_PIXELS = 3

# Converts a Gaussian's sigma to its full width at half maximum.
_SIGMA_TO_FWHM = 2.0 * np.sqrt(2.0 * np.log(2.0))

# The star finder keeps sources of any roundness from -1 to 1, its own
# default. Plumbing, not tuned.
_FINDER_ROUNDNESS_RANGE = (-1.0, 1.0)

_CHANNEL_NAMES = ("red", "green", "blue")


@dataclass(frozen=True)
class StarWidthMatch:
    """What the star width matching found and did.

    Attributes
    ----------
    applied : `bool`
        Whether a channel was sharpened.
    reason : `str`
        Why nothing was changed, or an empty string.
    star_count : `int`
        The number of stars measured.
    widths_before : `tuple` [`float`, ...]
        The red, green and blue star widths before, in pixels (the second
        moment given as a full width at half maximum), or empty when they
        could not be measured.
    widths_after : `tuple` [`float`, ...]
        The same widths after sharpening, or empty when nothing was changed.
    sharpened : `tuple` [`str`, ...]
        The names of the channels that were sharpened.
    filter_noise_gains : `dict` [`str`, `float`]
        For each sharpened channel, the root of the sum of the squared values
        of the middle region's filter
        (the factor by which the filter changes the noise of independent
        pixels).
    """

    applied: bool
    reason: str = ""
    star_count: int = 0
    widths_before: tuple[float, ...] = ()
    widths_after: tuple[float, ...] = ()
    sharpened: tuple[str, ...] = ()
    filter_noise_gains: dict[str, float] = field(default_factory=dict)

    def ratios(self, widths: tuple[float, ...]) -> tuple[float, ...]:
        """Give the red-to-green and blue-to-green width ratios.

        Parameters
        ----------
        widths : `tuple` [`float`, ...]
            Red, green and blue widths.

        Returns
        -------
        ratios : `tuple` [`float`, ...]
            Red over green and blue over green, or empty when there are no
            widths.
        """
        if len(widths) != 3 or widths[1] <= 0.0:
            return ()
        return (widths[0] / widths[1], widths[2] / widths[1])

    def record(self) -> dict[str, Any]:
        """Give the result for the preview settings record.

        Returns
        -------
        record : `dict`
            Plain numbers and text for the JSON file.
        """
        return {
            "applied": self.applied,
            "reason": self.reason,
            "star_count": self.star_count,
            "widths_before_pixels": [round(value, 3) for value in self.widths_before],
            "widths_after_pixels": [round(value, 3) for value in self.widths_after],
            "ratios_to_green_before": [round(value, 3) for value in self.ratios(self.widths_before)],
            "ratios_to_green_after": [round(value, 3) for value in self.ratios(self.widths_after)],
            "sharpened": list(self.sharpened),
            "filter_noise_gains": {name: round(value, 3) for name, value in self.filter_noise_gains.items()},
        }

    def step_sentence(self) -> str:
        """Say in one sentence what the step did, for the list of steps.

        Returns
        -------
        sentence : `str`
            For example ``"Star width matching: red sharpened, red/green
            1.33 -> 1.05"``, or the reason when nothing was changed.
        """
        if not self.applied:
            return f"Star width matching not needed ({self.reason})"
        before, after = self.ratios(self.widths_before), self.ratios(self.widths_after)
        parts = []
        for name in self.sharpened:
            index = 0 if name == "red" else 1
            parts.append(f"{name}/green {before[index]:.2f} -> {after[index]:.2f}" if after else name)
        channels = ", ".join(self.sharpened)
        return f"Star width matching: {channels} sharpened on {self.star_count} stars ({'; '.join(parts)})"


def find_measurable_stars(image: np.ndarray) -> np.ndarray:
    """Find bright, unsaturated, isolated stars on the green channel.

    The stars come from the shared star finder (`star_finding`) with its
    isolation and saturation filters, so every star-width measurement of a
    color stack starts from the same stars.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear color image, channels first, shape (3, height, width).

    Returns
    -------
    positions : `numpy.ndarray`
        Shape (n, 2): the x and y position of each star, brightest first,
        at most `_MAXIMUM_STARS` of them.
    """
    finder_width, peak_sigmas, isolation, saturation_share = (
        float(value) for value in resolve(STAR_WIDTH_MATCH_STAR_SELECTION).value
    )
    outer = float(resolve(STAR_WIDTH_MATCH_RADII_PIXELS).value[2])
    green = image[1]
    measured_sky = measure_sky_background(np.asarray(green, dtype=np.float64))
    if measured_sky is None:
        return np.empty((0, 2))
    sky, noise = measured_sky
    if not noise > 0.0:
        return np.empty((0, 2))
    sources = find_sources_above_background(
        np.nan_to_num(green - np.float32(sky)),
        noise=noise,
        expected_fwhm_px=finder_width,
        threshold_sigma=peak_sigmas,
        roundness_range=_FINDER_ROUNDNESS_RANGE,
        isolation_px=isolation,
    )
    if len(sources) < 2:
        return np.empty((0, 2))
    positions = source_positions(sources)
    margin = int(np.ceil(outer)) + _EDGE_MARGIN_PIXELS
    height, width = green.shape
    x, y = positions[:, 0], positions[:, 1]
    inside = (x > margin) & (x < width - 1 - margin) & (y > margin) & (y < height - 1 - margin)
    positions = positions[inside]
    ceiling = saturation_share * min(float(np.nanmax(channel)) for channel in image)
    usable = unsaturated_boxes(image, positions, int(np.ceil(outer)), ceiling, floor=0.0)
    return positions[usable][:_MAXIMUM_STARS]


def _stamps(image: np.ndarray, positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cut a centered, sky-subtracted stamp of each star in each channel.

    Each stamp is moved by a fraction of a pixel so the star's center falls
    on the middle pixel, and the sky (the median of the ring) is taken off.

    Parameters
    ----------
    image : `numpy.ndarray`
        An image, shape (channels, height, width).
    positions : `numpy.ndarray`
        Star positions, shape (n, 2).

    Returns
    -------
    stamps : `numpy.ndarray`
        Shape (n, channels, side, side), 64-bit floats.
    distance : `numpy.ndarray`
        Each stamp pixel's distance from the middle, shape (side, side).
    """
    _, inner, outer = (float(value) for value in resolve(STAR_WIDTH_MATCH_RADII_PIXELS).value)
    half = int(np.ceil(outer))
    side = 2 * half + 1
    offsets = np.arange(side) - half
    distance = np.hypot(offsets[None, :], offsets[:, None])
    ring = (distance > inner) & (distance <= outer)
    channels = image.shape[0]
    stamps = np.empty((len(positions), channels, side, side))
    for index, (star_x, star_y) in enumerate(positions):
        column, row = round(star_x), round(star_y)
        rows = slice(row - half - 1, row + half + 2)
        cut = image[:, rows, column - half - 1 : column + half + 2].astype(np.float64)
        for channel in range(channels):
            moved = ndimage.shift(cut[channel], (row - star_y, column - star_x), order=3, mode="nearest")
            stamps[index, channel] = moved[1:-1, 1:-1]
    stamps -= np.median(stamps[:, :, ring], axis=2)[:, :, None, None]
    return stamps, distance


def measure_channel_widths(image: np.ndarray, positions: np.ndarray) -> tuple[float, ...]:
    """Measure the star width of each channel of an image.

    This is the library's one second-moment star width for the color
    preview. Each star's width is the spread of its light around its center:
    the root of the mean squared distance from the center inside the
    measuring circle, weighted by the light above the sky ring's median,
    converted to the full width at half maximum a Gaussian star of that
    spread would have (`_SIGMA_TO_FWHM`). The stamps are moved so the
    star's center falls on the middle pixel. Pixels below the sky are kept
    as they are (negative), so sky noise does not widen the star. A star
    whose light inside the circle is not above zero gives no width. The
    channel's width is the median over the stars.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear image, shape (channels, height, width) or (height, width).
    positions : `numpy.ndarray`
        The stars to measure, shape (n, 2), such as from
        `find_measurable_stars`.

    Returns
    -------
    widths : `tuple` [`float`, ...]
        One width in pixels per channel (red, green and blue for a color
        image), or empty when there are no stars. A channel in which no star
        could be measured gives NaN.
    """
    if len(positions) == 0:
        return ()
    cube = image if image.ndim == 3 else image[np.newaxis]
    radius = float(resolve(STAR_WIDTH_MATCH_RADII_PIXELS).value[0])
    stamps, distance = _stamps(cube, positions)
    inside = distance <= radius
    light = stamps[:, :, inside]
    totals = light.sum(axis=2)
    spread = (light * distance[inside] ** 2).sum(axis=2) / np.where(totals > 0.0, totals, np.nan)
    sigma = np.sqrt(np.where(spread > 0.0, spread, np.nan) / 2.0)
    widths = []
    for channel in range(cube.shape[0]):
        values = sigma[:, channel]
        values = values[np.isfinite(values)]
        widths.append(float(np.median(values) * _SIGMA_TO_FWHM) if values.size else float("nan"))
    return tuple(widths)


def _average_stars(image: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Build the average, round star of each channel.

    Each star is scaled to a total of one inside the measuring circle, the
    stars are averaged, the result is averaged over rings of equal distance
    from the center (half-pixel steps) and cut at the sky ring's inner radius.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear color image, shape (3, height, width).
    positions : `numpy.ndarray`
        The stars, shape (n, 2).

    Returns
    -------
    stars : `numpy.ndarray`
        Shape (3, side, side); each channel adds up to one.
    """
    radius, inner, _ = (float(value) for value in resolve(STAR_WIDTH_MATCH_RADII_PIXELS).value)
    stamps, distance = _stamps(image, positions)
    totals = stamps[:, :, distance <= radius].sum(axis=2)
    usable = np.all(totals > 0.0, axis=1)
    mean = (stamps[usable] / totals[usable][:, :, None, None]).mean(axis=0)
    rings = np.round(distance * 2.0).astype(int).ravel()
    counts = np.bincount(rings)
    stars = np.zeros_like(mean)
    for channel in range(3):
        sums = np.bincount(rings, weights=mean[channel].ravel())
        profile = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
        stars[channel] = np.where(distance <= inner, profile[rings].reshape(distance.shape), 0.0)
        stars[channel] /= stars[channel].sum()
    return stars


def _sharpening_filter(wide_star: np.ndarray, target_star: np.ndarray, regularization: float) -> np.ndarray:
    """Work out the filter that turns the wide average star into the target.

    This is a Wiener filter: in frequency space it is the target star times
    the conjugate of the wide star, over the wide star's power plus the
    noise term. The filter is scaled so its values add up to one.

    Parameters
    ----------
    wide_star : `numpy.ndarray`
        The wide channel's average star, a square of odd side.
    target_star : `numpy.ndarray`
        Green's average star, the same shape.
    regularization : `float`
        The noise term.

    Returns
    -------
    kernel : `numpy.ndarray`
        The filter, a square of side ``2 * _FILTER_HALF_WIDTH_PIXELS + 1``,
        32-bit floats.
    """
    half = wide_star.shape[0] // 2

    def on_grid(star: np.ndarray) -> np.ndarray:
        """Put a star on the filter grid, its center on the first pixel.

        Returns
        -------
        grid : `numpy.ndarray`
            The star wrapped around the grid's corner.
        """
        grid = np.zeros((_FILTER_GRID_PIXELS, _FILTER_GRID_PIXELS))
        grid[: star.shape[0], : star.shape[1]] = star
        return np.roll(grid, (-half, -half), axis=(0, 1))

    wide, target = fft.fft2(on_grid(wide_star)), fft.fft2(on_grid(target_star))
    response = target * np.conj(wide) / (np.abs(wide) ** 2 + regularization)
    kernel = np.real(fft.ifft2(response))
    keep = _FILTER_HALF_WIDTH_PIXELS
    kernel = np.roll(kernel, (keep, keep), axis=(0, 1))[: 2 * keep + 1, : 2 * keep + 1]
    return (kernel / kernel.sum()).astype(np.float32)


def _grid_weights(length: int, count: int) -> list[np.ndarray]:
    """Give the blending weight of each region along one side of the image.

    The weight of a region is 1 at its center and falls evenly to 0 at the
    centers of its neighbors; beyond the outermost centers it stays at the
    edge value. The weights add up to one at every pixel.

    Parameters
    ----------
    length : `int`
        The number of pixels along the side.
    count : `int`
        The number of regions along the side.

    Returns
    -------
    weights : `list` [`numpy.ndarray`]
        One array of `length` weights for each region.
    """
    centers = (np.arange(count) + 0.5) * length / count
    pixels = np.arange(length)
    return [np.interp(pixels, centers, np.eye(count)[index]).astype(np.float32) for index in range(count)]


def _region_of(position: np.ndarray, shape: tuple[int, int], count: int) -> np.ndarray:
    """Say which grid region each star lies in.

    Parameters
    ----------
    position : `numpy.ndarray`
        Star positions, shape (n, 2), x then y.
    shape : `tuple` [`int`, `int`]
        The image's height and width.
    count : `int`
        The number of regions along each side.

    Returns
    -------
    regions : `numpy.ndarray`
        Shape (n, 2): the row and column of each star's region.
    """
    height, width = shape
    rows = np.clip((position[:, 1] * count / height).astype(int), 0, count - 1)
    columns = np.clip((position[:, 0] * count / width).astype(int), 0, count - 1)
    return np.column_stack([rows, columns])


def _apply_filters(channel: np.ndarray, kernels: list[list[np.ndarray]]) -> np.ndarray:
    """Filter one channel with a filter for each grid region, blended smoothly.

    The channel is mirrored at the image edges. Pixels with no data (zero or
    below, or not a number, such as the empty border of a registered stack)
    are filled with the channel's median for the filtering and set back
    afterward, so the border does not ring into the picture. Each region's
    filter is used
    from the centers of the regions on one side to those on the other, with
    the weights of `_grid_weights`.

    Parameters
    ----------
    channel : `numpy.ndarray`
        One linear channel.
    kernels : `list` [`list` [`numpy.ndarray`]]
        The filter of each region, by row and column, from
        `_sharpening_filter`; all the same size.

    Returns
    -------
    filtered : `numpy.ndarray`
        The filtered channel, 32-bit floats, never below zero.
    """
    count = len(kernels)
    height, width = channel.shape
    empty = ~np.isfinite(channel) | (channel <= 0.0)
    fill = np.float32(np.median(channel[~empty])) if np.any(~empty) else np.float32(0.0)
    half = kernels[0][0].shape[0] // 2
    padded = np.pad(np.where(empty, fill, channel).astype(np.float32), half, mode="reflect")
    row_weights, column_weights = _grid_weights(height, count), _grid_weights(width, count)
    filtered = np.zeros((height, width), dtype=np.float32)
    for row in range(count):
        rows = np.flatnonzero(row_weights[row] > 0.0)
        row_start, row_stop = int(rows[0]), int(rows[-1]) + 1
        for column in range(count):
            columns = np.flatnonzero(column_weights[column] > 0.0)
            column_start, column_stop = int(columns[0]), int(columns[-1]) + 1
            piece = padded[row_start : row_stop + 2 * half, column_start : column_stop + 2 * half]
            result = signal.oaconvolve(piece, kernels[row][column], mode="valid").astype(np.float32)
            weight = np.outer(
                row_weights[row][row_start:row_stop], column_weights[column][column_start:column_stop]
            )
            filtered[row_start:row_stop, column_start:column_stop] += result * weight
    del padded
    np.maximum(filtered, 0.0, out=filtered)
    filtered[empty] = channel[empty]
    return filtered


@dataclass(frozen=True)
class _BrightStar:
    """The surroundings of one bright star, where the filter is not used.

    Attributes
    ----------
    window : `tuple` [`slice`, `slice`]
        The rows and columns around the star, out to the guard's outer
        distance.
    keep : `numpy.ndarray`
        For each pixel of the window, how much is left unfiltered: 1 out to
        the guard's first distance, falling evenly to 0 at the second.
    """

    window: tuple[slice, slice]
    keep: np.ndarray


def _bright_stars(image: np.ndarray) -> list[_BrightStar]:
    """Find the bright stars and how far around each the filter is not used.

    A bright star is a group of touching pixels at or above the saturation
    share (the last number of `STAR_WIDTH_MATCH_STAR_SELECTION`) of the
    smallest channel's brightest value, in any channel.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear color image, shape (3, height, width).

    Returns
    -------
    stars : `list` [`_BrightStar`]
        One entry per bright star; empty when there is none.
    """
    share = float(resolve(STAR_WIDTH_MATCH_STAR_SELECTION).value[3])
    near, far = (float(value) for value in resolve(STAR_WIDTH_MATCH_BRIGHT_STAR_GUARD_PIXELS).value)
    limit = share * min(float(np.nanmax(channel)) for channel in image)
    labels, count = ndimage.label(np.any(image >= limit, axis=0))
    if count == 0:
        return []
    reach = int(np.ceil(far))
    height, width = labels.shape
    stars = []
    for index, found in enumerate(ndimage.find_objects(labels), start=1):
        if found is None:
            continue
        rows = slice(max(found[0].start - reach, 0), min(found[0].stop + reach, height))
        columns = slice(max(found[1].start - reach, 0), min(found[1].stop + reach, width))
        distance = ndimage.distance_transform_edt(labels[rows, columns] != index)
        keep = np.clip((far - distance) / (far - near), 0.0, 1.0).astype(np.float32)
        stars.append(_BrightStar((rows, columns), keep))
    return stars


def _typical_colors(image: np.ndarray, positions: np.ndarray) -> tuple[float, ...]:
    """Give the color of the typical measured star, channel over green.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear color image, shape (3, height, width).
    positions : `numpy.ndarray`
        The measured stars, shape (n, 2).

    Returns
    -------
    colors : `tuple` [`float`, ...]
        For red, green and blue, the median over the stars of the channel's
        light over green's light inside the measuring circle (green's is 1).
    """
    radius = float(resolve(STAR_WIDTH_MATCH_RADII_PIXELS).value[0])
    stamps, distance = _stamps(image, positions)
    totals = stamps[:, :, distance <= radius].sum(axis=2)
    usable = totals[:, 1] > 0.0
    return tuple(float(np.median(totals[usable, channel] / totals[usable, 1])) for channel in range(3))


def _guard_bright_stars(
    filtered: np.ndarray, channel: np.ndarray, green: np.ndarray, color: float, stars: list[_BrightStar]
) -> None:
    """Give a filtered channel green's shape near bright stars.

    Around each bright star the channel is set to green's light above the
    sky times the color of the typical measured star (`_typical_colors`),
    plus the channel's sky. The star takes green's shape, so neither the
    skirt nor the filter's ringing around the flat core is left. The star's
    own color cannot be measured: its core is cut flat, and more of the
    narrow green star is cut than of the wide red one, so the ratio of the
    light that is left reads too red (a blue M 45 star came out pink). Away
    from the star this fades into the filtered values (see
    `STAR_WIDTH_MATCH_BRIGHT_STAR_GUARD_PIXELS`).

    Parameters
    ----------
    filtered : `numpy.ndarray`
        The filtered channel, changed in place.
    channel : `numpy.ndarray`
        The channel's old values.
    green : `numpy.ndarray`
        The green channel.
    color : `float`
        The typical star's light in this channel over its light in green.
    stars : `list` [`_BrightStar`]
        The bright stars, from `_bright_stars`.
    """
    channel_sky = float(np.nanmedian(channel[::4, ::4]))
    green_sky = float(np.nanmedian(green[::4, ::4]))
    for star in stars:
        shaped = channel_sky + (np.nan_to_num(green[star.window]) - green_sky) * color
        filtered[star.window] += star.keep * (shaped - filtered[star.window])


def match_star_widths(image: np.ndarray) -> tuple[np.ndarray | None, StarWidthMatch]:
    """Sharpen the color channels whose stars are wider than green's.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear color image, channels first, shape (3, height, width).

    Returns
    -------
    matched : `numpy.ndarray` or `None`
        The new image, or `None` when nothing was changed.
    result : `StarWidthMatch`
        What was found and done.
    """
    if image.ndim != 3 or image.shape[0] != 3:
        return None, StarWidthMatch(applied=False, reason="the image is not a color image")
    image = np.asarray(image, dtype=np.float32)
    positions = find_measurable_stars(image)
    minimum = int(resolve(STAR_WIDTH_MATCH_MINIMUM_STARS).value)
    if len(positions) < minimum:
        return None, StarWidthMatch(
            applied=False,
            reason=f"only {len(positions)} stars could be measured, fewer than {minimum}",
            star_count=len(positions),
        )
    before = measure_channel_widths(image, positions)
    tolerance, largest = (float(value) for value in resolve(STAR_WIDTH_MATCH_RATIO_LIMITS).value)
    ratios = {name: before[index] / before[1] for index, name in ((0, "red"), (2, "blue"))}
    text = f"red {ratios['red']:.2f}, blue {ratios['blue']:.2f} of green"
    if max(ratios.values()) > largest:
        return None, StarWidthMatch(
            applied=False,
            reason=f"a channel's stars are more than {largest:.1f} times as wide as green's ({text}), "
            "which is not a lens effect",
            star_count=len(positions),
            widths_before=before,
        )
    wide = [(index, name) for index, name in ((0, "red"), (2, "blue")) if ratios[name] > tolerance]
    if not wide:
        return None, StarWidthMatch(
            applied=False,
            reason=f"the channels' star widths already agree ({text}; limit {tolerance:.2f})",
            star_count=len(positions),
            widths_before=before,
        )
    regularization = float(resolve(STAR_WIDTH_MATCH_REGULARIZATION).value)
    count = int(resolve(STAR_WIDTH_MATCH_GRID).value)
    whole_frame = _average_stars(image, positions)
    regions = _region_of(positions, image.shape[1:], count)
    region_stars = [[whole_frame] * count for _ in range(count)]
    for row in range(count):
        for column in range(count):
            inside = (regions[:, 0] == row) & (regions[:, 1] == column)
            if int(inside.sum()) >= minimum:
                region_stars[row][column] = _average_stars(image, positions[inside])
    bright = _bright_stars(image)
    colors = _typical_colors(image, positions) if bright else (1.0, 1.0, 1.0)
    matched = image.copy()
    gains: dict[str, float] = {}
    for index, name in wide:
        kernels = [
            [_sharpening_filter(stars[index], stars[1], regularization) for stars in row_stars]
            for row_stars in region_stars
        ]
        gains[name] = float(np.sqrt(np.sum(kernels[count // 2][count // 2].astype(np.float64) ** 2)))
        filtered = _apply_filters(image[index], kernels)
        _guard_bright_stars(filtered, image[index], image[1], colors[index], bright)
        matched[index] = filtered
        del filtered
    after = measure_channel_widths(matched, positions)
    return matched, StarWidthMatch(
        applied=True,
        star_count=len(positions),
        widths_before=before,
        widths_after=after,
        sharpened=tuple(name for _, name in wide),
        filter_noise_gains=gains,
    )


def match_star_widths_in_file(input_path: str, output_path: str) -> StarWidthMatch:
    """Match the star widths of a linear color FITS image and save the result.

    Nothing is written unless a channel is sharpened. A file that cannot be
    read is not an error: the step is skipped and the result says why.

    Parameters
    ----------
    input_path : `str`
        Path of the linear color image.
    output_path : `str`
        Path to write when a channel is sharpened. The header of the input
        is kept.

    Returns
    -------
    result : `StarWidthMatch`
        What was found and done.
    """
    try:
        data = read_data(input_path)
        header = read_header(input_path)
    except (OSError, ValueError, KeyError) as error:
        return StarWidthMatch(applied=False, reason=f"the image could not be read ({error})")
    if data is None:
        return StarWidthMatch(applied=False, reason="the image has no data")
    matched, result = match_star_widths(np.asarray(data))
    del data
    if matched is not None:
        write_image(output_path, matched, header)
    logger.info("%s.", result.step_sentence())
    return result
