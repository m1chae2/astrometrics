"""Measure a plate solution against the Gaia stars of the field.

The plate solver (Siril) fits its solution to the stars of its own Gaia
catalog file. That fit says how well the solver agrees with itself. This
module asks the same question of the solution from the outside: it takes a
solution, the pixel positions of the stars detected in the image, and the Gaia
stars of the field moved to the date of the image, and does two things:

1. It matches each detected star to the nearest Gaia star within a small
   radius (`GAIA_MATCH_RADIUS_PIXELS`).
2. It throws out matches that sit more than `CLIP_SIGMAS` robust standard
   deviations from the median distance (blends, double stars, wrong matches),
   and repeats until the set of stars stops changing.

The root mean square (RMS) distance of the stars that are left is the
residual of the solution against Gaia. The solution is never changed. The
stars that are left are the "Gaia-matched stars" of the check: the quality
gate judges their RMS distance, and the expected residual
(`post_processing/expected_residual.py`) is found over the same stars.

Limits: the check assumes the detected centroids are unbiased. A star that
saturates, or is blended, is clipped by the loop rather than found by name.
The RMS is measured on the stars that were kept, so it is slightly smaller
than the error at a position with no star.
"""

from dataclasses import dataclass

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from astrometricslib.foundation.parameter_context import record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.utilities.robust_statistics import MAD_TO_SIGMA

# The fewest matched stars for the RMS distance to mean anything. The RMS over
# fewer stars is dominated by one or two wrong matches. A design value, not
# tuned.
MINIMUM_MATCHED_STARS = TunedParameter(
    name="astrometry.gaia_match.minimum_matched_stars",
    default=40,
    unit="stars",
    description="Fewest Gaia-matched stars with which the solution's RMS distance to Gaia is measured.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-02",
)

# A match is accepted within this many star widths of the solution's position.
# It must be larger than the solution's error and small enough that a
# neighbouring star is rarely mistaken for the right one. It was 4.0 for the
# solve-field solutions, which were off by up to 2 pixels; Siril's solutions
# are off by a fraction of a pixel (AST-23), so 4.0 is generous.
# Stands for the match radius, in pixels of the solution's scale. It follows
# the star width: the value is a factor times the star width the run measured
# (4.0 on the reference setup).
GAIA_MATCH_RADIUS_PIXELS = TunedParameter(
    name="astrometry.gaia_match_radius_pixels",
    default=factor_for(4.0, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="How far from a detected star's position a Gaia star may sit and still be matched to it.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=4.0,
    equipment=frozenset({EquipmentKind.CAMERA, EquipmentKind.TELESCOPE}),
    register_entry="AST-02",
)

# Stars farther than this many robust standard deviations (1.4826 times the
# median absolute deviation) from the median distance are left out. 3 keeps
# 99.7 percent of Gaussian errors.
CLIP_SIGMAS = TunedParameter(
    name="astrometry.clip_sigmas",
    default=3.0,
    unit="robust sigma",
    description="How many robust standard deviations from the median a star may sit before it is left out.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-02",
)

# The most passes of clipping.
MAXIMUM_PASSES = TunedParameter(
    name="astrometry.maximum_passes",
    default=5,
    unit="passes",
    description="The most passes of clipping.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-02",
)


@dataclass
class GaiaMatch:
    """What matching the detected stars to Gaia found.

    Attributes
    ----------
    star_count : `int`
        How many stars were kept (0 when too few matched).
    rms_arcsec : `float` or `None`
        The RMS distance between the kept stars and their Gaia positions
        under the solution. `None` when too few stars matched.
    star_indices : `tuple` [`int`, ...]
        The indices (into the `pixel_positions` given) of the kept stars.
        Empty when too few matched. The expected residual is found over these
        same stars, so it can be compared with the RMS.
    reason : `str`
        Why no RMS was measured, or an empty string.
    """

    star_count: int = 0
    rms_arcsec: float | None = None
    star_indices: tuple[int, ...] = ()
    reason: str = ""


def _clip(separations_arcsec: np.ndarray, clip_sigmas: float) -> np.ndarray:
    """Find the matches that sit near the bulk of the distances.

    Parameters
    ----------
    separations_arcsec : `numpy.ndarray`
        The distance of each match.
    clip_sigmas : `float`
        How many robust standard deviations from the median still count
        (`CLIP_SIGMAS`).

    Returns
    -------
    keep : `numpy.ndarray`
        A boolean mask: `True` for distances within `CLIP_SIGMAS` robust
        standard deviations of the median.
    """
    median = np.median(separations_arcsec)
    spread = MAD_TO_SIGMA * np.median(np.abs(separations_arcsec - median))
    if spread <= 0:
        return np.ones(separations_arcsec.size, dtype=bool)
    return np.abs(separations_arcsec - median) <= clip_sigmas * spread


def measure_gaia_match(wcs: WCS, pixel_positions: np.ndarray, gaia_coordinates: SkyCoord) -> GaiaMatch:
    """Measure a plate solution against the Gaia stars of the field.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The solution to measure. It is not changed.
    pixel_positions : `numpy.ndarray`
        An N by 2 array of the detected stars' zero-based (x, y) centroids.
    gaia_coordinates : `astropy.coordinates.SkyCoord`
        The Gaia stars of the field at the date of the image.

    Returns
    -------
    match : `GaiaMatch`
        The RMS distance and the stars it was found over, or a reason when
        too few stars matched.
    """
    # The solution's own scale is the run's plate scale, so record it before
    # the limits that follow the scale are read.
    scale_arcsec = record_plate_scale(wcs)
    minimum_matched_stars = resolve(MINIMUM_MATCHED_STARS).value
    match_radius_pixels = resolve(GAIA_MATCH_RADIUS_PIXELS).value
    clip_sigmas = resolve(CLIP_SIGMAS).value
    maximum_passes = resolve(MAXIMUM_PASSES).value
    positions = np.asarray(pixel_positions, dtype=float)
    if positions.ndim != 2 or positions.shape[0] < minimum_matched_stars or len(gaia_coordinates) == 0:
        return GaiaMatch(reason="too few detected stars or no Gaia stars to match")
    if scale_arcsec is None:
        return GaiaMatch(reason="the solution has no usable pixel scale")
    radius_arcsec = match_radius_pixels * scale_arcsec
    sky = wcs.all_pix2world(positions, 0)
    detected = SkyCoord(sky[:, 0] * u.deg, sky[:, 1] * u.deg)
    _nearest, separation, _ = detected.match_to_catalog_sky(gaia_coordinates)
    distances = separation.arcsec
    kept = np.flatnonzero(distances <= radius_arcsec)
    if kept.size < minimum_matched_stars:
        return GaiaMatch(reason=f"only {kept.size} stars matched Gaia")
    for _ in range(maximum_passes):
        keep = _clip(distances[kept], clip_sigmas)
        if keep.all():
            break
        kept = kept[keep]
        if kept.size < minimum_matched_stars:
            return GaiaMatch(reason=f"only {kept.size} stars were left after clipping")
    rms = float(np.sqrt(np.mean(distances[kept] ** 2)))
    return GaiaMatch(star_count=int(kept.size), rms_arcsec=rms, star_indices=tuple(int(i) for i in kept))


def record_plate_scale(wcs: WCS) -> float | None:
    """Record the plate scale of a plate solution for the rest of the run.

    The run's plate scale is what the sky-angle limits (match radii) follow.
    The call does nothing outside a run.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        A plate solution.

    Returns
    -------
    scale_arcsec : `float` or `None`
        The mean pixel scale in arcseconds per pixel, or `None` when the
        solution has no usable scale.
    """
    try:
        scale_arcsec = float(np.mean(proj_plane_pixel_scales(wcs))) * 3600.0
    except ValueError, TypeError, np.linalg.LinAlgError:
        return None
    if not np.isfinite(scale_arcsec) or scale_arcsec <= 0.0:
        return None
    record_basis(RunBasis.PLATE_SCALE_ARCSEC_PER_PX, scale_arcsec)
    return scale_arcsec
