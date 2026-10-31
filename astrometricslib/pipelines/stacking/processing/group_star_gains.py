"""Measuring each exposure group's brightness scale on isolated stars.

For an imaging stack the gain between two exposure groups is the median
ratio of the aperture fluxes of isolated stars that are not saturated in
either group. A group whose faintest or brightest quarter of stars
disagrees with that median is left out. A group with too few usable stars
falls back to the pixel measurement in `group_gains`.

The overview of the whole method is in the `exposure_groups` module
docstring.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from astrometricslib.foundation.constants import FULL_SCALE_COUNTS
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import measure_aperture_photometry
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids
from astrometricslib.pipelines.stacking.processing.group_gains import (
    DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
    PLAUSIBLE_GAIN_RANGE,
    GroupGainMeasurement,
    estimate_sky_level,
    is_plausible_gain,
    measure_group_gains,
    record_sky_ratios,
)

logger = logging.getLogger(__name__)


# Star-flux gains (`measure_group_gains_with_stars`): the way an imaging
# group's brightness scale is measured first. The pixel ratios above are the
# fallback for a group that shares too few clean stars with the reference
# (STAR_GAIN_MINIMUM_STARS) and the method for every spectral stack, which has
# no field of stars. The pixel method above looks at pixels at least
# GAIN_SOURCE_SIGMAS noises above the sky. On a Nikon D5300 stack that floor is
# only 5-8% of the sky level, so those pixels are mostly sky gradients and the
# difference in star width between two nights, and the mid-range and bright-end
# ratios disagree even when the camera is linear (investigated 2026-10-13:
# NGC 6888 60 s read 0.96 mid-range and 1.06 at the bright end, and a real
# aperture measurement of the same stars gave 1.07 at every brightness). So the
# gain is the median ratio of each star's own flux: the light inside a circle
# of STAR_GAIN_APERTURE_RADIUS_PIXELS minus the sky measured in the ring
# between STAR_GAIN_SKY_INNER_PIXELS and STAR_GAIN_SKY_OUTER_PIXELS, from the
# same isolated stars in both groups. A circle that large keeps the same
# fraction of a star's light whether the star is a pixel wider or narrower in
# one group, which the pixel ratio cannot (see
# documentation/library_design/validation/stacking.md, STK-19).
STAR_GAIN_APERTURE_RADIUS_PIXELS = 14.0

STAR_GAIN_SKY_INNER_PIXELS = 19.0

STAR_GAIN_SKY_OUTER_PIXELS = 25.0

# How many of the reference's brightest stars are looked for. A star is
# isolated when no other detected star lies within the sky ring's outer
# radius, so a neighbour's light does not leak into its sky measurement or
# aperture. The brightest stars are the ones most likely to be clipped, so the
# search is wide enough that plenty of unclipped ones are left.
STAR_GAIN_DETECTION_COUNT = 3000

# A star is measured only when no pixel in its circle or sky ring, in either
# group, reaches STAR_GAIN_CLIP_FRACTION_OF_CEILING of the camera's clip
# ceiling (as a fraction of the stack's 65535 full scale, see
# `star_clip_level`). A stack's clip level cannot be read off the stack: the
# flat field and Siril's normalisation scale every pixel, so the Nikon D5300
# group stacks of NGC 6888 saturate anywhere from 0.21 to 0.38 where the
# camera's 16383 ADU ceiling is 0.25. And a star close to the ceiling is
# already biased: with the clip level swept on NGC 6888, the 120 s group's
# gain read 0.757, 0.774, 0.778, 0.782 and 0.795 for levels of 0.10, 0.15,
# 0.20, 0.237 and 0.34 (stars whose pixels all stay under it), and its
# bright quarter drifted from 0.759 to 0.833 while the faint quarter stayed
# at 0.76. At 0.10 (0.4 of the ceiling) the quarters agree to 0.4%, at 0.15
# (0.6) to 1.3-2.7%. Half the ceiling sits in that quiet range and still
# leaves 1,000 or more unclipped stars on the Nikon fields tried (see the
# tests and documentation of the validation of 2026-10-13).
STAR_GAIN_CLIP_FRACTION_OF_CEILING = 0.5

# A star's flux must stand STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE times above the
# noise of an empty aperture in each group (see
# `_measure_empty_aperture_noise`). Why it matters: a faint star's ratio is
# noisy, and ranking stars by the noisy reference flux makes the faintest
# quarter look too bright. On M 106 (10 frames of 120 s against 51 frames of
# 182 s) the faintest quarter of 1,600 stars read 1.62 against an overall
# 1.137, and in the faintest octiles half the stars' ratios spread over 0.9
# to 2.8. Dropping stars below 10 times the noise removes that bias (see
# documentation/library_design/validation/stacking.md, STK-19).
STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE = 10.0

# How many empty apertures are measured to find the noise, how far from every
# detected star each must lie (the sky ring's outer radius twice, so no ring
# touches a star's wings), and the seed that keeps the choice repeatable.
STAR_GAIN_EMPTY_APERTURES = 200

STAR_GAIN_NOISE_PROBE_STARS = 50

STAR_GAIN_EMPTY_APERTURE_ATTEMPTS = 3000

STAR_GAIN_EMPTY_APERTURE_SEED = 20261013

# The fewest stars two groups must share (isolated, with no pixel at or above
# the saturation level in the circle or its sky ring, in either group) for
# the star gain to be used, and STAR_GAIN_MINIMUM_STARS_PER_QUARTILE the fewest
# in the faint quarter and the bright quarter for the linearity test. A group
# below either count falls back to the pixel method. The counts are design
# choices (see documentation/library_design/validation/stacking.md, STK-19).
STAR_GAIN_MINIMUM_STARS = 16

STAR_GAIN_MINIMUM_STARS_PER_QUARTILE = 4


@dataclass
class StarFluxRatios:
    """The star-flux ratios of one group against the reference group.

    Attributes
    ----------
    gain : `float`
        The median of the group's flux per second over the reference's, over
        all the stars used.
    faint_ratio : `float`
        The same median over the faintest quarter of the stars (by reference
        flux).
    bright_ratio : `float`
        The same median over the brightest quarter.
    stars_used : `int`
        How many stars both groups measured cleanly.
    """

    gain: float
    faint_ratio: float
    bright_ratio: float
    stars_used: int


def _photometry_plane(image: np.ndarray) -> np.ndarray:
    """Reduce an image to the one 2-D plane the star fluxes are measured on.

    Returns
    -------
    plane : `numpy.ndarray`
        The image itself when it is 2-D, otherwise the mean over its first
        (colour) axis, as `float32`.
    """
    array = np.asarray(image, dtype=np.float32)
    return array if array.ndim == 2 else array.mean(axis=0)


def _usable_plane(mask: np.ndarray) -> np.ndarray:
    """Reduce a usable-pixel mask to 2-D: a pixel is usable in every colour.

    Returns
    -------
    usable : `numpy.ndarray`
        A 2-D boolean array.
    """
    mask = np.asarray(mask, dtype=bool)
    return mask if mask.ndim == 2 else mask.all(axis=0)


def find_isolated_stars(stars: np.ndarray) -> np.ndarray:
    """Keep the stars that have no detected neighbour nearby.

    Parameters
    ----------
    stars : `numpy.ndarray`
        Star centres, shape ``(n, 2)`` as (row, column), as from
        `detect_star_centroids`.

    Returns
    -------
    isolated : `numpy.ndarray`
        The stars with no other star within `STAR_GAIN_SKY_OUTER_PIXELS` of
        them, so a neighbour's light does not leak into the aperture or the
        sky ring.
    """
    if len(stars) < 2:
        return stars
    neighbour_distance = cKDTree(stars).query(stars, k=2)[0][:, 1]
    return stars[neighbour_distance > STAR_GAIN_SKY_OUTER_PIXELS]


def _measure_star_fluxes(plane: np.ndarray, usable: np.ndarray, stars: np.ndarray) -> np.ndarray:
    """Measure the sky-subtracted flux of each star in one image.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D image in counts per second.
    usable : `numpy.ndarray`
        2-D, `True` where a pixel can be trusted (covered and below the
        saturation level).
    stars : `numpy.ndarray`
        Star centres, shape ``(n, 2)``, as (row, column).

    Returns
    -------
    fluxes : `numpy.ndarray`
        One flux per star in the image's own units, or NaN for a star with any
        untrusted pixel inside its sky ring's outer radius, which includes the
        aperture. That is how a star with a clipped core is left out.
    """
    reach = math.ceil(STAR_GAIN_SKY_OUTER_PIXELS)
    offsets = np.arange(-reach, reach + 1)
    inside_ring = np.hypot(*np.meshgrid(offsets, offsets)) <= STAR_GAIN_SKY_OUTER_PIXELS
    fluxes = np.full(len(stars), np.nan)
    for index, (row, column) in enumerate(stars):
        center_row, center_column = round(row), round(column)
        window = usable[
            center_row - reach : center_row + reach + 1, center_column - reach : center_column + reach + 1
        ]
        if window.shape != inside_ring.shape or not window[inside_ring].all():
            continue
        measurement = measure_aperture_photometry(
            plane,
            float(column),
            float(row),
            radius=STAR_GAIN_APERTURE_RADIUS_PIXELS,
            annulus_inner=STAR_GAIN_SKY_INNER_PIXELS,
            annulus_outer=STAR_GAIN_SKY_OUTER_PIXELS,
            cutout_radius=reach + 1,
            saturation_threshold_adu=math.inf,
        )
        fluxes[index] = measurement.net_flux_adu
    return fluxes


def _measure_empty_aperture_noise(
    difference_plane: np.ndarray, usable: np.ndarray, stars: np.ndarray, random_generator: np.random.Generator
) -> float:
    """Measure the noise a star's flux ratio carries, from empty apertures.

    The same circle and sky ring as a star's are placed at random positions at
    least twice the sky ring's outer radius from every detected star, on the
    difference of the two groups' images (the group minus the reference times
    the gain), and the flux in each is measured. In that difference the sky
    structure and faint stars that both groups show cancel, and what is left is
    the noise that is not shared, which is what makes a star's ratio uncertain.
    The flux of an empty aperture is noise with a mean of zero (the ring's
    median is subtracted), but `measure_aperture_photometry` reports negative
    fluxes as 0, so the spread is read from the 84.13th percentile of the
    fluxes, which is one standard deviation above the mean of a bell curve and
    is not touched by the zeros.

    Parameters
    ----------
    difference_plane : `numpy.ndarray`
        A 2-D image: the group minus the reference scaled by the gain.
    usable : `numpy.ndarray`
        2-D, `True` where a pixel can be trusted in both groups.
    stars : `numpy.ndarray`
        Detected star centres, shape ``(n, 2)`` as (row, column), which the
        empty apertures stay away from.
    random_generator : `numpy.random.Generator`
        Source of the random positions.

    Returns
    -------
    noise : `float`
        The noise of an aperture flux in the image's units, or NaN when fewer
        than half of `STAR_GAIN_EMPTY_APERTURES` positions could be found.
    """
    margin = math.ceil(STAR_GAIN_SKY_OUTER_PIXELS) + 1
    height, width = difference_plane.shape
    tree = cKDTree(stars) if len(stars) else None
    positions = []
    for _ in range(STAR_GAIN_EMPTY_APERTURE_ATTEMPTS):
        row = random_generator.uniform(margin, height - margin)
        column = random_generator.uniform(margin, width - margin)
        if tree is not None and tree.query([row, column])[0] <= 2.0 * STAR_GAIN_SKY_OUTER_PIXELS:
            continue
        positions.append((row, column))
        if len(positions) >= 4 * STAR_GAIN_EMPTY_APERTURES:
            break
    fluxes = _measure_star_fluxes(difference_plane, usable, np.array(positions).reshape(-1, 2))
    fluxes = fluxes[np.isfinite(fluxes)][:STAR_GAIN_EMPTY_APERTURES]
    if fluxes.size < STAR_GAIN_EMPTY_APERTURES // 2:
        return float("nan")
    return float(np.percentile(fluxes, 84.13))


def measure_star_flux_ratios(
    per_second_images: list[np.ndarray], star_masks: list[np.ndarray], reference_index: int
) -> list[StarFluxRatios | None]:
    """Compare each group's stars with the reference group's, star by star.

    Each isolated star of the reference (`find_isolated_stars`) is measured in
    every group with a fixed-size circle and a sky ring (see
    `STAR_GAIN_APERTURE_RADIUS_PIXELS`). A star counts for a group only when it
    is clean in both that group and the reference and stands at least
    `STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE` above the noise of the two groups'
    difference (`_measure_empty_aperture_noise`; a faint star's ratio is noisy,
    and ranking stars by a noisy flux makes the faint ones look too bright).
    The group's gain is the median flux ratio. The faint and bright quarters of
    the stars (ranked by reference flux) give two more medians: a linear camera
    gives the same ratio for faint and bright stars, while a bright end that is
    compressed or clipped does not.

    Parameters
    ----------
    per_second_images : `list` [`numpy.ndarray`]
        Each group's image in counts per second, all the same shape.
    star_masks : `list` [`numpy.ndarray`]
        For each group, `True` where its pixels are covered, not saturated and
        under `star_clip_level`.
    reference_index : `int`
        The group the others are compared with.

    Returns
    -------
    ratios : `list` [`StarFluxRatios` or `None`]
        One entry per group. `None` for the reference and for a group that
        shares fewer than `STAR_GAIN_MINIMUM_STARS` clean stars with it, or
        too few per quarter (`STAR_GAIN_MINIMUM_STARS_PER_QUARTILE`), so the
        caller falls back to another method.
    """
    reference_plane = _photometry_plane(per_second_images[reference_index])
    detected = detect_star_centroids(reference_plane, count=STAR_GAIN_DETECTION_COUNT)
    stars = find_isolated_stars(detected)
    results: list[StarFluxRatios | None] = [None] * len(per_second_images)
    if len(stars) < STAR_GAIN_MINIMUM_STARS:
        return results
    random_generator = np.random.default_rng(STAR_GAIN_EMPTY_APERTURE_SEED)
    reference_usable = _usable_plane(star_masks[reference_index])
    reference_fluxes = _measure_star_fluxes(reference_plane, reference_usable, stars)
    for index, image in enumerate(per_second_images):
        if index == reference_index:
            continue
        plane = _photometry_plane(image)
        usable = _usable_plane(star_masks[index])
        fluxes = _measure_star_fluxes(plane, usable, stars)
        measured = np.isfinite(reference_fluxes) & np.isfinite(fluxes) & (reference_fluxes > 0) & (fluxes > 0)
        if int(np.count_nonzero(measured)) < STAR_GAIN_MINIMUM_STARS:
            continue
        # A first gain from the brightest stars, whose noise hardly matters,
        # lets the difference image be formed.
        brightest = np.flatnonzero(measured)[np.argsort(reference_fluxes[measured])[::-1]]
        brightest = brightest[:STAR_GAIN_NOISE_PROBE_STARS]
        first_gain = float(np.median(fluxes[brightest] / reference_fluxes[brightest]))
        noise = _measure_empty_aperture_noise(
            plane - first_gain * reference_plane, usable & reference_usable, detected, random_generator
        )
        if not np.isfinite(noise) or noise <= 0:
            continue
        clean = measured & (reference_fluxes >= STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE * noise)
        if int(np.count_nonzero(clean)) < STAR_GAIN_MINIMUM_STARS:
            continue
        order = np.argsort(reference_fluxes[clean])
        ratios = (fluxes[clean] / reference_fluxes[clean])[order]
        quarter = len(ratios) // 4
        if quarter < STAR_GAIN_MINIMUM_STARS_PER_QUARTILE:
            continue
        results[index] = StarFluxRatios(
            gain=float(np.median(ratios)),
            faint_ratio=float(np.median(ratios[:quarter])),
            bright_ratio=float(np.median(ratios[-quarter:])),
            stars_used=len(ratios),
        )
    return results


def measure_group_gains_with_stars(
    per_second_images: list[np.ndarray],
    star_masks: list[np.ndarray],
    trusted_masks: list[np.ndarray],
    reference_index: int,
    tolerance: float = DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
) -> list[GroupGainMeasurement]:
    """Measure each group's brightness scale from its stars, else its pixels.

    The gain of a group is the median star-flux ratio (see
    `measure_star_flux_ratios`). The group is marked `nonlinear` when the
    faint-star or bright-star ratio is more than `tolerance` from the gain,
    as a fraction of the gain. The `GroupGainMeasurement` fields keep their old
    names: `bright_end_ratio` is the bright-star ratio and `disagreement` is
    the larger distance of the two quarter ratios from the gain. A group with
    too few clean stars is measured with the pixel ratios of
    `measure_group_gains` instead. A gain outside `PLAUSIBLE_GAIN_RANGE` is
    not believed and the group keeps a gain of 1, as in that function. Every
    group also gets its sky level relative to the reference's (see
    `SKY_RATIO_LIMIT`).

    Parameters
    ----------
    per_second_images : `list` [`numpy.ndarray`]
        Each group's image in counts per second.
    star_masks : `list` [`numpy.ndarray`]
        For each group, `True` where its pixels are covered and not saturated.
        The stars are judged on these.
    trusted_masks : `list` [`numpy.ndarray`]
        `star_masks` with a clipped-at-zero group's near-zero pixels removed.
        The sky level and the pixel fallback use these.
    reference_index : `int`
        The group the others are compared with. Its gain is 1.
    tolerance : `float`, optional
        How far the faint-star or bright-star ratio may be from the gain, as
        a fraction of the gain.

    Returns
    -------
    measurements : `list` [`GroupGainMeasurement`]
        One entry per group.
    """
    sky_levels = [
        estimate_sky_level(image, mask) for image, mask in zip(per_second_images, trusted_masks, strict=True)
    ]
    star_ratios = measure_star_flux_ratios(per_second_images, star_masks, reference_index)
    fallback_indices = {
        index for index, ratios in enumerate(star_ratios) if index != reference_index and ratios is None
    }
    if fallback_indices:
        measurements = measure_group_gains(
            per_second_images,
            trusted_masks,
            reference_index,
            tolerance,
            only=fallback_indices,
            sky_levels=sky_levels,
        )
    else:
        measurements = [GroupGainMeasurement(sky_level=sky_level) for sky_level in sky_levels]
        measurements[reference_index] = GroupGainMeasurement(
            gain=1.0,
            bright_end_ratio=1.0,
            disagreement=0.0,
            sky_level=sky_levels[reference_index],
            measured=True,
            method="reference",
        )
    for index, ratios in enumerate(star_ratios):
        if ratios is None:
            continue
        if not is_plausible_gain(ratios.gain):
            logger.warning(
                "Exposure group %d measured a star-flux gain of %.3g, outside %s; using 1 instead.",
                index,
                ratios.gain,
                PLAUSIBLE_GAIN_RANGE,
            )
            measurements[index] = GroupGainMeasurement(
                bright_end_ratio=ratios.bright_ratio,
                sky_level=sky_levels[index],
                method="stars",
                stars_used=ratios.stars_used,
                faint_ratio=ratios.faint_ratio,
            )
            continue
        disagreement = (
            max(abs(ratios.bright_ratio - ratios.gain), abs(ratios.faint_ratio - ratios.gain)) / ratios.gain
        )
        measurements[index] = GroupGainMeasurement(
            gain=ratios.gain,
            bright_end_ratio=ratios.bright_ratio,
            disagreement=disagreement,
            nonlinear=disagreement > tolerance,
            sky_level=sky_levels[index],
            measured=True,
            method="stars",
            stars_used=ratios.stars_used,
            faint_ratio=ratios.faint_ratio,
        )
    record_sky_ratios(measurements, reference_index)
    return measurements


def star_clip_level(clip_ceiling_adu: float | None = None) -> float:
    """Give the pixel value above which a star is not trusted for the gain.

    Parameters
    ----------
    clip_ceiling_adu : `float`, optional
        The camera's clip ceiling in ADU (from its camera profile). Without
        it the ceiling is taken to be the 16-bit full scale.

    Returns
    -------
    level : `float`
        `STAR_GAIN_CLIP_FRACTION_OF_CEILING` of the ceiling, as a fraction of
        `FULL_SCALE_COUNTS` (the units of a Siril stack).
    """
    ceiling = FULL_SCALE_COUNTS if clip_ceiling_adu is None or clip_ceiling_adu <= 0 else clip_ceiling_adu
    return STAR_GAIN_CLIP_FRACTION_OF_CEILING * min(1.0, ceiling / FULL_SCALE_COUNTS)
