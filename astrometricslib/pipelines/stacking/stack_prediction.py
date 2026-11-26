"""Purpose: Say what a stack would do with its frames, without stacking.

Description: Before a person starts a stack, they want to know which frames
the stack would set aside (clouds, trailed stars, a frame off the field, a
minority gain setting) and which dark, flat and bias frames each exposure
group would be calibrated with. `predict_stack` answers both by running the
decisions the stack runs:

* the frame screening (`frame_screening.screen_frames`) in its dry-run mode,
  which makes the same decisions but moves no file and stores no row;
* the split into exposure groups (`stack_runner.split_into_stack_batches`);
* the calibration choice for each group (`batch_calibration`), the same
  functions the Siril staging code calls.

No decision is written a second time here, so the prediction and the stack
cannot disagree. Nothing is stacked, moved, stored or saved. The frames are
read (the cloud and trailing check measures each one), so a large target
takes a while.
"""

import os
from typing import Any, Literal

from astrometricslib._internal.drivers.batch_calibration import (
    choose_batch_calibration,
    choose_flat_bias,
    describe_bias_settings,
)
from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary, CalibrationSelection
from astrometricslib.models.processing_results import (
    PredictedCalibration,
    PredictedFrame,
    PredictedGroup,
    StackPrediction,
)
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.stacking.frame_screening import (
    BACKGROUND_SPLIT_REASON,
    EARLIER_QUARANTINE_REASON,
    EXPOSURE_NOT_FOLDABLE_REASON_PREFIX,
    MINORITY_GAIN_REASON,
    FrameScreening,
    screen_frames,
)
from astrometricslib.pipelines.stacking.pre_processing.camera_selection import split_frames_by_camera
from astrometricslib.pipelines.stacking.stack_runner import split_into_stack_batches

__all__ = ["predict_stack"]

_KIND_BY_REASON = {
    MINORITY_GAIN_REASON: "minority_gain",
    EARLIER_QUARANTINE_REASON: "already_set_aside",
    BACKGROUND_SPLIT_REASON: "background_split",
}


def _predicted_calibration(
    kind: str, selection: CalibrationSelection, extra_flags: list[str] | None = None
) -> PredictedCalibration:
    """Describe one calibration selection for the prediction.

    Parameters
    ----------
    kind : `str`
        ``"dark"``, ``"bias"``, ``"flat"`` or ``"flat_bias"``.
    selection : `CalibrationSelection`
        What the library chose.
    extra_flags : `list` [`str`], optional
        Notes to add to the selection's own flags.

    Returns
    -------
    calibration : `PredictedCalibration`
        The count, the folders, the settings and the flags.
    """
    folders = sorted({os.path.dirname(path) for path in selection.frames})
    settings = describe_bias_settings(selection) if selection.chosen_settings else ""
    return PredictedCalibration(
        kind=kind,
        frame_count=len(selection.frames),
        folders=folders,
        settings=settings,
        flags=[*selection.flags, *(extra_flags or [])],
        blocking_flags=list(selection.blocking_flags),
    )


def _predicted_group(group: Any, library: CalibrationLibrary) -> PredictedGroup:
    """Choose the calibration one exposure group would get.

    Parameters
    ----------
    group : `ExposureGroup`
        One batch the stack would stack on its own.
    library : `CalibrationLibrary`
        The library of calibration frames.

    Returns
    -------
    predicted : `PredictedGroup`
        The group's frames and its dark, bias, flat and flat-bias choice.
    """
    paths = sorted(frame.path for frame in group.frames)
    chosen = choose_batch_calibration(library, group.frames, paths)
    flat_bias_choice = choose_flat_bias(
        library, chosen.camera, chosen.binning, chosen.flat, len(chosen.flat.frames), chosen.bias
    )
    flat_bias = None
    if flat_bias_choice.frames:
        flat_bias = PredictedCalibration(
            kind="flat_bias",
            frame_count=len(flat_bias_choice.frames),
            folders=sorted({os.path.dirname(path) for path in flat_bias_choice.frames}),
        )
    parts = {"dark": chosen.dark, "bias": chosen.bias, "flat": chosen.flat}
    warnings = [
        *(f"{name}: {flag}" for name, part in parts.items() for flag in part.flags),
        *(f"{name} (not used): {flag}" for name, part in parts.items() for flag in part.blocking_flags),
        *(f"flat bias: {note}" for note in flat_bias_choice.notes),
    ]
    for name, part in parts.items():
        if not part.frames and not part.blocking_flags:
            warnings.append(f"{name}: no {name} frames fit these lights.")
    return PredictedGroup(
        exposure_seconds=float(group.exposure_seconds),
        night=group.night,
        frames_kept=len(group.frames),
        first_file=os.path.basename(paths[0]) if paths else "",
        last_file=os.path.basename(paths[-1]) if paths else "",
        filter_name=chosen.filter_name,
        gain=chosen.gain,
        offset=chosen.offset,
        binning=chosen.binning,
        temperature_c=chosen.temperature_c,
        dark=_predicted_calibration("dark", chosen.dark),
        bias=_predicted_calibration("bias", chosen.bias),
        flat=_predicted_calibration("flat", chosen.flat),
        flat_bias=flat_bias,
        warnings=warnings,
    )


def _predicted_frames(screening: FrameScreening) -> tuple[list[PredictedFrame], list[PredictedFrame]]:
    """List the frames the screening set aside and the ones it flagged.

    Parameters
    ----------
    screening : `FrameScreening`
        The result of the dry-run screening.

    Returns
    -------
    set_aside : `list` [`PredictedFrame`]
        The frames left out, in the order the checks left them out.
    flagged : `list` [`PredictedFrame`]
        The frames kept with a warning.
    """
    decisions = {}
    if screening.quarantine_report is not None:
        decisions = {decision.path: decision for decision in screening.quarantine_report.moved}
    set_aside = []
    for excluded in screening.excluded_frames:
        decision = decisions.get(excluded.path)
        if decision is not None:
            kind, reason = decision.kind, decision.reason
        else:
            kind, reason = _KIND_BY_REASON.get(excluded.reason, "other"), excluded.reason
            if excluded.reason.startswith(EXPOSURE_NOT_FOLDABLE_REASON_PREFIX):
                kind = "exposure_not_foldable"
        set_aside.append(
            PredictedFrame(path=excluded.path, file=os.path.basename(excluded.path), reason=reason, kind=kind)
        )
    flagged = [
        PredictedFrame(
            path=decision.path,
            file=os.path.basename(decision.path),
            reason=decision.reason,
            kind="uneven_sky",
        )
        for decision in screening.flagged_frames
    ]
    return set_aside, flagged


def list_chosen_frames(
    target: Target, chosen: list[FrameRecord], kind: Literal["imaging", "spectral"]
) -> StackPrediction:
    """Report the frame choice alone, without the frame checks.

    Parameters
    ----------
    target : `Target`
        The target to stack. It is not changed.
    chosen : `list` [`FrameRecord`]
        The frames the stack would be given.
    kind : {"imaging", "spectral"}
        Which kind of frames they are.

    Returns
    -------
    prediction : `StackPrediction`
        The count, camera and first and last file of the chosen frames. No
        frame is set aside or flagged and no calibration is chosen, because
        no check ran.
    """
    return StackPrediction(
        target_id=target.id,
        kind=kind,
        camera=next(iter(split_frames_by_camera(chosen)), None),
        frames_selected=len(chosen),
        frames_kept=len(chosen),
        first_file=os.path.basename(chosen[0].path) if chosen else "",
        last_file=os.path.basename(chosen[-1].path) if chosen else "",
        checks_run=False,
        nothing_left=not chosen,
        note="Only the frames were chosen: no frame check ran. Nothing was stacked, moved, stored or saved.",
    )


def predict_stack(
    target: Target,
    chosen: list[FrameRecord],
    kind: Literal["imaging", "spectral"],
    calibration: CalibrationLibrary,
) -> StackPrediction:
    """Say which frames a stack would set aside and what calibrates the rest.

    Parameters
    ----------
    target : `Target`
        The target to stack. It is not changed.
    chosen : `list` [`FrameRecord`]
        The frames the stack would be given, from `choose_frames_to_stack`
        or the caller. The records are copied, so they are not changed.
    kind : {"imaging", "spectral"}
        Which kind of frames they are.
    calibration : `CalibrationLibrary`
        The library of calibration frames, as the stack would use it.

    Returns
    -------
    prediction : `StackPrediction`
        The frames set aside or flagged, and each exposure group's dark,
        bias and flat choice with the library's flags.
    """
    has_spectral = any(frame_is_spectral(frame) for frame in chosen)
    screening = screen_frames(target, chosen, has_spectral, None, None, dry_run=True)
    set_aside, flagged = _predicted_frames(screening)
    report = screening.quarantine_report
    warnings = []
    for gate in screening.gate_results:
        if gate.status in (GateStatus.FAILED, GateStatus.NOT_CHECKED) and gate.detail:
            warnings.append(f"{gate.name}: {gate.detail}")
    if report is not None:
        warnings.extend(report.notes)
    batches = split_into_stack_batches(screening.frames, has_spectral) if screening.frames else []
    groups = [_predicted_group(batch, calibration) for batch in batches]
    return StackPrediction(
        target_id=target.id,
        kind=kind,
        camera=next(iter(split_frames_by_camera(chosen)), None),
        frames_selected=len(chosen),
        frames_kept=len(screening.frames),
        first_file=os.path.basename(chosen[0].path) if chosen else "",
        last_file=os.path.basename(chosen[-1].path) if chosen else "",
        set_aside=set_aside,
        flagged=flagged,
        groups=groups,
        warnings=warnings,
        gates=screening.gate_results,
        unreadable=list(report.unreadable) if report is not None else [],
        nothing_left=not screening.frames,
    )
