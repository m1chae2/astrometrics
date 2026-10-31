"""Checks the dark and bias frames a stack used, and the lights' temperatures.

A stack applies a master dark and a master bias to its lights. The frame by
frame numbers of those calibration frames are stored by the measuring job (see
`calibration_measurement_run.py`). This module reads them for the frames the
stack really used, judges them with the checks of `calibration_assessment.py`,
and turns the answers into the stack's gates:

- ``dark_level``, ``dark_noise``, ``dark_zero_pixel_fraction`` and
  ``dark_light_leak`` for the darks.
- ``bias_level``, ``bias_noise`` and ``bias_zero_pixel_fraction`` for the
  biases.
- ``dark_missing``: lights that had no dark at their exposure.
- ``light_temperature_spread``: lights whose sensor temperature moved by more
  than the dark temperature tolerance inside one exposure group.

A gate only warns. A stack never stops because of them. The number each gate
measures is stored with the gate, and none of the numbers depends on the limit
(the level, noise and light leak are written in value steps or ratios), so a
limit can change without restacking.

A frame the job has not measured yet gives a ``not_checked`` gate with the
reason. The stack never measures calibration frames itself, because that would
add half a second a frame to every stack.

The result goes into the run's diagnostics (see `store_in_diagnostics`), and
`calibration_gates` in `assess_input_quality.py` reads it from there.
"""

import logging
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from astrometricslib.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    DARK_FRAME_PATHS_KEY,
    DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
    CalibrationLibrary,
    CalibrationSet,
)
from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.quality.gate_result import (
    GateResult,
    GateStatus,
    failed_gate,
    passed_gate,
    unchecked_gate,
)
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.pipelines.shared.frame_grouping import frame_exposure_seconds
from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import calibration_subject_id
from astrometricslib.pipelines.shared.quality.metrics import CALIBRATION_FRAME_METRICS, FRAME_METRICS
from astrometricslib.pipelines.stacking.pre_processing.calibration_assessment import (
    BIAS_LEVEL_GATE_NAME,
    BIAS_NOISE_GATE_NAME,
    BIAS_ZERO_PIXEL_GATE_NAME,
    CALIBRATION_FRAME_COUNT_CHECK_NAME,
    DARK_LEVEL_GATE_NAME,
    DARK_LIGHT_LEAK_GATE_NAME,
    DARK_NOISE_GATE_NAME,
    DARK_ZERO_PIXEL_GATE_NAME,
    FrameFacts,
    SetJudgement,
    assess_dark_or_bias_frames,
    build_bias_reference,
    check_not_checked,
    frame_facts_from_rows,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CALIBRATION_COUNT_FLAGS_KEY",
    "CALIBRATION_HEALTH_GATES_KEY",
    "CALIBRATION_UNIQUE_COUNTS_KEY",
    "DARK_EXPOSURE_MATCH_SECONDS",
    "DARK_MISSING_GATE_NAME",
    "TEMPERATURE_SPREAD_GATE_NAME",
    "StackCalibrationHealth",
    "assess_stack_calibration",
    "store_in_diagnostics",
]

# The keys of the run's diagnostics that hold the result.
CALIBRATION_HEALTH_GATES_KEY = "calibration_health_gates"
CALIBRATION_COUNT_FLAGS_KEY = "calibration_count_health_flags"
CALIBRATION_UNIQUE_COUNTS_KEY = "calibration_unique_frame_counts"

# The gates that are not about one set's frames.
DARK_MISSING_GATE_NAME = "dark_missing"
TEMPERATURE_SPREAD_GATE_NAME = "light_temperature_spread"

# A dark fits a light when their exposures differ by less than this many
# seconds. It is the jitter `select_dark_frames` allows (see
# `_dark_paths_at_exposure` in `drivers/calibration_library.py`).
DARK_EXPOSURE_MATCH_SECONDS = 0.1

# Lights whose exposures round to the same 0.1 s are one batch, the batch the
# darks are chosen for.
_BATCH_EXPOSURE_DECIMALS = 1

_NOT_MEASURED_REASON = (
    "{count} of the {total} {kind} frame(s) the stack used have no stored numbers yet; run the measuring job "
    "(calibration measure_frames) and stack again"
)
_MISSING_SOURCE = "a dark at the lights' exposure within 0.1 s (a rule of how the library selects darks)"
_SPREAD_SOURCE = "dark temperature tolerance of 3 C (validation register STK-55, a design estimate)"

# The current definition version of each metric the checks read. A stored row
# of another version is not used.
_CURRENT_VERSIONS = {
    spec.name: spec.definition_version for spec in (*FRAME_METRICS, *CALIBRATION_FRAME_METRICS)
}


@dataclass
class StackCalibrationHealth:
    """What the checks found on the calibration a stack used.

    Attributes
    ----------
    gates : `list` [`GateResult`]
        The new gates. Empty when the stack's quality store was not given.
    count_flags : `list` [`str`]
        One sentence for each master that is thin or built from copies. The
        ``calibration_frame_count`` gate adds them to its own flags.
    unique_counts : `dict` [`str`, `int`]
        The number of different frames behind the dark and the bias masters,
        for a kind the stack used.
    """

    gates: list[GateResult] = field(default_factory=list)
    count_flags: list[str] = field(default_factory=list)
    unique_counts: dict[str, int] = field(default_factory=dict)


def _all_not_checked(reason: str) -> StackCalibrationHealth:
    """Build the result for a stack whose calibration cannot be looked at.

    Parameters
    ----------
    reason : `str`
        Why nothing could be checked.

    Returns
    -------
    health : `StackCalibrationHealth`
        Every gate ``not_checked`` with the reason.
    """
    names = (
        DARK_LEVEL_GATE_NAME,
        DARK_NOISE_GATE_NAME,
        DARK_ZERO_PIXEL_GATE_NAME,
        DARK_LIGHT_LEAK_GATE_NAME,
        BIAS_LEVEL_GATE_NAME,
        BIAS_NOISE_GATE_NAME,
        BIAS_ZERO_PIXEL_GATE_NAME,
        DARK_MISSING_GATE_NAME,
        TEMPERATURE_SPREAD_GATE_NAME,
    )
    return StackCalibrationHealth(gates=[check_not_checked(name, reason) for name in names])


def _frame_batches(target_frames: Sequence[Any]) -> dict[float, list[Any]]:
    """Group light frames by exposure, the way darks are chosen for them.

    Parameters
    ----------
    target_frames : `Sequence`
        The light frame records of the stack.

    Returns
    -------
    batches : `dict` [`float`, `list`]
        The frames of each exposure, rounded to 0.1 s. Frames with no exposure
        are left out.
    """
    batches: dict[float, list[Any]] = defaultdict(list)
    for frame in target_frames:
        exposure = frame_exposure_seconds(frame)
        if exposure is not None:
            batches[round(exposure, _BATCH_EXPOSURE_DECIMALS)].append(frame)
    return dict(batches)


def _dark_missing_gate(
    batches: Mapping[float, list[Any]], dark_exposures: Sequence[float], applied_dark: bool | None
) -> GateResult:
    """Judge whether every batch of lights had a dark at its exposure.

    Parameters
    ----------
    batches : `Mapping` [`float`, `list`]
        The lights by exposure.
    dark_exposures : `Sequence` [`float`]
        The exposures of the darks the stack used.
    applied_dark : `bool` or `None`
        Whether the engine applied a dark. `None` when it did not say.

    Returns
    -------
    gate : `GateResult`
        Failed when a batch has no dark within `DARK_EXPOSURE_MATCH_SECONDS`.
        The value is the number of lights in those batches; the limit is 0.
    """
    if not batches:
        return unchecked_gate(DARK_MISSING_GATE_NAME, "the lights record no exposure", _MISSING_SOURCE)
    if applied_dark is None and not dark_exposures:
        return unchecked_gate(
            DARK_MISSING_GATE_NAME,
            "the stacking run did not say which calibration it applied",
            _MISSING_SOURCE,
        )
    if applied_dark and not dark_exposures:
        return unchecked_gate(
            DARK_MISSING_GATE_NAME,
            "a dark was applied but the library no longer lists the files, so its exposure is not known",
            _MISSING_SOURCE,
        )
    without = {
        exposure: frames
        for exposure, frames in batches.items()
        if not any(abs(exposure - dark) < DARK_EXPOSURE_MATCH_SECONDS for dark in dark_exposures)
    }
    total = sum(len(frames) for frames in batches.values())
    missing = sum(len(frames) for frames in without.values())
    if not without:
        return passed_gate(DARK_MISSING_GATE_NAME, 0.0, 0.0, _MISSING_SOURCE)
    listed = ", ".join(
        f"{exposure:g} s ({len(frames)} frames)" for exposure, frames in sorted(without.items())
    )
    return failed_gate(
        DARK_MISSING_GATE_NAME,
        f"{missing} of {total} light frame(s) had no dark at their exposure: {listed}. Nothing removes their "
        "dark current and hot pixels; a bias was used instead if the library held one",
        float(missing),
        0.0,
        _MISSING_SOURCE,
    )


def _temperature_spread_gate(batches: Mapping[float, list[Any]], used_dark: bool) -> GateResult:
    """Judge whether the lights' temperature stayed within the dark tolerance.

    Parameters
    ----------
    batches : `Mapping` [`float`, `list`]
        The lights by exposure.
    used_dark : `bool`
        Whether the stack applied a dark. Without one the spread does not
        matter.

    Returns
    -------
    gate : `GateResult`
        Failed when the sensor temperature of the lights of one exposure
        spans more than `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`. The value is
        the widest span in degrees C.
    """
    if not used_dark:
        return unchecked_gate(TEMPERATURE_SPREAD_GATE_NAME, "the stack used no dark", _SPREAD_SOURCE)
    spans: dict[float, float] = {}
    for exposure, frames in batches.items():
        temperatures = [
            frame.sensor_temperature_c
            for frame in frames
            if getattr(frame, "sensor_temperature_c", None) is not None
            and math.isfinite(frame.sensor_temperature_c)
        ]
        if len(temperatures) > 1:
            spans[exposure] = max(temperatures) - min(temperatures)
    if not spans:
        return unchecked_gate(
            TEMPERATURE_SPREAD_GATE_NAME, "the lights record no sensor temperature to compare", _SPREAD_SOURCE
        )
    worst_exposure, worst = max(spans.items(), key=lambda item: item[1])
    if worst > DEFAULT_DARK_TEMPERATURE_TOLERANCE_C:
        return failed_gate(
            TEMPERATURE_SPREAD_GATE_NAME,
            f"the sensor temperature of the {worst_exposure:g} s lights spans {worst:.1f} C, more than the "
            f"{DEFAULT_DARK_TEMPERATURE_TOLERANCE_C:g} C a dark can match; the mean temperature "
            "hides the drift",
            worst,
            DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
            _SPREAD_SOURCE,
        )
    return passed_gate(
        TEMPERATURE_SPREAD_GATE_NAME, worst, DEFAULT_DARK_TEMPERATURE_TOLERANCE_C, _SPREAD_SOURCE
    )


def _merge_checks(name: str, judgements: Sequence[tuple[str, SetJudgement]]) -> GateResult:
    """Combine one check over several sets into the stack's one gate.

    Parameters
    ----------
    name : `str`
        The check's name.
    judgements : `Sequence` [`tuple`]
        A label for each set (such as its exposure) and its judgement.

    Returns
    -------
    gate : `GateResult`
        The failed check with the largest number, its sentence joined with
        those of the other failed sets; else the passed check with the largest
        number; else the first check that did not run; else a check that did
        not run because the stack used no frames of this kind.
    """
    entries = [
        (label, check) for label, judgement in judgements for check in judgement.checks if check.name == name
    ]
    failed = [(label, check) for label, check in entries if check.status is GateStatus.FAILED]
    if failed:
        worst = max(failed, key=lambda entry: entry[1].measured_value or 0.0)[1]
        detail = "; ".join(
            f"{label}: {check.detail}" if len(judgements) > 1 else check.detail for label, check in failed
        )
        return worst.model_copy(update={"detail": detail})
    passed = [check for _, check in entries if check.status is GateStatus.PASSED]
    if passed:
        return max(passed, key=lambda check: check.measured_value or 0.0)
    first = next((check for _, check in entries), None)
    return first if first is not None else check_not_checked(name, "the stack used no frames of this kind")


def _sets_of_staged_frames(
    library: CalibrationLibrary, kind: str, staged_paths: Sequence[str]
) -> list[tuple[CalibrationSet, list[str]]]:
    """Find the library sets the staged frames came from.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library.
    kind : `str`
        ``"dark"`` or ``"bias"``.
    staged_paths : `Sequence` [`str`]
        The files the run staged.

    Returns
    -------
    sets : `list` [`tuple`]
        Each set that holds a staged file, with those files.
    """
    wanted = set(staged_paths)
    found = []
    for calibration_set in library.list_calibration_sets(kind, validate_paths=False):
        used = [path for path in calibration_set.paths if path in wanted]
        if used:
            found.append((calibration_set, used))
    return found


def _read_facts(store: QualityStore, kind: str, sets: Sequence[CalibrationSet]) -> dict[str, FrameFacts]:
    """Read the stored numbers of the frames of some sets.

    Parameters
    ----------
    store : `QualityStore`
        The quality store.
    kind : `str`
        ``"dark"`` or ``"bias"``.
    sets : `Sequence` [`CalibrationSet`]
        The sets whose frames are wanted.

    Returns
    -------
    facts : `dict` [`str`, `FrameFacts`]
        By path, for the frames that have rows of the current definitions.
    """
    subject_paths = {
        calibration_subject_id(path, kind): path for calibration_set in sets for path in calibration_set.paths
    }
    rows = [
        row
        for row in store.query(level=SubjectLevel.FRAME, subject_id_prefix=f"calibration/{kind}/")
        if row.definition_version == _CURRENT_VERSIONS.get(row.metric)
    ]
    return frame_facts_from_rows(rows, subject_paths)


def assess_stack_calibration(
    diagnostics: Mapping[str, Any],
    target_frames: Sequence[Any],
    library: CalibrationLibrary | None,
    store: QualityStore | None,
    configuration: Any,
) -> StackCalibrationHealth:
    """Judge the calibration frames a stack used and the lights' temperatures.

    Parameters
    ----------
    diagnostics : `Mapping`
        What the stacking run reported. Read: the staged dark and bias files
        (``dark_frame_paths`` and ``bias_frame_paths``) and
        ``calibration_applied``.
    target_frames : `Sequence`
        The light frame records of the stack.
    library : `CalibrationLibrary` or `None`
        The calibration library, for the settings of each set. `None` leaves
        every gate ``not_checked``.
    store : `QualityStore` or `None`
        The quality store holding the stored numbers. `None` leaves the checks
        of the frames ``not_checked``.
    configuration : `AppConfiguration`
        The configuration, for the camera profiles and the minimum frame
        count.

    Returns
    -------
    health : `StackCalibrationHealth`
        The gates, the sentences for thin or copied masters, and the number
        of different frames behind each master.
    """
    if library is None:
        return _all_not_checked("the stack had no calibration library to look the frames up in")
    dark_paths = list(diagnostics.get(DARK_FRAME_PATHS_KEY) or [])
    bias_paths = list(diagnostics.get(BIAS_FRAME_PATHS_KEY) or [])
    applied = diagnostics.get("calibration_applied")
    batches = _frame_batches(target_frames)
    dark_sets = _sets_of_staged_frames(library, "dark", dark_paths)
    dark_exposures = [
        calibration_set.exposure_seconds
        for calibration_set, _ in dark_sets
        if calibration_set.exposure_seconds is not None
    ]
    health = StackCalibrationHealth()
    judgements: dict[str, list[tuple[str, SetJudgement]]] = {"dark": [], "bias": []}
    minimum_frames = configuration.get_minimum_calibration_frames()
    no_store = "the stack had no quality store to read the calibration frames' numbers from"

    if store is not None:
        bias_sets_all = library.list_calibration_sets("bias", validate_paths=False)
        facts = {
            **_read_facts(store, "dark", [item for item, _ in dark_sets]),
            **_read_facts(store, "bias", bias_sets_all),
        }
        for kind, staged in (
            ("dark", dark_sets),
            ("bias", _sets_of_staged_frames(library, "bias", bias_paths)),
        ):
            for calibration_set, used in staged:
                profile = resolve_camera_profile(calibration_set.camera, configuration)
                reference = None
                if kind == "dark":
                    reference = build_bias_reference([
                        facts[path]
                        for bias_set in bias_sets_all
                        if bias_set.has_setting_of(calibration_set)
                        for path in bias_set.paths
                        if path in facts
                    ])
                judgement = assess_dark_or_bias_frames(
                    kind,
                    used,
                    facts,
                    minimum_frames=minimum_frames,
                    profile=profile,
                    setting=(calibration_set.gain, calibration_set.offset, calibration_set.binning),
                    exposure_seconds=calibration_set.exposure_seconds,
                    reference=reference,
                )
                label = (
                    f"{calibration_set.exposure_seconds:g} s darks"
                    if kind == "dark" and calibration_set.exposure_seconds is not None
                    else f"{kind} frames"
                )
                judgements[kind].append((label, judgement))
                count_check = next(
                    check for check in judgement.checks if check.name == CALIBRATION_FRAME_COUNT_CHECK_NAME
                )
                # A set under the configured minimum by file count is already
                # flagged by the engine's own count flags.
                if count_check.status is GateStatus.FAILED and len(used) >= minimum_frames:
                    health.count_flags.append(f"{label}: {count_check.detail}")
        for kind in ("dark", "bias"):
            if judgements[kind]:
                health.unique_counts[kind] = sum(
                    judgement.unique_frame_count for _, judgement in judgements[kind]
                )

    for kind, names in (
        (
            "dark",
            (
                DARK_LEVEL_GATE_NAME,
                DARK_NOISE_GATE_NAME,
                DARK_ZERO_PIXEL_GATE_NAME,
                DARK_LIGHT_LEAK_GATE_NAME,
            ),
        ),
        ("bias", (BIAS_LEVEL_GATE_NAME, BIAS_NOISE_GATE_NAME, BIAS_ZERO_PIXEL_GATE_NAME)),
    ):
        for name in names:
            if not judgements[kind]:
                reason = no_store if store is None else f"the stack used no {kind} frames"
                health.gates.append(check_not_checked(name, reason))
                continue
            gate = _merge_checks(name, judgements[kind])
            unmeasured = sum(j.frame_count - j.measured_frame_count for _, j in judgements[kind])
            if gate.status is GateStatus.NOT_CHECKED and unmeasured:
                total = sum(j.frame_count for _, j in judgements[kind])
                gate = check_not_checked(
                    name,
                    _NOT_MEASURED_REASON.format(count=unmeasured, total=total, kind=kind),
                    gate.limit_source,
                )
            health.gates.append(gate)

    applied_dark = None if not isinstance(applied, Mapping) else applied.get("dark")
    health.gates.append(_dark_missing_gate(batches, dark_exposures, applied_dark))
    health.gates.append(_temperature_spread_gate(batches, bool(dark_exposures) or bool(applied_dark)))
    return health


def store_in_diagnostics(diagnostics: dict[str, Any], health: StackCalibrationHealth) -> None:
    """Put the result where `calibration_gates` reads it.

    Parameters
    ----------
    diagnostics : `dict`
        The run's diagnostics, changed in place.
    health : `StackCalibrationHealth`
        What `assess_stack_calibration` returned.
    """
    diagnostics[CALIBRATION_HEALTH_GATES_KEY] = [gate.model_dump() for gate in health.gates]
    diagnostics[CALIBRATION_COUNT_FLAGS_KEY] = list(health.count_flags)
    diagnostics[CALIBRATION_UNIQUE_COUNTS_KEY] = dict(health.unique_counts)
