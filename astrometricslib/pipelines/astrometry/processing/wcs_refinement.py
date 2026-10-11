"""Refit a plate solution to the Gaia stars of the field.

The plate solver (solve-field) fits its solution to the few dozen stars of its
own index files. Those stars are bright and few, and their catalog positions
carry the errors of the index. The solution can therefore sit a pixel or more
from the truth and still pass a check that only asks how well the solver agrees
with itself. On 2026-10-10, against Gaia DR3, the solution of Alnath (24 stars
fitted, 2 frames) was 2.7 arcseconds (1.4 pixels) off, and NGC 2403's was 0.6
arcsecond (0.3 pixel) off. M 13's was good to 0.1 arcsecond.

This module takes a solution, the pixel positions of the stars detected in the
image, and the Gaia stars of the field moved to the date of the image, and
does three things:

1. It matches each detected star to the nearest Gaia star within a small
   radius.
2. It fits a new tangent-plane solution with second-order SIP distortion
   (`astropy.wcs.utils.fit_wcs_from_points`) to the matches, throws out stars
   that sit more than `CLIP_SIGMAS` robust standard deviations from the fit
   (blends, double stars, wrong matches), and repeats with the new solution
   until the set of matched stars stops changing.
3. It keeps the new solution only when it fits the Gaia stars better than the
   old one and does not move the field by more than `MAXIMUM_SHIFT_ARCSEC`.

The result says what was measured either way, so the quality summary can show
the residual against Gaia, which does not depend on the solver.

Limits: the fit assumes the detected centroids are unbiased. A star that
saturates, or is blended, is clipped by the loop rather than found by name.
The residual is measured on the stars the fit used, so it is slightly smaller
than the error at a position with no star.
"""

from dataclasses import dataclass

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astropy.wcs.utils import fit_wcs_from_points, proj_plane_pixel_scales

# The fewest matched stars with which a second-order SIP fit is attempted. The
# fit has two linear and ten distortion terms for each axis plus the reference
# point; 40 stars leaves a wide margin, and fewer leave the high-order terms
# free to follow noise. A design value, not tuned.
MINIMUM_MATCHED_STARS = 40

# The degree of the SIP distortion. The solver's own solutions use 2 and the
# M 13 stack showed up to 1.8 arcseconds of distortion at a corner.
SIP_DEGREE = 2

# A match is first accepted within this many pixels of the old solution. It
# must be larger than the old solution's error (up to 2 pixels was found) and
# small enough that a neighbouring star is rarely mistaken for the right one.
# Later passes use `REFINED_MATCH_RADIUS_PIXELS`.
INITIAL_MATCH_RADIUS_PIXELS = 4.0
REFINED_MATCH_RADIUS_PIXELS = 1.5

# Stars farther than this many robust standard deviations (1.4826 times the
# median absolute deviation) from the fit are left out of it. 3 keeps 99.7
# percent of Gaussian errors.
CLIP_SIGMAS = 3.0

# The most passes of match, fit and clip.
MAXIMUM_PASSES = 5

# A refined solution that moves any of a 5 by 5 grid of points in the image by
# more than this is refused. A solved field is already near the truth, so a
# larger change means the fit followed wrong matches. 30 arcseconds is more
# than ten times the largest error seen (2.7 arcseconds).
MAXIMUM_SHIFT_ARCSEC = 30.0


@dataclass
class WcsRefinement:
    """What the refinement found.

    Attributes
    ----------
    wcs : `astropy.wcs.WCS`
        The solution to use: the refined one when `applied`, else the one
        given.
    applied : `bool`
        `True` when the refined solution replaced the given one.
    reason : `str`
        Why it was not applied, or an empty string.
    star_count : `int`
        How many stars the final fit used (0 when no fit was made).
    rms_before_arcsec, rms_after_arcsec : `float` or `None`
        The RMS distance between the matched stars and their Gaia positions
        under the old and the new solution, over the final set of stars.
        `None` when no fit was made.
    shift_arcsec : `float` or `None`
        The largest move of the 5 by 5 grid of image points between the two
        solutions. `None` when no fit was made.
    """

    wcs: WCS
    applied: bool
    reason: str = ""
    star_count: int = 0
    rms_before_arcsec: float | None = None
    rms_after_arcsec: float | None = None
    shift_arcsec: float | None = None


def _separations_arcsec(
    wcs: WCS, pixel_positions: np.ndarray, gaia_coordinates: SkyCoord, radius_arcsec: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Match each detected star to its nearest Gaia star under a solution.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The solution that turns pixels into sky positions.
    pixel_positions : `numpy.ndarray`
        An N by 2 array of zero-based (x, y) centroids.
    gaia_coordinates : `astropy.coordinates.SkyCoord`
        The Gaia positions at the date of the image.
    radius_arcsec : `float`
        The largest distance that counts as a match.

    Returns
    -------
    star_indices : `numpy.ndarray`
        The indices (into `pixel_positions`) of the matched stars.
    gaia_indices : `numpy.ndarray`
        The Gaia index matched to each of them.
    separations_arcsec : `numpy.ndarray`
        The distance of each match.
    """
    sky = wcs.all_pix2world(pixel_positions, 0)
    detected = SkyCoord(sky[:, 0] * u.deg, sky[:, 1] * u.deg)
    nearest, separation, _ = detected.match_to_catalog_sky(gaia_coordinates)
    within = separation.arcsec <= radius_arcsec
    return np.flatnonzero(within), nearest[within], separation.arcsec[within]


def _clip(separations_arcsec: np.ndarray) -> np.ndarray:
    """Find the matches that sit near the bulk of the distances.

    Parameters
    ----------
    separations_arcsec : `numpy.ndarray`
        The distance of each match.

    Returns
    -------
    keep : `numpy.ndarray`
        A boolean mask: `True` for distances within `CLIP_SIGMAS` robust
        standard deviations of the median.
    """
    median = np.median(separations_arcsec)
    spread = 1.4826 * np.median(np.abs(separations_arcsec - median))
    if spread <= 0:
        return np.ones(separations_arcsec.size, dtype=bool)
    return np.abs(separations_arcsec - median) <= CLIP_SIGMAS * spread


def _grid_shift_arcsec(first: WCS, second: WCS, image_shape: tuple[int, int]) -> float:
    """Find how far two solutions disagree over the image.

    Parameters
    ----------
    first, second : `astropy.wcs.WCS`
        The two solutions.
    image_shape : `tuple` [`int`, `int`]
        The image's (rows, columns).

    Returns
    -------
    shift_arcsec : `float`
        The largest distance between the two solutions' sky positions over a
        5 by 5 grid of pixels.
    """
    rows, columns = image_shape
    grid_x, grid_y = np.meshgrid(np.linspace(0, columns - 1, 5), np.linspace(0, rows - 1, 5))
    points = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    one = first.all_pix2world(points, 0)
    two = second.all_pix2world(points, 0)
    separation = SkyCoord(one[:, 0] * u.deg, one[:, 1] * u.deg).separation(
        SkyCoord(two[:, 0] * u.deg, two[:, 1] * u.deg)
    )
    return float(np.max(separation.arcsec))


def refine_wcs_with_gaia(
    wcs: WCS,
    pixel_positions: np.ndarray,
    gaia_coordinates: SkyCoord,
    image_shape: tuple[int, int],
) -> WcsRefinement:
    """Refit a plate solution to the Gaia stars of the field.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The solution to improve.
    pixel_positions : `numpy.ndarray`
        An N by 2 array of the detected stars' zero-based (x, y) centroids.
    gaia_coordinates : `astropy.coordinates.SkyCoord`
        The Gaia stars of the field at the date of the image.
    image_shape : `tuple` [`int`, `int`]
        The image's (rows, columns).

    Returns
    -------
    refinement : `WcsRefinement`
        The solution to use and what was measured. The given solution comes
        back unchanged, with a reason, when there are too few matched stars,
        the fit is not better than the old one, or it moves the field too far.
    """
    positions = np.asarray(pixel_positions, dtype=float)
    if positions.ndim != 2 or positions.shape[0] < MINIMUM_MATCHED_STARS or len(gaia_coordinates) == 0:
        return WcsRefinement(wcs, False, "too few detected stars or no Gaia stars to match")

    scale_arcsec = float(np.mean(proj_plane_pixel_scales(wcs))) * 3600.0
    radius_arcsec = INITIAL_MATCH_RADIUS_PIXELS * scale_arcsec
    projection_point = SkyCoord(wcs.wcs.crval[0] * u.deg, wcs.wcs.crval[1] * u.deg)
    current = wcs
    used: np.ndarray | None = None
    fitted: WCS | None = None

    for _ in range(MAXIMUM_PASSES):
        star_indices, gaia_indices, separations = _separations_arcsec(
            current, positions, gaia_coordinates, radius_arcsec
        )
        if star_indices.size < MINIMUM_MATCHED_STARS:
            return WcsRefinement(wcs, False, f"only {star_indices.size} stars matched Gaia")
        keep = _clip(separations)
        star_indices, gaia_indices = star_indices[keep], gaia_indices[keep]
        if star_indices.size < MINIMUM_MATCHED_STARS:
            return WcsRefinement(wcs, False, f"only {star_indices.size} stars were left after clipping")
        try:
            fitted = fit_wcs_from_points(
                (positions[star_indices, 0], positions[star_indices, 1]),
                gaia_coordinates[gaia_indices],
                proj_point=projection_point,
                projection="TAN",
                sip_degree=SIP_DEGREE,
            )
        except (ValueError, np.linalg.LinAlgError) as error:
            return WcsRefinement(wcs, False, f"the fit failed: {error}")
        stable = used is not None and np.array_equal(used, star_indices)
        used = star_indices
        current = fitted
        radius_arcsec = REFINED_MATCH_RADIUS_PIXELS * scale_arcsec
        if stable:
            break

    if used is None or fitted is None:
        return WcsRefinement(wcs, False, "no fit was made")
    # Distances of exactly the stars the fit used, under both solutions. The
    # old solution is judged on them too, so the two RMS values are comparable.
    sky_new = fitted.all_pix2world(positions[used], 0)
    sky_old = wcs.all_pix2world(positions[used], 0)
    truth = _gaia_positions_for(positions[used], fitted, gaia_coordinates)
    new_distance = SkyCoord(sky_new[:, 0] * u.deg, sky_new[:, 1] * u.deg).separation(truth).arcsec
    old_distance = SkyCoord(sky_old[:, 0] * u.deg, sky_old[:, 1] * u.deg).separation(truth).arcsec
    rms_after = float(np.sqrt(np.mean(new_distance**2)))
    rms_before = float(np.sqrt(np.mean(old_distance**2)))
    shift = _grid_shift_arcsec(wcs, fitted, image_shape)
    outcome = WcsRefinement(
        wcs,
        False,
        star_count=int(used.size),
        rms_before_arcsec=rms_before,
        rms_after_arcsec=rms_after,
        shift_arcsec=shift,
    )
    if shift > MAXIMUM_SHIFT_ARCSEC:
        outcome.reason = f"the fit moved the field by {shift:.1f} arcsec, more than {MAXIMUM_SHIFT_ARCSEC:g}"
        return outcome
    if rms_after >= rms_before:
        outcome.reason = "the refined solution does not fit Gaia better than the given one"
        return outcome
    outcome.wcs = fitted
    outcome.applied = True
    return outcome


def _gaia_positions_for(pixel_positions: np.ndarray, wcs: WCS, gaia_coordinates: SkyCoord) -> SkyCoord:
    """Find the Gaia star nearest to each detected star under a solution.

    Parameters
    ----------
    pixel_positions : `numpy.ndarray`
        An N by 2 array of zero-based (x, y) centroids.
    wcs : `astropy.wcs.WCS`
        The solution that places them on the sky.
    gaia_coordinates : `astropy.coordinates.SkyCoord`
        The Gaia positions.

    Returns
    -------
    nearest : `astropy.coordinates.SkyCoord`
        The Gaia position nearest to each star.
    """
    sky = wcs.all_pix2world(pixel_positions, 0)
    detected = SkyCoord(sky[:, 0] * u.deg, sky[:, 1] * u.deg)
    nearest, _, _ = detected.match_to_catalog_sky(gaia_coordinates)
    return gaia_coordinates[nearest]
