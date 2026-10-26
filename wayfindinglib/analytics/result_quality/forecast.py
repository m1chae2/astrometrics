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

A stack of several exposure groups is checked with the group model
(`noise_growth.forecast_group_noise`, `plan_group_stack`). Each group's frame
noise ``s_g`` is not stored; it is rebuilt from the group's summed
``stacking.frame_weight`` rows, ``stacking.group_frames_stacked_count`` and
``stacking.group_exposure_s``, and its gain is ``stacking.group_gain_applied``
(`stack_groups_of_run`). A group that holds folded frames (a length with
fewer than 5 frames, put into the nearest group) also needs
``stacking.group_reference_exposure_s``: Siril scales each of its frames to
the reference frame, which changes the frame's noise
(`noise_growth.StackGroup.frame_noise_factors`). Without that row the pair is
not checked. The earlier stack's noise floor is part of every
forecast: the measured empty-aperture noise against the
expected noise (version 2, which includes Siril's normalization scale), or a
fit over the history, reconciled by `noise_growth.choose_noise_floor`.

A pair is not checkable, and says why, when the two stacks differ in more than
the added frames (`_confound_reason`): the aperture set
(``stacking.aperture_set_number``), the noise definition, the calibration
(read from the calibration record, `attach_calibration_identities`: camera,
optic, flat and bias masters, the dark master of an uncooled camera, the Siril
and stacking-algorithm versions), the heaviest exposure group, or a switch
between one group and several. A run with no aperture identity or no
calibration record counts as confounded. Frames added to the later stack that
were taken before the earlier stack was made are not new (`_new_frame_reason`).
The sensor-temperature band that the plan names for the uncooled Nikon is not
stored, so it is not checked.

A target with two or more observing nights also has an earlier-nights subset
stack (all nights but the last). `stack_runs_from_measurements` can read it as
a run marked ``is_earlier_nights_subset``, and `subset_pairs` pairs it with
the library run it was cut from. The pair is checked with the same rules,
except that every frame the library stack added must start on a later observing
night than the subset's newest frame (`_subset_new_frame_reason`). The check
carries ``basis="earlier_nights_subset"``. `forecast_gate_state` reads the
checks in order and says whether "take N more" is closed, exploratory (only a
subset check agreed) or confirmed (two library stacks agreed).

The module also turns the run history into the steps of the noise-floor fit
(`noise_steps_from_runs`) and reads the stop signal from it
(`target_stop_signal`), so the target report and tonight's ranking use the same
history. The stop signal uses the floor `noise_floor_for_runs` chooses, which
can come from the newest stack alone.

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
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from astrometricslib import (
    EARLIER_NIGHTS_VARIANT,
    CaptureSetting,
    MeasurementReference,
    MeasurementStatus,
    QualityMeasurement,
    SubjectLevel,
    aperture_identity_from_number,
    fold_exposure_lengths,
    observing_night_id,
    resolve_camera_profile,
)
from wayfindinglib.analytics.result_quality.efficiency import FrameObservation
from wayfindinglib.analytics.result_quality.noise_growth import (
    MINIMUM_GAIN_FRACTION,
    NoiseForecast,
    NoiseStep,
    StackFrame,
    StackGroup,
    StackNoiseFloor,
    StopSignal,
    WeightingMode,
    aperture_noise_relative_error,
    choose_noise_floor,
    fit_noise_floor,
    forecast_group_noise,
    forecast_noise_with_weights,
    forecast_stack_noise,
    frame_noise_from_group_weight,
    group_frame_rate_variances,
    identify_weighting_mode,
    measure_stack_noise_floor,
    new_frames_like,
    noise_ratio_after_adding,
    noise_ratio_with_floor,
    plan_group_stack,
    predict_weighting_mode,
    stop_signal,
)
from wayfindinglib.models.planning.target_report import (
    ForecastBasis,
    ForecastCheck,
    ForecastedChange,
    ForecastGate,
    ForecastOutcome,
)

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
APERTURE_SET_METRIC = "stacking.aperture_set_number"
GROUP_EXPOSURE_METRIC = "stacking.group_exposure_s"
GROUP_FRAMES_STACKED_METRIC = "stacking.group_frames_stacked_count"
GROUP_GAIN_METRIC = "stacking.group_gain_applied"
GROUP_REFERENCE_EXPOSURE_METRIC = "stacking.group_reference_exposure_s"

GROUP_METRICS = (
    GROUP_EXPOSURE_METRIC,
    GROUP_FRAMES_STACKED_METRIC,
    GROUP_GAIN_METRIC,
    GROUP_REFERENCE_EXPOSURE_METRIC,
)
"""The exposure-group rows. They sit on the group's own stack subject (the
file in the ``groups`` folder), not on the combined stack, so they are read
apart from the noise rows."""

PINNED_STACK_METRICS = {
    NOISE_METRIC: 3,
    NORMALIZED_NOISE_METRIC: 3,
    EXPECTED_NOISE_METRIC: 2,
    FRAME_WEIGHT_METRIC: 1,
    REFERENCE_FLUX_METRIC: 3,
    APERTURE_SET_METRIC: 1,
    GROUP_EXPOSURE_METRIC: 1,
    GROUP_FRAMES_STACKED_METRIC: 1,
    GROUP_GAIN_METRIC: 1,
    GROUP_REFERENCE_EXPOSURE_METRIC: 1,
}
"""The metrics read, and the definition version each is pinned to (decision
S13). A row of another version is not used. A test fails when the registry
moves past one of these versions."""

COOLED_CAMERA_NAME_MARKERS = ("zwo", "asi")
"""Words that mark a cooled astronomy camera in its name. A new dark master
does not confound two stacks from such a camera, because its dark current is
held at a set temperature and is small. For any other camera (the uncooled
Nikon) the dark master is part of the calibration. The camera profile does not
say whether the camera is cooled, so the name decides. Scope: the ZWO ASI533MM
Pro. Register row WAY-D70."""


class RecordedGroup(BaseModel):
    """What a stack run stored about one exposure group.

    The rows sit on the group's own stack subject (the file in the
    ``groups`` folder), so the combined stack's record has to collect them.

    Parameters
    ----------
    subject_id : `str`
        The group stack's id in the measurement store.
    exposure_seconds : `float`
        The group's exposure length (``stacking.group_exposure_s``).
    frames_stacked : `int`
        Frames stacked in the group (``stacking.group_frames_stacked_count``).
    gain_applied : `float` or `None`
        The brightness gain the combine applied
        (``stacking.group_gain_applied``). ``None`` when no row has a value:
        the combine applied none, so the forecast uses 1.
    reference_exposure_seconds : `float` or `None`
        The exposure of the group's registration reference frame
        (``stacking.group_reference_exposure_s``). ``None`` when the row is
        missing or not measured. A group that holds frames of more than one
        length cannot be forecast without it.
    """

    model_config = _MODEL_CONFIG

    subject_id: str
    exposure_seconds: float
    frames_stacked: int
    gain_applied: float | None = None
    reference_exposure_seconds: float | None = None


FileIdentity = tuple[str, int | None, int | None]
"""A calibration master as the store names it: path, size in bytes and
modification time in nanoseconds. Two equal identities are the same file."""


class CalibrationSummary(BaseModel):
    """The calibration a stack run used, kept for the confound rules.

    Built from a stored calibration record
    (`QualityRecords.calibration_identities`) by
    `calibration_summary_from_record`. The record is read with duck typing
    because the record's class is not in the science library's public root.

    Parameters
    ----------
    camera, optic : `str` or `None`
        The camera and the telescope or lens.
    focal_length_mm : `float` or `None`
        The focal length.
    flat_masters, bias_masters, dark_masters : `tuple` [`FileIdentity`, ...]
        The master files, sorted.
    siril_version : `str` or `None`
        The Siril version that combined the frames.
    stacking_algorithm_version : `int` or `None`
        The stacking algorithm version.
    """

    model_config = _MODEL_CONFIG

    camera: str | None = None
    optic: str | None = None
    focal_length_mm: float | None = None
    flat_masters: tuple[FileIdentity, ...] = ()
    bias_masters: tuple[FileIdentity, ...] = ()
    dark_masters: tuple[FileIdentity, ...] = ()
    siril_version: str | None = None
    stacking_algorithm_version: int | None = None


def _file_identities(files: Iterable[Any]) -> tuple[FileIdentity, ...]:
    """Turn stored master-file identities into sorted tuples.

    Parameters
    ----------
    files : `Iterable` [`Any`]
        Objects with ``path``, ``size_bytes`` and ``modified_ns``.

    Returns
    -------
    identities : `tuple` [`FileIdentity`, ...]
        One tuple per file, sorted so two lists compare equal when they hold
        the same files.
    """
    return tuple(
        sorted(
            ((str(item.path), item.size_bytes, item.modified_ns) for item in files),
            key=lambda identity: (identity[0], identity[1] or 0, identity[2] or 0),
        )
    )


def calibration_summary_from_record(record: Any) -> CalibrationSummary:
    """Keep what the confound rules need from a stored calibration record.

    Parameters
    ----------
    record : `Any`
        A calibration record (`CalibrationIdentities`): ``camera``,
        ``optic``, ``focal_length_mm``, ``flat_masters``, ``bias_masters``,
        ``dark_masters``, ``siril_version`` and ``stacking_algorithm_version``.

    Returns
    -------
    summary : `CalibrationSummary`
        The summary.
    """
    return CalibrationSummary(
        camera=record.camera,
        optic=record.optic,
        focal_length_mm=record.focal_length_mm,
        flat_masters=_file_identities(record.flat_masters),
        bias_masters=_file_identities(record.bias_masters),
        dark_masters=_file_identities(record.dark_masters),
        siril_version=record.siril_version,
        stacking_algorithm_version=record.stacking_algorithm_version,
    )


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
    aperture_identity : `str` or `None`
        The identity of the aperture set the noise was measured in, such as
        ``"v3-156104df543d"`` (`aperture_identity_from_number` of the
        ``stacking.aperture_set_number`` row). Equal text means the same set.
        ``None`` when the row is missing (a stack made before the row
        existed) or was not measured.
    noise_definition_version : `int` or `None`
        The definition version of the stack's noise row.
    groups : `list` [`RecordedGroup`]
        The exposure groups the run recorded. Empty for a run that stored
        none (a stack made before part 2a) and for a run that made several
        stacks, because no stored row says which group belongs to which
        stack (see ``problems["groups"]``).
    calibration : `CalibrationSummary` or `None`
        The calibration the run used. ``None`` until
        `attach_calibration_identities` adds it; a run with no record counts
        as confounded.
    is_earlier_nights_subset : `bool`
        ``True`` for the run of the earlier-nights subset stack (all observing
        nights but the last, cut from the library stack), whose rows sit on
        a subject with the variant `EARLIER_NIGHTS_VARIANT`. ``False`` for a
        library stack run.
    problems : `dict` [`str`, `str`]
        Why a part is missing, by part name (``normalized_noise``,
        ``expected_noise``, ``members``, ``aperture_identity``, ``groups``).
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
    aperture_identity: str | None = None
    noise_definition_version: int | None = None
    groups: list[RecordedGroup] = Field(default_factory=list)
    calibration: CalibrationSummary | None = None
    is_earlier_nights_subset: bool = False
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


def _recorded_group(subject_id: str, rows: Sequence[QualityMeasurement]) -> RecordedGroup | None:
    """Build one exposure group's record from its rows.

    Parameters
    ----------
    subject_id : `str`
        The group stack's subject id.
    rows : `Sequence` [`QualityMeasurement`]
        The group subject's rows of the exposure-group metrics.

    Returns
    -------
    group : `RecordedGroup` or `None`
        ``None`` when the exposure or the frame count is missing.
    """
    grouped = _newest_by_subject(rows)
    exposure, _ = _usable_value(grouped.get((subject_id, GROUP_EXPOSURE_METRIC), []), GROUP_EXPOSURE_METRIC)
    stacked, _ = _usable_value(
        grouped.get((subject_id, GROUP_FRAMES_STACKED_METRIC), []), GROUP_FRAMES_STACKED_METRIC
    )
    gain, _ = _usable_value(grouped.get((subject_id, GROUP_GAIN_METRIC), []), GROUP_GAIN_METRIC)
    reference, _ = _usable_value(
        grouped.get((subject_id, GROUP_REFERENCE_EXPOSURE_METRIC), []), GROUP_REFERENCE_EXPOSURE_METRIC
    )
    if exposure is None or exposure <= 0.0 or stacked is None or stacked < 1:
        return None
    return RecordedGroup(
        subject_id=subject_id,
        exposure_seconds=float(exposure),
        frames_stacked=round(stacked),
        gain_applied=float(gain) if gain is not None and gain > 0.0 else None,
        reference_exposure_seconds=float(reference) if reference is not None and reference > 0.0 else None,
    )


def stack_runs_from_measurements(
    measurements: Iterable[QualityMeasurement], *, include_earlier_nights: bool = False
) -> list[StackRunRecord]:
    """Group a target's stored rows into its library stack runs.

    Only rows of the library stack are used, unless `include_earlier_nights`
    is set. A row of any other subset stack has a ``variant`` and is always
    left out. The noise rows are read at the stack level and the weight rows
    at the frame level; both carry the run id, which joins them. A run that
    made more than one stack shares its member list between them.

    Parameters
    ----------
    measurements : `Iterable` [`QualityMeasurement`]
        All rows of the target, with every run kept (``latest_only=False``).
    include_earlier_nights : `bool`, optional
        Also build the runs of the earlier-nights subset stack (all nights
        but the last), whose stack rows have the variant
        `EARLIER_NIGHTS_VARIANT`. Each is marked
        ``is_earlier_nights_subset``. Callers that fit a noise floor or read
        a stop signal must leave this off, because a subset is not part of
        the library's history. Defaults to `False`.

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        One record per stack in each run, oldest first. A run that stored no
        noise row at all is left out.
    """
    stack_rows: dict[tuple[str, str, bool], list[QualityMeasurement]] = defaultdict(list)
    weight_rows: dict[str, list[QualityMeasurement]] = defaultdict(list)
    group_rows: dict[str, dict[str, list[QualityMeasurement]]] = defaultdict(lambda: defaultdict(list))
    for row in measurements:
        if row.metric not in PINNED_STACK_METRICS:
            continue
        variant = row.subject.variant
        is_subset = include_earlier_nights and variant == EARLIER_NIGHTS_VARIANT
        if row.subject.level == SubjectLevel.STACK and (variant is None or is_subset):
            if row.metric in GROUP_METRICS:
                # A group's rows are kept apart: a group subject has no noise
                # rows, so it must not become a run of its own.
                group_rows[row.run_id][row.subject.subject_id].append(row)
            else:
                stack_rows[row.run_id, row.subject.subject_id, is_subset].append(row)
        elif (
            row.subject.level == SubjectLevel.FRAME
            and row.metric == FRAME_WEIGHT_METRIC
            and (variant is None or is_subset)
        ):
            if is_subset:
                # A subset's weight row sits on "<frame id>#<label>" so that it
                # does not shadow the library's row. The frame's own id is the
                # text before the last "#".
                frame_id = row.subject.subject_id.rsplit("#", 1)[0]
                subject = row.subject.model_copy(update={"subject_id": frame_id})
                row = row.model_copy(update={"subject": subject})
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
    stacks_in_run: dict[str, int] = defaultdict(int)
    for run_id, _stack_id, _is_subset in stack_rows:
        stacks_in_run[run_id] += 1
    records = []
    for (run_id, stack_id, is_subset), rows in stack_rows.items():
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
        identity_value, identity_reason = _usable_value(
            grouped.get((stack_id, APERTURE_SET_METRIC), []), APERTURE_SET_METRIC
        )
        identity = None
        if identity_value is None:
            problems["aperture_identity"] = identity_reason or "The stack has no aperture-set row."
        else:
            try:
                identity = aperture_identity_from_number(identity_value)
            except ValueError:
                problems["aperture_identity"] = (
                    f"The stored aperture-set number {identity_value!r} is invalid."
                )
        recorded_groups: list[RecordedGroup] = []
        if stacks_in_run[run_id] > 1:
            problems["groups"] = (
                "The run made more than one stack, and no stored row says which exposure group "
                "belongs to which stack."
            )
        else:
            for group_subject, group_subject_rows in sorted(group_rows.get(run_id, {}).items()):
                group = _recorded_group(group_subject, group_subject_rows)
                if group is not None:
                    recorded_groups.append(group)
        noise_version_rows = grouped.get((stack_id, NOISE_METRIC), [])
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
                aperture_identity=identity,
                noise_definition_version=(
                    noise_version_rows[0].definition_version if noise_version_rows else None
                ),
                groups=recorded_groups,
                is_earlier_nights_subset=is_subset,
                problems=problems,
            )
        )
    return sorted(records, key=lambda record: (record.measured_at, record.run_id))


def attach_calibration_identities(
    runs: Iterable[StackRunRecord], identities: Iterable[Any]
) -> list[StackRunRecord]:
    """Put each run's stored calibration record on the run.

    The calibration is not a measurement row: it is a record of master file
    identities, read with `QualityRecords.calibration_identities`. A run is
    matched on its run id and its stack's subject id. A run with no record
    keeps ``calibration`` as ``None``, and the confound rules then count it as
    confounded.

    Parameters
    ----------
    runs : `Iterable` [`StackRunRecord`]
        The runs from `stack_runs_from_measurements`.
    identities : `Iterable` [`Any`]
        The stored calibration records (`CalibrationIdentities`).

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        Copies of the runs, with the calibration added where a record exists.
    """
    by_key = {(record.run_id, record.subject_id): record for record in identities}
    attached = []
    for run in runs:
        record = by_key.get((run.run_id, run.stack_subject_id))
        attached.append(
            run
            if record is None
            else run.model_copy(update={"calibration": calibration_summary_from_record(record)})
        )
    return attached


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


class RunGroups(BaseModel):
    """The exposure groups of one stack run, rebuilt from its stored rows.

    Parameters
    ----------
    is_group_stack : `bool`
        True when the stacker split the run's frames into more than one
        exposure group (`fold_exposure_lengths`). The group fields are filled
        only then.
    groups : `list` [`StackGroup`]
        One group for each exposure length, in length order. Each holds its
        frames, its frame noise ``s_g`` (in ADU), its gain ``G_g``, and the
        recorded in-group weights.
    group_weights : `list` [`float`]
        ``W_g`` of each group: the sum of its frames' recorded weights.
    group_lengths : `list` [`float`]
        The exposure length each group was filed under
        (`fold_exposure_lengths`). A folded group's mean exposure differs
        from it.
    reason : `str` or `None`
        Why the groups could not be rebuilt (a missing row or weight).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    is_group_stack: bool
    groups: list[StackGroup] = Field(default_factory=list)
    group_weights: list[float] = Field(default_factory=list)
    group_lengths: list[float] = Field(default_factory=list)
    reason: str | None = None

    @property
    def heaviest_exposure_seconds(self) -> float | None:
        """Give the exposure length of the group with the largest weight.

        Returns
        -------
        length : `float` or `None`
            The length the heaviest group was filed under, in seconds (a
            folded group's mean exposure moves when frames are folded in, so
            the length is the stable name of the group); ``None`` with no
            groups. The heaviest group is the combine's reference: it sets the
            brightness scale the other groups are matched to.
        """
        if not self.groups:
            return None
        index = max(range(len(self.groups)), key=lambda position: self.group_weights[position])
        if len(self.group_lengths) == len(self.groups):
            return self.group_lengths[index]
        return self.groups[index].mean_exposure_seconds


def stack_groups_of_run(
    run: StackRunRecord, frames: Sequence[StackFrame], *, electrons_per_adu: float | None = None
) -> RunGroups:
    """Rebuild the exposure groups of a stack run from its stored rows.

    The part 2a rows hold, for each group, the frames stacked and the
    exposure (the group's own stack subject) and the gain applied, and, for
    each frame, its raw combine weight ``W_g w_i``. The frame noise ``s_g`` is
    not stored. It comes back from the group's weight: the weights of a
    group's frames add up to ``W_g = N_g t_g^2 / s_g^2``, so
    ``s_g = t_g sqrt(N_g / W_g)`` (`frame_noise_from_group_weight`).

    A group that holds folded frames takes its reference frame's exposure from
    ``stacking.group_reference_exposure_s``; with no such row it is refused,
    because the folded frames' scaling cannot be known. A group of one length
    needs no reference row (a group with a stored reference keeps it).

    One link is missing from the stored rows: no row says which frames belong
    to which group. Each frame is placed by its own exposure length with the
    stacker's own rule (`fold_exposure_lengths`), which is how the stacker
    placed it.

    Parameters
    ----------
    run : `StackRunRecord`
        The run, with its groups and recorded weights.
    frames : `Sequence` [`StackFrame`]
        The run's member frames, in the order of `run.member_frame_ids`.
    electrons_per_adu : `float`, optional
        The camera's gain, kept on each group so a new group can be planned.

    Returns
    -------
    groups : `RunGroups`
        ``is_group_stack`` false for a run of one exposure group (or of mixed
        lengths in one group); else the groups, or the reason there are none.
    """
    lengths = fold_exposure_lengths([frame.exposure_seconds for frame in frames])
    unique = sorted({length for length in lengths if length is not None})
    if len(unique) < 2:
        return RunGroups(is_group_stack=False)
    if len(run.recorded_weights) != len(frames) or any(weight is None for weight in run.recorded_weights):
        return RunGroups(
            is_group_stack=True,
            reason=(
                "The stacker's weights were not recorded for every frame, so the groups' weights are unknown."
            ),
        )
    weights = [float(weight or 0.0) for weight in run.recorded_weights]
    groups: list[StackGroup] = []
    group_weights: list[float] = []
    group_lengths: list[float] = []
    for length in unique:
        indices = [index for index, group_length in enumerate(lengths) if group_length == length]
        # A folded group's stored exposure is the mean of its frames, which
        # can differ from its length, so the mean is tried first.
        mean_exposure = math.fsum(frames[index].exposure_seconds for index in indices) / len(indices)
        recorded = next(
            (
                group
                for target in (mean_exposure, length)
                for group in run.groups
                if abs(group.exposure_seconds - target) <= 0.005 * target
            ),
            None,
        )
        if recorded is None:
            return RunGroups(
                is_group_stack=True,
                reason=f"Run {run.run_id} stored no row for its {length:g} s exposure group.",
            )
        if recorded.frames_stacked != len(indices):
            return RunGroups(
                is_group_stack=True,
                reason=(
                    f"Run {run.run_id} says its {length:g} s group stacked {recorded.frames_stacked} frames, "
                    f"but {len(indices)} are listed as members."
                ),
            )
        own_lengths = {fold_exposure_lengths([frames[index].exposure_seconds])[0] for index in indices}
        if len(own_lengths) > 1 and recorded.reference_exposure_seconds is None:
            return RunGroups(
                is_group_stack=True,
                reason=(
                    f"Run {run.run_id} folded frames of another length into its {length:g} s group and "
                    "stored no reference-frame exposure for it, so the folded frames' scaling is unknown."
                ),
            )
        total = math.fsum(weights[index] for index in indices)
        if total <= 0.0:
            return RunGroups(
                is_group_stack=True, reason=f"The {length:g} s group's recorded weights add up to 0."
            )
        groups.append(
            StackGroup(
                frames=tuple(frames[index] for index in indices),
                frame_noise_adu=frame_noise_from_group_weight(
                    total, recorded.frames_stacked, recorded.exposure_seconds
                ),
                gain=recorded.gain_applied or 1.0,
                electrons_per_adu=electrons_per_adu,
                in_group_weights=tuple(weights[index] for index in indices),
                reference_exposure_seconds=recorded.reference_exposure_seconds,
                noise_length_seconds=length,
            )
        )
        group_weights.append(total)
        group_lengths.append(float(length))
    return RunGroups(
        is_group_stack=True, groups=groups, group_weights=group_weights, group_lengths=group_lengths
    )


def single_stack_floor(record: StackRunRecord) -> StackNoiseFloor | None:
    """Find a run's noise floor from its own measured and expected noise.

    Both noises are in the stack's units. The expected noise is version 2 of
    ``stacking.expected_empty_aperture_noise_stack_unit``, which includes
    Siril's normalization scale (plan decision D9), so it is the random part
    of the measured noise.

    Parameters
    ----------
    record : `StackRunRecord`
        The run.

    Returns
    -------
    floor : `StackNoiseFloor` or `None`
        The floor result (found or not), or ``None`` when the run lacks a
        measured noise, an expected noise or 2 apertures.
    """
    if (
        record.noise is None
        or record.noise <= 0.0
        or record.expected_noise is None
        or record.expected_noise <= 0.0
        or record.aperture_count is None
        or record.aperture_count < 2
    ):
        return None
    return measure_stack_noise_floor(record.noise, record.expected_noise, record.aperture_count)


def noise_floor_for_runs(runs: Sequence[StackRunRecord]) -> StackNoiseFloor | None:
    """Choose the noise floor of a target's newest stack.

    The one-stack floor of the newest run (`single_stack_floor`) and the fit
    over the runs that share its aperture set (`fit_noise_floor`) are
    reconciled by `choose_noise_floor`. A fit never mixes aperture sets: the
    normalized noise of two sets is not on one scale.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs, up to and including the stack being
        extended.

    Returns
    -------
    floor : `StackNoiseFloor` or `None`
        The chosen floor, or ``None`` when neither estimate can be made.
    """
    ordered = sorted(runs, key=lambda item: (item.measured_at, item.run_id))
    if not ordered:
        return None
    newest = ordered[-1]
    same_set = [record for record in ordered if record.aperture_identity == newest.aperture_identity]
    steps = noise_steps_from_runs(same_set)
    fit = fit_noise_floor(steps) if steps else None
    expected_variance = steps[-1].expected_variance if steps and steps[-1].label == newest.run_id else None
    return choose_noise_floor(single_stack_floor(newest), fit, expected_variance)


def camera_gain_for_setting(config: Any, setting: CaptureSetting) -> float | None:
    """Look up a camera's gain, in electrons per ADU, at a capture setting.

    The camera profile holds it for each gain setting, offset and binning. The
    offset does not change the gain unless the frame is cut off at zero, which
    a sky-dominated planned frame is not, so the profile's offset fallback is
    allowed.

    Parameters
    ----------
    config : `Any`
        The application configuration (the camera profiles live in it).
    setting : `CaptureSetting`
        The capture setting of the planned frames.

    Returns
    -------
    gain : `float` or `None`
        Electrons per ADU, or ``None`` when the camera has no profile or no
        entry for the setting.
    """
    profile = resolve_camera_profile(setting.camera, config)
    if profile.is_generic_fallback:
        return None
    entry = profile.sensor_noise_for(
        setting.gain_setting, setting.offset or 0, setting.binning or 1, clipped_at_zero=False
    )
    if entry is None or entry.gain_e_per_adu is None:
        return None
    value = float(entry.gain_e_per_adu.value)
    return value if value > 0.0 else None


def _camera_is_cooled(camera: str | None) -> bool:
    """Say whether a camera name marks a cooled astronomy camera.

    Parameters
    ----------
    camera : `str` or `None`
        The camera name.

    Returns
    -------
    cooled : `bool`
        True when the name holds one of `COOLED_CAMERA_NAME_MARKERS`.
    """
    name = (camera or "").casefold()
    return any(marker in name for marker in COOLED_CAMERA_NAME_MARKERS)


def _confound_reason(previous: StackRunRecord, current: StackRunRecord) -> str | None:
    """Say whether two stacks cannot be compared, from the runs' records.

    These are the rules that need only the stored rows of the two runs. The
    rules that need the frames are `_new_frame_reason` and the heaviest-group
    rule in `_group_forecast`.

    Parameters
    ----------
    previous, current : `StackRunRecord`
        The earlier and the later run.

    Returns
    -------
    reason : `str` or `None`
        A short plain sentence naming the first rule that holds, or ``None``
        when none does.
    """
    for record in (previous, current):
        if record.aperture_identity is None:
            return (
                f"Run {record.run_id} has no aperture-set identity, so its noise cannot be tied to the same "
                "apertures as the other run's."
            )
    if previous.aperture_identity != current.aperture_identity:
        return (
            f"The two stacks' noise was measured in different aperture sets ({previous.aperture_identity} "
            f"and {current.aperture_identity}), so the values cannot be compared."
        )
    if previous.noise_definition_version != current.noise_definition_version:
        return (
            f"The noise was measured with definition version {previous.noise_definition_version} for run "
            f"{previous.run_id} and {current.noise_definition_version} for run {current.run_id}."
        )
    for record in (previous, current):
        if record.calibration is None:
            return (
                f"Run {record.run_id} has no calibration record, so a change of flats, biases or software "
                "cannot be ruled out."
            )
    before = cast("CalibrationSummary", previous.calibration)
    after = cast("CalibrationSummary", current.calibration)
    if before.camera != after.camera:
        return f"The camera changed from {before.camera} to {after.camera}."
    if before.optic != after.optic or before.focal_length_mm != after.focal_length_mm:
        return "The telescope or lens changed between the two stacks."
    if before.flat_masters != after.flat_masters:
        return "The flat master changed between the two stacks."
    if before.bias_masters != after.bias_masters:
        return "The bias master changed between the two stacks."
    if before.dark_masters != after.dark_masters and not _camera_is_cooled(after.camera):
        return "The dark master changed between the two stacks."
    for name, first, second in (
        ("Siril version", before.siril_version, after.siril_version),
        ("stacking algorithm version", before.stacking_algorithm_version, after.stacking_algorithm_version),
    ):
        if first is None or second is None:
            return f"The {name} was not recorded for both stacks."
        if first != second:
            return f"The {name} changed from {first} to {second}."
    return None


def _new_frame_reason(
    previous: StackRunRecord, new_frames: Sequence[StackFrame], observations: Mapping[str, FrameObservation]
) -> str | None:
    """Say whether the frames added to the later stack are really new.

    A frame the earlier stack left out although it was already taken (set
    aside and restored, or not yet synced) is not new data of the kind the
    forecast assumes. The earlier stack's made time is the time its noise was
    measured, which is a little after the stack was made; a frame taken in
    between is counted as old, which errs on the side of not checking.

    Parameters
    ----------
    previous : `StackRunRecord`
        The earlier run.
    new_frames : `Sequence` [`StackFrame`]
        The frames in the later stack and not in the earlier one.
    observations : `Mapping` [`str`, `FrameObservation`]
        The frames' measurements, for their start times.

    Returns
    -------
    reason : `str` or `None`
        A sentence when a frame has no start time or was taken before the
        earlier stack was made; ``None`` otherwise.
    """
    made_at = (
        previous.measured_at if previous.measured_at.tzinfo else previous.measured_at.replace(tzinfo=UTC)
    )
    made_epoch = made_at.timestamp()
    unknown = 0
    old = 0
    for frame in new_frames:
        observation = observations.get(frame.frame_id)
        start = None if observation is None else observation.start_epoch_seconds
        if start is None:
            unknown += 1
        elif start < made_epoch:
            old += 1
    if old:
        return (
            f"{old} of the {len(new_frames)} added frames were taken before the earlier stack was made, so "
            "they are not new frames."
        )
    if unknown:
        return (
            f"{unknown} of the {len(new_frames)} added frames have no start time, so it cannot be shown that "
            "they are new."
        )
    return None


def _subset_new_frame_reason(
    subset: StackRunRecord,
    library: StackRunRecord,
    new_frames: Sequence[StackFrame],
    observations: Mapping[str, FrameObservation],
) -> str | None:
    """Say whether the frames the library stack added are from a later night.

    The pair of an earlier-nights subset and its library stack differs by the
    last observing night. Every frame is older than both stacks, so the rule
    of `_new_frame_reason` cannot apply. Here the subset must hold only
    frames of earlier nights: each added frame must start on a later
    observing night (`observing_night_id`) than the subset's newest frame.

    Parameters
    ----------
    subset, library : `StackRunRecord`
        The earlier-nights subset run and the library run it pairs with.
    new_frames : `Sequence` [`StackFrame`]
        The frames in the library stack and not in the subset.
    observations : `Mapping` [`str`, `FrameObservation`]
        The frames' measurements, for their start times.

    Returns
    -------
    reason : `str` or `None`
        A sentence when the subset holds a frame with no frame record or start
        time, when an added frame has no start time, or when an added frame
        is not from a later observing night; ``None`` otherwise.
    """
    subset_ids = set(subset.member_frame_ids)
    library_ids = set(library.member_frame_ids)
    if not subset_ids <= library_ids:
        return (
            f"The subset holds {len(subset_ids - library_ids)} frame(s) that the library stack does "
            "not, so it was not cut from that stack."
        )
    subset_starts = []
    for frame_id in subset.member_frame_ids:
        observation = observations.get(frame_id)
        start = None if observation is None else observation.start_epoch_seconds
        if start is None:
            return (
                "A frame of the earlier-nights stack has no start time, so its newest night cannot be found."
            )
        subset_starts.append(start)
    newest_night = observing_night_id(max(subset_starts))
    unknown = 0
    not_later = 0
    for frame in new_frames:
        observation = observations.get(frame.frame_id)
        start = None if observation is None else observation.start_epoch_seconds
        if start is None:
            unknown += 1
        elif observing_night_id(start) <= newest_night:
            not_later += 1
    if not_later:
        return (
            f"{not_later} of the {len(new_frames)} frames added by the library stack are not from a later "
            f"observing night than the earlier-nights stack's newest night ({newest_night})."
        )
    if unknown:
        return (
            f"{unknown} of the {len(new_frames)} added frames have no start time, so it cannot be shown that "
            "they are from a later night."
        )
    return None


class _PairForecast(BaseModel):
    """The two forecast numbers of a pair, or why they cannot be made.

    Parameters
    ----------
    planned, given : `float` or `None`
        The forecast changes in noise (-0.29 is a fall of 29%).
    mode_name : `str`
        The weighting mode named for the reader.
    equal_weights : `bool`
        Whether the later stack's weights were equal.
    reason : `str` or `None`
        Why the pair cannot be checked.
    """

    planned: float | None = None
    given: float | None = None
    mode_name: str = "unknown"
    equal_weights: bool = False
    reason: str | None = None


def _is_group_stack(frames: Sequence[StackFrame]) -> bool:
    """Say whether the stacker would split these frames into exposure groups.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        The stack's frames.

    Returns
    -------
    is_group : `bool`
        True when the frames fall into more than one group length.
    """
    lengths = fold_exposure_lengths([frame.exposure_seconds for frame in frames])
    return None not in lengths and len(set(lengths)) > 1


def _given_group_forecast(
    run: StackRunRecord, frames: Sequence[StackFrame], run_groups: RunGroups
) -> NoiseForecast:
    """Predict a group stack's noise from the weights the run recorded.

    The coefficient of frame ``i`` in group ``g`` is ``W_g w_i / (sum(W) G_g)``
    (the recorded weight over the gain, over the sum of the recorded weights),
    and the combine scales the result by the mean exposure ``t_bar``.

    Parameters
    ----------
    run : `StackRunRecord`
        The run, with its recorded weights.
    frames : `Sequence` [`StackFrame`]
        The run's member frames, in the order of the recorded weights.
    run_groups : `RunGroups`
        The run's groups (`stack_groups_of_run`).

    Returns
    -------
    forecast : `NoiseForecast`
        Mode ``group``, in the stack's scale, with ``mean_exposure_seconds``
        set.
    """
    weight_of = {
        frame.frame_id: float(weight or 0.0)
        for frame, weight in zip(frames, run.recorded_weights, strict=True)
    }
    ordered = [frame for group in run_groups.groups for frame in group.frames]
    adjusted = [
        weight_of[frame.frame_id] / group.gain for group in run_groups.groups for frame in group.frames
    ]
    base = forecast_noise_with_weights(
        ordered,
        adjusted,
        WeightingMode.GROUP,
        weight_normalization=math.fsum(weight_of.values()),
        rate_variances=group_frame_rate_variances(run_groups.groups),
    )
    if base.variance is None:
        return base
    mean_exposure = math.fsum(frame.exposure_seconds for frame in ordered) / len(ordered)
    variance = base.variance * mean_exposure**2
    return NoiseForecast(
        variance=variance,
        noise=math.sqrt(variance),
        frame_count=len(ordered),
        mode=WeightingMode.GROUP,
        mean_exposure_seconds=mean_exposure,
    )


def _group_forecast(
    previous: StackRunRecord,
    current: StackRunRecord,
    previous_frames: Sequence[StackFrame],
    current_frames: Sequence[StackFrame],
    new_frames: Sequence[StackFrame],
    floor: StackNoiseFloor | None,
    electrons_per_adu: float | None,
) -> _PairForecast:
    """Forecast a pair of stacks that were both combined from exposure groups.

    The planned forecast places the new frames with the stacker's own rule
    (`plan_group_stack`); the forecast given the frames taken uses the
    recorded weights. Both carry the earlier stack's floor.

    Parameters
    ----------
    previous, current : `StackRunRecord`
        The earlier and the later run.
    previous_frames, current_frames : `Sequence` [`StackFrame`]
        Their member frames.
    new_frames : `Sequence` [`StackFrame`]
        The frames only the later stack holds.
    floor : `StackNoiseFloor` or `None`
        The earlier stack's noise floor.
    electrons_per_adu : `float` or `None`
        The camera gain, needed to predict the frame noise of a group the new
        frames start.

    Returns
    -------
    forecast : `_PairForecast`
        The numbers, or the reason (a missing row, or the heaviest group
        changing).
    """
    previous_groups = stack_groups_of_run(previous, previous_frames, electrons_per_adu=electrons_per_adu)
    current_groups = stack_groups_of_run(current, current_frames, electrons_per_adu=electrons_per_adu)
    for groups in (previous_groups, current_groups):
        if groups.reason is not None:
            return _PairForecast(reason=groups.reason)
    heaviest_before = previous_groups.heaviest_exposure_seconds
    heaviest_after = current_groups.heaviest_exposure_seconds
    if heaviest_before != heaviest_after:
        return _PairForecast(
            reason=(
                f"The heaviest exposure group changed from {heaviest_before:g} s to {heaviest_after:g} s, "
                "which rescales the stack, so the two noise values are not like for like."
            )
        )
    noises = {
        length: group.frame_noise_adu
        for length, group in zip(current_groups.group_lengths, current_groups.groups, strict=True)
        if group.frame_noise_adu is not None
    }
    evidence = identify_weighting_mode(
        current_frames, current.recorded_weights, group_frame_noises_adu=noises
    )
    mode_name = evidence.mode.value if evidence.mode != WeightingMode.UNKNOWN else "recorded_weights"
    planned_exposure = float(np.median([frame.exposure_seconds for frame in new_frames]))
    plan = plan_group_stack(
        previous_groups.groups,
        new_frames_like(previous_frames, len(new_frames), planned_exposure),
        electrons_per_adu=electrons_per_adu,
    )
    before = forecast_group_noise(previous_groups.groups)
    given_before = _given_group_forecast(previous, previous_frames, previous_groups)
    given_after = _given_group_forecast(current, current_frames, current_groups)
    if (
        before.noise is None
        or plan.forecast.noise is None
        or given_before.noise is None
        or given_after.noise is None
    ):
        return _PairForecast(
            mode_name=mode_name,
            reason=plan.reason
            or before.reason
            or given_after.reason
            or "The group model could not be evaluated.",
        )
    planned_ratio = noise_ratio_with_floor(before, plan.forecast, floor)
    given_ratio = noise_ratio_with_floor(given_before, given_after, floor)
    if planned_ratio is None or given_ratio is None:
        return _PairForecast(mode_name=mode_name, reason="The group model could not be evaluated.")
    return _PairForecast(planned=planned_ratio - 1.0, given=given_ratio - 1.0, mode_name=mode_name)


def _flat_forecast(
    previous: StackRunRecord,
    current: StackRunRecord,
    previous_frames: Sequence[StackFrame],
    current_frames: Sequence[StackFrame],
    new_frames: Sequence[StackFrame],
    floor: StackNoiseFloor | None,
    configured_stack_weight: str | None,
) -> _PairForecast:
    """Forecast a pair of stacks that are each one exposure group.

    Parameters
    ----------
    previous, current : `StackRunRecord`
        The earlier and the later run.
    previous_frames, current_frames : `Sequence` [`StackFrame`]
        Their member frames.
    new_frames : `Sequence` [`StackFrame`]
        The frames only the later stack holds.
    floor : `StackNoiseFloor` or `None`
        The earlier stack's noise floor.
    configured_stack_weight : `str` or `None`
        The configured stack weight, used for the planned forecast when the
        earlier run's mode is not known.

    Returns
    -------
    forecast : `_PairForecast`
        The numbers, or the reason they cannot be made.
    """
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
        return _PairForecast(
            mode_name=mode_name,
            reason=given_after.reason or before.reason or "The forward model could not be evaluated.",
        )
    planned_ratio = noise_ratio_with_floor(before, planned_after, floor)
    given_ratio = noise_ratio_with_floor(given_before, given_after, floor)
    if planned_ratio is None or given_ratio is None:
        return _PairForecast(mode_name=mode_name, reason="The forward model could not be evaluated.")
    return _PairForecast(
        planned=planned_ratio - 1.0,
        given=given_ratio - 1.0,
        mode_name=mode_name,
        equal_weights=current_mode.mode == WeightingMode.EQUAL,
    )


def _floor_sentence(floor: StackNoiseFloor | None) -> str:
    """Say in a sentence what the forecast assumed about a noise floor.

    Parameters
    ----------
    floor : `StackNoiseFloor` or `None`
        The earlier stack's floor.

    Returns
    -------
    text : `str`
        A sentence naming the floor, or saying none was found or measured.
    """
    if floor is None:
        return "No noise floor could be measured for the earlier stack."
    if not floor.floor_detected:
        return "The earlier stack showed no noise floor."
    share = 100 * (floor.floor_variance_share or 0.0)
    return (
        f"The forecast already includes a noise floor that makes up {share:.0f}% of the earlier "
        "stack's noise variance."
    )


def _check_pair_unlabeled(
    previous: StackRunRecord,
    current: StackRunRecord,
    observations: Mapping[str, FrameObservation],
    paired_relative_error: float | None,
    configured_stack_weight: str | None,
    *,
    history: Sequence[StackRunRecord] = (),
    electrons_per_adu: float | None = None,
) -> ForecastCheck:
    """Check the forecast for one pair of neighbouring stack runs.

    The pair is not checkable when the two stacks differ in more than the
    added frames: the aperture set, the noise definition, the calibration,
    the Siril or stacking-algorithm version, the heaviest exposure group, or
    when an added frame is older than the earlier stack. See
    `_confound_reason`, `_new_frame_reason` and `_group_forecast`.

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
    history : `Sequence` [`StackRunRecord`], optional
        The runs up to and including the earlier run, for the floor fit. The
        earlier run alone is used when this is left out.
    electrons_per_adu : `float`, optional
        The camera gain, for the frame noise of a group the new frames start.

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
    confounded = _confound_reason(previous, current)
    if confounded is not None:
        return _not_checkable(current, confounded, mode_name, evidence)
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
    numbers = {"measured": measured, "tolerance": tolerance, "paired": paired}
    if not new_frames:
        return _not_checkable(
            current,
            "The later stack holds no frame that the earlier one did not, so no gain was forecast.",
            mode_name,
            evidence,
            **numbers,
        )
    not_new = (
        _subset_new_frame_reason(previous, current, new_frames, observations)
        if previous.is_earlier_nights_subset
        else _new_frame_reason(previous, new_frames, observations)
    )
    if not_new is not None:
        return _not_checkable(current, not_new, mode_name, evidence, **numbers)

    # The frames' real weights are the recorded ones. The mode is named for the
    # reader when the weights match one, but nothing depends on it.
    if any(weight is None for weight in [*previous.recorded_weights, *current.recorded_weights]):
        return _not_checkable(
            current,
            "The stacker's weights were not recorded for every frame (Siril does not report them in its "
            "noise mode), so the forecast given the frames taken cannot be made. Nothing is guessed.",
            mode_name,
            evidence,
            **numbers,
        )
    floor = noise_floor_for_runs(list(history) if history else [previous])
    previous_is_group = _is_group_stack(previous_frames)
    current_is_group = _is_group_stack(current_frames)
    if previous_is_group != current_is_group:
        return _not_checkable(
            current,
            "One stack was combined from exposure groups and the other was a single group. The combine "
            "rescales the brightness, so the two noise values are not like for like.",
            mode_name,
            evidence,
            **numbers,
        )
    forecast = (
        _group_forecast(
            previous, current, previous_frames, current_frames, new_frames, floor, electrons_per_adu
        )
        if current_is_group
        else _flat_forecast(
            previous, current, previous_frames, current_frames, new_frames, floor, configured_stack_weight
        )
    )
    mode_name = forecast.mode_name
    if forecast.planned is None or forecast.given is None:
        return _not_checkable(
            current,
            forecast.reason or "The forward model could not be evaluated.",
            mode_name,
            evidence,
            **numbers,
        )
    planned, given = forecast.planned, forecast.given
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
            + (" The stacker gave the frames equal weight." if forecast.equal_weights else "")
        ).strip()
    else:
        outcome = ForecastOutcome.SHORT_UNKNOWN_REASON
        reason = (
            f"The noise changed by {100 * measured:.0f}% against {100 * planned:.0f}% planned, and the "
            f"frames taken explain a change of only {100 * given:.0f}%. The cause is not known. "
            f"{_floor_sentence(floor)}"
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


def _check_pair(
    previous: StackRunRecord,
    current: StackRunRecord,
    observations: Mapping[str, FrameObservation],
    paired_relative_error: float | None,
    configured_stack_weight: str | None,
    *,
    history: Sequence[StackRunRecord] = (),
    electrons_per_adu: float | None = None,
) -> ForecastCheck:
    """Check one pair of runs and label the check with what it compared.

    A pair whose earlier run is an earlier-nights subset is checked with the
    same rules as two library stacks, except that the frames the library stack
    added must be from a later observing night (`_subset_new_frame_reason`).
    The check is labeled ``earlier_nights_subset``; any other is labeled
    ``later_stack``. See `_check_pair_unlabeled` for the rules and the
    parameters.

    Parameters
    ----------
    previous, current : `StackRunRecord`
        The earlier and the later run.
    observations : `Mapping` [`str`, `FrameObservation`]
        The measurements of every frame, by frame id.
    paired_relative_error : `float` or `None`
        The relative error of the noise ratio when paired.
    configured_stack_weight : `str` or `None`
        The configured stack weight setting.
    history : `Sequence` [`StackRunRecord`], optional
        The runs up to and including the earlier run, for the floor fit.
    electrons_per_adu : `float`, optional
        The camera gain.

    Returns
    -------
    check : `ForecastCheck`
        The check, with its `basis` set.
    """
    check = _check_pair_unlabeled(
        previous,
        current,
        observations,
        paired_relative_error,
        configured_stack_weight,
        history=history,
        electrons_per_adu=electrons_per_adu,
    )
    basis = (
        ForecastBasis.EARLIER_NIGHTS_SUBSET
        if previous.is_earlier_nights_subset
        else ForecastBasis.LATER_STACK
    )
    return check.model_copy(update={"basis": basis})


def forecast_check(
    runs: Sequence[StackRunRecord],
    observations: Iterable[FrameObservation],
    *,
    configured_stack_weight: str | None = None,
    paired_relative_error_by_run: Mapping[str, float] | None = None,
    electrons_per_adu: float | None = None,
) -> list[ForecastCheck]:
    """Check each stack run's gain against the forecast from the run before.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs (`stack_runs_from_measurements`, with
        `attach_calibration_identities`). They are put in time order here.
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
    electrons_per_adu : `float`, optional
        The camera gain at the target's setting (`camera_gain_for_setting`),
        for the frame noise of an exposure group the new frames start.

    Returns
    -------
    checks : `list` [`ForecastCheck`]
        One check for each run after the first, oldest first. Empty when there
        are fewer than two runs, because there is no forecast to check.
    """
    by_id = {observation.frame_id: observation for observation in observations}
    ordered = library_runs(runs)
    # Each entry is (when the later run was made, 0 for a subset check and 1
    # for a real one, run id, check). At a tie the real check sorts after the
    # subset check, so a real disagreement closes the gate that a subset check
    # opened at the same stack.
    made: list[tuple[datetime, int, str, ForecastCheck]] = []
    for position, (previous, current) in enumerate(pairwise(ordered)):
        paired = (paired_relative_error_by_run or {}).get(current.run_id)
        check = _check_pair(
            previous,
            current,
            by_id,
            paired,
            configured_stack_weight,
            history=ordered[: position + 1],
            electrons_per_adu=electrons_per_adu,
        )
        made.append((current.measured_at, 1, current.run_id, check))
    for subset, library in subset_pairs(runs):
        paired = (paired_relative_error_by_run or {}).get(library.run_id)
        check = _check_pair(
            subset,
            library,
            by_id,
            paired,
            configured_stack_weight,
            electrons_per_adu=electrons_per_adu,
        )
        made.append((library.measured_at, 0, library.run_id, check))
    made.sort(key=lambda item: (_as_utc(item[0]), item[1], item[2]))
    return [item[3] for item in made]


def _as_utc(moment: datetime) -> datetime:
    """Give a time with a zone, counting a time with none as UTC.

    Parameters
    ----------
    moment : `datetime`
        The time.

    Returns
    -------
    moment : `datetime`
        The same time, zone aware.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def library_runs(runs: Iterable[StackRunRecord]) -> list[StackRunRecord]:
    """Keep the library stack runs, in time order.

    Parameters
    ----------
    runs : `Iterable` [`StackRunRecord`]
        Runs from `stack_runs_from_measurements`, with or without
        earlier-nights subset runs.

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        The runs that are not earlier-nights subsets, oldest first.
    """
    return sorted(
        (run for run in runs if not run.is_earlier_nights_subset),
        key=lambda record: (_as_utc(record.measured_at), record.run_id),
    )


def subset_pairs(runs: Iterable[StackRunRecord]) -> list[tuple[StackRunRecord, StackRunRecord]]:
    """Pair each earlier-nights subset run with its library run.

    The library run is the one the subset was cut from. It is the one whose
    members include every member of the subset and that was made closest in
    time to the subset (the older one at a tie).
    A subset made before and a subset made after its library stack both pair
    correctly, and a later library stack that also holds the subset's frames is
    passed over. When no library run holds the subset's members (or the
    members are not known), the closest library run in time is used, and the
    check then says why it cannot be made. When there are several subset runs
    for one library run (a stack made again), only the newest is kept.

    Parameters
    ----------
    runs : `Iterable` [`StackRunRecord`]
        Library and subset runs together.

    Returns
    -------
    pairs : `list` [`tuple`]
        ``(subset, library)`` for each subset that has a library run, oldest
        subset first.
    """
    everything = list(runs)
    libraries = library_runs(everything)
    if not libraries:
        return []
    newest_by_library: dict[str, tuple[StackRunRecord, StackRunRecord]] = {}
    subsets = sorted(
        (run for run in everything if run.is_earlier_nights_subset),
        key=lambda record: (_as_utc(record.measured_at), record.run_id),
    )
    for subset in subsets:
        members = set(subset.member_frame_ids)
        candidates = [
            run for run in libraries if members and members < set(run.member_frame_ids)
        ] or libraries
        library = min(
            candidates,
            key=lambda run: (
                abs((_as_utc(run.measured_at) - _as_utc(subset.measured_at)).total_seconds()),
                run.run_id,
            ),
        )
        newest_by_library[library.run_id] = (subset, library)
    return sorted(newest_by_library.values(), key=lambda pair: (_as_utc(pair[0].measured_at), pair[0].run_id))


def forecast_gate_state(checks: Iterable[ForecastCheck]) -> ForecastGate:
    """Say whether "take N more" may be shown, and on what footing.

    The checks are read in the order `forecast_check` returns them (oldest
    first; at one stack a subset check comes before a real one).

    * A check of two library stacks that agreed (``as_forecast``) makes the
      gate ``confirmed``, and it stays so. This is the rule before the
      earlier-nights check existed, and it is the only way to open the gate
      for a target with one observing night.
    * A check of two library stacks that did not agree closes the gate, unless
      it is already confirmed.
    * A check from an earlier-nights subset that agreed opens a closed gate as
      ``exploratory``. One that did not agree closes an exploratory gate.
    * A check that could not be made changes nothing.

    Parameters
    ----------
    checks : `Iterable` [`ForecastCheck`]
        The target's forecast checks.

    Returns
    -------
    gate : `ForecastGate`
        ``closed``, ``exploratory`` or ``confirmed``.
    """
    gate = ForecastGate.CLOSED
    for check in checks:
        if check.outcome == ForecastOutcome.NOT_CHECKABLE:
            continue
        agreed = check.outcome == ForecastOutcome.AS_FORECAST
        if check.basis == ForecastBasis.LATER_STACK:
            if agreed:
                gate = ForecastGate.CONFIRMED
            elif gate != ForecastGate.CONFIRMED:
                gate = ForecastGate.CLOSED
        elif agreed:
            if gate == ForecastGate.CLOSED:
                gate = ForecastGate.EXPLORATORY
        elif gate == ForecastGate.EXPLORATORY:
            gate = ForecastGate.CLOSED
    return gate


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

    The floor is the one `noise_floor_for_runs` chooses: the fit over two or
    more stacks, or the one-stack floor of the newest run. Doubling the data
    then halves the random variance and leaves the floor, so the noise falls
    from ``sqrt(1 + f)`` to ``sqrt(0.5 + f)`` (in units of today's random
    noise), with ``f`` the floor's share of the random variance.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs.

    Returns
    -------
    signal : `StopSignal`
        ``stopped`` is true only when a floor was found and doubling the data
        would cut the noise by under 10%. It is ``False`` when a fit over two
        or more stacks found no floor, and ``None`` (no signal) when the
        history is too short: a single stack with no floor is not enough to
        say.
    """
    ordered = sorted(runs, key=lambda item: (item.measured_at, item.run_id))
    newest_identity = ordered[-1].aperture_identity if ordered else None
    steps = noise_steps_from_runs([
        record for record in ordered if record.aperture_identity == newest_identity
    ])
    if not steps:
        return StopSignal(reason="No stack run has both a measured and an expected noise.")
    floor = noise_floor_for_runs(ordered)
    if floor is not None and floor.floor_detected and floor.floor_to_random_variance_ratio is not None:
        ratio = floor.floor_to_random_variance_ratio
        gain = 1.0 - math.sqrt((0.5 + ratio) / (1.0 + ratio))
        stopped = gain < MINIMUM_GAIN_FRACTION
        return StopSignal(
            stopped=stopped,
            predicted_gain_fraction=gain,
            reason=(
                f"Doubling the data would cut the noise by {100 * gain:.0f} percent"
                + (
                    ", below the limit; more frames will not help until the floor is fixed."
                    if stopped
                    else "."
                )
            ),
        )
    fit = fit_noise_floor(steps)
    if floor is not None and fit.steps_used >= 2 and fit.alpha is not None:
        return StopSignal(stopped=False, reason="No floor was found, so more frames are expected to help.")
    return stop_signal(fit, steps[-1].expected_variance)


__all__ = [
    "AGREEMENT_TOLERANCES",
    "COOLED_CAMERA_NAME_MARKERS",
    "FORECAST_CHECK_ENTRY",
    "FORECAST_CHECK_ROW",
    "GROUP_METRICS",
    "PINNED_STACK_METRICS",
    "CalibrationSummary",
    "RecordedGroup",
    "RunGroups",
    "StackRunRecord",
    "attach_calibration_identities",
    "calibration_summary_from_record",
    "camera_gain_for_setting",
    "forecast_check",
    "forecast_gate_state",
    "library_runs",
    "noise_floor_for_runs",
    "noise_steps_from_runs",
    "single_stack_floor",
    "stack_frames_from_observations",
    "stack_groups_of_run",
    "stack_runs_from_measurements",
    "subset_pairs",
    "target_stop_signal",
]
