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
not checked. The earlier stack's fixed-pattern noise is part of every
forecast: the measured empty-aperture noise against the
expected noise (version 2, which includes Siril's normalization scale), or a
fit over the history, reconciled by `noise_growth.choose_fixed_pattern_noise`.

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
a run marked ``is_earlier_nights_subset``, and `subset_pairs` pairs it with the
library run it was cut from. The pair is checked with the same rules, except
that every frame the library stack added must start on a later observing night
than the subset's newest frame (`_subset_new_frame_reason`). The check carries
``basis="earlier_nights_subset"``.
`recommended_additional_frames_status_from_checks` reads the checks in order
and says whether the recommended additional frames are not checked, provisional
(only a subset check agreed) or confirmed (two library stacks agreed).

The module also turns the run history into the steps of the fixed-pattern noise
fit (`noise_steps_from_runs`) and reads the stop signal from it
(`target_stop_signal`), so the target report and tonight's ranking use the same
history. The stop signal uses the fixed-pattern noise
`fixed_pattern_noise_for_runs` chooses, which can come from the newest stack
alone.

The noise model's own relative error is measured from the library's odd–even
differences, not borrowed (decision D17). `noise_model_error_sample` turns one
run into a sample (its odd–even random noise against its predicted noise), and
`noise_model_errors_by_setting_group` pools the samples of one capture setting
group (`noise_model_setting_key`: camera, optic, gain setting, offset and
binning) from one library-wide read of stack rows. A group with fewer than
`MINIMUM_MODEL_ERROR_TARGETS` targets keeps the stand-in value.

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
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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
    load_odd_even_aperture_record,
    observing_night_id,
    resolve_camera_profile,
)
from wayfindinglib.analytics.result_quality.efficiency import FrameObservation
from wayfindinglib.analytics.result_quality.fixed_pattern_location import (
    FixedPatternAccount,
    LinePatternScatters,
    apertures_from_record,
    explain_with_calibration_masters,
    locate_fixed_pattern,
)
from wayfindinglib.analytics.result_quality.noise_growth import (
    FIXED_PATTERN_BASIS_ODD_EVEN_DIFFERENCE,
    FIXED_PATTERN_BASIS_SETUP_HISTORY,
    MINIMUM_GAIN_FRACTION,
    MINIMUM_HISTORY_TARGETS,
    NoiseForecast,
    NoiseModelError,
    NoiseModelErrorSample,
    NoiseStep,
    StackFixedPatternNoise,
    StackFrame,
    StackGroup,
    StopSignal,
    WeightingMode,
    aperture_noise_relative_error,
    choose_fixed_pattern_noise,
    fit_fixed_pattern_noise,
    forecast_group_noise,
    forecast_noise_with_weights,
    forecast_stack_noise,
    frame_noise_from_group_weight,
    group_frame_rate_variances,
    identify_weighting_mode,
    measure_noise_model_error,
    measure_stack_fixed_pattern_noise,
    measure_stack_fixed_pattern_noise_from_odd_even_difference,
    new_frames_like,
    noise_ratio_after_adding,
    noise_ratio_with_fixed_pattern_noise,
    plan_group_stack,
    predict_weighting_mode,
    stop_signal,
)
from wayfindinglib.models.planning.target_report import (
    ForecastBasis,
    ForecastCheck,
    ForecastedChange,
    ForecastOutcome,
    RecommendedAdditionalFramesStatus,
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
EFFECTIVE_FRAME_COUNT_METRIC = "stacking.effective_frame_count"
ODD_EVEN_DIFFERENCE_NORMALIZED_METRIC = "stacking.odd_even_difference_noise_normalized"
ODD_EVEN_DIFFERENCE_APERTURE_SET_METRIC = "stacking.odd_even_difference_aperture_set_number"
ODD_EVEN_DIFFERENCE_VARIANCE_FACTOR_METRIC = "stacking.odd_even_difference_variance_factor"
ODD_EVEN_DIFFERENCE_MODEL_RATIO_METRIC = "stacking.odd_even_difference_model_variance_ratio"
ODD_EVEN_APERTURE_RECORD_COUNT_METRIC = "stacking.odd_even_difference_aperture_record_count"
ODD_EVEN_DIFFERENCE_ROW_SCATTER_METRIC = "stacking.odd_even_difference_row_average_scatter_stack_unit"
ODD_EVEN_DIFFERENCE_COLUMN_SCATTER_METRIC = "stacking.odd_even_difference_column_average_scatter_stack_unit"
ODD_EVEN_SUM_ROW_SCATTER_METRIC = "stacking.odd_even_sum_row_average_scatter_stack_unit"
ODD_EVEN_SUM_COLUMN_SCATTER_METRIC = "stacking.odd_even_sum_column_average_scatter_stack_unit"
FLAT_NOISE_IN_APERTURE_METRIC = "stacking.flat_noise_in_aperture_stack_unit"
EXPECTED_MASTER_DARK_NOISE_METRIC = "stacking.expected_master_dark_noise_stack_unit"
FRAMES_STACKED_METRIC = "stacking.frames_stacked_count"
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
    EFFECTIVE_FRAME_COUNT_METRIC: 1,
    FRAMES_STACKED_METRIC: 2,
    ODD_EVEN_DIFFERENCE_NORMALIZED_METRIC: 1,
    ODD_EVEN_DIFFERENCE_APERTURE_SET_METRIC: 1,
    ODD_EVEN_DIFFERENCE_VARIANCE_FACTOR_METRIC: 1,
    ODD_EVEN_DIFFERENCE_MODEL_RATIO_METRIC: 1,
    ODD_EVEN_APERTURE_RECORD_COUNT_METRIC: 1,
    ODD_EVEN_DIFFERENCE_ROW_SCATTER_METRIC: 1,
    ODD_EVEN_DIFFERENCE_COLUMN_SCATTER_METRIC: 1,
    ODD_EVEN_SUM_ROW_SCATTER_METRIC: 1,
    ODD_EVEN_SUM_COLUMN_SCATTER_METRIC: 1,
    FLAT_NOISE_IN_APERTURE_METRIC: 1,
    EXPECTED_MASTER_DARK_NOISE_METRIC: 1,
    GROUP_EXPOSURE_METRIC: 1,
    GROUP_FRAMES_STACKED_METRIC: 1,
    GROUP_GAIN_METRIC: 1,
    GROUP_REFERENCE_EXPOSURE_METRIC: 1,
}
"""The metrics read, and the definition version each is pinned to (decision
S13). A row of another version is not used. A test fails when the registry
moves past one of these versions."""

MINIMUM_EARLIER_NIGHTS_FRAMES = 8
"""An earlier-nights forecast check counts only when the earlier-nights stack
holds at least this many frames (the stack's stored
``stacking.frames_stacked_count``). A smaller pair is not checkable, so it does
not make the recommended additional frames provisional. User decision
2026-10-26: M 13's 4-frame first night opened a provisional count alone. This
is more than the general `astrometricslib` subset minimum of 4 frames, which is
unchanged. Design estimate, register row to be added."""

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


ODD_EVEN_DIFFERENCE_DETAIL_METRICS = frozenset({
    ODD_EVEN_APERTURE_RECORD_COUNT_METRIC,
    ODD_EVEN_DIFFERENCE_ROW_SCATTER_METRIC,
    ODD_EVEN_DIFFERENCE_COLUMN_SCATTER_METRIC,
    ODD_EVEN_SUM_ROW_SCATTER_METRIC,
    ODD_EVEN_SUM_COLUMN_SCATTER_METRIC,
})
"""The odd–even difference job's rows about where the fixed pattern is (the
per-aperture record and the four line-pattern scatters, decision D21). They are
joined to the run like the rows below, but a missing one is not a problem of
the noise comparison, so they are read apart."""

ODD_EVEN_DIFFERENCE_METRICS = frozenset({
    ODD_EVEN_DIFFERENCE_NORMALIZED_METRIC,
    ODD_EVEN_DIFFERENCE_APERTURE_SET_METRIC,
    ODD_EVEN_DIFFERENCE_VARIANCE_FACTOR_METRIC,
    ODD_EVEN_DIFFERENCE_MODEL_RATIO_METRIC,
})
"""The odd–even difference rows read. They sit on the library stack's subject
but belong to a later run (the odd–even difference job runs after the stack),
so they
are read apart from the noise rows and joined to the run they follow."""


class OddEvenDifferenceRecord(BaseModel):
    """The odd–even difference measurement of one library stack.

    The job takes the odd-minus-even difference of two half stacks, which
    cancels everything fixed on the sky, so its noise is the random noise alone
    (see
    `noise_growth.measure_stack_fixed_pattern_noise_from_odd_even_difference`).

    Parameters
    ----------
    run_id : `str`
        The run of the odd–even difference job.
    measured_at : `datetime`
        When its newest row was stored.
    normalized_noise : `float` or `None`
        The difference's noise as a fraction of the reference stars' flux (no
        unit), measured in the apertures of `aperture_identity`.
    apertures : `int` or `None`
        The empty apertures behind `normalized_noise`.
    aperture_identity : `str` or `None`
        The identity of the aperture set it was measured in.
    variance_factor : `float` or `None`
        The difference's variance in units of one frame's variance
        (``F = sum((a - b)^2)`` over the frames of both halves).
    model_variance_ratio : `float` or `None`
        The difference's variance in units of the library stack's, with each
        frame's modeled noise. Used only when `variance_factor` is missing.
    aperture_record_path : `str` or `None`
        The per-aperture record file beside the stack (read from the reason of
        ``stacking.odd_even_difference_aperture_record_count``), for the shape
        tests of the fixed pattern.
    aperture_record_count : `int` or `None`
        How many apertures the record holds.
    line_scatters : `LinePatternScatters` or `None`
        The four stored line-pattern scatters, when all are measured.
    problems : `dict` [`str`, `str`]
        Why a part is missing, by part name.
    """

    model_config = _MODEL_CONFIG

    run_id: str
    measured_at: datetime
    normalized_noise: float | None = None
    apertures: int | None = None
    aperture_identity: str | None = None
    variance_factor: float | None = None
    model_variance_ratio: float | None = None
    aperture_record_path: str | None = None
    aperture_record_count: int | None = None
    line_scatters: LinePatternScatters | None = None
    problems: dict[str, str] = Field(default_factory=dict)

    def full_stack_equivalent(self, library_effective_count: float | None) -> float | None:
        """Give the full stack's normalized random noise.

        Built as the job builds it. With equal frame noise, a stack with
        weight shares ``s`` has the variance ``sigma^2 x sum(s^2)``, which is
        ``sigma^2 / N_eff`` for the library stack, and the difference has
        ``sigma^2 x F``. So the library stack's random noise is the
        difference noise divided by ``sqrt(N_eff x F)``. When the effective
        frame count or ``F`` is missing, the difference noise divided by the
        square root of the stored model variance ratio is used instead.

        Parameters
        ----------
        library_effective_count : `float` or `None`
            The library stack's effective frame count
            (``stacking.effective_frame_count``).

        Returns
        -------
        noise : `float` or `None`
            The normalized random noise of the full stack, or ``None`` when
            the difference noise or both divisors are missing.
        """
        if self.normalized_noise is None or self.normalized_noise <= 0.0:
            return None
        if (
            library_effective_count is not None
            and library_effective_count > 0.0
            and self.variance_factor is not None
            and self.variance_factor > 0.0
        ):
            return self.normalized_noise / math.sqrt(library_effective_count * self.variance_factor)
        if self.model_variance_ratio is not None and self.model_variance_ratio > 0.0:
            return self.normalized_noise / math.sqrt(self.model_variance_ratio)
        return None


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
    noise_measured_at : `datetime` or `None`
        When the stack's noise row was stored. An odd–even difference
        measurement is used only when it is newer.
    groups : `list` [`RecordedGroup`]
        The exposure groups the run recorded. Empty for a run that stored
        none (a stack made before part 2a) and for a run that made several
        stacks, because no stored row says which group belongs to which
        stack (see ``problems["groups"]``).
    effective_frame_count : `float` or `None`
        The stack's effective frame count (``stacking.effective_frame_count``).
    frames_stacked : `int` or `None`
        The frames the stack holds (``stacking.frames_stacked_count``);
        ``None`` when not stored.
    odd_even_difference : `OddEvenDifferenceRecord` or `None`
        The newest odd–even difference measurement made after this run's noise
        row and before the next run of the same stack, or ``None``.
    flat_noise : `float` or `None`
        The noise the master flat adds to an aperture sum, in stack units
        (``stacking.flat_noise_in_aperture_stack_unit``).
    master_dark_noise : `float` or `None`
        The noise the master dark adds, in stack units, a lower bound
        (``stacking.expected_master_dark_noise_stack_unit``); ``None`` for a
        stack of several exposure groups.
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
    effective_frame_count: float | None = None
    frames_stacked: int | None = None
    odd_even_difference: OddEvenDifferenceRecord | None = None
    flat_noise: float | None = None
    master_dark_noise: float | None = None
    noise_measured_at: datetime | None = None
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
        Also build the runs of the earlier-nights subset stack (all nights but
        the last), whose stack rows have the variant `EARLIER_NIGHTS_VARIANT`.
        Each is marked ``is_earlier_nights_subset``. Callers that fit
        fixed-pattern noise or read a stop signal must leave this off, because
        a subset is not part of the library's history. Defaults to `False`.

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        One record per stack in each run, oldest first. A run that stored no
        noise row at all is left out.
    """
    stack_rows: dict[tuple[str, str, bool], list[QualityMeasurement]] = defaultdict(list)
    weight_rows: dict[str, list[QualityMeasurement]] = defaultdict(list)
    group_rows: dict[str, dict[str, list[QualityMeasurement]]] = defaultdict(lambda: defaultdict(list))
    half_rows: dict[tuple[str, str], list[QualityMeasurement]] = defaultdict(list)
    for row in measurements:
        if row.metric not in PINNED_STACK_METRICS:
            continue
        variant = row.subject.variant
        if row.metric in ODD_EVEN_DIFFERENCE_METRICS or row.metric in ODD_EVEN_DIFFERENCE_DETAIL_METRICS:
            # The job's rows follow the stack's own run, so they must not make
            # a run of their own. The earlier-nights subset has none.
            if row.subject.level == SubjectLevel.STACK and variant is None:
                half_rows[row.run_id, row.subject.subject_id].append(row)
            continue
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
        effective_count, _count_reason = _usable_value(
            grouped.get((stack_id, EFFECTIVE_FRAME_COUNT_METRIC), []), EFFECTIVE_FRAME_COUNT_METRIC
        )
        stacked_count, _stacked_reason = _usable_value(
            grouped.get((stack_id, FRAMES_STACKED_METRIC), []), FRAMES_STACKED_METRIC
        )
        flat_noise, _flat_reason = _usable_value(
            grouped.get((stack_id, FLAT_NOISE_IN_APERTURE_METRIC), []), FLAT_NOISE_IN_APERTURE_METRIC
        )
        dark_noise, _dark_reason = _usable_value(
            grouped.get((stack_id, EXPECTED_MASTER_DARK_NOISE_METRIC), []), EXPECTED_MASTER_DARK_NOISE_METRIC
        )
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
                effective_frame_count=effective_count,
                frames_stacked=round(stacked_count) if stacked_count is not None else None,
                flat_noise=flat_noise if flat_noise is not None and flat_noise > 0.0 else None,
                master_dark_noise=dark_noise if dark_noise is not None and dark_noise > 0.0 else None,
                noise_measured_at=max((row.measured_at for row in noise_rows), default=None),
                is_earlier_nights_subset=is_subset,
                problems=problems,
            )
        )
    ordered = sorted(records, key=lambda record: (record.measured_at, record.run_id))
    return _attach_odd_even_differences(ordered, half_rows)


APERTURE_RECORD_PATH_PATTERN = re.compile(r"(\S.*?_odd_even_apertures\.json)")
"""Finds the per-aperture record's file name in the reason of the record-count
row. The pipeline writes the path there; a path may hold spaces, so the match
runs from the first non-space character up to the file ending."""


def _aperture_record_of_rows(
    grouped: Mapping[tuple[str, str], Sequence[QualityMeasurement]],
) -> tuple[str | None, int | None]:
    """Read the per-aperture record's path and count from the job's rows.

    Parameters
    ----------
    grouped : `Mapping`
        The job's newest rows by ``(subject id, metric)``.

    Returns
    -------
    path, count : `tuple` [`str` or `None`, `int` or `None`]
        The file path found in the record-count row's reason (after an
        optional ``Per-aperture record:`` label) and the count, or ``None``
        for a part that is missing.
    """
    for (_subject, metric), rows in grouped.items():
        if metric != ODD_EVEN_APERTURE_RECORD_COUNT_METRIC:
            continue
        value, _reason = _usable_value(rows, metric)
        if value is None or value < 1:
            return None, None
        for row in rows:
            reason_text = (row.reason or "").replace("Per-aperture record:", "")
            match = APERTURE_RECORD_PATH_PATTERN.search(reason_text)
            if match:
                return match.group(1).strip(), round(value)
        return None, round(value)
    return None, None


def _line_scatters_of_rows(
    grouped: Mapping[tuple[str, str], Sequence[QualityMeasurement]],
) -> LinePatternScatters | None:
    """Read the four line-pattern scatters from the job's rows.

    Parameters
    ----------
    grouped : `Mapping`
        The job's newest rows by ``(subject id, metric)``.

    Returns
    -------
    scatters : `LinePatternScatters` or `None`
        The scatters with the line counts from the rows' sample counts (the
        smaller of the sum's and the difference's, when they differ), or
        ``None`` when any of the four is missing or not positive.
    """
    values: dict[str, float] = {}
    counts: dict[str, int] = {}
    wanted = {
        ODD_EVEN_DIFFERENCE_ROW_SCATTER_METRIC: "difference_row",
        ODD_EVEN_DIFFERENCE_COLUMN_SCATTER_METRIC: "difference_column",
        ODD_EVEN_SUM_ROW_SCATTER_METRIC: "sum_row",
        ODD_EVEN_SUM_COLUMN_SCATTER_METRIC: "sum_column",
    }
    for (_subject, metric), rows in grouped.items():
        name = wanted.get(metric)
        if name is None:
            continue
        value, _reason = _usable_value(rows, metric)
        count = next((row.sample_count for row in rows if row.sample_count), None)
        if value is None or value <= 0.0 or count is None or count < 2:
            return None
        values[name] = value
        counts[name] = count
    if len(values) != len(wanted):
        return None
    return LinePatternScatters(
        **values,
        row_count=min(counts["difference_row"], counts["sum_row"]),
        column_count=min(counts["difference_column"], counts["sum_column"]),
    )


def _odd_even_difference_record(run_id: str, rows: Sequence[QualityMeasurement]) -> OddEvenDifferenceRecord:
    """Build one odd–even difference record from the rows of one job run.

    Parameters
    ----------
    run_id : `str`
        The job's run.
    rows : `Sequence` [`QualityMeasurement`]
        The run's odd–even difference rows on one library stack.

    Returns
    -------
    record : `OddEvenDifferenceRecord`
        The usable values; a missing one is named in ``problems``.
    """
    grouped = _newest_by_subject(rows)
    problems: dict[str, str] = {}
    values: dict[str, float | None] = {}
    for metric in ODD_EVEN_DIFFERENCE_METRICS:
        found = [pair for key, pair in grouped.items() if key[1] == metric]
        value, reason = _usable_value(found[0], metric) if found else (None, f"{metric} has no row.")
        values[metric] = value
        if value is None:
            problems[metric] = reason or f"{metric} was not measured."
    normalized_rows = next(
        (pair for key, pair in grouped.items() if key[1] == ODD_EVEN_DIFFERENCE_NORMALIZED_METRIC), []
    )
    apertures = next((row.sample_count for row in normalized_rows if row.sample_count), None)
    identity = None
    identity_value = values[ODD_EVEN_DIFFERENCE_APERTURE_SET_METRIC]
    if identity_value is not None:
        try:
            identity = aperture_identity_from_number(identity_value)
        except ValueError:
            problems[ODD_EVEN_DIFFERENCE_APERTURE_SET_METRIC] = (
                f"The stored aperture-set number {identity_value!r} is invalid."
            )
    record_path, record_count = _aperture_record_of_rows(grouped)
    return OddEvenDifferenceRecord(
        run_id=run_id,
        measured_at=max(row.measured_at for row in rows),
        normalized_noise=values[ODD_EVEN_DIFFERENCE_NORMALIZED_METRIC],
        apertures=apertures,
        aperture_identity=identity,
        variance_factor=values[ODD_EVEN_DIFFERENCE_VARIANCE_FACTOR_METRIC],
        model_variance_ratio=values[ODD_EVEN_DIFFERENCE_MODEL_RATIO_METRIC],
        aperture_record_path=record_path,
        aperture_record_count=record_count,
        line_scatters=_line_scatters_of_rows(grouped),
        problems=problems,
    )


def _attach_odd_even_differences(
    records: list[StackRunRecord], half_rows: Mapping[tuple[str, str], Sequence[QualityMeasurement]]
) -> list[StackRunRecord]:
    """Give each library run the odd–even difference that follows it.

    The job measures the halves of a stack after the stack's noise row is
    stored, so a measurement belongs to the newest run of its stack whose
    noise row is not newer than the measurement. A measurement older than
    every noise row of its stack belongs to none.

    Parameters
    ----------
    records : `list` [`StackRunRecord`]
        The runs, oldest first.
    half_rows : `Mapping`
        The odd–even difference rows by ``(job run id, stack subject id)``.

    Returns
    -------
    records : `list` [`StackRunRecord`]
        The same runs, each with ``odd_even_difference`` set where one belongs.
    """
    attached: dict[int, OddEvenDifferenceRecord] = {}
    for (run_id, stack_id), rows in half_rows.items():
        candidate = _odd_even_difference_record(run_id, rows)
        owners = [
            index
            for index, record in enumerate(records)
            if not record.is_earlier_nights_subset
            and record.stack_subject_id == stack_id
            and record.noise_measured_at is not None
            and _as_utc(record.noise_measured_at) <= _as_utc(candidate.measured_at)
        ]
        if not owners:
            continue
        owner = max(owners, key=lambda index: (_as_utc(records[index].noise_measured_at), index))
        current = attached.get(owner)
        if current is None or _as_utc(candidate.measured_at) > _as_utc(current.measured_at):
            attached[owner] = candidate
    return [
        record.model_copy(update={"odd_even_difference": attached[index]}) if index in attached else record
        for index, record in enumerate(records)
    ]


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
        # A folded group's stored exposure is the mean over every frame
        # submitted to it, including frames the stacker then dropped, so it can
        # differ from the mean of the listed members (M 27: 127.8 s stored,
        # 129.0 s over its 20 stacked members, 0.9 percent apart). The group's
        # reference exposure is exactly its folded length, so it is tried
        # first; the mean and the length, within 0.5 percent, are the fallback.
        mean_exposure = math.fsum(frames[index].exposure_seconds for index in indices) / len(indices)
        recorded = next(
            (
                group
                for group in run.groups
                if group.reference_exposure_seconds is not None
                and math.isclose(group.reference_exposure_seconds, length, rel_tol=1e-9)
            ),
            None,
        ) or next(
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


def _odd_even_difference_problem(record: StackRunRecord) -> str | None:
    """Say why a run's odd–even difference cannot decide the pattern noise.

    The measurement must be of this stack (the join in
    `stack_runs_from_measurements` makes it newer than the stack's noise row),
    in the same aperture set as the stack's noise row, and both noises must be
    measured.

    Parameters
    ----------
    record : `StackRunRecord`
        The run.

    Returns
    -------
    problem : `str` or `None`
        The reason it cannot be used, or ``None`` when it can.
    """
    half = record.odd_even_difference
    if half is None:
        return "no odd–even difference measurement was stored for this stack"
    if record.noise_measured_at is None or _as_utc(half.measured_at) < _as_utc(record.noise_measured_at):
        return "the odd–even difference measurement is older than the stack's noise measurement"
    if (
        record.noise is None
        or record.noise <= 0.0
        or record.reference_flux is None
        or record.aperture_count is None
        or record.aperture_count < 2
    ):
        return "the stack's own noise was not measured"
    if half.normalized_noise is None or half.apertures is None or half.apertures < 2:
        return "the odd–even difference noise was not measured"
    if record.aperture_identity is None or half.aperture_identity != record.aperture_identity:
        return "the odd–even difference was measured in other apertures than the stack"
    if half.full_stack_equivalent(record.effective_frame_count) is None:
        return "the odd–even difference has no variance factor or model ratio to scale it to the full stack"
    return None


def odd_even_difference_noise_inputs(record: StackRunRecord) -> tuple[float, int, float, int] | None:
    """Give the four numbers that compare a stack with its odd–even difference.

    This is the one place that decides whether the odd–even difference is
    usable (`_odd_even_difference_problem`) and builds the full-stack
    equivalent of its noise (`OddEvenDifferenceRecord.full_stack_equivalent`).
    The fixed-pattern noise and the loss account both read it, so they use the
    same record of the same stack.

    Parameters
    ----------
    record : `StackRunRecord`
        The run.

    Returns
    -------
    inputs : `tuple` [`float`, `int`, `float`, `int`] or `None`
        The stack's measured noise, its apertures, the random noise of the
        full stack (all in stack units) and the difference's apertures, or
        ``None`` when the odd–even difference cannot be used.
    """
    half = record.odd_even_difference
    if half is None or _odd_even_difference_problem(record) is not None:
        return None
    equivalent = half.full_stack_equivalent(record.effective_frame_count)
    if (
        equivalent is None
        or record.noise is None
        or record.reference_flux is None
        or record.aperture_count is None
        or half.apertures is None
    ):
        return None
    return record.noise, record.aperture_count, equivalent * record.reference_flux, half.apertures


def single_stack_fixed_pattern_noise(
    record: StackRunRecord, model_error: NoiseModelError | None = None
) -> StackFixedPatternNoise | None:
    """Find a run's fixed-pattern noise from its own measured noise.

    When the run has a usable odd–even difference measurement
    (`_odd_even_difference_problem` says none is missing), the random part is
    that direct measurement, scaled to the full stack
    (`OddEvenDifferenceRecord.full_stack_equivalent`), and the test counts
    measurement errors only
    (`measure_stack_fixed_pattern_noise_from_odd_even_difference`). The two
    noises are compared as normalized noises, so the stack's brightness scale
    cancels, and are put back in the stack's units with the stack's reference
    flux. Otherwise the fixed-pattern noise falls back to the noise model: the
    expected noise is version 2 of
    ``stacking.expected_empty_aperture_noise_stack_unit``, which includes
    Siril's normalization scale (plan decision D9), and the test counts the
    model's own error too (`measure_stack_fixed_pattern_noise`).

    Parameters
    ----------
    record : `StackRunRecord`
        The run.
    model_error : `NoiseModelError`, optional
        The noise model's own relative error, used only on the model basis.
        The stand-in value when omitted.

    Returns
    -------
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The fixed-pattern noise result (found, possible or neither), or
        ``None`` when neither basis can be used.
    """
    problem = _odd_even_difference_problem(record)
    half = record.odd_even_difference
    inputs = odd_even_difference_noise_inputs(record)
    if inputs is not None:
        return measure_stack_fixed_pattern_noise_from_odd_even_difference(*inputs)
    model_fixed_pattern = _model_single_stack_fixed_pattern_noise(record, model_error)
    if model_fixed_pattern is not None and half is not None and problem is not None:
        return model_fixed_pattern.model_copy(
            update={
                "reason": f"{model_fixed_pattern.reason} The odd–even difference was not used: {problem}."
            }
        )
    return model_fixed_pattern


def _model_single_stack_fixed_pattern_noise(
    record: StackRunRecord, model_error: NoiseModelError | None = None
) -> StackFixedPatternNoise | None:
    """Find a run's fixed-pattern noise against the noise model's prediction.

    Parameters
    ----------
    record : `StackRunRecord`
        The run.
    model_error : `NoiseModelError`, optional
        The noise model's own relative error.

    Returns
    -------
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The fixed-pattern noise result (found or not), or ``None`` when the run
        lacks a measured noise, an expected noise or 2 apertures.
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
    return measure_stack_fixed_pattern_noise(
        record.noise, record.expected_noise, record.aperture_count, model_error
    )


def fixed_pattern_noise_for_runs(
    runs: Sequence[StackRunRecord],
    model_error: NoiseModelError | None = None,
    history: Mapping[SetupKey, Mapping[str, float]] | None = None,
    target_id: str | None = None,
) -> StackFixedPatternNoise | None:
    """Choose the fixed-pattern noise of a target's newest stack.

    The one-stack fixed-pattern noise of the newest run
    (`single_stack_fixed_pattern_noise`) and the fit over the runs that share
    its aperture set (`fit_fixed_pattern_noise`) are reconciled by
    `choose_fixed_pattern_noise`. A fit never mixes aperture sets: the
    normalized noise of two sets is not on one scale.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs, up to and including the stack being
        extended.
    model_error : `NoiseModelError`, optional
        The noise model's own relative error for the target's capture setting
        group (`noise_model_error_for_setting`), used when the single-stack
        test is on the model basis.
    history : `Mapping`, optional
        The fixed-pattern share of each target by setup
        (`fixed_pattern_shares_by_setup`). Used only when the target has no
        measured fixed-pattern noise of its own (decision D23).
    target_id : `str`, optional
        The target, so its own share is left out of its borrowed median.

    Returns
    -------
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The chosen fixed_pattern, or ``None`` when neither estimate can be
        made and the setup has no history of `MINIMUM_HISTORY_TARGETS` other
        targets.
    """
    ordered = sorted(runs, key=lambda item: (item.measured_at, item.run_id))
    if not ordered:
        return None
    newest = ordered[-1]
    same_set = [record for record in ordered if record.aperture_identity == newest.aperture_identity]
    steps = noise_steps_from_runs(same_set)
    fit = fit_fixed_pattern_noise(steps) if steps else None
    expected_variance = steps[-1].expected_variance if steps and steps[-1].label == newest.run_id else None
    chosen = choose_fixed_pattern_noise(
        single_stack_fixed_pattern_noise(newest, model_error), fit, expected_variance
    )
    if chosen is None and history:
        return history_fixed_pattern_noise(history, newest, target_id)
    return chosen


SetupKey = tuple[str | None, str | None, tuple[FileIdentity, ...]]
"""What a fixed-pattern history is keyed by: the camera, the optic and the set
of flat masters the stack used (decision D23). A new flat master changes the
key, so a new flat set starts with no history."""


@dataclass(frozen=True)
class NoiseHistory:
    """What the library's stacks say about noise, read once for all targets.

    Parameters
    ----------
    model_errors : `dict`
        The noise model's own error by capture setting group
        (`noise_model_errors_by_setting_group`).
    fixed_pattern_shares : `dict`
        Each target's measured fixed-pattern share by setup
        (`fixed_pattern_shares_by_setup`).
    """

    model_errors: Mapping[Any, NoiseModelError] = field(default_factory=dict)
    fixed_pattern_shares: Mapping[SetupKey, Mapping[str, float]] = field(default_factory=dict)


def fixed_pattern_setup_key(calibration: CalibrationSummary | None) -> SetupKey | None:
    """Give the setup a run's fixed-pattern history belongs to.

    Parameters
    ----------
    calibration : `CalibrationSummary` or `None`
        The calibration record of the run
        (`attach_calibration_identities`).

    Returns
    -------
    key : `tuple` or `None`
        ``(camera, optic, flat masters)`` with the masters as the store names
        them (path, size, modification time), or ``None`` when the run has no
        calibration record.
    """
    if calibration is None:
        return None
    return (calibration.camera, calibration.optic, calibration.flat_masters)


def fixed_pattern_share_of_run(record: StackRunRecord) -> float | None:
    """Give a run's measured fixed-pattern share of its noise variance.

    The share is ``1 - random^2 / measured^2``, from the run's odd–even
    difference (`odd_even_difference_noise_inputs`), the measurement that does
    not lean on the noise model. A value below 0 is taken as 0: a stack cannot
    have less than no fixed pattern. A run with no usable odd–even difference
    has no share.

    Parameters
    ----------
    record : `StackRunRecord`
        The run.

    Returns
    -------
    share : `float` or `None`
        The share, from 0 to 1.
    """
    inputs = odd_even_difference_noise_inputs(record)
    if inputs is None:
        return None
    measured, _measured_apertures, random_noise, _random_apertures = inputs
    return float(min(1.0, max(0.0, 1.0 - (random_noise / measured) ** 2)))


def fixed_pattern_shares_by_setup(
    measurements: Iterable[QualityMeasurement], identities: Iterable[Any]
) -> dict[SetupKey, dict[str, float]]:
    """Collect every target's measured fixed-pattern share by setup.

    The caller makes the one library-wide read of stack rows that
    `noise_model_errors_by_setting_group` also uses, and one read of the
    calibration records; this function makes no queries. Each target gives at
    most one share to a setup: its newest run that has one.

    Parameters
    ----------
    measurements : `Iterable` [`QualityMeasurement`]
        Stack-level rows of every target, with every run kept.
    identities : `Iterable` [`Any`]
        The stored calibration records of every run
        (``QualityRecords.calibration_identities()``).

    Returns
    -------
    shares : `dict`
        For each setup key, the share of each target by target id.
    """
    rows = list(measurements)
    owner_by_stack: dict[str, str] = {}
    for row in rows:
        subject = row.subject
        if subject.level == SubjectLevel.STACK and subject.target_id is not None:
            owner_by_stack.setdefault(subject.subject_id, subject.target_id)
    runs = attach_calibration_identities(stack_runs_from_measurements(rows), identities)
    newest: dict[tuple[SetupKey, str], tuple[datetime, float]] = {}
    for run in runs:
        target_id = owner_by_stack.get(run.stack_subject_id)
        key = fixed_pattern_setup_key(run.calibration)
        share = fixed_pattern_share_of_run(run)
        if target_id is None or key is None or share is None:
            continue
        stamp = _as_utc(run.measured_at)
        if (key, target_id) not in newest or stamp > newest[key, target_id][0]:
            newest[key, target_id] = (stamp, share)
    shares: dict[SetupKey, dict[str, float]] = defaultdict(dict)
    for (key, target_id), (_stamp, share) in newest.items():
        shares[key][target_id] = share
    return dict(shares)


def history_fixed_pattern_noise(
    history: Mapping[SetupKey, Mapping[str, float]], run: StackRunRecord, target_id: str | None = None
) -> StackFixedPatternNoise | None:
    """Start a target's fixed-pattern noise from other targets of its setup.

    Used only for a target with no fixed-pattern noise of its own. The share is
    the median of the other targets' shares, taken only from at least
    `MINIMUM_HISTORY_TARGETS` of them. The result is marked (`basis`,
    `history_target_count`) so every statement that uses it says the noise is
    assumed, not measured on this target.

    Parameters
    ----------
    history : `Mapping`
        The shares by setup (`fixed_pattern_shares_by_setup`).
    run : `StackRunRecord`
        The target's newest library run, with its calibration attached.
    target_id : `str`, optional
        The target, whose own share (if any) is left out.

    Returns
    -------
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The assumed fixed-pattern noise, or ``None`` when the run has no
        setup key or fewer than `MINIMUM_HISTORY_TARGETS` other targets share
        its setup. A median share of 0 gives a result with nothing detected.
    """
    key = fixed_pattern_setup_key(run.calibration)
    if key is None:
        return None
    others = [share for other, share in history.get(key, {}).items() if other != target_id]
    if len(others) < MINIMUM_HISTORY_TARGETS:
        return None
    median_share = float(np.median(others))
    common = {
        "source": "setup_history",
        "basis": FIXED_PATTERN_BASIS_SETUP_HISTORY,
        "history_target_count": len(others),
        "normalized_units": False,
    }
    words = (
        f"This assumes fixed-pattern noise like other targets of this setup (median of {len(others)} "
        "targets), not measured on this one"
    )
    if median_share <= 0.0:
        return StackFixedPatternNoise(
            **common,
            fixed_pattern_variance_share=0.0,
            reason=f"{words}: the median share of the other targets is 0, so none is assumed.",
        )
    share = min(median_share, 0.99)
    return StackFixedPatternNoise(
        **common,
        fixed_pattern_noise_detected=True,
        fixed_pattern_to_random_variance_ratio=share / (1.0 - share),
        fixed_pattern_variance_share=share,
        reason=f"{words}: the median fixed-pattern share of their noise variance is {100 * share:.0f}%.",
    )


def fixed_pattern_account_for_run(
    record: StackRunRecord | None,
    fixed_pattern: StackFixedPatternNoise | None,
    *,
    load_record: Callable[[str], dict | None] = load_odd_even_aperture_record,
) -> FixedPatternAccount | None:
    """Say where a run's fixed-pattern noise is and what explains it.

    The shape tests (decision D21) run only for a fixed-pattern noise that was
    found, never for one that is merely possible, because a pattern that is not
    proven has no shape to name. They read the per-aperture record the
    odd–even difference job wrote beside the stack. The calibration account
    (decision D22) runs for a found or possible fixed-pattern noise measured
    on this stack in stack units.

    Parameters
    ----------
    record : `StackRunRecord` or `None`
        The run whose fixed-pattern noise was measured.
    fixed_pattern : `StackFixedPatternNoise` or `None`
        Its fixed-pattern noise (`fixed_pattern_noise_for_runs`).
    load_record : `Callable`, optional
        Reads a per-aperture record file; the library's loader by default.

    Returns
    -------
    account : `FixedPatternAccount` or `None`
        The location and the calibration breakdown, each only when it could be
        made; ``None`` when neither could.
    """
    if record is None or fixed_pattern is None:
        return None
    location = None
    half = record.odd_even_difference
    if fixed_pattern.fixed_pattern_noise_detected and half is not None and half.aperture_record_path:
        apertures = apertures_from_record(load_record(half.aperture_record_path))
        if apertures or half.line_scatters is not None:
            location = locate_fixed_pattern(apertures, half.line_scatters)
    calibration = None
    if (
        fixed_pattern.source == "single_stack"
        and not fixed_pattern.normalized_units
        and fixed_pattern.fixed_pattern_variance is not None
        and fixed_pattern.fixed_pattern_variance_error is not None
    ):
        calibration = explain_with_calibration_masters(
            fixed_pattern.fixed_pattern_variance,
            fixed_pattern.fixed_pattern_variance_error,
            record.flat_noise,
            record.master_dark_noise,
        )
    if location is None and calibration is None:
        return None
    return FixedPatternAccount(location=location, calibration=calibration)


def noise_model_setting_key(
    setting: CaptureSetting,
) -> tuple[str, str | None, int | None, int | None, int | None]:
    """Give the capture setting group a noise model error is measured for.

    The group is the camera, the optic (telescope or lens), the gain setting,
    the offset and the binning. These fix the camera's read noise and gain, the
    sensor scale and the flat-field behavior, which is what the noise model's
    error depends on. The filter and the exposure are left out on purpose: the
    model already takes the sky rate and the exposure into account, and
    including them would split the library into groups too small to reach
    `MINIMUM_MODEL_ERROR_TARGETS`.

    Parameters
    ----------
    setting : `CaptureSetting`
        The capture setting of a stack or frame.

    Returns
    -------
    key : `tuple`
        ``(camera, telescope, gain_setting, offset, binning)``.
    """
    return (setting.camera, setting.telescope, setting.gain_setting, setting.offset, setting.binning)


def noise_model_error_sample(record: StackRunRecord, target_id: str) -> NoiseModelErrorSample | None:
    """Turn one run into a check of the noise model.

    Parameters
    ----------
    record : `StackRunRecord`
        The run, with its odd–even difference attached.
    target_id : `str`
        The target the stack belongs to.

    Returns
    -------
    sample : `NoiseModelErrorSample` or `None`
        The full-stack random noise from the odd–even difference
        (`odd_even_difference_noise_inputs`, the record the fixed-pattern
        noise reads) against the run's predicted noise, in stack units; or
        ``None`` when the odd–even difference is unusable or the run has no
        predicted noise.
    """
    inputs = odd_even_difference_noise_inputs(record)
    if inputs is None or record.expected_noise is None or record.expected_noise <= 0.0:
        return None
    _measured, _measured_apertures, random_noise, random_apertures = inputs
    return NoiseModelErrorSample.from_noises(target_id, random_noise, record.expected_noise, random_apertures)


def noise_model_errors_by_setting_group(
    measurements: Iterable[QualityMeasurement], settings: Mapping[int, CaptureSetting]
) -> dict[tuple[str, str | None, int | None, int | None, int | None], NoiseModelError]:
    """Measure the noise model's error for every capture setting group.

    The caller makes one library-wide read of stack-level rows (every run kept,
    library stacks only) and one read of the capture settings; this function
    makes no queries. Each stack gives one sample, from its newest run that has
    a usable odd–even difference, and each target gives at most one sample to a
    group (its newest). A group's samples go through
    `measure_noise_model_error`.

    Parameters
    ----------
    measurements : `Iterable` [`QualityMeasurement`]
        Stack-level rows of every target, with every run kept.
    settings : `Mapping` [`int`, `CaptureSetting`]
        The capture settings the rows point at, by id.

    Returns
    -------
    errors : `dict`
        A `NoiseModelError` for each setting group (`noise_model_setting_key`)
        that has at least one sample. A group not in the result has none; look
        it up with `noise_model_error_for_setting`.
    """
    rows = list(measurements)
    owner_by_stack: dict[str, tuple[str, int]] = {}
    for row in rows:
        subject = row.subject
        if (
            subject.level == SubjectLevel.STACK
            and subject.target_id is not None
            and subject.capture_setting_id in settings
        ):
            owner_by_stack.setdefault(subject.subject_id, (subject.target_id, subject.capture_setting_id))
    newest: dict[tuple[Any, ...], tuple[datetime, NoiseModelErrorSample]] = {}
    for run in stack_runs_from_measurements(rows):
        owner = owner_by_stack.get(run.stack_subject_id)
        if owner is None:
            continue
        target_id, setting_id = owner
        sample = noise_model_error_sample(run, target_id)
        if sample is None:
            continue
        key = (noise_model_setting_key(settings[setting_id]), target_id)
        stamp = _as_utc(run.measured_at)
        if key not in newest or stamp > newest[key][0]:
            newest[key] = (stamp, sample)
    by_group: dict[
        tuple[str, str | None, int | None, int | None, int | None], list[NoiseModelErrorSample]
    ] = defaultdict(list)
    for (group, _target_id), (_stamp, sample) in newest.items():
        by_group[group].append(sample)
    return {group: measure_noise_model_error(samples) for group, samples in by_group.items()}


def noise_model_error_for_setting(
    errors: Mapping[tuple[str, str | None, int | None, int | None, int | None], NoiseModelError] | None,
    setting: CaptureSetting | None,
) -> NoiseModelError:
    """Look up the noise model's error for a capture setting.

    Parameters
    ----------
    errors : `Mapping` or `None`
        The result of `noise_model_errors_by_setting_group`.
    setting : `CaptureSetting` or `None`
        The capture setting.

    Returns
    -------
    model_error : `NoiseModelError`
        The group's error, or the stand-in value (zero targets) when the group
        has no sample or no errors were read.
    """
    if errors is None or setting is None:
        return NoiseModelError()
    return errors.get(noise_model_setting_key(setting), NoiseModelError())


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
    fixed_pattern: StackFixedPatternNoise | None,
    electrons_per_adu: float | None,
) -> _PairForecast:
    """Forecast a pair of stacks that were both combined from exposure groups.

    The planned forecast places the new frames with the stacker's own rule
    (`plan_group_stack`); the forecast given the frames taken uses the
    recorded weights. Both carry the earlier stack's fixed_pattern.

    Parameters
    ----------
    previous, current : `StackRunRecord`
        The earlier and the later run.
    previous_frames, current_frames : `Sequence` [`StackFrame`]
        Their member frames.
    new_frames : `Sequence` [`StackFrame`]
        The frames only the later stack holds.
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The earlier stack's fixed-pattern noise.
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
    planned_ratio = noise_ratio_with_fixed_pattern_noise(before, plan.forecast, fixed_pattern)
    given_ratio = noise_ratio_with_fixed_pattern_noise(given_before, given_after, fixed_pattern)
    if planned_ratio is None or given_ratio is None:
        return _PairForecast(mode_name=mode_name, reason="The group model could not be evaluated.")
    return _PairForecast(planned=planned_ratio - 1.0, given=given_ratio - 1.0, mode_name=mode_name)


def _flat_forecast(
    previous: StackRunRecord,
    current: StackRunRecord,
    previous_frames: Sequence[StackFrame],
    current_frames: Sequence[StackFrame],
    new_frames: Sequence[StackFrame],
    fixed_pattern: StackFixedPatternNoise | None,
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
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The earlier stack's fixed-pattern noise.
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
    planned_ratio = noise_ratio_with_fixed_pattern_noise(before, planned_after, fixed_pattern)
    given_ratio = noise_ratio_with_fixed_pattern_noise(given_before, given_after, fixed_pattern)
    if planned_ratio is None or given_ratio is None:
        return _PairForecast(mode_name=mode_name, reason="The forward model could not be evaluated.")
    return _PairForecast(
        planned=planned_ratio - 1.0,
        given=given_ratio - 1.0,
        mode_name=mode_name,
        equal_weights=current_mode.mode == WeightingMode.EQUAL,
    )


def _fixed_pattern_sentence(fixed_pattern: StackFixedPatternNoise | None) -> str:
    """Say in a sentence what the forecast assumed about fixed-pattern noise.

    Parameters
    ----------
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The earlier stack's fixed_pattern.

    Returns
    -------
    text : `str`
        A sentence naming the fixed-pattern noise, or saying none was found or
        measured.
    """
    if fixed_pattern is None:
        return "No fixed-pattern noise could be measured for the earlier stack."
    tested = (
        "tested against the random noise measured from the odd and even half stacks"
        if fixed_pattern.basis == FIXED_PATTERN_BASIS_ODD_EVEN_DIFFERENCE
        else "tested against the noise model"
    )
    if fixed_pattern.possible:
        return (
            "The earlier stack showed possible fixed-pattern noise "
            f"({fixed_pattern.sigmas or 0.0:.1f} standard errors, not proven; {tested}); "
            "the forecast does not include it."
        )
    if not fixed_pattern.fixed_pattern_noise_detected:
        return f"The earlier stack showed no fixed-pattern noise ({tested})."
    share = 100 * (fixed_pattern.fixed_pattern_variance_share or 0.0)
    return (
        f"The forecast already includes fixed-pattern noise that makes up {share:.0f}% of the earlier "
        f"stack's noise variance ({fixed_pattern.basis_text})."
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
        The runs up to and including the earlier run, for the fixed-pattern
        noise fit. The earlier run alone is used when this is left out.
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
    if previous.is_earlier_nights_subset:
        held = (
            previous.frames_stacked if previous.frames_stacked is not None else len(previous.member_frame_ids)
        )
        if held < MINIMUM_EARLIER_NIGHTS_FRAMES:
            return _not_checkable(
                current,
                f"The earlier-nights stack holds {held} frames, fewer than the "
                f"{MINIMUM_EARLIER_NIGHTS_FRAMES} a check needs.",
                mode_name,
                evidence,
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
    fixed_pattern = fixed_pattern_noise_for_runs(list(history) if history else [previous])
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
            previous, current, previous_frames, current_frames, new_frames, fixed_pattern, electrons_per_adu
        )
        if current_is_group
        else _flat_forecast(
            previous,
            current,
            previous_frames,
            current_frames,
            new_frames,
            fixed_pattern,
            configured_stack_weight,
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
            f"{_fixed_pattern_sentence(fixed_pattern)}"
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
        The runs up to and including the earlier run, for the fixed-pattern
        noise fit.
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
    # subset check, so a real disagreement withdraws the status that a subset
    # check gave at the same stack.
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


def recommended_additional_frames_status_from_checks(
    checks: Iterable[ForecastCheck],
) -> RecommendedAdditionalFramesStatus:
    """Say whether the recommended additional frames may be shown, and why.

    The checks are read in the order `forecast_check` returns them (oldest
    first; at one stack a subset check comes before a real one).

    * A check of two library stacks that agreed (``as_forecast``) makes the
      status ``confirmed``, and it stays so. This is the rule before the
      earlier-nights check existed, and it is the only way to confirm the
      count for a target with one observing night.
    * A check of two library stacks that did not agree sets the status to
      ``not_checked``, unless it is already confirmed.
    * A check from an earlier-nights subset that agreed sets a ``not_checked``
      status to ``provisional``. One that did not agree sets a ``provisional``
      status back to ``not_checked``.
    * A check that could not be made changes nothing.

    Parameters
    ----------
    checks : `Iterable` [`ForecastCheck`]
        The target's forecast checks.

    Returns
    -------
    frames_status : `RecommendedAdditionalFramesStatus`
        ``not_checked``, ``provisional`` or ``confirmed``.
    """
    frames_status = RecommendedAdditionalFramesStatus.NOT_CHECKED
    for check in checks:
        if check.outcome == ForecastOutcome.NOT_CHECKABLE:
            continue
        agreed = check.outcome == ForecastOutcome.AS_FORECAST
        if check.basis == ForecastBasis.LATER_STACK:
            if agreed:
                frames_status = RecommendedAdditionalFramesStatus.CONFIRMED
            elif frames_status != RecommendedAdditionalFramesStatus.CONFIRMED:
                frames_status = RecommendedAdditionalFramesStatus.NOT_CHECKED
        elif agreed:
            if frames_status == RecommendedAdditionalFramesStatus.NOT_CHECKED:
                frames_status = RecommendedAdditionalFramesStatus.PROVISIONAL
        elif frames_status == RecommendedAdditionalFramesStatus.PROVISIONAL:
            frames_status = RecommendedAdditionalFramesStatus.NOT_CHECKED
    return frames_status


def noise_steps_from_runs(runs: Sequence[StackRunRecord]) -> list[NoiseStep]:
    """Turn the run history into the steps of the fixed-pattern noise fit.

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

    The fixed-pattern noise is the one `fixed_pattern_noise_for_runs` chooses:
    the fit over two or more stacks, or the one-stack fixed-pattern noise of
    the newest run. Doubling the data then halves the random variance and
    leaves the fixed-pattern noise, so the noise falls from ``sqrt(1 + f)`` to
    ``sqrt(0.5 + f)`` (in units of today's random noise), with ``f`` the
    fixed-pattern noise's share of the random variance.

    Parameters
    ----------
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs.

    Returns
    -------
    signal : `StopSignal`
        ``stopped`` is true only when fixed-pattern noise was found and
        doubling the data would cut the noise by under 10%. It is ``False``
        when a fit over two or more stacks found no fixed-pattern noise, and
        ``None`` (no signal) when the history is too short: a single stack with
        no fixed-pattern noise is not enough to say.
    """
    ordered = sorted(runs, key=lambda item: (item.measured_at, item.run_id))
    newest_identity = ordered[-1].aperture_identity if ordered else None
    steps = noise_steps_from_runs([
        record for record in ordered if record.aperture_identity == newest_identity
    ])
    if not steps:
        return StopSignal(reason="No stack run has both a measured and an expected noise.")
    fixed_pattern = fixed_pattern_noise_for_runs(ordered)
    if (
        fixed_pattern is not None
        and fixed_pattern.fixed_pattern_noise_detected
        and fixed_pattern.fixed_pattern_to_random_variance_ratio is not None
    ):
        ratio = fixed_pattern.fixed_pattern_to_random_variance_ratio
        gain = 1.0 - math.sqrt((0.5 + ratio) / (1.0 + ratio))
        stopped = gain < MINIMUM_GAIN_FRACTION
        return StopSignal(
            stopped=stopped,
            predicted_gain_fraction=gain,
            reason=(
                f"Doubling the data would cut the noise by {100 * gain:.0f} percent"
                + (
                    ", below the limit; more frames will not help until the fixed-pattern noise is fixed."
                    if stopped
                    else "."
                )
            ),
        )
    fit = fit_fixed_pattern_noise(steps)
    if fixed_pattern is not None and fit.steps_used >= 2 and fit.alpha is not None:
        return StopSignal(
            stopped=False, reason="No fixed-pattern noise was found, so more frames are expected to help."
        )
    return stop_signal(fit, steps[-1].expected_variance)


__all__ = [
    "AGREEMENT_TOLERANCES",
    "COOLED_CAMERA_NAME_MARKERS",
    "FORECAST_CHECK_ENTRY",
    "FORECAST_CHECK_ROW",
    "GROUP_METRICS",
    "MINIMUM_EARLIER_NIGHTS_FRAMES",
    "PINNED_STACK_METRICS",
    "CalibrationSummary",
    "NoiseHistory",
    "RecordedGroup",
    "RunGroups",
    "SetupKey",
    "StackRunRecord",
    "attach_calibration_identities",
    "calibration_summary_from_record",
    "camera_gain_for_setting",
    "fixed_pattern_account_for_run",
    "fixed_pattern_noise_for_runs",
    "fixed_pattern_setup_key",
    "fixed_pattern_share_of_run",
    "fixed_pattern_shares_by_setup",
    "forecast_check",
    "history_fixed_pattern_noise",
    "library_runs",
    "noise_model_error_for_setting",
    "noise_model_error_sample",
    "noise_model_errors_by_setting_group",
    "noise_model_setting_key",
    "noise_steps_from_runs",
    "odd_even_difference_noise_inputs",
    "recommended_additional_frames_status_from_checks",
    "single_stack_fixed_pattern_noise",
    "stack_frames_from_observations",
    "stack_groups_of_run",
    "stack_runs_from_measurements",
    "subset_pairs",
    "target_stop_signal",
]
