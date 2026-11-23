"""Tell instrumental changes in a light curve from real variability.

A candidate variable is a star whose light curve changes more than its noise
explains. Some changes come from the instrument and the way the frames were
taken, not from the star. Two kinds were found on M 52 (2026-10-03, 42 frames
of 120 s): 12 of the 19 flagged stars were not listed as variable in any
catalog. This module holds the two checks that keep such stars from being
flagged. Neither check removes a light curve. A star that fails a check keeps
its light curve and its numbers, and is marked "not judged" with the reason
(`PhotometryResult.not_judged_reason`), so no warning is lost.

* **Steps.** The mount moves the field by several pixels between groups of
  frames, for example when it dithers or re-centers. On M 52 the shift of the
  field jumped by 2.0 to 3.8 pixels at four frames, while the frame-to-frame
  jitter was 0.3 pixel. Four stars changed brightness by 4 to 8 percent
  exactly across those jumps, and not one of the other 386 stars did. A real
  star does not know when the mount moved. A star whose light curve is mostly
  a set of flat levels, one per group of frames, with jumps at the shifts, is
  treated as an instrument effect (see `step_artifact_reason`). A step
  that moves the whole field is taken out first: a star is judged against
  the field's own jump (see `field_step_summary`), not against zero.
* **Coverage.** A star that saturates in many frames loses those points
  (see `VariabilityAnalyzer._remove_saturated_points`), and the frames that
  stay still have a peak that is nearly clipped, so their flux is too low in
  a way that depends on seeing. A star whose aperture and sky ring come
  close to the frame edge in some frames is measured on a cut-off ring. Both
  light curves are less reliable than the rest (see `coverage_reason` and
  `edge_reason`).

M 52 is one field and the register entry says so (PHO-20). The shifts of the
frames are not stored with the light curves, so the step check runs only while
the analyzer still holds them, which is during the photometry run.
"""

import math
from collections.abc import Hashable, Mapping, Sequence

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    SKY_ANNULUS_OUTER_RADIUS_PX,
)

__all__ = [
    "MINIMUM_UNSATURATED_FRACTION",
    "STEP_FIELD_MINIMUM_SPREADS",
    "STEP_MINIMUM_BLOCK_VARIANCE_FRACTION",
    "STEP_MINIMUM_SHIFT_PX",
    "STEP_MINIMUM_SIGNIFICANCE",
    "STEP_SHIFT_JITTER_SIGMAS",
    "coverage_reason",
    "edge_reason",
    "field_step_summary",
    "registration_step_threshold_px",
    "step_artifact_reason",
]

# The smallest share of a star's measured frames in which it must be
# unsaturated for the star to be judged. A frame is flagged saturated when
# the aperture holds saturated pixels (`is_saturation_significant`). A star
# near saturation clips its peak in some frames and not in others, and the
# frames that are not flagged still lose light that the seeing decides. One
# frame in ten is the most we accept before the star's light curve is judged
# by a different star's rules. On M 52 the three flagged stars with a
# saturated share over 10 percent lost 17, 41 and 52 percent of their frames;
# every cataloged variable had none. Partly measured (PHO-20): one field.
MINIMUM_UNSATURATED_FRACTION = TunedParameter(
    name="photometry.minimum_unsaturated_fraction",
    default=0.9,
    unit="fraction",
    description="Smallest share of a star's frames that must be unsaturated for the star to be judged.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-20",
)

# A step in the field is a change in the frame-to-frame shift that is this
# many robust standard deviations above the typical change. On M 52 the
# typical change was 0.29 pixel with a robust spread of 0.20 pixel, so six
# spreads give 1.5 pixels, below the four real steps (2.0 to 3.8 pixels) and
# above every ordinary frame (the 90th percentile of the change was 1.1
# pixels). A statistics choice, with the floor below.
STEP_SHIFT_JITTER_SIGMAS = TunedParameter(
    name="photometry.step_shift_jitter_sigmas",
    default=6.0,
    unit="sigma",
    description="How many robust spreads above the typical frame-to-frame shift change counts as a step.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-20",
)

# The smallest shift change, in pixels, that counts as a step however steady
# the mount was. One pixel is about a third of a star's width on the reference
# setup, the least the field must move for a star to land on different
# pixels in a way that matters.
STEP_MINIMUM_SHIFT_PX = TunedParameter(
    name="photometry.step_minimum_shift_px",
    default=1.0,
    unit="px",
    description="Smallest change in the frame shift that counts as a step.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-20",
)

# How many times its expected error a star's jump across a step must be. The
# error is the scatter of the star's frames inside its groups, so a star with
# an unsteady light curve needs a bigger jump. On M 52 the four stars jumped
# by 8.5 to 15.9 errors. The largest such jump of any cataloged variable was
# 3.8. Five sits between them, and is far enough above chance that none of
# the 390 stars of a field passes by noise. A statistics choice.
STEP_MINIMUM_SIGNIFICANCE = TunedParameter(
    name="photometry.step_minimum_significance",
    default=5.0,
    unit="sigma",
    description="How many times its error a star's jump across a registration step must be.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-20",
)

# The share of a star's variance that must lie between the groups of frames
# (the groups the steps cut the night into) rather than inside them. A star
# with no extra variance has about 1 divided by the number of groups of it
# (0.2 for five groups). The median over the stars of M 52 was 0.14 and the
# 90th percentile 0.34. The four stars were at 0.81 to 0.91; the cataloged
# variables, whose changes are slow, were at 0.20 to 0.82 but had no jump that
# stood out from the changes elsewhere (see the last test in
# `step_artifact_reason`). One half means most of the variance.
STEP_MINIMUM_BLOCK_VARIANCE_FRACTION = TunedParameter(
    name="photometry.step_minimum_block_variance_fraction",
    default=0.5,
    unit="fraction",
    description="Share of a star's variance that must lie between groups of frames for a step verdict.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-20",
)

# How many robust spreads a star's jump across a step must differ from the
# field's own jump there. A step in the field moves many stars at once (a
# flat-field or position effect), and a star that only follows the field is
# not a variable star. The field's jump at a step is the median of the
# jumps of all stars, and its spread is 1.4826 times their median absolute
# deviation. On M 31 (2026-11-04/05, 114 frames, very precise stars) the
# field-wide jump alone put 399 of about 497 stars above five errors, so the
# star's own error cannot be the only yardstick. Five spreads is the same
# distance used for the field-noise cutoff of the variability rule, and is
# far enough above chance that no star of a field of a few hundred passes by
# noise. A statistics choice. Partly measured (PHO-20): checked against the
# M 52 stars; the M 31 shifts are not stored.
STEP_FIELD_MINIMUM_SPREADS = TunedParameter(
    name="photometry.step_field_minimum_spreads",
    default=5.0,
    unit="sigma",
    description="Robust spreads a star's step jump must differ from the field's jump.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-20",
)

# The fewest stars that give the field's jump at a step. A median and a
# spread of fewer than ten values move with one star, so a smaller field is
# judged on the star's own error alone (the earlier rule). A method choice.
_STEP_FIELD_MINIMUM_STARS = 10

# How many frames either side of a step make the jump, and the fewest frames a
# group needs to give its scatter. Three frames give a median that one bad
# frame cannot move. These are method choices.
_FRAMES_EACH_SIDE_OF_STEP = 3
_MINIMUM_FRAMES_PER_GROUP = 3

# The standard error of a sample median of normally distributed values is
# sqrt(pi / 2) = 1.2533 times that of their mean.
_MEDIAN_STANDARD_ERROR_FACTOR = 1.2533

# 1.4826 times the median absolute deviation estimates the standard
# deviation of normally distributed values.
_MAD_TO_SIGMA = 1.4826


def registration_step_threshold_px(frame_shifts_px: Sequence[tuple[float, float]]) -> float:
    """Find how large a change of the frame shift counts as a step.

    The threshold comes from the session itself. It is the median change of
    the shift from one frame to the next plus `STEP_SHIFT_JITTER_SIGMAS`
    robust spreads of that change, never below `STEP_MINIMUM_SHIFT_PX`. A
    steady mount gives a small threshold and a restless one a larger one.

    Parameters
    ----------
    frame_shifts_px : `Sequence` [`tuple` [`float`, `float`]]
        The shift of each frame against the first one, ``(x, y)`` in pixels,
        in time order.

    Returns
    -------
    threshold_px : `float`
        The smallest change of the shift between consecutive frames that is
        a step, in pixels.
    """
    minimum_px = resolve(STEP_MINIMUM_SHIFT_PX).value
    sigmas = resolve(STEP_SHIFT_JITTER_SIGMAS).value
    if len(frame_shifts_px) < 3:
        return float(minimum_px)
    shifts = np.asarray(frame_shifts_px, dtype=float)
    changes = np.hypot(np.diff(shifts[:, 0]), np.diff(shifts[:, 1]))
    median = float(np.median(changes))
    spread = _MAD_TO_SIGMA * float(np.median(np.abs(changes - median)))
    return float(max(minimum_px, median + sigmas * spread))


def _jump_size(values: np.ndarray, index: int) -> float:
    """Measure the jump of the median of three points across one index.

    Parameters
    ----------
    values : `numpy.ndarray`
        The light curve, in time order.
    index : `int`
        The first point after the jump.

    Returns
    -------
    jump : `float`
        The median of the points after minus the median of those before, in
        the units of the values. 0 when either side has fewer than two points.
    """
    before = values[max(0, index - _FRAMES_EACH_SIDE_OF_STEP) : index]
    after = values[index : index + _FRAMES_EACH_SIDE_OF_STEP]
    if before.size < 2 or after.size < 2:
        return 0.0
    return float(np.median(after) - np.median(before))


def _jump_significance(values: np.ndarray, index: int, scatter: float) -> float:
    """Measure the jump of a light curve across one index.

    Parameters
    ----------
    values : `numpy.ndarray`
        The light curve, in time order.
    index : `int`
        The first point after the jump.
    scatter : `float`
        The star's scatter inside its groups of frames, in the units of the
        values.

    Returns
    -------
    significance : `float`
        The jump of the median, in expected errors. 0 when either side has
        fewer than two points.
    """
    before = values[max(0, index - _FRAMES_EACH_SIDE_OF_STEP) : index]
    after = values[index : index + _FRAMES_EACH_SIDE_OF_STEP]
    if before.size < 2 or after.size < 2:
        return 0.0
    error = scatter * _MEDIAN_STANDARD_ERROR_FACTOR * math.sqrt(1.0 / before.size + 1.0 / after.size)
    return abs(float(np.median(after)) - float(np.median(before))) / error


def _relative_step_jumps(
    values: Sequence[float],
    timestamps: Sequence[Hashable],
    frame_shifts_px: Sequence[tuple[float, float]],
    step_threshold_px: float,
) -> dict[Hashable, float]:
    """Measure one star's relative jump at each registration step.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The star's light curve, in time order.
    timestamps : `Sequence` [`Hashable`]
        The time of each point, in the same order.
    frame_shifts_px : `Sequence` [`tuple` [`float`, `float`]]
        The shift of the field at each point, in pixels.
    step_threshold_px : `float`
        The change of the shift that counts as a step.

    Returns
    -------
    jumps : `dict` [`Hashable`, `float`]
        For each step with at least two points on each side, the time of the
        first point after the step and the change of the median of three
        points, as a fraction of the star's median level.
    """
    levels = np.asarray(values, dtype=float)
    shifts = np.asarray(frame_shifts_px, dtype=float)
    if (
        levels.size != len(shifts)
        or levels.size != len(timestamps)
        or levels.size < 2 * _MINIMUM_FRAMES_PER_GROUP
    ):
        return {}
    keep = np.isfinite(levels)
    levels, shifts = levels[keep], shifts[keep]
    kept_times = [timestamp for timestamp, kept in zip(timestamps, keep, strict=True) if kept]
    median_level = float(np.median(levels)) if levels.size else 0.0
    if median_level <= 0.0:
        return {}
    levels = levels / median_level
    changes = np.r_[0.0, np.hypot(np.diff(shifts[:, 0]), np.diff(shifts[:, 1]))]
    jumps: dict[Hashable, float] = {}
    for index in np.where(changes >= step_threshold_px)[0]:
        before = levels[max(0, index - _FRAMES_EACH_SIDE_OF_STEP) : index]
        after = levels[index : index + _FRAMES_EACH_SIDE_OF_STEP]
        if before.size >= 2 and after.size >= 2:
            jumps[kept_times[int(index)]] = float(np.median(after) - np.median(before))
    return jumps


def field_step_summary(
    light_curves: Sequence[tuple[Sequence[float], Sequence[Hashable], Sequence[tuple[float, float]]]],
    step_threshold_px: float,
) -> dict[Hashable, tuple[float, float]]:
    """Find how the whole field jumps at each registration step.

    A step can move most stars together (a flat-field or position effect).
    That common jump is a property of the field, not of any one star. Each
    star's relative jump at a step is measured (see `step_artifact_reason`
    for the jump) and the field's jump is the median over the stars. The
    spread is 1.4826 times the median absolute deviation of the star jumps
    around that median.

    Parameters
    ----------
    light_curves : `Sequence` [`tuple`]
        One ``(values, timestamps, frame_shifts_px)`` per star, each in time
        order and of equal length.
    step_threshold_px : `float`
        The change of the shift that counts as a step.

    Returns
    -------
    steps : `dict` [`Hashable`, `tuple` [`float`, `float`]]
        For each step time, the field's median jump and its robust spread, as
        fractions of a star's level. A step measured on fewer than
        `_STEP_FIELD_MINIMUM_STARS` stars is left out.
    """
    jumps_by_time: dict[Hashable, list[float]] = {}
    for values, timestamps, shifts in light_curves:
        for timestamp, jump in _relative_step_jumps(values, timestamps, shifts, step_threshold_px).items():
            jumps_by_time.setdefault(timestamp, []).append(jump)
    summary: dict[Hashable, tuple[float, float]] = {}
    for timestamp, jumps in jumps_by_time.items():
        if len(jumps) < _STEP_FIELD_MINIMUM_STARS:
            continue
        median = float(np.median(jumps))
        spread = _MAD_TO_SIGMA * float(np.median(np.abs(np.asarray(jumps) - median)))
        summary[timestamp] = (median, spread)
    return summary


def step_artifact_reason(
    values: Sequence[float],
    frame_shifts_px: Sequence[tuple[float, float]],
    step_threshold_px: float,
    timestamps: Sequence[Hashable] | None = None,
    field_steps: Mapping[Hashable, tuple[float, float]] | None = None,
) -> str | None:
    """Say whether a light curve changes with the registration steps.

    The tests, all of which must hold:

    1. At least one step lies inside the star's light curve (the shift
       changes by at least ``step_threshold_px`` between two points).
    2. The steps cut the points into groups. The groups of three or more
       points give the star's scatter inside a group (1.4826 times the median
       absolute deviation from each group's median). At least half of the
       star's variance (`STEP_MINIMUM_BLOCK_VARIANCE_FRACTION`) lies between
       the groups.
    3. The largest jump across a step is at least `STEP_MINIMUM_SIGNIFICANCE`
       times its error. The jump is the change of the median of three points
       before and after the step.
    4. That jump is larger than any jump of the same kind where the field
       did not step. A slow change, or an event that begins between steps,
       has jumps elsewhere that are as large, and is left to the other
       tests.

    When ``field_steps`` is given (see `field_step_summary`), the star is
    judged against the field. The field's own jump is taken out of the
    star's light curve at each step before tests 2 to 4, and the star's
    remaining jump must also be at least `STEP_FIELD_MINIMUM_SPREADS` robust
    spreads of the field's jumps. A star that only follows the field is then
    not put down to the steps.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The star's light curve (normalized or detrended), in time order.
    frame_shifts_px : `Sequence` [`tuple` [`float`, `float`]]
        The shift of the field at each of the star's points, ``(x, y)`` in
        pixels, in the same order.
    step_threshold_px : `float`
        The change of the shift that counts as a step (see
        `registration_step_threshold_px`).
    timestamps : `Sequence` [`Hashable`], optional
        The time of each point, in the same order. Needed with
        ``field_steps``.
    field_steps : `Mapping` [`Hashable`, `tuple` [`float`, `float`]], optional
        The field's median jump and robust spread at each step time. When
        `None` or empty, the star is judged on its own error alone.

    Returns
    -------
    reason : `str` or `None`
        A sentence saying why the star's variability is put down to the
        steps, with the numbers. `None` when the star passes.
    """
    levels = np.asarray(values, dtype=float)
    shifts = np.asarray(frame_shifts_px, dtype=float)
    if levels.size != len(shifts) or levels.size < 2 * _MINIMUM_FRAMES_PER_GROUP:
        return None
    keep = np.isfinite(levels)
    levels, shifts = levels[keep], shifts[keep]
    kept_times = (
        [timestamp for timestamp, kept in zip(timestamps, keep, strict=True) if kept]
        if timestamps is not None and len(timestamps) == keep.size
        else None
    )
    median_level = float(np.median(levels))
    if median_level <= 0.0:
        return None
    levels = levels / median_level
    changes = np.r_[0.0, np.hypot(np.diff(shifts[:, 0]), np.diff(shifts[:, 1]))]
    is_step = changes >= step_threshold_px
    if not is_step.any():
        return None
    # Take the field's own jump out of the light curve at each step, and
    # note the spread the star's remaining jump must beat there.
    required_jump = np.zeros(levels.size)
    if field_steps and kept_times is not None:
        correction = 0.0
        for index in range(levels.size):
            field_step = field_steps.get(kept_times[index]) if is_step[index] else None
            if field_step is not None:
                correction += field_step[0]
                required_jump[index] = resolve(STEP_FIELD_MINIMUM_SPREADS).value * max(field_step[1], 1.0e-6)
            levels[index] -= correction
    group = np.cumsum(is_step)
    residuals = [
        levels[group == label] - np.median(levels[group == label])
        for label in np.unique(group)
        if int((group == label).sum()) >= _MINIMUM_FRAMES_PER_GROUP
    ]
    if not residuals:
        return None
    scatter = max(_MAD_TO_SIGMA * float(np.median(np.abs(np.concatenate(residuals)))), 1.0e-6)
    total = float(np.sum((levels - levels.mean()) ** 2))
    if total <= 0.0:
        return None
    between = sum(
        float((group == label).sum()) * (float(levels[group == label].mean()) - float(levels.mean())) ** 2
        for label in np.unique(group)
    )
    between_fraction = between / total
    # A step counts only when what is left of the star's jump (the field's
    # own jump is already out) is also large against the field's spread.
    step_jumps = [
        _jump_significance(levels, int(index), scatter)
        if abs(_jump_size(levels, int(index))) >= required_jump[index]
        else 0.0
        for index in np.where(is_step)[0]
    ]
    other_jumps = [
        _jump_significance(levels, index, scatter)
        for index in range(_FRAMES_EACH_SIDE_OF_STEP, levels.size - _FRAMES_EACH_SIDE_OF_STEP)
        if not is_step[index - _FRAMES_EACH_SIDE_OF_STEP + 1 : index + _FRAMES_EACH_SIDE_OF_STEP].any()
    ]
    largest_step_jump = max(step_jumps)
    largest_other_jump = max(other_jumps, default=0.0)
    if (
        between_fraction >= resolve(STEP_MINIMUM_BLOCK_VARIANCE_FRACTION).value
        and largest_step_jump >= resolve(STEP_MINIMUM_SIGNIFICANCE).value
        and largest_step_jump > largest_other_jump
    ):
        return (
            f"its brightness changes with the field's registration steps: {between_fraction:.0%} of its "
            f"variance lies between groups of frames, and it jumps {largest_step_jump:.1f} errors across a "
            f"shift of the field against {largest_other_jump:.1f} anywhere else"
        )
    return None


def coverage_reason(unsaturated_fraction: float | None) -> str | None:
    """Say whether a star was saturated in too many of its frames to be judged.

    Parameters
    ----------
    unsaturated_fraction : `float` or `None`
        The share of the star's measured frames in which it was not saturated
        (the saturated points that were removed count as saturated). `None`
        when it is not known.

    Returns
    -------
    reason : `str` or `None`
        A sentence with the numbers, or `None` when the star passes.
    """
    minimum = resolve(MINIMUM_UNSATURATED_FRACTION).value
    if unsaturated_fraction is None or unsaturated_fraction >= minimum:
        return None
    return (
        f"it was saturated in {1.0 - unsaturated_fraction:.0%} of its frames "
        f"(at most {1.0 - minimum:.0%} is accepted), so its remaining points have clipped peaks"
    )


def edge_reason(
    x_px: float | None,
    y_px: float | None,
    frame_shape: tuple[int, int] | None,
    largest_shift_px: float,
) -> str | None:
    """Say whether a star lies too near the frame edge to be judged.

    The star's aperture and sky ring must fit inside the frame in every
    frame. The ring reaches the outer radius of the sky ring plus one pixel
    from the star (the cutout radius of `measure_aperture_photometry`), and
    the field moves by up to ``largest_shift_px`` over the session. A star
    closer to an edge than the sum has frames in which it cannot be
    measured, or is measured on a cut-off ring.

    Parameters
    ----------
    x_px, y_px : `float` or `None`
        The star's position in the reference frame, in pixels.
    frame_shape : `tuple` [`int`, `int`] or `None`
        The frame's ``(height, width)``, in pixels.
    largest_shift_px : `float`
        The largest distance the field moved from the reference frame, in
        pixels.

    Returns
    -------
    reason : `str` or `None`
        A sentence with the numbers, or `None` when the star passes or its
        position is not known.
    """
    if x_px is None or y_px is None or frame_shape is None:
        return None
    required_px = math.ceil(resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value) + 1 + largest_shift_px
    height, width = frame_shape
    distance_px = min(x_px, y_px, width - 1 - x_px, height - 1 - y_px)
    if distance_px >= required_px:
        return None
    return (
        f"it lies {distance_px:.0f} px from the frame edge, and its sky ring needs {required_px:.0f} px "
        "(the ring plus the largest shift of the field)"
    )
