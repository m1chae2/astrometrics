"""Choose and weight the comparison stars for a session's light curves.

Differential photometry divides every star's flux by a signal made from other
stars in the same pictures. The signal removes what all stars share: cloud,
air clarity, airmass. It must not contain anything that only some stars do, or
that starts or stops partway through the session. This module builds that
signal in four steps.

1. **Candidates.** A star is a candidate when it is measured in at least 95
   percent of the usable frames, its median flux is at least 50 times its own
   error (signal-to-noise of 50, so it adds at most 2 percent noise), it is
   never saturated and not crowded by a bright neighbor, and no catalog lists
   it as variable. A faint star is not left out for one frame in which noise
   pushes its measured flux below zero: that flux is kept, with its error. A
   star with no errors is held to the old rule, a positive flux in every
   usable frame. The candidates are ranked by their median flux. The
   brightest 2 percent are skipped (they come closest to saturation and to
   the camera's non-linear range). The band reaches down to the brightest 30
   percent. At most 100 candidates are kept for the next step.
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
* A flux can be zero or below. A faint star in a noisy frame measures a little
  below zero about half the time, and the table keeps the measured value. The
  frames a star was not measured in (no error, as at the frame edge) are
  `numpy.nan`, and every step below weights them zero.
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
import warnings
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.photometry.post_processing.known_variability_labels import (
    is_listed_as_variable,
)

# The fewest comparison stars a session may end with. Below this, the
# ensemble's own noise and its chance of containing a variable star are too
# large to trust. Vetting stops dropping stars at this size.
MINIMUM_COMPARISON_STARS = TunedParameter(
    name="photometry.minimum_comparison_stars",
    default=5,
    unit="stars",
    description="Fewest comparison stars a session may end with.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-01",
)

# The default cap on the comparison set. More stars lower the ensemble noise
# as 1 / sqrt(N), but the extra stars are fainter and noisier, so the gain
# shrinks quickly. A design choice.
DEFAULT_MAXIMUM_COMPARISON_STARS = TunedParameter(
    name="photometry.default_maximum_comparison_stars",
    default=20,
    unit="stars",
    description="Default cap on the size of the comparison set.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-01",
)

# The share of the brightest candidates that is skipped. Stars this bright are
# the likeliest to be close to saturation, where a flux no longer rises in
# step with the star's brightness. A design choice.
BRIGHTEST_SKIPPED_FRACTION = TunedParameter(
    name="photometry.brightest_skipped_fraction",
    default=0.02,
    unit="fraction",
    description="Share of the brightest candidate comparison stars that is skipped.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-01",
)

# The brightness rank, as a share of the candidates, at which the band ends.
# A design choice.
FAINTEST_BAND_FRACTION = TunedParameter(
    name="photometry.faintest_band_fraction",
    default=0.30,
    unit="fraction",
    description=(
        "Brightness rank, as a share of the candidates, at which the band of candidate comparison stars ends."
    ),
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-01",
)

# The band is widened to at least this many candidates, so vetting has stars
# to drop. A field with fewer candidates than this uses all of them.
MINIMUM_VETTING_CANDIDATES = TunedParameter(
    name="photometry.minimum_vetting_candidates",
    default=10,
    unit="stars",
    description="Fewest candidates that go into vetting, so vetting has stars to drop.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-D05",
)

# The most candidates that go into vetting. Each round of vetting looks at
# every pair of stars, so the cost grows with the square of this number.
MAXIMUM_VETTING_CANDIDATES = TunedParameter(
    name="photometry.maximum_vetting_candidates",
    default=100,
    unit="stars",
    description="Most candidates that go into vetting, which bounds its cost.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-D05",
)

# A comparison candidate must have a median flux at least this many times its
# own 1-sigma error. A comparison star's noise goes into every star it divides:
# one star at signal-to-noise S adds a fractional noise of 1 / S to the level,
# so S = 50 is 2 percent (0.022 mag) for a single star, and the weighted mean
# of several such stars is lower. The errors come from the CCD equation with
# the camera's gain, plus the scintillation term once it is added. Checked on
# the raw frames of NGC 7331, M 31, M 57 and M 52 (2026-11-22, PHO-21): the
# stars this rule turns away have a median signal-to-noise under 50 and
# measure at or below zero in some frames; the comparison sets it builds
# have an ensemble error and member scatter equal to or lower than the old
# rule's (M 31, M 52) or far lower (M 57).
MINIMUM_COMPARISON_SIGNAL_TO_NOISE = TunedParameter(
    name="photometry.minimum_comparison_signal_to_noise",
    default=50.0,
    unit="ratio",
    description="Least median ratio of a comparison candidate's flux to its own 1-sigma error.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-21",
)

# A comparison candidate must have a flux and an error in at least this share
# of the usable frames. A star that is lost in a few frames (it sits at the
# frame edge when the field shifts) can still serve: the frames it is missing
# from give it no weight. Below 95 percent the star is missing from so many
# frames that the signal there would rest on too few stars. A design choice,
# checked against the missing shares of the four fields of PHO-21.
MINIMUM_MEASURED_FRAME_SHARE = TunedParameter(
    name="photometry.minimum_measured_frame_share",
    default=0.95,
    unit="fraction",
    description="Share of the usable frames in which a comparison candidate must have a flux and an error.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-21",
)

# A frame takes part in choosing the comparison set when at least this share
# of the typical number of measured stars has a positive flux in it. A frame
# below it (a lost field, cloud, a short exposure that leaves faint stars
# under the noise) is left out of the choice only: it is still normalized with
# the comparison stars measured in it and stays in the light curves. A
# comparison star must be measured in almost every frame that takes part
# (`MINIMUM_MEASURED_FRAME_SHARE`), so each frame that stays in would shrink
# the pool to the stars it still holds. The count uses stars with a positive
# flux, so a frame where noise hides the faint stars is still set aside.
# Derivation and check (see PHO-D07): the real frame worker was run on every
# frame and the share of each frame counted (2026-11-22). M 31 (121 frames of
# 2026-11-05, the 30 s frames are the low ones): shares 0.68 to 1.05, and the
# pool of stars positive in all frames grows from 5 at 0.5 to 89 at 0.8 and
# 411 at 0.9. NGC 7331 (135 frames): shares 0.81 to 1.29, pool 0 at 0.8 and
# 1 at 0.9. The shares have no gap, so 0.9 is a design estimate: it keeps
# the frames at the typical count and sets the weakest tenth aside.
MINIMUM_USABLE_FRAME_FRACTION = TunedParameter(
    name="photometry.minimum_usable_frame_fraction",
    default=0.9,
    unit="fraction",
    description="Share of the typical number of measured stars a frame needs to count as usable.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-D07",
)

# A star is too scattered when its reduced chi-square is more than this many
# standard deviations above the reference value. The standard deviation of a
# reduced chi-square with N - 1 degrees of freedom is sqrt(2 / (N - 1)).
EXCESS_SCATTER_SIGMA = TunedParameter(
    name="photometry.excess_scatter_sigma",
    default=3.0,
    unit="sigma",
    description=(
        "How many standard deviations above the reference reduced chi-square a comparison star "
        "may sit before it counts as too scattered."
    ),
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-01",
)

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
_MINIMUM_FRAMES_FOR_VETTING = TunedParameter(
    name="photometry.minimum_frames_for_vetting",
    default=4,
    unit="frames",
    description="Fewest frames for which the comparison-star consistency test is meaningful.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-D07",
)


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
        Shape ``(stars, frames)``, ADU per second. The measured value, which
        can be zero or below for a faint star in a noisy frame. `numpy.nan`
        where the star was not measured in the frame (it has no error, as at
        the frame edge), or, for a star whose light curve has no errors at
        all, where its flux is not above zero.
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
        Frames that do not take part in choosing the comparison set because
        too few stars were measured in them. They still get a level from the
        chosen stars (see `levels_for_frames_outside_the_fit`).
    unusable_fluxes, unusable_errors : `numpy.ndarray`
        Shape ``(stars, unusable frames)``, as `fluxes` and `errors`, for
        the frames in `unusable_timestamps`. A flux that is not above zero is
        `numpy.nan` here: in a frame that measured few stars, a star counts
        as measured only when it is seen.
    crowded : `numpy.ndarray` or `None`
        One `bool` per star: a bright neighbor lies within its aperture's
        reach (see `CROWDING_MARGIN_PX`), so its flux may carry the
        neighbor's light. `None` when no star recorded crowding. A crowded
        star is never chosen as a comparison star.
    """

    star_ids: tuple[str, ...]
    timestamps: tuple[datetime, ...]
    fluxes: np.ndarray
    errors: np.ndarray
    ever_saturated: np.ndarray
    saturation_trusted: np.ndarray
    listed_variable: np.ndarray
    unusable_timestamps: tuple[datetime, ...]
    crowded: np.ndarray | None = None
    unusable_fluxes: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    unusable_errors: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))


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

    A frame is usable when at least `MINIMUM_USABLE_FRAME_FRACTION` of the
    typical number of stars have a positive flux in it. The table holds only
    usable frames; the others are kept apart in `FluxTable.unusable_fluxes`,
    because they do not take part in choosing the comparison set. In a usable
    frame a flux that is zero or below is kept when the star has an error
    there; it is only `numpy.nan` when the star was not measured.

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
    minimum_usable_frame_fraction = resolve(MINIMUM_USABLE_FRAME_FRACTION).value
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
    crowded = np.zeros(row_count, dtype=bool)
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
            if not math.isfinite(flux):
                continue
            if errors_line_up:
                # With an error, the measured flux counts even when it is zero
                # or below. A zero error means the star was not measured (it
                # was too close to the frame edge).
                error = float(light_curve.flux_errors[index])
                if not (math.isfinite(error) and error > 0):
                    continue
                errors[row, column[light_curve.timestamps[index]]] = error
            elif not flux > 0:
                continue
            fluxes[row, column[light_curve.timestamps[index]]] = flux
        listed_variable[row] = is_listed_as_variable(star)
        crowded[row] = bool(light_curve.crowded)

    positive = fluxes > 0
    measured_per_frame = np.sum(positive, axis=0)
    typical = float(np.median(measured_per_frame))
    if typical <= 0:
        return None
    usable = measured_per_frame >= minimum_usable_frame_fraction * typical
    return FluxTable(
        star_ids=tuple(star.id for star in tracked),
        timestamps=tuple(stamp for stamp, keep in zip(stamps, usable, strict=True) if keep),
        fluxes=fluxes[:, usable],
        errors=errors[:, usable],
        ever_saturated=ever_saturated,
        saturation_trusted=saturation_trusted,
        listed_variable=listed_variable,
        unusable_timestamps=tuple(stamp for stamp, keep in zip(stamps, usable, strict=True) if not keep),
        unusable_fluxes=np.where(positive[:, ~usable], fluxes[:, ~usable], np.nan),
        unusable_errors=np.where(positive[:, ~usable], errors[:, ~usable], np.nan),
        crowded=crowded if any(star.photometry.crowded is not None for star in tracked) else None,
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
        mean, so it has no unit. `numpy.nan` marks a frame the star was not
        measured in.

    Returns
    -------
    noise : `numpy.ndarray`
        One fractional noise per star, never below `MINIMUM_FRACTIONAL_NOISE`.
    """
    stars = relative.shape[0]
    if stars < 2 or relative.shape[1] < 3:
        return np.full(stars, MINIMUM_FRACTIONAL_NOISE)
    # A frame a star was not measured in counts as the star's own mean, so it
    # adds no difference.
    relative = np.where(np.isfinite(relative), relative, 1.0)
    others_mean = (relative.sum(axis=0) - relative) / (stars - 1)
    differences = np.diff(relative - others_mean, axis=1)
    centre = np.median(differences, axis=1, keepdims=True)
    mad = np.median(np.abs(differences - centre), axis=1)
    return np.maximum(_MAD_TO_SIGMA * mad / math.sqrt(2.0), MINIMUM_FRACTIONAL_NOISE)


def _weights_and_ratios(
    relative: np.ndarray, fractional_error: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Turn ratios and errors into weights, giving zero weight to missing ones.

    Parameters
    ----------
    relative, fractional_error : `numpy.ndarray`
        Shape ``(stars, frames)``. Flux divided by the star's mean, and the
        1-sigma error of each entry. `numpy.nan` marks a star that was not
        measured in a frame.

    Returns
    -------
    weights, ratios, measured : `tuple` [`numpy.ndarray`]
        Same shape. The weight ``1 / sigma**2`` (0 where not measured), the
        ratio (0 where not measured) and a `bool` that is `True` where the
        star was measured.
    """
    measured = np.isfinite(relative) & np.isfinite(fractional_error) & (fractional_error > 0)
    safe_error = np.where(measured, fractional_error, 1.0)
    weights = np.where(measured, 1.0 / safe_error**2, 0.0)
    ratios = np.where(measured, relative, 0.0)
    return weights, ratios, measured


def _leave_one_out(relative: np.ndarray, fractional_error: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Build, for each star, the weighted ensemble of all the other stars.

    Parameters
    ----------
    relative : `numpy.ndarray`
        Shape ``(stars, frames)``. Flux divided by the star's mean. A frame a
        star was not measured in is `numpy.nan` and gives it no weight.
    fractional_error : `numpy.ndarray`
        Same shape. The 1-sigma error of each entry of `relative`.

    Returns
    -------
    level, variance : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        Both of shape ``(stars, frames)``. Row ``i`` is the inverse-variance
        weighted mean of the other stars' ratios, and its variance
        ``1 / sum(weights)``. Both are `numpy.nan` in a frame where no other
        star was measured.
    """
    weights, ratios, _ = _weights_and_ratios(relative, fractional_error)
    total_weight = weights.sum(axis=0)
    weighted_sum = (weights * ratios).sum(axis=0)
    others_weight = total_weight - weights
    with np.errstate(divide="ignore", invalid="ignore"):
        level = (weighted_sum - weights * ratios) / others_weight
        variance = 1.0 / others_weight
    empty = others_weight <= 0
    return np.where(empty, np.nan, level), np.where(empty, np.nan, variance)


def _reduced_chi_squares(relative: np.ndarray, fractional_error: np.ndarray) -> np.ndarray:
    """Measure how far each star scatters beyond what its errors allow.

    For star ``i`` the residual in a frame is its ratio minus the ensemble of
    the others. The expected variance is the star's own variance plus the
    ensemble's. The result is the sum of squared residuals over expected
    variances, divided by the number of frames the star was measured in minus
    one. It is near 1 for a constant star whose errors are right.

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
    _, ratios, measured = _weights_and_ratios(relative, fractional_error)
    valid = measured & np.isfinite(level)
    safe_error = np.where(valid, fractional_error, 1.0)
    terms = np.where(
        valid,
        (ratios - np.where(valid, level, 0.0)) ** 2 / (safe_error**2 + np.where(valid, variance, 0.0)),
        0.0,
    )
    degrees_of_freedom = np.maximum(valid.sum(axis=1) - 1, 1)
    return terms.sum(axis=1) / degrees_of_freedom


def _vet_candidates(
    fluxes: np.ndarray, errors: np.ndarray | None, maximum: int
) -> tuple[list[int], list[int]]:
    """Drop the candidates that scatter too much, one at a time.

    Parameters
    ----------
    fluxes : `numpy.ndarray`
        Shape ``(candidates, frames)``, ADU per second. A frame a candidate
        was not measured in is `numpy.nan`.
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
    minimum_frames_for_vetting = resolve(_MINIMUM_FRAMES_FOR_VETTING).value
    minimum_comparison_stars = resolve(MINIMUM_COMPARISON_STARS).value
    excess_scatter_sigma = resolve(EXCESS_SCATTER_SIGMA).value
    candidate_count, frame_count = fluxes.shape
    means = np.nanmean(fluxes, axis=1, keepdims=True)
    relative = fluxes / means
    if errors is None:
        fractional_error = np.repeat(_point_to_point_noise(relative)[:, None], frame_count, axis=1)
    else:
        fractional_error = errors / means

    active = list(range(candidate_count))
    dropped: list[int] = []
    if frame_count >= minimum_frames_for_vetting:
        limit = 1.0 + excess_scatter_sigma * math.sqrt(2.0 / (frame_count - 1))
        while len(active) > minimum_comparison_stars:
            chi_square = _reduced_chi_squares(relative[active], fractional_error[active])
            scale = max(float(np.median(chi_square)), _CONSISTENT_CHI_SQUARE)
            worst = int(np.argmax(chi_square))
            if chi_square[worst] <= limit * scale:
                break
            dropped.append(active.pop(worst))

    if len(active) > maximum:
        typical_error = np.nanmedian(fractional_error[active], axis=1)
        best = np.argsort(typical_error, kind="stable")[:maximum]
        active = sorted(active[position] for position in best.tolist())
    return active, dropped


def median_signal_to_noise(table: FluxTable) -> np.ndarray:
    """Give each star's median flux over its own error.

    Parameters
    ----------
    table : `FluxTable`
        The session's fluxes and errors.

    Returns
    -------
    signal_to_noise : `numpy.ndarray`
        One value per star, no unit. It is computed over the frames where the
        star has a flux and an error. `numpy.nan` for a star with no errors.
    """
    with np.errstate(divide="ignore", invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(np.where(table.errors > 0, table.fluxes / table.errors, np.nan), axis=1)


def comparison_pool_rules(table: FluxTable) -> list[tuple[str, np.ndarray]]:
    """Apply the candidate rules one after the other and keep each result.

    Each mask holds the stars that passed this rule and every rule before it,
    so the counts shrink down the list. The rules are, in order: a flux (and
    an error) in at least `MINIMUM_MEASURED_FRAME_SHARE` of the usable
    frames; a median signal-to-noise of at least
    `MINIMUM_COMPARISON_SIGNAL_TO_NOISE`; never saturated; not crowded (left
    out of the list when no star recorded crowding). A star whose light curve
    has no errors has no signal-to-noise, so it must instead have a positive
    flux in every usable frame, which is the rule before these two.

    Parameters
    ----------
    table : `FluxTable`
        The session's fluxes.

    Returns
    -------
    rules : `list` [`tuple` [`str`, `numpy.ndarray`]]
        A short name for each rule and the `bool` mask (one per star) of the
        stars that passed it and the rules before it.
    """
    minimum_share = resolve(MINIMUM_MEASURED_FRAME_SHARE).value
    minimum_signal_to_noise = resolve(MINIMUM_COMPARISON_SIGNAL_TO_NOISE).value
    finite = np.isfinite(table.fluxes)
    has_errors = np.any(np.isfinite(table.errors), axis=1)
    frame_count = max(table.fluxes.shape[1], 1)
    measured = np.where(
        has_errors,
        finite.sum(axis=1) >= minimum_share * frame_count,
        np.all(finite & (table.fluxes > 0), axis=1),
    )
    bright = measured & (~has_errors | (median_signal_to_noise(table) >= minimum_signal_to_noise))
    unsaturated = bright & ~table.ever_saturated & table.saturation_trusted
    rules = [("measured", measured), ("signal_to_noise", bright), ("unsaturated", unsaturated)]
    if table.crowded is not None:
        rules.append(("uncrowded", unsaturated & ~table.crowded))
    return rules


def select_comparison_set(table: FluxTable, maximum: int | None = None) -> ComparisonSelection:
    """Choose the comparison stars for a session.

    A candidate has a flux and an error in at least
    `MINIMUM_MEASURED_FRAME_SHARE` of the usable frames, a median
    signal-to-noise of at least `MINIMUM_COMPARISON_SIGNAL_TO_NOISE`, is never
    saturated, is not crowded by a bright neighbor, and is not listed as
    variable by a catalog (see `comparison_pool_rules`). The candidates are
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
    minimum_comparison_stars = resolve(MINIMUM_COMPARISON_STARS).value
    brightest_skipped_fraction = resolve(BRIGHTEST_SKIPPED_FRACTION).value
    faintest_band_fraction = resolve(FAINTEST_BAND_FRACTION).value
    minimum_vetting_candidates = resolve(MINIMUM_VETTING_CANDIDATES).value
    maximum_vetting_candidates = resolve(MAXIMUM_VETTING_CANDIDATES).value
    if maximum is None:
        maximum = resolve(DEFAULT_MAXIMUM_COMPARISON_STARS).value
    maximum = max(maximum, minimum_comparison_stars)
    eligible = comparison_pool_rules(table)[-1][1]
    listed_rows = np.flatnonzero(eligible & table.listed_variable)
    pool = np.flatnonzero(eligible & ~table.listed_variable)
    if pool.size == 0:
        return ComparisonSelection((), (), tuple(listed_rows.tolist()), 0, False)

    brightness = np.nanmedian(table.fluxes[pool], axis=1)
    ranked = pool[np.argsort(-brightness, kind="stable")]
    first = math.floor(brightest_skipped_fraction * ranked.size)
    last = max(
        math.ceil(faintest_band_fraction * ranked.size), min(ranked.size, first + minimum_vetting_candidates)
    )
    band = ranked[first:last][:maximum_vetting_candidates]

    # The errors set the weights when every measured flux has one.
    uses_errors = bool(np.all(np.isfinite(table.errors[band]) | ~np.isfinite(table.fluxes[band])))
    kept, dropped = _vet_candidates(table.fluxes[band], table.errors[band] if uses_errors else None, maximum)
    return ComparisonSelection(
        member_rows=tuple(band[kept].tolist()),
        vetted_out_rows=tuple(band[dropped].tolist()),
        listed_variable_rows=tuple(listed_rows.tolist()),
        candidate_count=int(band.size),
        uses_errors=uses_errors,
    )


def describe_comparison_pool(table: FluxTable, selection: ComparisonSelection) -> str:
    """Count the stars left after each rule of `select_comparison_set`.

    The rules run in the order the sentence lists them, so the counts shrink
    from left to right.

    Parameters
    ----------
    table : `FluxTable`
        The session's fluxes.
    selection : `ComparisonSelection`
        What `select_comparison_set` chose from the table.

    Returns
    -------
    counts : `str`
        The counts joined by semicolons, without a final period.
    """
    rules = dict(comparison_pool_rules(table))
    share = resolve(MINIMUM_MEASURED_FRAME_SHARE).value
    minimum_signal_to_noise = resolve(MINIMUM_COMPARISON_SIGNAL_TO_NOISE).value
    unlisted = rules.get("uncrowded", rules["unsaturated"]) & ~table.listed_variable
    parts = [
        f"{int(rules['measured'].sum())} of {len(table.star_ids)} stars have a flux in at least "
        f"{share:.0%} of the {len(table.timestamps)} usable frames",
        f"{int(rules['signal_to_noise'].sum())} of those have a median signal-to-noise of at least "
        f"{minimum_signal_to_noise:g}",
        f"{int(rules['unsaturated'].sum())} are never saturated",
    ]
    if "uncrowded" in rules:
        parts.append(f"{int(rules['uncrowded'].sum())} are not crowded")
    parts.append(f"{int(unlisted.sum())} are not listed as variable")
    if selection.candidate_count:
        parts.append(
            f"{len(selection.member_rows)} of {selection.candidate_count} candidates passed the "
            "constancy check"
        )
    return "; ".join(parts)


def describe_missing_comparison_stars(table: FluxTable, selection: ComparisonSelection) -> str:
    """Say in one sentence why a session has too few comparison stars.

    The sentence counts the stars left after each rule of
    `select_comparison_set`, in the order the rules are applied. When no
    star has a flux in enough usable frames, it also names the frame with
    the most missing stars, because a frame lost to cloud is the usual cause.

    Parameters
    ----------
    table : `FluxTable`
        The session's fluxes.
    selection : `ComparisonSelection`
        What `select_comparison_set` chose from the table.

    Returns
    -------
    reason : `str`
        The sentence, without a final period.
    """
    reason = describe_comparison_pool(table, selection)
    if table.timestamps and not comparison_pool_rules(table)[0][1].any():
        missing_per_frame = np.sum(~np.isfinite(table.fluxes), axis=0)
        worst = int(np.argmax(missing_per_frame))
        frames_missing_many = int(np.sum(missing_per_frame > 0.1 * len(table.star_ids)))
        reason += (
            f". {frames_missing_many} frame(s) lack a flux for more than a tenth of the stars; the worst, "
            f"{table.timestamps[worst].isoformat()}, lacks {int(missing_per_frame[worst])} (cloud or a lost "
            "field is the usual cause)"
        )
    return reason


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
        Shape ``(members, frames)``, ADU per second. At least two members.
        `numpy.nan` marks a frame a member was not measured in; it gets no
        weight there.
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
        A frame in which no member was measured has `numpy.nan` levels.
    """
    means = np.nanmean(fluxes[:, frame_mask], axis=1, keepdims=True)
    relative = fluxes / means
    if errors is None:
        fractional_error = np.repeat(
            _point_to_point_noise(relative[:, frame_mask])[:, None], fluxes.shape[1], axis=1
        )
    else:
        fractional_error = errors / means
    scale = float(means.mean())
    weights, ratios, _ = _weights_and_ratios(relative, fractional_error)
    total_weight = weights.sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        level = scale * (weights * ratios).sum(axis=0) / total_weight
        level_error = scale / np.sqrt(total_weight)
    member_levels, member_variance = _leave_one_out(relative, fractional_error)
    has_errors = errors is not None
    return EnsembleSignal(
        level=level,
        level_error=level_error if has_errors else None,
        member_levels=scale * member_levels,
        member_level_errors=scale * np.sqrt(member_variance) if has_errors else None,
    )


@dataclass(frozen=True, eq=False)
class OutsideFrameLevels:
    """The ensemble level of frames that did not take part in choosing the set.

    Attributes
    ----------
    level : `numpy.ndarray`
        One level per frame, ADU per second. `numpy.nan` where fewer than two
        comparison stars were measured in the frame.
    level_error : `numpy.ndarray` or `None`
        The 1-sigma error of each level, or `None` when the members have no
        errors.
    member_levels : `numpy.ndarray`
        Shape ``(members, frames)``. The level built from the other members
        that were measured in the frame. `numpy.nan` where none was.
    member_level_errors : `numpy.ndarray` or `None`
        The errors of `member_levels`, or `None`.
    member_counts : `numpy.ndarray`
        How many members were measured in each frame.
    """

    level: np.ndarray
    level_error: np.ndarray | None
    member_levels: np.ndarray
    member_level_errors: np.ndarray | None
    member_counts: np.ndarray


def levels_for_frames_outside_the_fit(
    fit_fluxes: np.ndarray,
    fit_errors: np.ndarray | None,
    frame_mask: np.ndarray,
    other_fluxes: np.ndarray,
    other_errors: np.ndarray | None,
) -> OutsideFrameLevels:
    """Give frames outside the fit a level from the members measured in them.

    Each member's mean and noise come from the fit frames in `frame_mask`,
    exactly as `build_ensemble_signal` finds them. A frame outside the fit
    is then divided by the weighted mean of the members that have a positive
    flux in it. A frame with fewer than two such members gets no level, and
    the caller reports it.

    Parameters
    ----------
    fit_fluxes : `numpy.ndarray`
        Shape ``(members, fit frames)``, ADU per second. `numpy.nan` marks a
        frame a member was not measured in.
    fit_errors : `numpy.ndarray` or `None`
        Same shape, or `None` when the members have no errors.
    frame_mask : `numpy.ndarray`
        One `bool` per fit frame, `True` for the frames the means use.
    other_fluxes : `numpy.ndarray`
        Shape ``(members, other frames)``. `numpy.nan` where a member has no
        positive flux in the frame.
    other_errors : `numpy.ndarray` or `None`
        Same shape, or `None`.

    Returns
    -------
    levels : `OutsideFrameLevels`
        The level of every other frame.
    """
    frame_count = other_fluxes.shape[1]
    means = np.nanmean(fit_fluxes[:, frame_mask], axis=1, keepdims=True)
    uses_errors = fit_errors is not None and other_errors is not None
    if uses_errors:
        fractional_error = other_errors / means
    else:
        noise = _point_to_point_noise(fit_fluxes[:, frame_mask] / means)
        fractional_error = np.repeat(noise[:, None], frame_count, axis=1)
    relative = other_fluxes / means
    measured = np.isfinite(relative) & np.isfinite(fractional_error)
    weights = np.where(measured, 1.0 / np.where(measured, fractional_error, 1.0) ** 2, 0.0)
    ratios = np.where(measured, relative, 0.0)
    counts = measured.sum(axis=0)
    total_weight = weights.sum(axis=0)
    scale = float(means.mean())
    with np.errstate(divide="ignore", invalid="ignore"):
        level = scale * (weights * ratios).sum(axis=0) / total_weight
        others_weight = total_weight - weights
        member_levels = scale * ((weights * ratios).sum(axis=0) - weights * ratios) / others_weight
        member_variance = 1.0 / others_weight
        level_error = scale / np.sqrt(total_weight)
    level = np.where(counts >= 2, level, np.nan)
    member_levels = np.where(measured & (counts >= 2), member_levels, np.nan)
    return OutsideFrameLevels(
        level=level,
        level_error=np.where(counts >= 2, level_error, np.nan) if uses_errors else None,
        member_levels=member_levels,
        member_level_errors=(
            np.where(measured & (counts >= 2), scale * np.sqrt(member_variance), np.nan)
            if uses_errors
            else None
        ),
        member_counts=counts,
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
