"""Choosing the empty apertures of a stack.

* `choose_apertures` and `choose_apertures_with_reason` pick up to 100 empty
  apertures and 10 bright, isolated reference stars on one stack, as sky
  positions (right ascension and declination, not pixels).
* `place_empty_apertures` draws the aperture positions. They stay clear of the
  edge, of bright stars found by the star finder, and of faint sources found
  by a mask made from the stack's own pixels (`analyze_faint_sources` and
  `faint_source_distance`), so a patch is chosen by a pixel mask made before
  placement, never by its own sum. The mask is not perfect: a 3.5-sigma noise
  peak near a patch can exclude it, so the noise is biased low by a small
  amount that was measured (validation entry STK-36).

The set itself is described in `empty_apertures.py` and kept in
`aperture_storage.py`.
"""

import math
from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree

from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    REFERENCE_RING_GAP_PX,
    REFERENCE_RING_WIDTH_PX,
    UNSPECIFIED_SETUP,
    ApertureSet,
    StackDepth,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_TO_SIGMA,
    RING_OUTER_PX,
    SaturationCheck,
    pixel_scale_arcsec,
    pixels_to_sky,
    signed_aperture_sum,
)
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids

# The number of empty apertures. The spread of n samples has a relative error
# of about 1.17 / sqrt(n) (see `MAD_SIGMA_ERROR_FACTOR`): about 12% for 100.
# More would cost little but the stack must have the room; a typical stack
# holds tens of thousands of apertures of this size, so 100 do not crowd it.
# A design choice.
NOISE_APERTURE_COUNT = 100

# The reference stars are measured in a larger aperture than the noise, so
# that their brightness does not change when the seeing changes. The radius is
# 3 times the star width (FWHM) of the stack that chose the stars: a round
# Gaussian star of that width has all but about 1e-11 of its light inside it,
# and a star with wings (a Moffat profile) keeps about 95% inside. The sky ring
# starts 3 px beyond the aperture and is 5 px wide, the same gap and width as
# the noise ring has around the noise aperture. The radius is never below
# 8 px (twice the noise aperture's), which a star width under 2.7 px would
# otherwise give.
REFERENCE_APERTURE_FWHM_MULTIPLE = 3.0

REFERENCE_APERTURE_MINIMUM_RADIUS_PX = 8.0

# How many reference stars fix the flux scale, and the fewest that must be
# measurable on a stack. The median of 10 stars is steady against one star that
# varies or has a neighbor; 5 is the smallest set that still has a median.
REFERENCE_STAR_COUNT = 10

MINIMUM_USABLE_REFERENCE_STARS = 5

# A stack needs this many usable apertures (20 gives a 28% error on the
# spread).
MINIMUM_USABLE_APERTURES = 20

# An empty aperture stays this far from every detected star, so a star's wings
# cannot reach the aperture or its ring: twice the ring's outer radius.
STAR_CLEARANCE_PX = 2.0 * RING_OUTER_PX

# Faint sources are kept out of the empty apertures by a mask made from the
# stack itself, before any aperture is drawn (so the rule never looks at an
# aperture's sum). The stack is high-pass filtered (the Gaussian-smoothed
# image at this width is taken away, about the ring's outer radius, so a
# smooth sky gradient or a broad nebula halo is not flagged), then smoothed at
# each width below (a faint star is the size of the star width, 1 to 2 px; a
# small galaxy or a knot of nebula is several pixels), and every pixel more
# than `FAINT_SOURCE_THRESHOLD_SIGMAS` robust sigmas above zero at any width is
# flagged. The sigma of each width is the median absolute deviation of that
# smoothed image over the pixels that hold data (not the empty edge filled
# with the median, which would pull the scale down), which stars cover too
# little of to move it, and which keeps the correlation of the stacked noise
# that smoothing does not remove. A crowded field raises the scale and so the
# effective threshold (validation entry STK-36 gives the measured rise).
FAINT_SOURCE_HIGH_PASS_SIGMA_PX = 12.0

FAINT_SOURCE_SMOOTHING_SIGMAS_PX = (1.5, 3.0, 6.0)

# A smoothed pixel is flagged above this many robust sigmas. For pure noise a
# bell curve passes 3.5 sigma in 2.3e-4 of its pixels, so a few apertures in a
# hundred lose a position to noise alone; the measured loss in the noise
# (a downward bias) is in validation entry STK-36. A design choice, checked
# on a synthetic noise field and on the M 57 stack.
FAINT_SOURCE_THRESHOLD_SIGMAS = 3.5

# An aperture stays this far from every flagged pixel (measured from the
# rounded pixel of its center): the ring's outer radius plus 1 px. Rounding the
# center to a pixel moves it by up to 0.71 px, and the ring reaches the pixels
# whose centers lie within the outer radius, so 1 px more guarantees that no
# flagged pixel lies in the aperture or in its sky ring.
FAINT_SOURCE_CLEARANCE_PX = RING_OUTER_PX + 1.0

# Two apertures stay at least this far apart: twice the ring's outer radius.
# Then no two rings share a pixel, so the sky level taken for one aperture
# does not feed another, and the sums are independent of each other.
APERTURE_SEPARATION_PX = 2.0 * RING_OUTER_PX

# Apertures stay this far from the edge so the whole ring is on the image.
EDGE_MARGIN_PX = math.ceil(RING_OUTER_PX) + 1

# How far an empty aperture stays from a pixel that one of the exposure groups
# of a group stack does not cover (its shifted border; see
# `ExposureGroupCombination.covered_masks`). The combine leaves a group out
# where its mask is empty, so the weights, and the noise, change across that
# border; the aperture's sky ring must not reach it. The outer ring radius
# plus one pixel is the same reach `signed_aperture_sum` keeps from the image
# edge. Design choice, not tuned.
UNCOVERED_CLEARANCE_PX = RING_OUTER_PX + 1.0

# How many stars to detect when choosing apertures and the reference stars.
DETECTED_STAR_COUNT = 400

# Random positions are drawn from a generator with this seed, so choosing
# apertures twice on the same stack gives the same apertures.
APERTURE_SEED = 20261023


def choose_apertures(
    plane: np.ndarray,
    wcs: WCS,
    wcs_source: str,
    created_from: str,
    setup_key: str = UNSPECIFIED_SETUP,
    saturation: SaturationCheck | None = None,
    depth: StackDepth | None = None,
    covered_mask: np.ndarray | None = None,
    sky_from_plate_solve: bool = False,
) -> ApertureSet | None:
    """Choose empty-sky apertures and reference stars on one stack.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    wcs_source : `str`
        Where the solution came from, kept with the set.
    created_from : `str`
        The stack's file name, kept with the set.
    setup_key : `str`, optional
        The camera, optic and reference exposure the set belongs to.
    saturation : `SaturationCheck`, optional
        Keeps stars that are saturated in the registration reference frame
        out of the reference stars. Left out, the stack's own 99.99th
        percentile screens the brightest stars instead.
    depth : `StackDepth`, optional
        The stack's frame counts, kept with the set. A set without them is
        never replaced for depth.
    covered_mask : `numpy.ndarray`, optional
        `True` where every exposure group of a group stack has data (see
        `place_empty_apertures`).
    sky_from_plate_solve : `bool`, optional
        True when ``wcs`` is a plate solve of this stack, so the stored sky
        positions are trusted (see `ApertureSet`).

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        See `choose_apertures_with_reason`.
    """
    return choose_apertures_with_reason(
        plane, wcs, wcs_source, created_from, setup_key, saturation, depth, covered_mask, sky_from_plate_solve
    )[0]


def choose_apertures_with_reason(
    plane: np.ndarray,
    wcs: WCS,
    wcs_source: str,
    created_from: str,
    setup_key: str = UNSPECIFIED_SETUP,
    saturation: SaturationCheck | None = None,
    depth: StackDepth | None = None,
    covered_mask: np.ndarray | None = None,
    sky_from_plate_solve: bool = False,
) -> tuple[ApertureSet | None, str]:
    """Choose empty-sky apertures and reference stars, and say why not if none.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    wcs_source : `str`
        Where the solution came from, kept with the set.
    created_from : `str`
        The stack's file name, kept with the set.
    setup_key : `str`, optional
        The camera, optic and reference exposure the set belongs to.
    saturation : `SaturationCheck`, optional
        Keeps stars that are saturated in the registration reference frame
        out of the reference stars. Left out, the stack's own 99.99th
        percentile screens the brightest stars instead.
    depth : `StackDepth`, optional
        The stack's frame counts, kept with the set.
    covered_mask : `numpy.ndarray`, optional
        `True` where every exposure group of a group stack has data, on the
        same pixel grid as `plane`. An aperture is kept at least
        `UNCOVERED_CLEARANCE_PX` from any pixel where it is `False`, because
        a group that does not reach a pixel changes how the others are
        weighted there.
    sky_from_plate_solve : `bool`, optional
        True when ``wcs`` is a plate solve of this stack, so the stored sky
        positions are trusted (see `ApertureSet`).

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The apertures (positions drawn from a generator with a fixed seed, at
        least `APERTURE_SEPARATION_PX` apart, at least `STAR_CLEARANCE_PX`
        from every detected star, at least `FAINT_SOURCE_CLEARANCE_PX` from
        every faint source found by `faint_source_distance`, and kept only
        where `signed_aperture_sum` works) and the brightest
        `REFERENCE_STAR_COUNT` isolated stars that are not saturated. `None`
        when fewer than `MINIMUM_USABLE_APERTURES` apertures or
        `MINIMUM_USABLE_REFERENCE_STARS` stars could be found.
    reason : `str`
        Why there is no set. Empty when there is one.
    """
    fwhm = measure_fwhm_from_data(plane)
    if fwhm is None or not math.isfinite(fwhm) or fwhm <= 0:
        return None, "the stack's star width could not be measured, so the reference aperture has no size"
    radius = max(REFERENCE_APERTURE_FWHM_MULTIPLE * fwhm, REFERENCE_APERTURE_MINIMUM_RADIUS_PX)
    ring_inner = radius + REFERENCE_RING_GAP_PX
    ring_outer = ring_inner + REFERENCE_RING_WIDTH_PX
    # Every bright star counts for the clearance and the isolation, saturated
    # or not: a saturated star has the widest wings.
    stars = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    analysis = analyze_faint_sources(plane)
    positions = place_empty_apertures(plane, stars, analysis.distance, covered_mask)
    if len(positions) < MINIMUM_USABLE_APERTURES:
        return None, (
            f"only {len(positions)} empty apertures could be placed on this stack, away from bright stars "
            f"and from faint sources ({FAINT_SOURCE_THRESHOLD_SIGMAS} sigma or more above the sky at a "
            f"smoothing of {FAINT_SOURCE_SMOOTHING_SIGMAS_PX} px); the mask flagged "
            f"{100 * analysis.flagged_fraction:.0f}% of the image and its clearance excludes "
            f"{100 * analysis.excluded_fraction:.0f}% of it; at least "
            f"{MINIMUM_USABLE_APERTURES} are needed"
        )
    percentile_screen = None
    if saturation is None:
        percentile_screen = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=True)
    reference: list[tuple[float, float]] = []
    baseline: list[float] = []
    for row, column in _isolated_stars(stars, clearance=max(STAR_CLEARANCE_PX, ring_outer + 1.0)):
        if saturation is not None and saturation.is_saturated(row, column):
            continue
        if percentile_screen is not None and (
            len(percentile_screen) == 0 or np.min(np.hypot(*(percentile_screen - (row, column)).T)) > 1.0
        ):
            continue
        flux = signed_aperture_sum(plane, column, row, radius, ring_inner, ring_outer)
        if flux is None or flux <= 0:
            continue
        reference.append((row, column))
        baseline.append(flux)
        if len(reference) >= REFERENCE_STAR_COUNT:
            break
    if len(reference) < MINIMUM_USABLE_REFERENCE_STARS:
        return (
            None,
            f"only {len(reference)} isolated, unsaturated reference stars could be found on this stack",
        )
    median_baseline = float(np.median(baseline))
    mask_scales = tuple(scale / median_baseline for scale in analysis.noise_scales)
    return (
        ApertureSet(
            aperture_sky=pixels_to_sky(wcs, np.array(positions, dtype=float)),
            reference_sky=pixels_to_sky(wcs, np.array(reference, dtype=float)),
            wcs_source=wcs_source,
            pixel_scale_arcsec=pixel_scale_arcsec(wcs),
            created_from=created_from,
            reference_baseline_flux=np.array(baseline, dtype=float),
            reference_radius_px=float(radius),
            reference_fwhm_px=float(fwhm),
            saturation_source=(
                saturation.source
                if saturation is not None
                else "the stack's own 99.99th percentile (no registration reference frame was available)"
            ),
            setup_key=setup_key,
            defining_frame_count=None if depth is None else depth.frame_count,
            defining_effective_frame_count=None if depth is None else depth.effective_frame_count,
            mask_noise_scales=mask_scales,
            flagged_fraction=analysis.flagged_fraction,
            excluded_fraction=analysis.excluded_fraction,
            sky_positions_from_plate_solve=sky_from_plate_solve,
        ),
        "",
    )


def place_empty_apertures(
    plane: np.ndarray,
    stars: np.ndarray,
    source_distance: np.ndarray | None = None,
    covered_mask: np.ndarray | None = None,
) -> list[tuple[float, float]]:
    """Draw empty-sky aperture positions, away from stars and faint sources.

    Positions are drawn from a generator with a fixed seed. A draw is kept
    when it is at least `EDGE_MARGIN_PX` from the edge, at least
    `STAR_CLEARANCE_PX` from every given star, at least
    `FAINT_SOURCE_CLEARANCE_PX` from every faint source (see
    `faint_source_distance`), at least `APERTURE_SEPARATION_PX` from every
    kept position, and where `signed_aperture_sum` works. When a covered mask
    is given, a draw is also kept at least `UNCOVERED_CLEARANCE_PX` from every
    pixel the mask marks as not covered. No test looks at an aperture's sum,
    so the choice cannot favor quiet apertures.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    stars : `numpy.ndarray`
        Star centers, shape ``(n, 2)`` as (row, column).
    source_distance : `numpy.ndarray`, optional
        The stack's `faint_source_distance`, when the caller already has it.
        Found here when left out.
    covered_mask : `numpy.ndarray`, optional
        `True` where every exposure group of a group stack has data, on the
        same pixel grid as `plane`. Left out for a stack of one group.

    Returns
    -------
    positions : `list` [`tuple` [`float`, `float`]]
        The kept (row, column) positions, at most `NOISE_APERTURE_COUNT`.
    """
    star_tree = cKDTree(stars) if len(stars) else None
    if source_distance is None:
        source_distance = faint_source_distance(plane)
    # The distance of every pixel to the nearest pixel a group does not cover
    # (zero on the uncovered pixels themselves).
    uncovered_distance = None if covered_mask is None else distance_transform_edt(covered_mask)
    height, width = plane.shape
    generator = np.random.default_rng(APERTURE_SEED)
    positions: list[tuple[float, float]] = []
    for _ in range(40 * NOISE_APERTURE_COUNT):
        row = generator.uniform(EDGE_MARGIN_PX, height - EDGE_MARGIN_PX)
        column = generator.uniform(EDGE_MARGIN_PX, width - EDGE_MARGIN_PX)
        if star_tree is not None and star_tree.query([row, column])[0] <= STAR_CLEARANCE_PX:
            continue
        if source_distance[round(row), round(column)] <= FAINT_SOURCE_CLEARANCE_PX:
            continue
        if (
            uncovered_distance is not None
            and uncovered_distance[round(row), round(column)] <= UNCOVERED_CLEARANCE_PX
        ):
            continue
        if positions and np.min(np.hypot(*(np.array(positions) - (row, column)).T)) < APERTURE_SEPARATION_PX:
            continue
        if signed_aperture_sum(plane, column, row) is None:
            continue
        positions.append((row, column))
        if len(positions) >= NOISE_APERTURE_COUNT:
            break
    return positions


@dataclass(frozen=True)
class FaintSourceAnalysis:
    """What the faint-source mask found in one stack.

    Parameters
    ----------
    distance : `numpy.ndarray`
        For every pixel, the distance in pixels to the nearest flagged pixel.
        Infinite when nothing is flagged; zero everywhere when the
        stack holds no usable pixel.
    noise_scales : `tuple` [`float`, ...]
        The robust noise scale (1.4826 times the median absolute deviation over
        the pixels that hold data) of the smoothed, background-free image at
        each of `FAINT_SOURCE_SMOOTHING_SIGMAS_PX`, in the stack's own units.
    flagged_fraction : `float`
        The share of all pixels that were flagged.
    usable_fraction : `float`
        The share of all pixels that hold data (finite and not exactly zero).
    excluded_fraction : `float`
        The share of all pixels within `FAINT_SOURCE_CLEARANCE_PX` of a flagged
        pixel, where no aperture may be centered. This is the share that limits
        placement; it is larger than the flagged share.
    """

    distance: np.ndarray
    noise_scales: tuple[float, ...]
    flagged_fraction: float
    usable_fraction: float
    excluded_fraction: float = 0.0


def analyze_faint_sources(plane: np.ndarray) -> FaintSourceAnalysis:
    """Find the faint sources of a stack and say how the mask was scaled.

    The rule uses only the stack's pixels, never an aperture's sum. The
    stack's large-scale background is taken away (a Gaussian of
    `FAINT_SOURCE_HIGH_PASS_SIGMA_PX`), the result is smoothed at each of
    `FAINT_SOURCE_SMOOTHING_SIGMAS_PX`, and a pixel is flagged when any
    smoothed value lies more than `FAINT_SOURCE_THRESHOLD_SIGMAS` robust
    sigmas above zero. Empty edge pixels (exactly zero or not finite) are
    filled with the stack's median first, so the edge does not look like a
    source, and they are left out of each noise scale, so the filled edge does
    not pull the scale down. The working arrays are 32-bit and freed as soon
    as they are used, which keeps the peak memory near five copies of the
    image (see validation entry STK-36).

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.

    Returns
    -------
    analysis : `FaintSourceAnalysis`
        The distance map, the noise scales and the flagged share.
    """
    image = np.asarray(plane, dtype=np.float32)
    usable = np.isfinite(image) & (image != 0)
    usable_fraction = float(np.count_nonzero(usable)) / usable.size if usable.size else 0.0
    if not usable.any():
        return FaintSourceAnalysis(np.zeros(image.shape, dtype=np.float32), (), 0.0, 0.0, 1.0)
    filled = np.where(usable, image, np.float32(np.median(image[usable])))
    flat = filled - gaussian_filter(filled, FAINT_SOURCE_HIGH_PASS_SIGMA_PX)
    del filled
    flagged = np.zeros(image.shape, dtype=bool)
    scales: list[float] = []
    for width in FAINT_SOURCE_SMOOTHING_SIGMAS_PX:
        smooth = gaussian_filter(flat, width)
        values = smooth[usable]
        center = np.median(values)
        values -= center
        np.abs(values, out=values)
        sigma = MAD_TO_SIGMA * float(np.median(values))
        del values
        scales.append(sigma)
        if sigma > 0:
            flagged |= smooth > FAINT_SOURCE_THRESHOLD_SIGMAS * sigma
        del smooth
    del flat
    flagged_fraction = float(np.count_nonzero(flagged)) / flagged.size
    if not flagged.any():
        distance = np.full(image.shape, np.inf, dtype=np.float32)
    else:
        distance = distance_transform_edt(~flagged)
    excluded_fraction = float(np.count_nonzero(distance <= FAINT_SOURCE_CLEARANCE_PX)) / distance.size
    return FaintSourceAnalysis(distance, tuple(scales), flagged_fraction, usable_fraction, excluded_fraction)


def faint_source_distance(plane: np.ndarray) -> np.ndarray:
    """Give each pixel's distance to the nearest faint source in a stack.

    See `analyze_faint_sources` for the rule.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.

    Returns
    -------
    distance : `numpy.ndarray`
        For every pixel, the distance in pixels to the nearest flagged pixel.
        Infinite when nothing is flagged.
    """
    return analyze_faint_sources(plane).distance


def _isolated_stars(stars: np.ndarray, clearance: float = STAR_CLEARANCE_PX) -> np.ndarray:
    """Keep the stars that have no detected neighbor within the clearance.

    Parameters
    ----------
    stars : `numpy.ndarray`
        Star centers, shape ``(n, 2)`` as (row, column), brightest first.
    clearance : `float`, optional
        How far the nearest neighbor must be.

    Returns
    -------
    isolated : `numpy.ndarray`
        The stars whose nearest neighbor is farther than the clearance, in
        the same order.
    """
    if len(stars) < 2:
        return stars
    nearest = cKDTree(stars).query(stars, k=2)[0][:, 1]
    return stars[nearest > clearance]


__all__ = [
    "FaintSourceAnalysis",
    "analyze_faint_sources",
    "choose_apertures",
    "choose_apertures_with_reason",
    "faint_source_distance",
    "place_empty_apertures",
]
