"""Purpose: Choose a target's frames for a stack, then run the stacking stage.

Description: `stack_target` is the owner of `processing.stacking.run`, the
one public way to stack. It uses the functions here:

* `frames_for_stack` turns the ``frames=`` argument into the light frames
  to stack: the caller's exact list, checked as one set, or, when none is
  given, every imaging light frame of the target.
* `choose_frames_to_stack` picks the light frames of one kind. Imaging
  frames and spectroscopy frames are never mixed, and frames from two
  cameras are never stacked together. The caller may narrow the choice by
  filter, by a range of file names, by a time window and by camera.
* `run_stack` runs the stacking stage (`stage.stack_frames`) on the chosen
  frames as a tracked job, holds a job slot while Siril runs, and saves the
  target when a stack was made. A public run makes no preview picture: the
  enhancement pipeline (`processing.enhancement.run`) makes it.

They raise an error from `astrometricslib.foundation.errors` when the
request cannot be met. None returns an error inside its result.
"""

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.errors import NotFoundError, ProcessingError
from astrometricslib.foundation.jobs.runner import registered_job
from astrometricslib.foundation.logging import get_log_context
from astrometricslib.models.processing_results import StackResult
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.camera_selection import (
    choose_camera_frames,
    ensure_single_camera,
    split_frames_by_camera,
)
from astrometricslib.pipelines.shared.frame_choice import FrameChoice
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.optic_selection import (
    choose_optic_frames,
    ensure_single_optic,
)
from astrometricslib.pipelines.shared.quality.frame_selection import FrameSelection, select_library_frames
from astrometricslib.pipelines.stacking.post_processing.preview_settings import PreviewSettings

if TYPE_CHECKING:
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

__all__ = ["SirilOptions", "choose_frames_to_stack", "frames_for_stack", "run_stack", "stack_target"]

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
        Where to write the stack instead of the usual place. Scripts only.
    log_file : `str` or `None`
        Where to write Siril's log. Scripts and the backend's job only.
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
    focal_length_mm: float | None = None,
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
    focal_length_mm : `float`, optional
        Keep only the frames taken at this focal length, rounded to whole
        millimeters. Applied with ``camera_id``, before the other choices.

    Returns
    -------
    chosen : `list` [`FrameRecord`]
        The frames, in file-name order, all from one camera and one optic.

    Raises
    ------
    NotFoundError
        If no frame matches, no camera matches ``camera_id``, or no frame
        was taken at ``focal_length_mm``.

    Notes
    -----
    An invalid-argument error is raised, through `ensure_single_camera` and
    `ensure_single_optic`, when the chosen frames come from more than one
    camera or optic.
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
    if focal_length_mm is not None:
        lights, optic_problem = choose_optic_frames(lights, focal_length_mm)
        if optic_problem:
            raise NotFoundError(
                optic_problem, details={"target": target.id, "focal_length_mm": focal_length_mm}
            )
    chosen = select_library_frames(lights, selection)
    if not chosen:
        raise NotFoundError(
            f"No {kind} light frames of {target.id!r} match that selection.", details={"target": target.id}
        )
    ensure_single_camera(chosen, target_id=target.id)
    ensure_single_optic(chosen, target_id=target.id)
    return chosen


def run_stack(
    target: Target,
    chosen: list[FrameRecord],
    *,
    kind: StackKind,
    force: bool,
    preview_settings: PreviewSettings,
    siril: SirilOptions,
    stacking_slot: Callable[[], AbstractContextManager],
    save_targets: Callable[[], None],
    drivers: Drivers | None = None,
    measured_at: datetime | None = None,
    quality_recording: FrameQualityRecording | None = None,
    make_preview: bool = True,
) -> StackResult:
    """Stack the chosen frames.

    Parameters
    ----------
    target : `Target`
        The target to stack. The stage records the stack, its quality
        summary and its preview on it.
    chosen : `list` [`FrameRecord`]
        The frames to stack, from `choose_frames_to_stack`.
    kind : {"imaging", "spectral"}
        Which kind of frames they are.
    force : `bool`
        Rebuild even if nothing changed since the stack on disk was made.
    preview_settings : `PreviewSettings`
        This run's preview choices.
    siril : `SirilOptions`
        This run's Siril settings.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    save_targets : `Callable`
        Saves the target catalog.
    drivers : `Drivers`, optional
        The stacking program to use. Left out, it is Siril.
    measured_at : `datetime`, optional
        When the stack started, with a time zone, for the quality rows the
        stage stores. The API layer supplies it. Left out, the stage stores
        no frame quality rows.
    quality_recording : `FrameQualityRecording`, optional
        The configuration and the shared calibration library for those rows.
        The API layer supplies it. Left out, the stage stores no frame
        quality rows.
    make_preview : `bool`, optional
        Also make the stack's preview picture. `stack_target` turns it off:
        the enhancement pipeline makes the picture.

    Returns
    -------
    result : `StackResult`
        The frames chosen and the stack made.

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

    from astrometricslib.pipelines.stacking import stage as stacking_stage

    # The job is recorded first, so a stack waiting for a slot shows in the
    # job list and returns its job id.
    with (
        registered_job(
            job_type="stacking",
            target_id=target.id,
            log_file=siril.log_file,
            completed_message=f"[{target.id}] Stacking completed",
            failed_message=f"[{target.id}] Stacking failed",
        ) as job,
        job.holding_slot(stacking_slot()),
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
            drivers=drivers,
            measured_at=measured_at,
            quality_recording=quality_recording,
            make_preview=make_preview,
        )
        # Stacking can finish without raising and still make no image, so
        # the outcome is decided here, not left to "no exception means done".
        job.mark(
            "completed" if stacked_path else "failed",
            100,
            message="Stacking completed" if stacked_path else "Stacking failed",
        )
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
            "quality_summary": quality,
            "note": (
                "To make its picture, use processing.enhancement.run. To compare this stack with the "
                "previous one, use quality.stacks.measure."
            ),
        }
    )


def frames_for_stack(target: Target, frames: FrameChoice | None) -> tuple[list[FrameRecord], StackKind]:
    """Turn a ``frames=`` argument into the frames of one stack and their kind.

    Parameters
    ----------
    target : `Target`
        The target the frames belong to.
    frames : `FrameChoice` or `None`
        The exact frames (a page, records or paths; see `frames_from_choice`),
        or `None` for every imaging light frame of the target.

    Returns
    -------
    chosen : `list` [`FrameRecord`]
        The frames, from one camera and one optic.
    kind : {"imaging", "spectral"}
        Which kind of frames they are.

    Notes
    -----
    Raises `InvalidArgumentError` if ``frames`` is empty or a page cut short,
    its frames come from more than one camera or optic, or they mix imaging
    and spectral frames; the message names each group and its frame count.
    Raises `NotFoundError` if ``frames`` is `None` and the target has no
    imaging light frame, or a path is not one of the target's frames.
    """
    from astrometricslib.pipelines.shared.frame_choice import check_frame_list, frames_from_choice

    if frames is None:
        return choose_frames_to_stack(target, "imaging", FrameSelection(), None), "imaging"
    chosen = frames_from_choice(frames, target)
    check_frame_list(chosen, target_id=target.id)
    return chosen, "spectral" if frame_is_spectral(chosen[0]) else "imaging"


def stack_target(
    target: Target,
    frames: FrameChoice | None = None,
    *,
    force: bool = False,
    siril: SirilOptions | None = None,
    catalog: Any,
    calibration_library: Any,
    drivers: Drivers | None = None,
    measured_at: datetime | None = None,
) -> StackResult:
    """Stack a target's frames, without making the preview picture.

    Parameters
    ----------
    target : `Target`
        The target to stack.
    frames : `FrameChoice`, optional
        The frames to stack (see `frames_for_stack`). Left out, every imaging
        light frame of the target.
    force : `bool`, optional
        Rebuild even if nothing changed since the stack on disk.
    siril : `SirilOptions`, optional
        Siril settings for this run that replace the configured ones.
    catalog : `TargetCatalog`
        The target catalog, which saves the target.
    calibration_library : `CalibrationLibrary`
        The shared calibration library the stack chooses its darks, biases
        and flats from.
    drivers : `Drivers`, optional
        The stacking program. Left out, Siril.
    measured_at : `datetime`, optional
        When the stack started, with a time zone, for the quality rows.

    Returns
    -------
    result : `StackResult`
        The frames chosen, the stack's path and its quality summary.
    """
    from astrometricslib.foundation.storage.process_locks import acquire_resource_slot
    from astrometricslib.pipelines.shared.target_records import save_quality_stamps
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

    chosen, kind = frames_for_stack(target, frames)
    config = catalog._config
    return run_stack(
        target,
        chosen,
        kind=kind,
        force=force,
        preview_settings=PreviewSettings(),
        siril=siril or SirilOptions(),
        stacking_slot=lambda: acquire_resource_slot(
            config, "job", config.get_max_concurrent_jobs(), work_name=f"stacking {target.id}"
        ),
        save_targets=catalog._save,
        drivers=drivers,
        measured_at=measured_at,
        quality_recording=FrameQualityRecording(
            config, calibration_library, save_target=lambda: save_quality_stamps(catalog, target)
        ),
        make_preview=False,
    )
