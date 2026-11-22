"""Add the scintillation term to every point's flux error.

The CCD equation (`frame_photometry.aperture_flux_error_adu`) counts the
star's light, the sky and the camera. It has no term for scintillation, the
twinkling of a star in the moving air. For a bright star scintillation is the
largest noise, so its error comes out too small. On the M 57 field the
brightest stars scattered about twice what their errors said.

This module adds the missing term. For frame ``j`` the term is a fraction of
the star's flux::

    sigma_scint = k * Y_j * flux

``Y_j`` is Young's prediction for the frame (`scintillation_relative_noise`,
from the aperture, the exposure, the airmass and the site height). ``k`` is a
coefficient found for each session, in this order:

1. Choose the session's comparison stars with the CCD-only errors, as
   `comparison_ensemble` does.
2. For each member and frame, compare the member with the weighted mean of
   the other members. The squared difference minus what the CCD errors
   predict is the excess. Scintillation predicts ``Y_j**2`` times
   ``1 + f``, where ``f`` is the share of it that the other members' own
   scintillation adds to their mean.
3. A weighted least-squares fit of the excess against that prediction gives
   ``k**2``. Points beyond 5 standard deviations are dropped first.
4. ``k`` is kept inside a plausible range. Too few members or frames, or no
   aperture, and ``k`` is the documented fallback factor.
5. The term is added in quadrature to every point's error. The normal
   comparison selection and weighting then run on the larger errors. This
   is one pass: the weights used to measure ``k`` are the CCD-only ones, and
   the final selection may differ a little from the one used to measure it.

``k`` takes up anything else that adds white scatter to bright stars and that
Young's formula does not hold (a site height set to 0, a flat residual). It
is a measured size of the whole excess, not only of the air.

What is measured and what is designed: ``k`` is measured on each run. The
member and frame minimums, the range of ``k`` and the 5-sigma clip are design
estimates (PHO-D38). The fallback factor of 1.5 is the factor Osborn et al.
(2015) found between Young's formula and measured scintillation.
"""

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.quality_summary import SessionScintillationRecord
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    build_flux_table,
    select_comparison_set,
)
from astrometricslib.pipelines.photometry.processing.noise_budget import (
    MAGNITUDES_PER_FRACTION,
    scintillation_relative_noise,
)

__all__ = [
    "SOURCE_FALLBACK",
    "SOURCE_MEASURED",
    "SOURCE_MEASURED_LIMITED",
    "SOURCE_NONE",
    "CoefficientFit",
    "ScintillationTerm",
    "add_scintillation_in_quadrature",
    "build_scintillation_term",
    "fit_scintillation_coefficient",
]

# What a session's record says about where its coefficient came from.
SOURCE_MEASURED = "measured"
SOURCE_MEASURED_LIMITED = "measured_limited"
SOURCE_FALLBACK = "fallback"
SOURCE_NONE = "none"

# The fewest comparison stars the coefficient may be fitted from. With fewer,
# one star's own flaw (a faint variable the catalog does not list) moves the
# fit. It equals the smallest comparison set a session may end with (PHO-01).
# A design estimate (PHO-D38).
MINIMUM_MEMBERS_FOR_COEFFICIENT = TunedParameter(
    name="photometry.scintillation_minimum_members",
    default=5,
    unit="stars",
    description="Fewest comparison stars from which a session's scintillation coefficient is fitted.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-D38",
)

# The fewest frames the coefficient may be fitted from. Scintillation is
# independent from one 30 s frame to the next, so each frame adds
# independent points. At 5 members and 20 frames the fit has 100 points, so
# the squared coefficient has a relative error of about sqrt(2 / 100) = 14
# percent, and the coefficient about 7 percent. A design estimate (PHO-D38).
MINIMUM_FRAMES_FOR_COEFFICIENT = TunedParameter(
    name="photometry.scintillation_minimum_frames",
    default=20,
    unit="frames",
    description="Fewest frames from which a session's scintillation coefficient is fitted.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-D38",
)

# The range the fitted coefficient is kept in. Measured scintillation lies
# within a factor of about 2 of Young's formula in both directions (Osborn et
# al. 2015), so a fit below 0.5 or above 3 (twice the fallback factor of 1.5)
# is more likely some other excess, such as a cloud, than scintillation. The
# term then uses the nearest end and the record says so. A design estimate
# (PHO-D38).
MINIMUM_COEFFICIENT = TunedParameter(
    name="photometry.scintillation_minimum_coefficient",
    default=0.5,
    unit="coefficient",
    description="Lowest scintillation coefficient (the multiplier on Young's prediction) that is kept.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="PHO-D38",
)
MAXIMUM_COEFFICIENT = TunedParameter(
    name="photometry.scintillation_maximum_coefficient",
    default=3.0,
    unit="coefficient",
    description="Highest scintillation coefficient (the multiplier on Young's prediction) that is kept.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="PHO-D38",
)

# The factor on Young's formula used when the session has too few comparison
# stars or frames to measure one. Osborn et al. (2015) found that Young's
# formula with a factor of 1.5 fits measured scintillation. On the M 57 night
# of 2026-10-03 the formula for the Apertura 75Q at 1460 m with this factor
# gave 4.8 mmag (30 s) and 3.6 mmag (60 s); the excess of the bright stars
# was 4.6 to 5.4 and 3.1 to 3.7 mmag (PHO-17). It depends on the telescope
# aperture and the site, which Young's formula carries. A published constant
# checked on one night (PHO-D38).
FALLBACK_YOUNG_FACTOR = TunedParameter(
    name="photometry.scintillation_fallback_factor",
    default=1.5,
    unit="coefficient",
    description="Factor on Young's formula used when the coefficient cannot be measured (Osborn 2015).",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.PUBLISHED_CONSTANT,
    register_entry="PHO-D38",
)

# Points whose residual lies beyond this many predicted standard deviations
# are left out of the fit. A cloud or a bad frame would otherwise make the
# coefficient absorb it. 5 passes 1 point in 1.7 million of a normal
# distribution. A design estimate (PHO-D38).
_CLIP_SIGMA = TunedParameter(
    name="photometry.scintillation_clip_sigma",
    default=5.0,
    unit="sigma",
    description="Residual size, in predicted deviations, beyond which a point is left out of the fit.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-D38",
)

# The fewest comparison stars that must be measured in a frame for the frame
# to give residuals. Two are needed for a leave-one-out mean; three keep the
# mean from being one star.
_MINIMUM_MEMBERS_PER_FRAME = 3

# The number of fit-and-clip passes. The fit settles in two or three; five
# leaves a margin. A method choice.
_FIT_PASSES = 5

# The least squared coefficient used to set the clip and the weights, so a
# fit that has gone to zero or below does not make them blind to scintillation.
_MINIMUM_FIT_COEFFICIENT_SQUARED = 0.01


@dataclass(frozen=True)
class CoefficientFit:
    """The result of fitting the coefficient to the comparison stars.

    Parameters
    ----------
    coefficient_squared : `float`
        The fitted square of the coefficient. It can be negative when the
        stars scatter less than their CCD errors predict.
    point_count : `int`
        How many star-and-frame points the fit used.
    frame_count : `int`
        How many frames those points came from.
    """

    coefficient_squared: float
    point_count: int
    frame_count: int


@dataclass(frozen=True)
class ScintillationTerm:
    """The term to add to a session's errors, and the record of it.

    Parameters
    ----------
    record : `SessionScintillationRecord`
        What was done and why, with the session id left empty.
    fraction_by_timestamp : `dict` [`datetime`, `float`]
        For each frame, the term as a fraction of a star's flux (``k * Y``).
        Empty when no term is added.
    """

    record: SessionScintillationRecord
    fraction_by_timestamp: dict[datetime, float]


def fit_scintillation_coefficient(
    fluxes: np.ndarray, errors: np.ndarray, young_fraction: np.ndarray
) -> CoefficientFit | None:
    """Fit the scale on Young's prediction that the comparison stars show.

    Each member is compared with the inverse-variance weighted mean of the
    other members in every frame. The relative residual ``r`` has the variance
    ``e_i**2 + v`` from the CCD errors (the member's own, and the mean's)
    plus ``k**2 * Y**2 * (1 + f)`` from scintillation, where ``f`` is
    ``sum(w**2) / sum(w)**2`` of the other members' weights. The squared
    coefficient is the weighted least-squares solution of
    ``r**2 - (e_i**2 + v) = k**2 * Y**2 * (1 + f)``, with weights of one over
    the squared total variance. Each member's median residual is removed
    first, so a star that is a little brighter or fainter than its mean
    level does not count as scatter.

    Parameters
    ----------
    fluxes : `numpy.ndarray`
        Shape ``(members, frames)``, ADU per second. Missing values are NaN.
    errors : `numpy.ndarray`
        Same shape, the CCD-only 1-sigma errors, ADU per second.
    young_fraction : `numpy.ndarray`
        One value per frame: Young's prediction as a fraction of the flux.
        NaN where the frame has no airmass or exposure.

    Returns
    -------
    fit : `CoefficientFit` or `None`
        The fit, or `None` when no usable point is left.
    """
    valid = np.isfinite(fluxes) & np.isfinite(errors) & (errors > 0) & (fluxes > 0)
    frame_ok = np.isfinite(young_fraction) & (valid.sum(axis=0) >= _MINIMUM_MEMBERS_PER_FRAME)
    valid &= frame_ok[None, :]
    if not valid.any():
        return None
    safe_fluxes = np.where(valid, fluxes, np.nan)
    means = np.nanmean(safe_fluxes, axis=1)
    keep_rows = np.isfinite(means) & (means > 0)
    relative = np.where(valid, fluxes / np.where(keep_rows, means, 1.0)[:, None], 0.0)
    fractional_error = np.where(valid, errors / np.where(keep_rows, means, 1.0)[:, None], 1.0)
    weights = np.where(valid, 1.0 / fractional_error**2, 0.0)
    total_weight = weights.sum(axis=0)
    others_weight = total_weight - weights
    others_weight_squared = (weights**2).sum(axis=0) - weights**2
    usable = valid & (others_weight > 0)
    if not usable.any():
        return None
    safe_others = np.where(usable, others_weight, 1.0)
    level = ((weights * relative).sum(axis=0) - weights * relative) / safe_others
    usable &= np.isfinite(level) & (level > 0)
    safe_level = np.where(usable, level, 1.0)
    residual = np.where(usable, relative / safe_level - 1.0, np.nan)
    # Remove each member's own median residual: it measures the level, not
    # the scatter.
    with np.errstate(all="ignore"):
        residual = residual - np.nanmedian(residual, axis=1, keepdims=True)
    ccd_variance = fractional_error**2 + 1.0 / safe_others
    scintillation_shape = young_fraction[None, :] ** 2 * (1.0 + others_weight_squared / safe_others**2)
    usable &= np.isfinite(residual) & np.isfinite(scintillation_shape)
    # Start from Young's prediction as written (k = 1). Each pass drops the
    # points beyond the clip limit of the current fit, which a cloud or a bad
    # frame would otherwise make the coefficient absorb, and fits again. The
    # clip uses the current fit, not k = 1, so a large coefficient is not cut
    # down to the size of the first guess.
    clip_sigma = resolve(_CLIP_SIGMA).value
    coefficient_squared = 1.0
    base_usable = usable
    usable = base_usable
    for _ in range(_FIT_PASSES):
        total_variance = (
            ccd_variance + max(coefficient_squared, _MINIMUM_FIT_COEFFICIENT_SQUARED) * scintillation_shape
        )
        usable = base_usable & (
            np.abs(np.where(base_usable, residual, 0.0)) <= clip_sigma * np.sqrt(total_variance)
        )
        if not usable.any():
            return None
        fit_weight = np.where(usable, 1.0 / total_variance**2, 0.0)
        excess = np.where(usable, residual**2 - ccd_variance, 0.0)
        denominator = float((fit_weight * scintillation_shape**2).sum())
        if denominator <= 0:
            return None
        coefficient_squared = float((fit_weight * excess * scintillation_shape).sum() / denominator)
    return CoefficientFit(
        coefficient_squared=coefficient_squared,
        point_count=int(usable.sum()),
        frame_count=int(usable.any(axis=0).sum()),
    )


def _young_fraction_by_timestamp(
    stars: list[StellarObject],
    exposure_seconds: dict[datetime, float],
    aperture_diameter_mm: float,
    site_elevation_m: float,
) -> dict[datetime, float]:
    """Find Young's prediction for every frame the stars were measured in.

    Parameters
    ----------
    stars : `list` [`StellarObject`]
        The session's stars; each light curve holds the frame's airmass.
    exposure_seconds : `dict` [`datetime`, `float`]
        The exposure of each frame.
    aperture_diameter_mm : `float`
        The telescope's aperture, in millimeters.
    site_elevation_m : `float`
        The site's height above sea level, in meters.

    Returns
    -------
    fraction_by_timestamp : `dict` [`datetime`, `float`]
        Young's fractional noise for each frame with a usable airmass and
        exposure. Frames without either are left out.
    """
    airmass_by_timestamp: dict[datetime, float] = {}
    for star in stars:
        photometry = star.photometry
        if photometry is None or len(photometry.airmasses) != len(photometry.timestamps):
            continue
        for timestamp, airmass in zip(photometry.timestamps, photometry.airmasses, strict=True):
            airmass_by_timestamp.setdefault(timestamp, airmass)
    young: dict[datetime, float] = {}
    for timestamp, airmass in airmass_by_timestamp.items():
        exposure = exposure_seconds.get(timestamp)
        if exposure is None or not (math.isfinite(airmass) and airmass > 0 and exposure > 0):
            continue
        young[timestamp] = scintillation_relative_noise(
            aperture_diameter_mm, airmass, exposure, site_elevation_m
        )
    return young


def _record(
    source: str,
    *,
    coefficient: float | None = None,
    measured: float | None = None,
    young_median: float | None = None,
    member_count: int | None = None,
    frame_count: int | None = None,
    reason: str | None = None,
) -> SessionScintillationRecord:
    """Build a record, turning fractions into millimagnitudes.

    Parameters
    ----------
    source : `str`
        One of the ``SOURCE_*`` values.
    coefficient : `float`, optional
        The coefficient applied.
    measured : `float`, optional
        The fitted coefficient before limiting.
    young_median : `float`, optional
        The median of Young's prediction over the frames, as a fraction.
    member_count, frame_count : `int`, optional
        What the fit rests on.
    reason : `str`, optional
        Why the source is not "measured".

    Returns
    -------
    record : `SessionScintillationRecord`
        The record, with the session id left empty.
    """
    predicted = young_median * MAGNITUDES_PER_FRACTION * 1000.0 if young_median is not None else None
    used = predicted * coefficient if predicted is not None and coefficient is not None else None
    return SessionScintillationRecord(
        source=source,
        coefficient=coefficient,
        measured_coefficient=measured,
        predicted_young_mmag=predicted,
        used_mmag=used,
        member_count=member_count or 0,
        frame_count=frame_count or 0,
        reason=reason,
    )


def build_scintillation_term(
    stars: list[StellarObject],
    exposure_seconds: dict[datetime, float],
    aperture_diameter_mm: float | None,
    aperture_reason: str | None,
    site_elevation_m: float,
    maximum_comparison_stars: int | None = None,
) -> ScintillationTerm:
    """Decide the scintillation term of one session.

    The stars' errors are the CCD-only errors when this is called. Nothing is
    changed; `add_scintillation_in_quadrature` applies the result.

    Parameters
    ----------
    stars : `list` [`StellarObject`]
        The session's stars with their raw light curves.
    exposure_seconds : `dict` [`datetime`, `float`]
        The exposure of each frame.
    aperture_diameter_mm : `float` or `None`
        The telescope's aperture, or `None` when it is not known.
    aperture_reason : `str` or `None`
        Why the aperture is not known.
    site_elevation_m : `float`
        The site's height above sea level, in meters. 0 means sea level or
        unknown; Young's formula then overstates by about 12 percent per
        kilometer of the true height, and the measured coefficient absorbs
        it. The fallback factor does not.
    maximum_comparison_stars : `int`, optional
        The cap on the comparison set, as in `select_comparison_set`.

    Returns
    -------
    term : `ScintillationTerm`
        The record and the per-frame fractions to add.
    """
    if aperture_diameter_mm is None:
        reason = aperture_reason or "the telescope's aperture is not known"
        return ScintillationTerm(_record(SOURCE_NONE, reason=f"no term added: {reason}"), {})
    young = _young_fraction_by_timestamp(stars, exposure_seconds, aperture_diameter_mm, site_elevation_m)
    if not young:
        return ScintillationTerm(
            _record(SOURCE_NONE, reason="no term added: no frame gave an airmass and an exposure"), {}
        )
    young_median = float(np.median(list(young.values())))
    minimum_members = resolve(MINIMUM_MEMBERS_FOR_COEFFICIENT).value
    minimum_frames = resolve(MINIMUM_FRAMES_FOR_COEFFICIENT).value
    fallback_factor = resolve(FALLBACK_YOUNG_FACTOR).value

    def fallback(
        reason: str, member_count: int | None = None, frame_count: int | None = None
    ) -> ScintillationTerm:
        """Use the documented factor and say why.

        Parameters
        ----------
        reason : `str`
            Why the coefficient could not be measured.
        member_count, frame_count : `int`, optional
            What the fit would have rested on.

        Returns
        -------
        term : `ScintillationTerm`
            The term with the fallback factor.
        """
        record = _record(
            SOURCE_FALLBACK,
            coefficient=fallback_factor,
            young_median=young_median,
            member_count=member_count,
            frame_count=frame_count,
            reason=f"{reason}; used the factor {fallback_factor:g} of Osborn et al. (2015)",
        )
        return ScintillationTerm(record, {stamp: fallback_factor * value for stamp, value in young.items()})

    table = build_flux_table(stars)
    if table is None:
        return fallback("no star has a positive flux in any frame")
    selection = select_comparison_set(table, maximum_comparison_stars)
    member_count = len(selection.member_rows)
    if not selection.uses_errors:
        return fallback("the comparison candidates lack CCD errors", member_count)
    if member_count < minimum_members:
        return fallback(
            f"{member_count} comparison star(s) are usable and {minimum_members} are needed", member_count
        )
    member_rows = list(selection.member_rows)
    table_young = np.array([young.get(stamp, math.nan) for stamp in table.timestamps], dtype=float)
    fit = fit_scintillation_coefficient(table.fluxes[member_rows], table.errors[member_rows], table_young)
    frame_count = 0 if fit is None else fit.frame_count
    if fit is None or frame_count < minimum_frames:
        return fallback(
            f"{frame_count} frame(s) with Young's prediction are usable and {minimum_frames} are needed",
            member_count,
            frame_count,
        )
    measured = math.sqrt(max(fit.coefficient_squared, 0.0))
    lowest = resolve(MINIMUM_COEFFICIENT).value
    highest = resolve(MAXIMUM_COEFFICIENT).value
    coefficient = min(max(measured, lowest), highest)
    limited = coefficient != measured
    reason = None
    if limited:
        reason = (
            f"the fitted coefficient {measured:.2f} lies outside {lowest:g} to {highest:g}, "
            f"so {coefficient:g} was used"
        )
    record = _record(
        SOURCE_MEASURED_LIMITED if limited else SOURCE_MEASURED,
        coefficient=coefficient,
        measured=measured,
        young_median=young_median,
        member_count=member_count,
        frame_count=frame_count,
        reason=reason,
    )
    return ScintillationTerm(record, {stamp: coefficient * value for stamp, value in young.items()})


def add_scintillation_in_quadrature(
    stars: list[StellarObject], fraction_by_timestamp: dict[datetime, float]
) -> None:
    """Add the term to every point's flux error, in place.

    Parameters
    ----------
    stars : `list` [`StellarObject`]
        The session's stars. A star whose errors do not line up with its
        timestamps keeps them as they are.
    fraction_by_timestamp : `dict` [`datetime`, `float`]
        The term of each frame as a fraction of a star's flux. A frame that
        is not listed keeps its errors.
    """
    for star in stars:
        photometry = star.photometry
        if photometry is None or not photometry.flux_errors:
            continue
        if not (len(photometry.flux_errors) == len(photometry.fluxes) == len(photometry.timestamps)):
            continue
        photometry.flux_errors = [
            _combine(error, flux, fraction_by_timestamp.get(stamp))
            for error, flux, stamp in zip(
                photometry.flux_errors, photometry.fluxes, photometry.timestamps, strict=True
            )
        ]


def _combine(error: float, flux: float, fraction: float | None) -> float:
    """Add ``fraction * flux`` to an error in quadrature.

    Parameters
    ----------
    error : `float`
        The CCD-only error, ADU per second.
    flux : `float`
        The flux, ADU per second.
    fraction : `float` or `None`
        The term as a fraction of the flux.

    Returns
    -------
    error : `float`
        The combined error. The input when it or the flux is not finite or
        the frame has no fraction.
    """
    if fraction is None or not (math.isfinite(error) and math.isfinite(flux)):
        return error
    return math.hypot(error, fraction * max(flux, 0.0))
