"""Purpose: Check a "take N more" forecast against the stack that followed it.

Description: A recommendation such as "20 more 90 s frames cut the noise by
about 29%" is a forecast. When the user takes more frames and stacks them, this
module rebuilds the forecast from the run before and compares it with what the
new stack measured (`QUALITY_METRICS_PLAN.md` Section 6.5, use case UC3). It
stores nothing: the history of stack runs is already in the measurement store,
so any forecast can be rebuilt on each read.

For each pair of neighbouring library stack runs of a target, three numbers are
found. All are changes in noise as a fraction, so a fall of 29% is -0.29.

* Planned. The forecast made before the session. The new frames are assumed to
  look like the target's past frames (`noise_growth.noise_ratio_after_adding`),
  in the number and exposure the user actually took.
* Given the frames taken. The forecast again, using the real new frames' own
  sky rates and the weights the stacker really recorded for each frame
  (`stacking.frame_weight`). No weighting mode is guessed or needed. A moonlit
  frame lowers it fairly.
* Measured. The ratio of the two stacks' normalized empty-aperture noise
  (`stacking.empty_aperture_noise_normalized`). Only the normalized value is
  compared, because the stack's own units differ from stack to stack.

Each number carries a tolerance of one standard error. The error of an
empty-aperture noise is 1.17 / sqrt(n) of the noise for n apertures
(`noise_growth.aperture_noise_relative_error`). The error of the ratio of two
unpaired stacks adds the two relative errors in quadrature. The two stacks
usually reuse the same apertures, which makes their errors partly shared, so
the
unpaired error is larger than the true one and the check is conservative. A
caller that has a paired error passes it, and the check then says it is paired.
Two numbers "agree" when they differ by no more than two tolerances, one-sided,
a false alarm of about 2.3 percent (design estimate, register row WAY-D70).

The outcome is one of four. The gain arrived as forecast. It fell short for
a known reason, which means the forecast given the frames taken already fell
short of the planned one and the measured value agrees with it. It fell short
for no known reason. Or the check could not be made, and the reason says why
(weights that were not recorded, a frame with no sky rate, a stack
with no normalized noise). Nothing is guessed in that last case.

The module also turns the run history into the steps of the noise-floor fit
(`noise_steps_from_runs`) and reads the stop signal from it
(`target_stop_signal`), so the target report and tonight's ranking use the same
history. A floor is reported only from two or more steps (`noise_growth`).

The planned forecast is made before the frames exist, so it must assume a
weighting mode. When that mode weights by star width, the check uses the
frame's effective star width in arcseconds
(`frame.star_width_effective_arcsec`)
as the stand-in for the weighted width, because the two move together. That
stand-in is a design estimate (register row WAY-D70), is used only for the
planned forecast, and is tested only on made-up stacks. The mode is the one the
earlier run recorded, or else the configured stack weight that the caller
passes (`forecast_check`).
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from itertools import pairwise

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from astrometricslib import MeasurementReference, MeasurementStatus, QualityMeasurement, SubjectLevel
from wayfindinglib.analytics.result_quality.efficiency import FrameObservation
from wayfindinglib.analytics.result_quality.noise_growth import (
    NoiseStep,
    StackFrame,
    StopSignal,
    WeightingMode,
    aperture_noise_relative_error,
    fit_noise_floor,
    forecast_noise_with_weights,
    forecast_stack_noise,
    identify_weighting_mode,
    noise_ratio_after_adding,
    predict_weighting_mode,
    stop_signal,
)
from wayfindinglib.models.planning.target_report import ForecastCheck, ForecastedChange, ForecastOutcome

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

FORECAST_CHECK_ENTRY = "WAY-70"
"""Register entry behind the forecast check."""

FORECAST_CHECK_ROW = "WAY-D70"
"""Register row behind the agreement width and the star-width stand-in."""

AGREEMENT_TOLERANCES = 2.0
"""Two changes in noise agree when they differ by no more than this many
tolerances. A tolerance is one standard error, and the test is one-sided (only
a shortfall counts), so two of them is a false alarm of about 2.3 percent. It
is the same width as the model-agreement test of
the loss account (`MODEL_AGREEMENT_SIGMAS`). Design estimate, register row
WAY-D70."""

NOISE_METRIC = "stacking.empty_aperture_noise_stack_unit"
NORMALIZED_NOISE_METRIC = "stacking.empty_aperture_noise_normalized"
EXPECTED_NOISE_METRIC = "stacking.expected_empty_aperture_noise_stack_unit"
FRAME_WEIGHT_METRIC = "stacking.frame_weight"
REFERENCE_FLUX_METRIC = "stacking.noise_reference_flux_stack_unit"

PINNED_STACK_METRICS = {
    NOISE_METRIC: 3,
    NORMALIZED_NOISE_METRIC: 3,
    EXPECTED_NOISE_METRIC: 2,
    FRAME_WEIGHT_METRIC: 1,
    REFERENCE_FLUX_METRIC: 3,
}
"""The metrics read, and the definition version each is pinned to (decision
S13). A row of another version is not used. A test fails when the registry
moves past one of these versions."""


class StackRunRecord(BaseModel):
    """What one library stack run stored that the forecast check needs.

    Parameters
    ----------
    run_id : `str`
        The run that made the stack.
    stack_subject_id : `str`
        The stack's id in the measurement store.
    measured_at : `datetime`
        When the run's noise was measured. Runs are ordered by it.
    normalized_noise : `float` or `None`
        The empty-aperture noise as a fraction of the reference stars'
        brightness (no unit). ``None`` when it was not measured.
    normalized_noise_error : `float` or `None`
        One standard error of `normalized_noise`.
    noise : `float` or `None`
        The empty-aperture noise in the stack's own units.
    expected_noise : `float` or `None`
        The noise the stack's frames predict, in the same units as `noise`.
    reference_flux : `float` or `None`
        The brightness of the fixed reference stars in the stack's own units
        (`stacking.noise_reference_flux_stack_unit`). Dividing the noise by it
        puts stacks with different pixel scales on one scale. When the row is
        missing but the noise and the normalized noise are both known, it is
        their ratio.
    aperture_count : `int` or `None`
        How many empty apertures the noise came from.
    noise_row_id, normalized_row_id : `int` or `None`
        The store rows of the two noise values, for evidence.
    member_frame_ids : `list` [`str`]
        The frames the stack holds, in id order.
    recorded_weights : `list` [`float` or `None`]
        The weight recorded for each member, in the same order. ``None`` for a
        weight that was not measured (Siril does not report it in its noise
        mode).
    problems : `dict` [`str`, `str`]
        Why a part is missing, by part name (``normalized_noise``,
        ``expected_noise``, ``members``).
    """

    model_config = _MODEL_CONFIG

    run_id: str
    stack_subject_id: str
    measured_at: datetime
    normalized_noise: float | None = None
    normalized_noise_error: float | None = None
    noise: float | None = None
    expected_noise: float | None = None
    reference_flux: float | None = None
    aperture_count: int | None = None
    noise_row_id: int | None = None
    normalized_row_id: int | None = None
    member_frame_ids: list[str] = Field(default_factory=list)
    recorded_weights: list[float | None] = Field(default_factory=list)
    problems: dict[str, str] = Field(default_factory=dict)

    @property
    def normalized_relative_error(self) -> float | None:
        """Give the relative error of the normalized noise.

        Returns
        -------
        relative_error : `float` or `None`
            The stored error over the value; else the empty-aperture error from
            the aperture count; ``None`` when neither is known.
        """
        if self.normalized_noise and self.normalized_noise_error is not None:
            return self.normalized_noise_error / self.normalized_noise
        if self.aperture_count is not None and self.aperture_count >= 2:
            return aperture_noise_relative_error(self.aperture_count)
        return None


def _newest_by_subject(rows: Iterable[QualityMeasurement]) -> dict[tuple[str, str], list[QualityMeasurement]]:
    """Keep the newest row of each metric for each subject and channel.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        Rows of any metrics and subjects.

    Returns
    -------
    grouped : `dict`
        The rows by ``(subject id, metric)``. A color stack keeps one row for
        each channel.
    """
    newest: dict[tuple[str, str, str | None], QualityMeasurement] = {}
    for row in rows:
        key = (row.subject.subject_id, row.metric, row.subject.channel)
        current = newest.get(key)
        stamp = (row.measured_at, row.row_id or 0)
        if current is None or stamp >= (current.measured_at, current.row_id or 0):
            newest[key] = row
    grouped: dict[tuple[str, str], list[QualityMeasurement]] = defaultdict(list)
    for (subject_id, metric, _channel), row in newest.items():
        grouped[subject_id, metric].append(row)
    return grouped


def _usable_value(rows: Sequence[QualityMeasurement], metric: str) -> tuple[float | None, str | None]:
    """Give the value of a pinned metric, or the reason it cannot be used.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        The newest row of the metric for each channel of one subject.
    metric : `str`
        The metric name.

    Returns
    -------
    value, reason : `tuple`
        The median over channels, or ``None`` and why it is missing.
    """
    pinned = PINNED_STACK_METRICS[metric]
    values = []
    reason = None
    for row in rows:
        if row.definition_version != pinned:
            reason = (
                f"{metric} is definition version {row.definition_version}; this code needs version "
                f"{pinned}. Remeasure the stack."
            )
        elif row.status == MeasurementStatus.NOT_MEASURED or row.value is None:
            reason = row.reason or f"{metric} was not measured."
        else:
            values.append(row.value)
    if values:
        return float(np.median(values)), None
    return None, reason or f"{metric} has no row."


def stack_runs_from_measurements(measurements: Iterable[QualityMeasurement]) -> list[StackRunRecord]:
    """Group a target's stored rows into its library stack runs.

    Only rows of the library stack are used (a row of a subset stack has a
    ``variant``). The noise rows are read at the stack level and the weight
    rows at the frame level; both carry the run id, which joins them. A run
    that made more than one stack shares its member list between them.

    Parameters
    ----------
    measurements : `Iterable` [`QualityMeasurement`]
        All rows of the target, with every run kept (``latest_only=False``).

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        One record per stack in each run, oldest first. A run that stored no
        noise row at all is left out.
    """
    stack_rows: dict[tuple[str, str], list[QualityMeasurement]] = defaultdict(list)
    weight_rows: dict[str, list[QualityMeasurement]] = defaultdict(list)
    for row in measurements:
        if row.metric not in PINNED_STACK_METRICS:
            continue
        if row.subject.level == SubjectLevel.STACK and row.subject.variant is None:
            stack_rows[row.run_id, row.subject.subject_id].append(row)
        elif row.subject.level == SubjectLevel.FRAME and row.metric == FRAME_WEIGHT_METRIC:
            weight_rows[row.run_id].append(row)
    members_by_run: dict[str, tuple[list[str], list[float | None]]] = {}
    for run_id, rows in weight_rows.items():
        grouped = _newest_by_subject(rows)
        frame_ids = sorted(subject_id for subject_id, _metric in grouped)
        weights: list[float | None] = []
        for frame_id in frame_ids:
            value, _reason = _usable_value(grouped[frame_id, FRAME_WEIGHT_METRIC], FRAME_WEIGHT_METRIC)
            weights.append(value)
        members_by_run[run_id] = (frame_ids, weights)
    records = []
    for (run_id, stack_id), rows in stack_rows.items():
        grouped = _newest_by_subject(rows)

        problems: dict[str, str] = {}
        normalized, reason = _usable_value(
            grouped.get((stack_id, NORMALIZED_NOISE_METRIC), []), NORMALIZED_NOISE_METRIC
        )
        if normalized is None:
            problems["normalized_noise"] = reason or "The normalized noise is missing."
        noise, noise_reason = _usable_value(grouped.get((stack_id, NOISE_METRIC), []), NOISE_METRIC)
        if noise is None:
            problems["noise"] = noise_reason or "The noise is missing."
        expected, expected_reason = _usable_value(
            grouped.get((stack_id, EXPECTED_NOISE_METRIC), []), EXPECTED_NOISE_METRIC
        )
        if expected is None:
            problems["expected_noise"] = expected_reason or "The expected noise is missing."
        flux, _flux_reason = _usable_value(
            grouped.get((stack_id, REFERENCE_FLUX_METRIC), []), REFERENCE_FLUX_METRIC
        )
        if (flux is None or flux <= 0.0) and noise and normalized:
            flux = noise / normalized
        normalized_rows = grouped.get((stack_id, NORMALIZED_NOISE_METRIC), [])
        noise_rows = grouped.get((stack_id, NOISE_METRIC), [])
        first_normalized = normalized_rows[0] if normalized_rows else None
        first_noise = noise_rows[0] if noise_rows else None
        error = (
            first_normalized.uncertainty
            if first_normalized is not None and first_normalized.status == MeasurementStatus.MEASURED
            else None
        )
        apertures = None
        for candidate in (first_normalized, first_noise):
            if candidate is not None and candidate.sample_count:
                apertures = candidate.sample_count
                break
        member_ids, recorded = members_by_run.get(run_id, ([], []))
        if not member_ids:
            problems["members"] = "The run stored no frame weights, so its member frames are not known."
        newest_time = max(row.measured_at for row in rows)
        records.append(
            StackRunRecord(
                run_id=run_id,
                stack_subject_id=stack_id,
                measured_at=newest_time,
                normalized_noise=normalized,
                normalized_noise_error=error,
                noise=noise,
                expected_noise=expected,
                reference_flux=flux if flux and flux > 0.0 else None,
                aperture_count=apertures,
                noise_row_id=first_noise.row_id if first_noise is not None else None,
                normalized_row_id=first_normalized.row_id if first_normalized is not None else None,
                member_frame_ids=member_ids,
                recorded_weights=recorded,
                problems=problems,
            )
        )
    return sorted(records, key=lambda record: (record.measured_at, record.run_id))


def stack_frames_from_observations(
    frame_ids: Sequence[str], observations: Mapping[str, FrameObservation]
) -> tuple[list[StackFrame], list[str]]:
    """Build the forward model's frames for the members of a stack.

    Parameters
    ----------
    frame_ids : `Sequence` [`str`]
        The member frames.
    observations : `Mapping` [`str`, `FrameObservation`]
        The measurements of each frame, by frame id.

    Returns
    -------
    frames, problems : `tuple`
        The usable frames, in the order of `frame_ids`, and one sentence for
        each frame that could not be used. A frame with no exposure, sky rate
        or read noise is a problem; a missing star width is not (only the
        width weights need it).
    """
    frames: list[StackFrame] = []
    problems: list[str] = []
    for frame_id in frame_ids:
        observation = observations.get(frame_id)
        if observation is None:
            problems.append(f"{frame_id}: no frame measurements were found.")
            continue
        exposure = observation.effective_exposure_seconds
        missing = [
            name
            for name, value in (
                ("exposure", exposure),
                ("sky rate", observation.sky_rate_e_per_s_per_px),
                ("read noise", observation.read_noise_e),
            )
            if value is None
        ]
        if missing:
            reasons = "; ".join(observation.missing_reasons.values())
            problems.append(f"{frame_id}: no {', '.join(missing)}." + (f" {reasons}" if reasons else ""))
            continue
        frames.append(
            StackFrame(
                frame_id=frame_id,
                exposure_seconds=float(exposure or 0.0),
                sky_rate_e_per_s_per_px=float(observation.sky_rate_e_per_s_per_px or 0.0),
                read_noise_e=float(observation.read_noise_e or 0.0),
                star_width=observation.star_width_effective_arcsec,
            )
        )
    return frames, problems


def _change(fraction: float | None, tolerance: float | None) -> ForecastedChange:
    """Wrap a change in noise and its tolerance.

    Parameters
    ----------
    fraction : `float` or `None`
        The change as a fraction (-0.29 is a fall of 29%).
    tolerance : `float` or `None`
        One standard error of it.

    Returns
    -------
    change : `ForecastedChange`
        The model value.
    """
    return ForecastedChange(noise_change_fraction=fraction, tolerance=tolerance)


def _not_checkable(
    current: StackRunRecord,
    reason: str,
    weighting_mode: str,
    evidence: list[MeasurementReference],
    *,
    planned: float | None = None,
    given: float | None = None,
    measured: float | None = None,
    tolerance: float | None = None,
    paired: bool = False,
) -> ForecastCheck:
    """Build a check that could not be made, with whatever was found.

    Parameters
    ----------
    current : `StackRunRecord`
        The later run.
    reason : `str`
        Why it could not be checked.
    weighting_mode : `str`
        The mode the later run used, or ``"unknown"``.
    evidence : `list` [`MeasurementReference`]
        The rows that were used.
    planned, given, measured, tolerance : `float` or `None`, optional
        Any number that was found before the check stopped.
    paired : `bool`, optional
        Whether the tolerance is paired.

    Returns
    -------
    check : `ForecastCheck`
        A check with outcome ``not_checkable``.
    """
    return ForecastCheck(
        stack_run_id=current.run_id,
        forecast_when_planned=_change(planned, tolerance),
        forecast_given_frames_taken=_change(given, tolerance),
        measured=_change(measured, tolerance),
        weighting_mode=weighting_mode,
        paired=paired,
        outcome=ForecastOutcome.NOT_CHECKABLE,
        reason=reason,
        evidence=evidence,
    )


def _evidence(*records: StackRunRecord) -> list[MeasurementReference]:
    """List the store rows of the normalized noise of some runs.

    Parameters
    ----------
    *records : `StackRunRecord`
        The runs.

    Returns
    -------
    evidence : `list` [`MeasurementReference`]
        One reference for each run that has a row id.
    """
    return [
        MeasurementReference(row_id=record.normalized_row_id)
        for record in records
        if record.normalized_row_id is not None
    ]


def _frame_difference_text(old: Sequence[StackFrame], new: Sequence[StackFrame]) -> str:
    """Say in words how the new frames differ from the old ones.

    Parameters
    ----------
    old, new : `Sequence` [`StackFrame`]
        The earlier and the newly added frames.

    Returns
    -------
    text : `str`
        For example ``"The new frames had 2.1 times the sky rate."`` Empty when
        no difference stands out.
    """
    parts = []
    old_sky = float(np.median([frame.sky_rate_e_per_s_per_px for frame in old]))
    new_sky = float(np.median([frame.sky_rate_e_per_s_per_px for frame in new]))
    if old_sky > 0.0 and abs(new_sky / old_sky - 1.0) >= 0.2:
        parts.append(f"The new frames had {new_sky / old_sky:.1f} times the sky rate of the earlier ones.")
    old_widths = [frame.star_width for frame in old if frame.star_width]
    new_widths = [frame.star_width for frame in new if frame.star_width]
    if old_widths and new_widths:
        ratio = float(np.median(new_widths)) / float(np.median(old_widths))
        if abs(ratio - 1.0) >= 0.1:
            parts.append(f"Their stars were {ratio:.2f} times as wide.")
    return " ".join(parts)


def _check_pair(
    previous: StackRunRecord,
    current: StackRunRecord,
    observations: Mapping[str, FrameObservation],
    paired_relative_error: float | None,
    configured_stack_weight: str | None,
) -> ForecastCheck:
    """Check the forecast for one pair of neighbouring stack runs.

    Parameters
    ----------
    previous, current : `StackRunRecord`
        The earlier and the later run.
    observations : `Mapping` [`str`, `FrameObservation`]
        The measurements of every frame, by frame id.
    paired_relative_error : `float` or `None`
        The relative error of the noise ratio when both stacks were measured in
        the same apertures; ``None`` for the unpaired error.
    configured_stack_weight : `str` or `None`
        The ``Processing.Siril.stack_weight`` setting, used only for the
        planned forecast when the earlier run's mode is not known.

    Returns
    -------
    check : `ForecastCheck`
        The three numbers, the tolerance and the outcome.
    """
    evidence = _evidence(previous, current)
    mode_name = "unknown"
    for record in (previous, current):
        if record.normalized_noise is None:
            why = record.problems.get("normalized_noise", "the normalized noise is missing.")
            return _not_checkable(current, f"Run {record.run_id}: {why}", mode_name, evidence)
        if "members" in record.problems:
            return _not_checkable(
                current, f"Run {record.run_id}: {record.problems['members']}", mode_name, evidence
            )
    previous_relative = previous.normalized_relative_error
    current_relative = current.normalized_relative_error
    if paired_relative_error is None and (previous_relative is None or current_relative is None):
        return _not_checkable(
            current,
            "The error of the measured noise is not known (no uncertainty or aperture count).",
            mode_name,
            evidence,
        )
    ratio_measured = float(current.normalized_noise or 0.0) / float(previous.normalized_noise or 1.0)
    paired = paired_relative_error is not None
    relative = (
        paired_relative_error
        if paired_relative_error is not None
        else math.hypot(float(previous_relative or 0.0), float(current_relative or 0.0))
    )
    tolerance = ratio_measured * relative
    measured = ratio_measured - 1.0

    previous_frames, previous_problems = stack_frames_from_observations(
        previous.member_frame_ids, observations
    )
    current_frames, current_problems = stack_frames_from_observations(current.member_frame_ids, observations)
    problems = [*previous_problems, *current_problems]
    if problems:
        return _not_checkable(
            current,
            "Frame measurements are missing: "
            + " ".join(problems[:3])
            + (" ..." if len(problems) > 3 else ""),
            mode_name,
            evidence,
            measured=measured,
            tolerance=tolerance,
            paired=paired,
        )
    previous_ids = set(previous.member_frame_ids)
    new_frames = [frame for frame in current_frames if frame.frame_id not in previous_ids]
    if not new_frames:
        return _not_checkable(
            current,
            "The later stack holds no frame that the earlier one did not, so no gain was forecast.",
            mode_name,
            evidence,
            measured=measured,
            tolerance=tolerance,
            paired=paired,
        )

    # The frames' real weights are the recorded ones. The mode is named for the
    # reader when the weights match one, but nothing depends on it.
    if any(weight is None for weight in [*previous.recorded_weights, *current.recorded_weights]):
        return _not_checkable(
            current,
            "The stacker's weights were not recorded for every frame (Siril does not report them in its "
            "noise mode), so the forecast given the frames taken cannot be made. Nothing is guessed.",
            mode_name,
            evidence,
            measured=measured,
            tolerance=tolerance,
            paired=paired,
        )
    current_weights = [float(weight or 0.0) for weight in current.recorded_weights]
    previous_weights = [float(weight or 0.0) for weight in previous.recorded_weights]
    current_mode = identify_weighting_mode(current_frames, current.recorded_weights)
    mode_name = current_mode.mode.value if current_mode.mode != WeightingMode.UNKNOWN else "recorded_weights"
    previous_mode = identify_weighting_mode(previous_frames, previous.recorded_weights)
    planned_mode = (
        previous_mode.mode
        if previous_mode.mode != WeightingMode.UNKNOWN
        else predict_weighting_mode(
            configured_stack_weight, [frame.exposure_seconds for frame in [*previous_frames, *new_frames]]
        )
    )

    before = forecast_stack_noise(previous_frames, planned_mode)
    planned_exposure = float(np.median([frame.exposure_seconds for frame in new_frames]))
    planned_after = noise_ratio_after_adding(
        previous_frames, previous_frames, len(new_frames), planned_mode, planned_exposure
    )
    given_after = forecast_noise_with_weights(current_frames, current_weights)
    given_before = forecast_noise_with_weights(previous_frames, previous_weights)
    if (
        before.noise is None
        or planned_after is None
        or planned_after.noise is None
        or given_after.noise is None
        or given_before.noise is None
    ):
        reason = given_after.reason or before.reason or "The forward model could not be evaluated."
        return _not_checkable(
            current,
            reason,
            mode_name,
            evidence,
            measured=measured,
            tolerance=tolerance,
            paired=paired,
        )
    planned = planned_after.noise / before.noise - 1.0
    given = given_after.noise / given_before.noise - 1.0
    width = AGREEMENT_TOLERANCES * tolerance

    if measured - planned <= width:
        return ForecastCheck(
            stack_run_id=current.run_id,
            forecast_when_planned=_change(planned, tolerance),
            forecast_given_frames_taken=_change(given, tolerance),
            measured=_change(measured, tolerance),
            weighting_mode=mode_name,
            paired=paired,
            outcome=ForecastOutcome.AS_FORECAST,
            reason="The noise fell as much as planned or more.",
            evidence=evidence,
        )
    difference = _frame_difference_text(previous_frames, new_frames)
    explained = (given - planned) > width and abs(measured - given) <= width
    if explained:
        outcome = ForecastOutcome.SHORT_KNOWN_REASON
        reason = (
            f"The forecast given the frames taken was {100 * given:.0f}% against "
            f"{100 * planned:.0f}% planned, and the measured "
            f"{100 * measured:.0f}% agrees with it. {difference}"
            + (
                " The stacker gave the frames equal weight."
                if current_mode.mode == WeightingMode.EQUAL
                else ""
            )
        ).strip()
    else:
        outcome = ForecastOutcome.SHORT_UNKNOWN_REASON
        reason = (
            f"The noise changed by {100 * measured:.0f}% against {100 * planned:.0f}% planned, and the "
            f"frames taken explain a change of only {100 * given:.0f}%. The cause is not known. "
            "A noise floor is named only from two or more steps of data."
        )
    return ForecastCheck(
        stack_run_id=current.run_id,
        forecast_when_planned=_change(planned, tolerance),
        forecast_given_frames_taken=_change(given, tolerance),
        measured=_change(measured, tolerance),
        weighting_mode=mode_name,
        paired=paired,
        outcome=outcome,
        reason=reason,
        evidence=evidence,
    )


def forecast_check(
    runs: Sequence[StackRunRecord],
    observations: Iterable[FrameObservation],
    *,
    configured_stack_weight: str | None = None,
    paired_relative_error_by_run: Mapping[str, float] | None = None,
) -> list[ForecastCheck]:
    """Check each stack run's gain against the forecast from the run before.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs (`stack_runs_from_measurements`). They
        are put in time order here.
    observations : `Iterable` [`FrameObservation`]
        The measurements of the frames in the runs
        (`frame_observations_from_measurements`).
    configured_stack_weight : `str` or `None`
        The configured ``Processing.Siril.stack_weight`` (``None`` when none
        is set). Real callers pass the configured value; the planned forecast
        uses it when the earlier run's recorded weights match no known mode.
        The forecast given the frames taken never uses it.
    paired_relative_error_by_run : `Mapping` [`str`, `float`], optional
        For a run whose noise was compared with the run before in the same
        apertures, the relative error of the ratio. The check then uses it and
        says it is paired.

    Returns
    -------
    checks : `list` [`ForecastCheck`]
        One check for each run after the first, oldest first. Empty when there
        are fewer than two runs, because there is no forecast to check.
    """
    by_id = {observation.frame_id: observation for observation in observations}
    ordered = sorted(runs, key=lambda record: (record.measured_at, record.run_id))
    checks = []
    for previous, current in pairwise(ordered):
        paired = (paired_relative_error_by_run or {}).get(current.run_id)
        checks.append(_check_pair(previous, current, by_id, paired, configured_stack_weight))
    return checks


def noise_steps_from_runs(runs: Sequence[StackRunRecord]) -> list[NoiseStep]:
    """Turn the run history into the steps of the noise-floor fit.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs.

    Returns
    -------
    steps : `list` [`NoiseStep`]
        One step for each run that has a measured noise, an expected noise and
        at least 2 apertures. The errors are unpaired. Noise and expected noise
        are divided by the stack's reference flux, so stacks with different
        pixel scales can be fitted together; runs without a flux are left out
        when any run has one.
    """
    candidates = []
    for record in sorted(runs, key=lambda item: (item.measured_at, item.run_id)):
        if (
            record.noise is None
            or record.noise <= 0.0
            or record.expected_noise is None
            or record.expected_noise <= 0.0
            or record.aperture_count is None
            or record.aperture_count < 2
        ):
            continue
        candidates.append(record)
    normalizable = [record for record in candidates if record.reference_flux]
    # Each stack has its own pixel scale, so stacks are fitted together only on
    # the normalized scale. Without a flux for any run, the raw values are used
    # and the fit says they are not normalized.
    chosen = normalizable if normalizable else candidates
    steps = []
    for record in chosen:
        flux = record.reference_flux if normalizable else None
        divisor = flux if flux else 1.0
        steps.append(
            NoiseStep(
                label=record.run_id,
                expected_variance=(record.expected_noise or 0.0) ** 2 / divisor**2,
                measured_noise=(record.noise or 0.0) / divisor,
                apertures=record.aperture_count or 2,
                normalized=flux is not None,
            )
        )
    return steps


def target_stop_signal(runs: Sequence[StackRunRecord]) -> StopSignal:
    """Say whether more frames of a target are still worth taking.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs.

    Returns
    -------
    signal : `StopSignal`
        ``stopped`` is true only when a floor was fitted from two or more
        steps and doubling the data would cut the noise by under 10%. It is
        ``None`` (no signal) when the history is too short.
    """
    steps = noise_steps_from_runs(runs)
    fit = fit_noise_floor(steps)
    if not steps:
        return StopSignal(reason="No stack run has both a measured and an expected noise.")
    return stop_signal(fit, steps[-1].expected_variance)


__all__ = [
    "AGREEMENT_TOLERANCES",
    "FORECAST_CHECK_ENTRY",
    "FORECAST_CHECK_ROW",
    "PINNED_STACK_METRICS",
    "StackRunRecord",
    "forecast_check",
    "noise_steps_from_runs",
    "stack_frames_from_observations",
    "stack_runs_from_measurements",
    "target_stop_signal",
]
