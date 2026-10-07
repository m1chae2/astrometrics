"""Purpose: Choose a target's frames for a stack, then run the stacking stage.

Description: `ProcessingPipelines.stack` is the one public way to stack. It
checks its arguments and hands the work to the two functions here:

* `choose_frames_to_stack` picks the light frames. Imaging frames and
  spectroscopy frames are never mixed, and frames from two cameras are
  never stacked together. The caller may narrow the choice by filter, by a
  range of file names, by a time window and by camera, or pass the exact
  frames to use.
* `run_stack` runs the stacking stage (`stage.stack_frames`) on the chosen
  frames as a tracked job, holds a stacking slot while Siril runs, and
  saves the target when a stack was made.

Both raise an error from `astrometricslib.foundation.errors` when the request
cannot be met. Neither returns an error inside its result.
"""

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal

from astrometricslib.drivers.job_logging import registered_job
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError, ProcessingError
from astrometricslib.foundation.logging import get_log_context
from astrometricslib.models.processing_results import StackResult
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.quality.frame_selection import FrameSelection, select_library_frames
from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings
from astrometricslib.pipelines.stacking.pre_processing.camera_selection import (
    choose_camera_frames,
    describe_cameras,
    split_frames_by_camera,
)

__all__ = ["SirilOptions", "choose_frames_to_stack", "run_stack"]

StackKind = Literal["imaging", "spectral"]

# Words in a file name that mark a product of an earlier stack, not a frame.
_STACK_PRODUCT_WORDS = ("_stacked", "starless", "starmask")


@dataclass(frozen=True)
class SirilOptions:
    """Siril settings for one stack that replace the configured ones.

    Each value left as `None` uses the app's configured setting.

    Attributes
    ----------
    rejection_sigma : `tuple` [`float`, `float`] or `None`
        Low and high sigma-clipping bounds for pixel rejection.
    filter_wfwhm : `str` or `None`
        Siril's weighted-FWHM frame filter, such as ``"90%"``.
    filter_round : `str` or `None`
        Siril's star-roundness frame filter.
    stack_weight : `str` or `None`
        How Siril weights each frame when it combines them.
    generate_rejmap : `bool` or `None`
        Whether to also write the rejection maps.
    output_file : `str` or `None`
        Where to write the stack instead of the usual place.
    log_file : `str` or `None`
        Where to write Siril's log.
    """

    rejection_sigma: tuple[float, float] | None = None
    filter_wfwhm: str | None = None
    filter_round: str | None = None
    stack_weight: str | None = None
    generate_rejmap: bool | None = None
    output_file: str | None = None
    log_file: str | None = None


def choose_frames_to_stack(
    target: Target,
    kind: StackKind,
    selection: FrameSelection,
    camera_id: str | None,
) -> list[FrameRecord]:
    """Pick the light frames of one kind that a selection names.

    Parameters
    ----------
    target : `Target`
        The target whose frames to choose from.
    kind : {"imaging", "spectral"}
        Which frames: imaging or spectroscopy. The two are never mixed.
    selection : `FrameSelection`
        The filter, file range and time window to keep.
    camera_id : `str` or `None`
        Keep only this camera's frames (a name or part of one). Applied
        before the other choices.

    Returns
    -------
    chosen : `list` [`FrameRecord`]
        The frames, in file-name order, all from one camera.

    Raises
    ------
    NotFoundError
        If no frame matches, or no camera matches ``camera_id``.
    """
    spectral = kind == "spectral"
    lights = [
        frame
        for frame in target.frames
        if str(frame.role).upper() == "LIGHT"
        and not any(word in frame.path.lower() for word in _STACK_PRODUCT_WORDS)
        and frame_is_spectral(frame) == spectral
    ]
    if camera_id:
        lights, camera_problem = choose_camera_frames(lights, camera_id)
        if camera_problem:
            raise NotFoundError(camera_problem, details={"target": target.id, "camera_id": camera_id})
    chosen = select_library_frames(lights, selection)
    if not chosen:
        raise NotFoundError(
            f"No {kind} light frames of {target.id!r} match that selection.", details={"target": target.id}
        )
    check_one_camera(target, chosen)
    return chosen


def check_one_camera(target: Target, frames: list[FrameRecord]) -> None:
    """Refuse frames that come from more than one camera.

    Parameters
    ----------
    target : `Target`
        The target the frames belong to, for the message.
    frames : `list` [`FrameRecord`]
        The frames about to be stacked.

    Raises
    ------
    InvalidArgumentError
        If the frames come from more than one camera. The message names
        each camera and how many frames it took.
    """
    cameras = split_frames_by_camera(frames)
    if len(cameras) > 1:
        raise InvalidArgumentError(
            f"The selected frames of {target.id!r} come from more than one camera: "
            f"{describe_cameras(cameras)}. Frames from different cameras are never stacked together. "
            "Pass camera_id to choose one.",
            details={"target": target.id, "cameras": {name: len(group) for name, group in cameras.items()}},
        )


def run_stack(
    target: Target,
    chosen: list[FrameRecord],
    *,
    kind: StackKind,
    plan_only: bool,
    force: bool,
    preview_settings: PreviewSettings,
    siril: SirilOptions,
    register_job: bool,
    stacking_slot: Callable[[], AbstractContextManager],
    save_targets: Callable[[], None],
) -> StackResult:
    """Stack the chosen frames, or only report the plan.

    Parameters
    ----------
    target : `Target`
        The target to stack. The stage records the stack, its quality
        summary and its preview on it.
    chosen : `list` [`FrameRecord`]
        The frames to stack, from `choose_frames_to_stack`.
    kind : {"imaging", "spectral"}
        Which kind of frames they are.
    plan_only : `bool`
        Only report the frames. Nothing is stacked or saved.
    force : `bool`
        Rebuild even if nothing changed since the stack on disk was made.
    preview_settings : `PreviewSettings`
        This run's preview choices.
    siril : `SirilOptions`
        This run's Siril settings.
    register_job : `bool`
        Record the run in the job list. When `False`, a job the caller
        already runs (named in the log context) still gets the provenance.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    save_targets : `Callable`
        Saves the target catalog.

    Returns
    -------
    result : `StackResult`
        The frames chosen and, unless ``plan_only``, the stack made.

    Raises
    ------
    ProcessingError
        If the stage finished without making a stack.
    """
    camera = next(iter(split_frames_by_camera(chosen)), None)
    result = StackResult(
        target_id=target.id,
        kind=kind,
        camera=camera,
        frames_selected=len(chosen),
        first_file=os.path.basename(chosen[0].path),
        last_file=os.path.basename(chosen[-1].path),
    )
    if plan_only:
        return result.model_copy(update={"plan_only": True, "note": "Nothing was stacked or saved."})

    from astrometricslib.pipelines.stacking import stage as stacking_stage

    with (
        stacking_slot(),
        registered_job(
            enabled=register_job,
            job_type="stacking",
            target_id=target.id,
            log_file=siril.log_file,
            completed_message=f"[{target.id}] Stacking completed successfully.",
            failed_message=f"[{target.id}] Stacking failed.",
        ) as job,
    ):
        stacked_path = stacking_stage.stack_frames(
            target,
            log_file=siril.log_file,
            frames_to_stack=chosen,
            rejection_sigma=siril.rejection_sigma,
            filter_wfwhm=siril.filter_wfwhm,
            filter_round=siril.filter_round,
            stack_weight=siril.stack_weight,
            generate_rejmap=siril.generate_rejmap,
            output_file=siril.output_file,
            job_id=job.job_id or get_log_context().get("job_id"),
            force=force,
            preview_settings=preview_settings,
        )
        # Stacking can finish without raising and still make no image, so
        # the outcome is decided here, not left to "no exception means done".
        job.mark("completed" if stacked_path else "failed", 100)
    if not stacked_path:
        raise ProcessingError(
            f"Stacking {target.id!r} finished without making a stack. See the job log.",
            details={"target": target.id},
        )
    save_targets()
    stacking = target.spectral_stacking if kind == "spectral" else target.stacking
    quality = getattr(stacking, "quality_summary", None)
    return result.model_copy(
        update={
            "stacked_path": stacked_path,
            "flagged": getattr(quality, "flagged", None),
            "flag_reasons": list(getattr(quality, "flag_reasons", []) or []),
            "quality_summary": quality,
            "note": "To compare this stack with the previous one, use QualityDiagnostics.stack_quality.",
        }
    )
