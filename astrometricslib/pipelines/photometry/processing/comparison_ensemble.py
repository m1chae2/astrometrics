"""Choose and weight the comparison stars for a session's light curves.

Differential photometry divides every star's flux by a signal made from other
stars in the same pictures. The signal removes what all stars share: cloud,
air clarity, airmass. It must not contain anything that only some stars do, or
that starts or stops partway through the session. This module builds that
signal in four steps.

1. **Candidates.** A star is a candidate when it is measured, unsaturated, and
   positive in every usable frame, and no catalog lists it as variable. The
   candidates are ranked by their median flux. The brightest 2 percent are
   skipped (they come closest to saturation and to the camera's non-linear
   range). The band reaches down to the brightest 30 percent. At most 100
   candidates are kept for the next step.
2. **Vetting.** A comparison star must be constant. Each candidate is compared
   with an ensemble built from the other candidates. The star whose scatter
   about that ensemble is largest, relative to the scatter its errors predict,
   is dropped. The step repeats until no remaining star scatters more than
   its errors allow, or until only 5 stars remain. "More than its errors
   allow" means a reduced chi-square above ``1 + 3 sqrt(2 / (N - 1))`` times
   the larger of 1 and the set's typical value, with N the number of frames.
   If more than the cap (default 20) remain, the stars with the smallest
   errors are kept.
3. **A fixed set.** The set that comes out is used for every frame of the
   session. A star missing or saturated in one frame was never a candidate,
   so the set cannot change from frame to frame.
4. **Weighting.** Each comparison star's flux is divided by its own mean over
   the session, so a bright star and a faint star count on the same scale.
   The per-frame signal is the inverse-variance weighted mean of those
   ratios. A star's weight is ``1 / sigma**2``, so a noisy star counts less.
   The ensemble error is ``1 / sqrt(sum of weights)``.

A comparison star is normalized against the ensemble of the *other* members
(leave-one-out), so its own noise and its own changes never enter its own
divisor. Without this, a dip in a member would be hidden by about 1 / N of its
depth.

Units and conventions:

* Fluxes are in ADU per second. The signal ``level`` is in ADU per second too:
  the weighted mean ratio times the mean of the members' session means. A
  star that is as bright as the average member has a normalized flux near 1.
* Errors are 1-sigma. When every candidate has errors from the CCD equation,
  those errors set the weights and the expected scatter. When any candidate
  lacks them, a noise estimate from the point-to-point differences of the
  star's residuals about the ensemble takes their place (1.4826 times the
  median absolute deviation of the differences, divided by the square root of
  2). The ensemble then has no error to report.
* Scatter is reported in magnitudes, ``2.5 log10(1 + CV)`` with CV the
  standard deviation divided by the mean.

What is measured and what is designed: the band edges (2 and 30 percent), the
5-star minimum, the 20-star cap and the 3-sigma consistency limit are design
choices. The tests in ``pipelines/photometry/test`` check the method on
synthetic light curves with known truth. No real field has been used to check
the limits.
"""

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.photometry.post_processing.known_variability_labels import (
    is_listed_as_variable,
)

# The fewest comparison stars a session may end with. Below this, the
# ensemble's own noise and its chance of containing a variable star are too
# large to trust. Vetting stops dropping stars at this size.
MINIMUM_COMPARISON_STARS = 5

# The default cap on the comparison set. More stars lower the ensemble noise
# as 1 / sqrt(N), but the extra stars are fainter and noisier, so the gain
# shrinks quickly. A design choice.
DEFAULT_MAXIMUM_COMPARISON_STARS = 20

# The share of the brightest candidates that is skipped. Stars this bright are
# the likeliest to be close to saturation, where a flux no longer rises in
# step with the star's brightness. A design choice.
BRIGHTEST_SKIPPED_FRACTION = 0.02

# The brightness rank, as a share of the candidates, at which the band ends.
# A design choice.
FAINTEST_BAND_FRACTION = 0.30

# The band is widened to at least this many candidates, so vetting has stars
# to drop. A field with fewer candidates than this uses all of them.
MINIMUM_VETTING_CANDIDATES = 10

# The most candidates that go into vetting. Each round of vetting looks at
# every pair of stars, so the cost grows with the square of this number.
MAXIMUM_VETTING_CANDIDATES = 100

# A frame is usable when at least this share of the typical number of
# measured stars has a positive flux in it. A frame where tracking failed or
# a cloud hid the field has far fewer, and is left out of the whole session.
MINIMUM_USABLE_FRAME_FRACTION = 0.5

# A star is too scattered when its reduced chi-square is more than this many
# standard deviations above the reference value. The standard deviation of a
# reduced chi-square with N - 1 degrees of freedom is sqrt(2 / (N - 1)).
EXCESS_SCATTER_SIGMA = 3.0

# The smallest fractional noise a star may be given. It stops a perfectly
# constant light curve (a test fixture, never real data) from dividing by zero.
MINIMUM_FRACTIONAL_NOISE = 1.0e-6

# The reduced chi-square that a constant star with correct errors has. A star
# is judged against the larger of this and the set's typical value. If the
# errors are too small for every star (an assumed gain), the typical value
# is the fairer reference. If they are too large, a star that fits its errors
# is still kept.
_CONSISTENT_CHI_SQUARE = 1.0

# 1.4826 times the median absolute deviation equals the standard deviation of
# normally distributed values.
_MAD_TO_SIGMA = 1.4826

# The fewest frames for which a consistency test is meaningful.
_MINIMUM_FRAMES_FOR_VETTING = 4


def fractional_to_magnitudes(fraction: float) -> float:
    """Convert a fractional flux change to magnitudes.

    Parameters
    ----------
    fraction : `float`
        A fractional change, such as a coefficient of variation. It has no
        unit.

    Returns
    -------
    magnitudes : `float`
        ``2.5 log10(1 + fraction)``, in magnitudes.
    """
    return 2.5 * math.log10(1.0 + fraction)


@dataclass(frozen=True, eq=False)
class FluxTable:
    """The measured fluxes of one session laid out as a table.

    Attributes
    ----------
    star_ids : `tuple` [`str`]
        The id of each row's star.
    timestamps : `tuple` [`datetime.datetime`]
        The usable frames, oldest first. One column each.
    fluxes : `numpy.ndarray`
        Shape ``(stars, frames)``, ADU per second. `numpy.nan` where the star
        has no positive flux in the frame.
    errors : `numpy.ndarray`
        Same shape, ADU per second. `numpy.nan` where the star has no usable
        error.
    ever_saturated : `numpy.ndarray`
        One `bool` per star: it was saturated in at least one frame.
    saturation_trusted : `numpy.ndarray`
        One `bool` per star: its saturation flags line up with its frames.
        A star whose flags do not line up cannot be shown unsaturated.
    listed_variable : `numpy.ndarray`
        One `bool` per star: a catalog lists it as variable.
    unusable_timestamps : `tuple` [`datetime.datetime`]
        Frames left out of the table because too few stars were measured.
    """

    star_ids: tuple[str, ...]
    timestamps: tuple[datetime, ...]
    fluxes: np.ndarray
    errors: np.ndarray
    ever_saturated: np.ndarray
    saturation_trusted: np.ndarray
    listed_variable: np.ndarray
    unusable_timestamps: tuple[datetime, ...]


@dataclass(frozen=True)
class ComparisonSelection:
    """Which stars were chosen, and which were turned away.

    Attributes
    ----------
    member_rows : `tuple` [`int`]
        Rows of the `FluxTable` that make up the comparison set.
    vetted_out_rows : `tuple` [`int`]
        Candidates dropped because their scatter exceeded what their errors
        allow, in the order they were dropped.
    listed_variable_rows : `tuple` [`int`]
        Rows of stars that were measured in every usable frame and never
        saturated, but that a catalog lists as variable.
    candidate_count : `int`
        How many stars went into vetting.
    uses_errors : `bool`
        Whether the CCD-equation errors set the weights. `False` means a
        noise estimate from the light curves was used.
    """

    member_rows: tuple[int, ...]
    vetted_out_rows: tuple[int, ...]
    listed_variable_rows: tuple[int, ...]
    candidate_count: int
    uses_errors: bool


@dataclass(frozen=True)
class ComparisonSetResult:
    """What one session recorded about its comparison set.

    Attributes
    ----------
    star_ids : `tuple` [`str`]
        The ids of the comparison stars. The same stars normalize every frame.
    rejected_ids : `tuple` [`str`]
        Candidates dropped by the constancy check.
    listed_variable_ids : `tuple` [`str`]
        Otherwise eligible stars left out because a catalog lists them as
        variable.
    scatter_mag : `float` or `None`
        The typical scatter of a comparison star's normalized light curve, in
        magnitudes: the median over the set of ``2.5 log10(1 + CV)``.
    expected_error_mag : `float` or `None`
        The typical error the propagated uncertainties predict for those same
        points, in magnitudes, or `None` when the session has no errors.
    candidate_count : `int`
        How many stars went into vetting.
    uses_errors : `bool`
        Whether the CCD-equation errors set the weights.
    frame_sizes : `tuple` [`int`]
        The number of comparison stars used for each frame. All entries are
        equal when the set is fixed.
    """

    star_ids: tuple[str, ...]
    rejected_ids: tuple[str, ...] = ()
    listed_variable_ids: tuple[str, ...] = ()
    scatter_mag: float | None = None
    expected_error_mag: float | None = None
    candidate_count: int = 0
    uses_errors: bool = False
    frame_sizes: tuple[int, ...] = ()

    @property
    def rejected_count(self) -> int:
        """Count the stars that were eligible but not allowed in the set.

        Returns
        -------
        count : `int`
            Candidates dropped by the constancy check plus the stars left out
            for being listed as variable.
        """
        return len(self.rejected_ids) + len(self.listed_variable_ids)

    @property
    def is_fixed(self) -> bool:
        """Say whether every frame used the same number of comparison stars.

        Returns
        -------
        is_fixed : `bool`
            `True` when every entry of `frame_sizes` equals the set size.
            `True` when no frame sizes were recorded.
        """
        return all(size == len(self.star_ids) for size in self.frame_sizes)


@dataclass(frozen=True, eq=False)
class EnsembleSignal:
    """The comparison signal of a session, frame by frame.

    Attributes
    ----------
    level : `numpy.ndarray`
        The ensemble level of each frame, ADU per second.
    level_error : `numpy.ndarray` or `None`
        The 1-sigma error of each level, ADU per second, or `None` when the
        members have no errors.
    member_levels : `numpy.ndarray`
        Shape ``(members, frames)``. The level each member is divided by: the
        ensemble built from the other members.
    member_level_errors : `numpy.ndarray` or `None`
        The 1-sigma errors of `member_levels`, or `None`.
    """

    level: np.ndarray
    level_error: np.ndarray | None
    member_levels: np.ndarray
    member_level_errors: np.ndarray | None


def build_flux_table(stars: list[StellarObject]) -> FluxTable | None:
    """Lay out every star's flux, error and saturation flags as a table.

    A frame is usable when at least half the typical number of stars have a
    positive flux in it. The table holds only usable frames.

    Parameters
    ----------
    stars : `list` [`StellarObject`]
        The session's stars with their raw light curves.

    Returns
    -------
    table : `FluxTable` or `None`
        `None` when no star has a measurement, or when no frame has a
        positive flux.
    """
    tracked = [star for star in stars if star.photometry is not None and star.photometry.fluxes]
    stamps = sorted({stamp for star in tracked for stamp in star.photometry.timestamps})
    if not stamps:
        return None
    column = {stamp: index for index, stamp in enumerate(stamps)}
    row_count, frame_count = len(tracked), len(stamps)
    fluxes = np.full((row_count, frame_count), np.nan)
    errors = np.full((row_count, frame_count), np.nan)
    ever_saturated = np.zeros(row_count, dtype=bool)
    saturation_trusted = np.ones(row_count, dtype=bool)
    listed_variable = np.zeros(row_count, dtype=bool)
    for row, star in enumerate(tracked):
        light_curve = star.photometry
        count = min(len(light_curve.timestamps), len(light_curve.fluxes))
        flags = light_curve.is_saturated
        if flags and len(flags) != len(light_curve.timestamps):
            saturation_trusted[row] = False
        elif flags:
            ever_saturated[row] = any(flags[:count])
        errors_line_up = bool(light_curve.flux_errors) and len(light_curve.flux_errors) == len(
            light_curve.timestamps
        )
        for index in range(count):
            flux = float(light_curve.fluxes[index])
            if not (math.isfinite(flux) and flux > 0):
                continue
            fluxes[row, column[light_curve.timestamps[index]]] = flux
            if errors_line_up:
                error = float(light_curve.flux_errors[index])
                if math.isfinite(error) and error > 0:
                    errors[row, column[light_curve.timestamps[index]]] = error
        listed_variable[row] = is_listed_as_variable(star)

    measured_per_frame = np.sum(np.isfinite(fluxes), axis=0)
    typical = float(np.median(measured_per_frame))
    if typical <= 0:
        return None
    usable = measured_per_frame >= MINIMUM_USABLE_FRAME_FRACTION * typical
    return FluxTable(
        star_ids=tuple(star.id for star in tracked),
        timestamps=tuple(stamp for stamp, keep in zip(stamps, usable, strict=True) if keep),
        fluxes=fluxes[:, usable],
        errors=errors[:, usable],
        ever_saturated=ever_saturated,
        saturation_trusted=saturation_trusted,
        listed_variable=listed_variable,
        unusable_timestamps=tuple(stamp for stamp, keep in zip(stamps, usable, strict=True) if not keep),
    )


def _point_to_point_noise(relative: np.ndarray) -> np.ndarray:
    """Estimate each star's fractional noise from neighbouring frames.

    For each star the residual about the unweighted mean of the others is
    taken in each frame. The noise is 1.4826 times the median absolute
    deviation of the frame-to-frame differences of that residual, divided by
    the square root of 2. Slow changes cancel in the differences, so this
    measures the white (frame-independent) noise.

    Parameters
    ----------
    relative : `numpy.ndarray`
        Shape ``(stars, frames)``. Each row is a star's flux divided by its
        mean, so it has no unit.

    Returns
    -------
    noise : `numpy.ndarray`
        One fractional noise per star, never below `MINIMUM_FRACTIONAL_NOISE`.
    """
    stars = relative.shape[0]
    if stars < 2 or relative.shape[1] < 3:
        return np.full(stars, MINIMUM_FRACTIONAL_NOISE)
    others_mean = (relative.sum(axis=0) - relative) / (stars - 1)
    differences = np.diff(relative - others_mean, axis=1)
    centre = np.median(differences, axis=1, keepdims=True)
    mad = np.median(np.abs(differences - centre), axis=1)
    return np.maximum(_MAD_TO_SIGMA * mad / math.sqrt(2.0), MINIMUM_FRACTIONAL_NOISE)


def _leave_one_out(relative: np.ndarray, fractional_error: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Build, for each star, the weighted ensemble of all the other stars.

    Parameters
    ----------
    relative : `numpy.ndarray`
        Shape ``(stars, frames)``. Flux divided by the star's mean.
    fractional_error : `numpy.ndarray`
        Same shape. The 1-sigma error of each entry of `relative`.

    Returns
    -------
    level, variance : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        Both of shape ``(stars, frames)``. Row ``i`` is the inverse-variance
        weighted mean of the other stars' ratios, and its variance
        ``1 / sum(weights)``.
    """
    weights = 1.0 / fractional_error**2
    total_weight = weights.sum(axis=0)
    weighted_sum = (weights * relative).sum(axis=0)
    others_weight = total_weight - weights
    return (weighted_sum - weights * relative) / others_weight, 1.0 / others_weight


def _reduced_chi_squares(relative: np.ndarray, fractional_error: np.ndarray) -> np.ndarray:
    """Measure how far each star scatters beyond what its errors allow.

    For star ``i`` the residual in a frame is its ratio minus the ensemble of
    the others. The expected variance is the star's own variance plus the
    ensemble's. The result is the sum of squared residuals over expected
    variances, divided by ``frames - 1``. It is near 1 for a constant star
    whose errors are right.

    Parameters
    ----------
    relative, fractional_error : `numpy.ndarray`
        Shape ``(stars, frames)``, as in `_leave_one_out`.

    Returns
    -------
    chi_square : `numpy.ndarray`
        One reduced chi-square per star. It has no unit.
    """
    level, variance = _leave_one_out(relative, fractional_error)
    residual = relative - level
    return np.sum(residual**2 / (fractional_error**2 + variance), axis=1) / (relative.shape[1] - 1)


def _vet_candidates(
    fluxes: np.ndarray, errors: np.ndarray | None, maximum: int
) -> tuple[list[int], list[int]]:
    """Drop the candidates that scatter too much, one at a time.

    Parameters
    ----------
    fluxes : `numpy.ndarray`
        Shape ``(candidates, frames)``, ADU per second, all positive.
    errors : `numpy.ndarray` or `None`
        Same shape. The CCD-equation errors, or `None` to use a noise
        estimate from the light curves.
    maximum : `int`
        The cap on the set size.

    Returns
    -------
    kept, dropped : `tuple` [`list` [`int`], `list` [`int`]]
        Candidate indices kept (in their input order) and dropped (in the
        order they were dropped). If more than `maximum` remain consistent,
        the ones with the smallest errors are kept.
    """
    candidate_count, frame_count = fluxes.shape
    means = fluxes.mean(axis=1, keepdims=True)
    relative = fluxes / means
    if errors is None:
        fractional_error = np.repeat(_point_to_point_noise(relative)[:, None], frame_count, axis=1)
    else:
        fractional_error = errors / means

    active = list(range(candidate_count))
    dropped: list[int] = []
    if frame_count >= _MINIMUM_FRAMES_FOR_VETTING:
        limit = 1.0 + EXCESS_SCATTER_SIGMA * math.sqrt(2.0 / (frame_count - 1))
        while len(active) > MINIMUM_COMPARISON_STARS:
            chi_square = _reduced_chi_squares(relative[active], fractional_error[active])
            scale = max(float(np.median(chi_square)), _CONSISTENT_CHI_SQUARE)
            worst = int(np.argmax(chi_square))
            if chi_square[worst] <= limit * scale:
                break
            dropped.append(active.pop(worst))

    if len(active) > maximum:
        typical_error = np.median(fractional_error[active], axis=1)
        best = np.argsort(typical_error, kind="stable")[:maximum]
        active = sorted(active[position] for position in best.tolist())
    return active, dropped


def select_comparison_set(
    table: FluxTable, maximum: int = DEFAULT_MAXIMUM_COMPARISON_STARS
) -> ComparisonSelection:
    """Choose the comparison stars for a session.

    A candidate is measured with a positive flux in every usable frame, never
    saturated, and not listed as variable by a catalog. The candidates are
    ranked by median flux. The brightest `BRIGHTEST_SKIPPED_FRACTION` are
    skipped and the band ends at the brightest `FAINTEST_BAND_FRACTION`. The
    band is widened to `MINIMUM_VETTING_CANDIDATES` stars when it is
    narrower, and cut to the brightest `MAXIMUM_VETTING_CANDIDATES`.
    Vetting then removes stars that scatter more than their errors allow.

    Parameters
    ----------
    table : `FluxTable`
        The session's fluxes.
    maximum : `int`, optional
        The cap on the set size. At least `MINIMUM_COMPARISON_STARS`.

    Returns
    -------
    selection : `ComparisonSelection`
        The chosen rows and the rows turned away.
    """
    maximum = max(maximum, MINIMUM_COMPARISON_STARS)
    eligible = np.all(np.isfinite(table.fluxes), axis=1) & ~table.ever_saturated & table.saturation_trusted
    listed_rows = np.flatnonzero(eligible & table.listed_variable)
    pool = np.flatnonzero(eligible & ~table.listed_variable)
    if pool.size == 0:
        return ComparisonSelection((), (), tuple(listed_rows.tolist()), 0, False)

    brightness = np.median(table.fluxes[pool], axis=1)
    ranked = pool[np.argsort(-brightness, kind="stable")]
    first = math.floor(BRIGHTEST_SKIPPED_FRACTION * ranked.size)
    last = max(
        math.ceil(FAINTEST_BAND_FRACTION * ranked.size), min(ranked.size, first + MINIMUM_VETTING_CANDIDATES)
    )
    band = ranked[first:last][:MAXIMUM_VETTING_CANDIDATES]

    uses_errors = bool(np.all(np.isfinite(table.errors[band])))
    kept, dropped = _vet_candidates(table.fluxes[band], table.errors[band] if uses_errors else None, maximum)
    return ComparisonSelection(
        member_rows=tuple(band[kept].tolist()),
        vetted_out_rows=tuple(band[dropped].tolist()),
        listed_variable_rows=tuple(listed_rows.tolist()),
        candidate_count=int(band.size),
        uses_errors=uses_errors,
    )


def build_ensemble_signal(
    fluxes: np.ndarray, errors: np.ndarray | None, frame_mask: np.ndarray
) -> EnsembleSignal:
    """Build the weighted comparison signal of a session.

    Each member's flux is divided by its mean over the frames in
    `frame_mask`. The signal in a frame is the inverse-variance weighted mean
    of those ratios. It is scaled back to ADU per second with the mean of the
    members' means. Each member also gets the signal built from the other
    members, so that its own flux never enters its divisor.

    Parameters
    ----------
    fluxes : `numpy.ndarray`
        Shape ``(members, frames)``, ADU per second, all positive. At least
        two members.
    errors : `numpy.ndarray` or `None`
        Same shape, the 1-sigma errors in ADU per second. `None` means the
        members have no errors, and a noise estimate sets the weights.
    frame_mask : `numpy.ndarray`
        One `bool` per frame. Frames set to `False` are left out of each
        star's mean (a frame already rejected as an outlier) but still get a
        signal.

    Returns
    -------
    signal : `EnsembleSignal`
        The level of every frame, and for every member the level without it.
    """
    means = fluxes[:, frame_mask].mean(axis=1, keepdims=True)
    relative = fluxes / means
    if errors is None:
        fractional_error = np.repeat(
            _point_to_point_noise(relative[:, frame_mask])[:, None], fluxes.shape[1], axis=1
        )
    else:
        fractional_error = errors / means
    scale = float(means.mean())
    weights = 1.0 / fractional_error**2
    total_weight = weights.sum(axis=0)
    level = scale * (weights * relative).sum(axis=0) / total_weight
    member_levels, member_variance = _leave_one_out(relative, fractional_error)
    has_errors = errors is not None
    return EnsembleSignal(
        level=level,
        level_error=scale / np.sqrt(total_weight) if has_errors else None,
        member_levels=scale * member_levels,
        member_level_errors=scale * np.sqrt(member_variance) if has_errors else None,
    )


def set_result(
    table: FluxTable,
    selection: ComparisonSelection,
    frame_sizes: tuple[int, ...] = (),
    scatter_mag: float | None = None,
    expected_error_mag: float | None = None,
) -> ComparisonSetResult:
    """Describe a selection for the summary and the gates.

    Parameters
    ----------
    table : `FluxTable`
        The table the selection was made from.
    selection : `ComparisonSelection`
        The chosen and rejected rows.
    frame_sizes : `tuple` [`int`], optional
        The number of comparison stars used for each frame.
    scatter_mag : `float`, optional
        The members' typical scatter, in magnitudes.
    expected_error_mag : `float`, optional
        The members' typical propagated error, in magnitudes.

    Returns
    -------
    result : `ComparisonSetResult`
        The record, with the rows turned into star ids.
    """
    return ComparisonSetResult(
        star_ids=tuple(table.star_ids[row] for row in selection.member_rows),
        rejected_ids=tuple(table.star_ids[row] for row in selection.vetted_out_rows),
        listed_variable_ids=tuple(table.star_ids[row] for row in selection.listed_variable_rows),
        scatter_mag=scatter_mag,
        expected_error_mag=expected_error_mag,
        candidate_count=selection.candidate_count,
        uses_errors=selection.uses_errors,
        frame_sizes=frame_sizes,
    )
