"""Tools for combining (stacking) many images into one.

This file handles running the external stacking software (Siril)
and checking the quality of the final stacked images.
"""

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.drivers.camera_profile_store import camera_identity
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import AstrometricsError, ConflictError
from astrometricslib.foundation.jobs.runner import get_current_job
from astrometricslib.foundation.run_abandonment import raise_if_abandoned
from astrometricslib.models.quality.gate_result import GateResult
from astrometricslib.models.target import Target
from astrometricslib.pipelines.stacking.calibration_health import measure_calibration_health
from astrometricslib.pipelines.stacking.frame_screening import screen_frames
from astrometricslib.pipelines.stacking.fwhm_sampling import measure_fwhm_degradation
from astrometricslib.pipelines.stacking.notice_reasons import (
    exposure_group_left_out_reason,
    single_frame_stack_reason,
)
from astrometricslib.pipelines.stacking.registration_records import (
    check_spectral_registration_quality,
    update_frame_registration_results,
)
from astrometricslib.pipelines.stacking.stack_run_context import StackRun
from astrometricslib.utilities.exceptions import DATA_ERRORS

if TYPE_CHECKING:
    from astrometricslib.models.quality.quality_summary import StackQualitySummary
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

logger = logging.getLogger(__name__)


def _report_stage(progress_current: int, message: str) -> None:
    """Tell the running job which step the stack has reached.

    The step goes to the job's log and moves its progress bar, so a person
    watching the job list can tell a slow stack from a stuck one. Does
    nothing when the stack is not running as a tracked job.

    Parameters
    ----------
    progress_current : `int`
        How far along the stack is, out of 100.
    message : `str`
        A short description of the step.
    """
    job = get_current_job()
    if job is not None:
        job.stage(progress_current, message)


def stack_frames(
    target: Target,
    log_file: str | None = None,
    frames_to_stack: list[Any] | None = None,
    rejection_sigma: tuple[float, float] | None = None,
    filter_wfwhm: str | None = None,
    filter_round: str | None = None,
    stack_weight: str | None = None,
    generate_rejmap: bool | None = None,
    output_file: str | None = None,
    job_id: str | None = None,
    force: bool = False,
    preview_settings: Any | None = None,
    drivers: Drivers | None = None,
    measured_at: datetime | None = None,
    quality_recording: FrameQualityRecording | None = None,
) -> str | None:
    """Run the main stacking process using the ImageProcessing driver.

    This function combines many individual pictures (sub-exposures) into one
    master image. Adding the images together makes the real stars and galaxies
    brighter, while randomly occurring noise (like camera read noise or heat
    noise) tends to cancel itself out. Before combining the images, we also
    use rejection algorithms to find and throw out temporary artifacts like
    satellite trails, cosmic rays, and hot pixels.

    Parameters
    ----------
    target : Target
        The astronomical target to stack.
    log_file : `str` or `None`, optional
        Where to save the output text from Siril.
    frames_to_stack : `list` of `Any` or `None`, optional
        A specific list of images to use. If None, it uses all images from the
        target.
    rejection_sigma : `tuple` of (`float`, `float`) or `None`, optional
        How strict the rejection algorithm should be (lower, upper limits).
    filter_wfwhm : `str` or `None`, optional
        A rule to throw out blurry images.
    filter_round : `str` or `None`, optional
        A rule to throw out images where stars aren't round.
    stack_weight : `str` or `None`, optional
        How to weight the images when combining them.
    generate_rejmap : `bool` or `None`, optional
        Whether to save a picture showing exactly which pixels were thrown out.
    output_file : `str` or `None`, optional
        Where to save the final stacked image.
    job_id : `str` or `None`, optional
        The tracked job this stack is running under, if any -- recorded
        as this run's IVOA provenance Activity when given.
    force : `bool`, optional
        Rebuild the stack even when its frames, calibration frames and
        settings are the same as when the stack on disk was made. Without
        it (and with the setting ``skip_unchanged_stacks_enabled`` on), an
        unchanged stack is kept and its path returned (see
        `stack_inputs.py`).
    preview_settings : `PreviewSettings` or `None`, optional
        Choices for this run's preview picture that replace the saved
        settings (see `stack_preview.PreviewSettings`).
    drivers : `Drivers` or `None`, optional
        The stacking program to use. Left out, it is Siril.
    measured_at : `datetime` or `None`, optional
        When the stack started, with a time zone. The frame quality rows the
        stage stores carry it. Pipeline code does not read the clock, so the
        caller supplies it.
    quality_recording : `FrameQualityRecording` or `None`, optional
        The configuration and the shared calibration library, from the caller,
        for the frame quality rows. The stage stores them only for an imaging
        stack with the quarantine step turned on, and only when both this and
        ``measured_at`` are given. Left out (tests), none are stored.

    Returns
    -------
    stacked_path : `str` or `None`
        The location of the new stacked image, or None if it failed. For a
        stack that was skipped as unchanged, the location of the stack that
        is already there.

    Notes
    -----
    A `ConflictError` is raised when the target has no frames to stack, mixes
    spectral and standard imaging frames, has frames from more than one
    camera, or has a subset-stack job running (see `target_stack_guard.py`).
    A `ProcessingError` is raised when no usable frames are left after
    filtering out mismatched or bad frames.
    """
    from astrometricslib.pipelines.stacking.target_stack_guard import library_stack_guard

    with library_stack_guard(target.id):
        return _stack_frames_guarded(
            target,
            log_file,
            frames_to_stack,
            rejection_sigma,
            filter_wfwhm,
            filter_round,
            stack_weight,
            generate_rejmap,
            output_file,
            job_id,
            force,
            preview_settings,
            drivers,
            measured_at,
            quality_recording,
        )


def _stack_frames_guarded(
    target: Target,
    log_file: str | None = None,
    frames_to_stack: list[Any] | None = None,
    rejection_sigma: tuple[float, float] | None = None,
    filter_wfwhm: str | None = None,
    filter_round: str | None = None,
    stack_weight: str | None = None,
    generate_rejmap: bool | None = None,
    output_file: str | None = None,
    job_id: str | None = None,
    force: bool = False,
    preview_settings: Any | None = None,
    drivers: Drivers | None = None,
    measured_at: datetime | None = None,
    quality_recording: FrameQualityRecording | None = None,
) -> str | None:
    """Do the work of `stack_frames` while the target's guard is held.

    Takes the same parameters as `stack_frames`. The work is a short list of
    named steps over one `StackRun`. The order of the steps matters: later
    steps read what earlier ones found, and several move files or store rows.
    The comments below name what each step adds.

    Returns
    -------
    stacked_path : `str` or `None`
        What `stack_frames` returns.

    Notes
    -----
    A `ConflictError` is raised, by the steps, when the target has no frames
    to stack, mixes spectral and standard imaging frames, or has frames from
    more than one camera. A `ProcessingError` is raised, by the frame
    screening, when no usable frames are left after filtering out
    mismatched or bad frames.
    """
    run = StackRun(
        target,
        log_file,
        frames_to_stack,
        rejection_sigma,
        filter_wfwhm,
        filter_round,
        stack_weight,
        generate_rejmap,
        output_file,
        job_id,
        force,
        preview_settings,
        drivers,
        measured_at,
        quality_recording,
    )
    _choose_frames(run)  # run.target_frames
    _check_frame_types(run)  # run.has_spectral
    _name_output_file(run)  # run.stack_file
    _screen_frames(run)  # run.target_frames, excluded_frames, gate_results, background_split
    _prepare_stack(run)  # run.engine, stack_weight, expected_path, inputs_record
    if _stack_is_unchanged(run):
        return run.stacked_path
    _run_stacking_engine(run)  # run.stacked_path, diagnostics
    _trim_stack_edges(run)  # run.crop_offset, excluded_frames
    summary = _summarize_stack(run)
    if run.stacked_path:
        _finish_made_stack(run, run.stacked_path, summary)
    return run.stacked_path


def _choose_frames(run: StackRun) -> None:
    """Pick the frames to stack and check they come from one camera.

    Sets ``run.target_frames``. The target's stacked, starless and star-mask
    pictures are never stacked.

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place.

    Raises
    ------
    ConflictError
        If there are no frames, or they come from more than one camera.
    """
    if run.frames_to_stack is not None:
        run.target_frames = run.frames_to_stack
    else:
        run.target_frames = [
            f
            for f in run.target.frames
            if not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]

    if not run.target_frames:
        raise ConflictError("Target has no frames available to stack.")

    # Frames from different cameras cannot be stacked together. Without this
    # check the gain filter in the screening step would keep whichever camera
    # had more frames.
    from astrometricslib.pipelines.stacking.pre_processing.camera_selection import ensure_single_camera

    ensure_single_camera(run.target_frames)

    _report_stage(5, f"Checking {len(run.target_frames)} frames")


def _check_frame_types(run: StackRun) -> None:
    """Check that the frames are all spectral or all standard imaging.

    Sets ``run.has_spectral``.

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place.

    Raises
    ------
    ConflictError
        If spectral and standard frames are mixed.
    """
    from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral

    has_spectral = any(frame_is_spectral(f) for f in run.target_frames)
    has_standard = any(not frame_is_spectral(f) for f in run.target_frames)
    if has_spectral and has_standard:
        raise ConflictError(
            "Target contains a mixed set of spectral ('SPEC') and standard imaging frames. "
            "Stacking mixed frame types is not permitted."
        )
    run.has_spectral = has_spectral


def _name_output_file(run: StackRun) -> None:
    """Give the stack a file name when the caller did not.

    The name holds the target, the filter and, when the target has stacks
    for several optical set-ups, a tag for this set-up. Sets
    ``run.stack_file`` (the caller's ``output_file`` when it gave one).

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place.
    """
    if run.output_file is not None:
        run.stack_file = run.output_file
        return
    safe_target_id = run.target.id.replace(" ", "_")
    filter_tag = None
    if run.has_spectral:
        filter_tag = "SPEC"
    else:
        filters = {getattr(f, "filter", None) for f in run.target_frames}
        filter_names = {
            f.name.upper() if hasattr(f, "name") else str(f).upper() for f in filters if f is not None
        }
        if len(filter_names) == 1:
            filter_tag = next(iter(filter_names))

    if filter_tag:
        canonical_mapping = {
            "LUMINANCE": "L",
            "RED": "R",
            "GREEN": "G",
            "BLUE": "B",
            "STAR ANALYZER 200": "SPEC",
            "SPECTROSCOPY": "SPEC",
        }
        filter_tag = canonical_mapping.get(filter_tag, filter_tag)
        output_file = f"{safe_target_id}_{filter_tag}_Stacked.fits"
    else:
        output_file = f"{safe_target_id}_Stacked.fits"

    configuration_tag = _disambiguating_configuration_tag(run.target, run.target_frames)
    if configuration_tag:
        output_file = output_file.removesuffix(".fits") + f"_{configuration_tag}.fits"
    run.stack_file = output_file


def _screen_frames(run: StackRun) -> None:
    """Leave out the frames that would harm the stack.

    The checks are in `frame_screening.py`. They may move bad frames out of
    the target's folder and store frame quality rows. Sets
    ``run.frames_submitted`` and replaces ``run.target_frames`` with the
    frames that pass.

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place.

    Notes
    -----
    A `ProcessingError` is raised when a check leaves no frames.
    """
    run.frames_submitted = len(run.target_frames)
    screening = screen_frames(
        run.target, run.target_frames, run.has_spectral, run.measured_at, run.quality_recording
    )
    run.target_frames = screening.frames
    run.excluded_frames = screening.excluded_frames
    run.gate_results = screening.gate_results
    run.background_split = screening.background_split
    _report_stage(15, f"Frame checks done, {len(run.target_frames)} frames kept")


def _prepare_stack(run: StackRun) -> None:
    """Choose the stacking program and weighting, and build the inputs record.

    The inputs record holds the frames, calibration frames, settings and
    stacking code (see `stack_inputs.py`). The next step compares it with the
    record of the stack on disk. Sets ``run.engine``, ``run.stack_weight``,
    ``run.expected_path`` and ``run.inputs_record``.

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place.
    """
    from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import build_stack_inputs_record
    from astrometricslib.pipelines.stacking.stack_settings import choose_weight_for_frames

    run.engine = (run.drivers or Drivers()).stacking_or_default()
    run.stack_weight = choose_weight_for_frames(
        run.target_frames, run.stack_weight, get_configuration(), is_spectral=run.has_spectral
    )
    run.expected_path = _expected_stack_path(run.target.id, run.stack_file)
    run.inputs_record = build_stack_inputs_record(
        run.target_frames,
        get_configuration(),
        {
            "rejection_sigma": run.rejection_sigma,
            "filter_wfwhm": run.filter_wfwhm,
            "filter_round": run.filter_round,
            "stack_weight": run.stack_weight,
            "generate_rejmap": run.generate_rejmap,
            "is_spectral": run.has_spectral,
        },
    )


def _stack_is_unchanged(run: StackRun) -> bool:
    """Say whether the stack on disk can be kept as it is.

    A stack that would come out the same is skipped: the same frames, the same
    calibration frames, the same settings and the same stacking code as when
    the stack on disk was made (see `stack_inputs.py`). Adding frames to one
    filter then leaves the target's other stacks alone. When the stack is
    skipped, ``run.stacked_path`` is set to the stack that is already there.

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place when the stack is skipped.

    Returns
    -------
    unchanged : `bool`
        `True` when the stack is kept and the run is over.
    """
    from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import decide_whether_to_restack

    decision = decide_whether_to_restack(
        run.expected_path,
        run.inputs_record,
        force=run.force,
        enabled=get_configuration().get_skip_unchanged_stacks_enabled(),
    )
    if decision.skip:
        logger.info(
            "Skipping the stack of '%s' at %s: its frames, calibration frames, settings and the "
            "stacking code are the same as when it was made. Pass force=True to rebuild it.",
            run.target.id,
            run.expected_path,
        )
        _record_unchanged_stack(run.target, run.target_frames, run.expected_path, run.has_spectral)
        run.stacked_path = run.expected_path
        return True
    if os.path.isfile(run.expected_path):
        logger.info("Rebuilding the stack of '%s': %s.", run.target.id, "; ".join(decision.reasons))
    return False


def _run_stacking_engine(run: StackRun) -> None:
    """Stack the frames with the stacking program.

    `run_stack` adds two safeguards for spectroscopy: frames of different
    exposure lengths are stacked one length at a time and then combined, and a
    registration that loses too many frames is retried with a different star
    detection. A restack overwrites the stack file. The old stack and its
    pictures wait in a staging folder until the new stack exists; if the
    restack fails, they go back where they were. Sets ``run.staging``,
    ``run.stacked_path`` and ``run.diagnostics``.

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place.

    Raises
    ------
    RuntimeError
        If the stack was not prepared first.

    Notes
    -----
    `RunAbandonedError` is raised if the caller gave up on the stack while
    it ran.
    """
    from astrometricslib.pipelines.stacking.stack_runner import run_stack

    if run.engine is None:
        raise RuntimeError("The stack must be prepared before the stacking program runs.")
    run.staging = _archive_stack_before_restack(run.target.id, run.stack_file)
    _report_stage(20, f"Calibrating, aligning and stacking {len(run.target_frames)} frames in Siril")
    try:
        run.stacked_path, run.diagnostics = run_stack(
            run.engine,
            run.target_frames,
            run.target.id,
            run.stack_file,
            run.log_file,
            run.has_spectral,
            job_id=run.job_id,
            rejection_sigma=run.rejection_sigma,
            filter_wfwhm=run.filter_wfwhm,
            filter_round=run.filter_round,
            stack_weight=run.stack_weight,
            generate_rejmap=run.generate_rejmap,
        )
        # A caller that gave up on this stack must not see it go on to trim,
        # summarize or record a stack. The restore below puts the old
        # stack back.
        raise_if_abandoned()
    except BaseException:
        _finish_stack_archive(run.staging, run.stack_file, run.target.id, kept=False)
        raise
    _finish_stack_archive(run.staging, run.stack_file, run.target.id, kept=bool(run.stacked_path))
    _report_stage(70, "Siril stack finished" if run.stacked_path else "Siril produced no stack")


def _trim_stack_edges(run: StackRun) -> None:
    """Trim the noisy edges of the stack, and list the corrupt frames.

    Frames shift between nights, so the edges of a stack of several nights
    are covered by fewer frames and are noisier. Trim them off. The check
    leaves a stack with clean edges as it is. It runs before the stack is
    measured and pictured, so both describe the trimmed stack. Sets
    ``run.crop_offset`` and adds the corrupt frames to
    ``run.excluded_frames``.

    Parameters
    ----------
    run : `StackRun`
        The run. Updated in place.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.stack_settings import trim_noisy_stack_edges

    run.crop_offset = trim_noisy_stack_edges(
        run.stacked_path, get_configuration(), is_spectral=run.has_spectral
    )
    run.excluded_frames.extend(
        ExcludedFrame(path=path, reason="corrupt or unreadable FITS file")
        for path in run.diagnostics.get("corrupt_frames_skipped", [])
    )


def _judge_calibration_frames(run: StackRun) -> None:
    """Judge the dark and bias frames the stack used from their stored numbers.

    The answer goes into ``run.diagnostics``, where the stack's calibration
    gates read it (see `stack_calibration_health.py`). The check only warns and
    never stops a stack: a failure here is logged and leaves the gates out.

    Parameters
    ----------
    run : `StackRun`
        The run, with the stacking engine's diagnostics.
    """
    from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
    from astrometricslib.pipelines.stacking.pre_processing.stack_calibration_health import (
        assess_stack_calibration,
        store_in_diagnostics,
    )

    recording = run.quality_recording
    configuration = get_configuration() if recording is None else recording.configuration
    try:
        health = assess_stack_calibration(
            run.diagnostics,
            run.target_frames,
            None if recording is None else recording.calibration_library,
            None if recording is None else QualityStore(quality_database_path(configuration)),
            configuration,
        )
    except Exception:  # a by-product of the stack: no failure here may fail it
        logger.exception("The calibration frames of the stack of '%s' were not judged", run.target.id)
        return
    store_in_diagnostics(run.diagnostics, health)


def _summarize_stack(run: StackRun) -> StackQualitySummary:
    """Build the quality summary and store it on the target.

    The summary goes on the target's spectral or standard stacking record.

    Parameters
    ----------
    run : `StackRun`
        The run.

    Returns
    -------
    summary : `StackQualitySummary`
        The finished summary.
    """
    _judge_calibration_frames(run)
    summary = _build_stack_quality_summary(
        target=run.target,
        is_spectral=run.has_spectral,
        frames_submitted=run.frames_submitted,
        target_frames=run.target_frames,
        excluded_frames=run.excluded_frames,
        diagnostics=run.diagnostics,
        background_split=run.background_split,
        stacked_path=run.stacked_path,
        gate_results=run.gate_results,
    )
    if run.has_spectral:
        run.target.spectral_stacking.quality_summary = summary
    else:
        run.target.stacking.quality_summary = summary
    return summary


def _finish_made_stack(run: StackRun, stacked_path: str, summary: StackQualitySummary) -> None:
    """Record a stack that was made: pointers, provenance, rows and picture.

    The order matters. The inputs record is saved last, because a stack with
    a record is one the next run may skip.

    Parameters
    ----------
    run : `StackRun`
        The run, with a stack made.
    stacked_path : `str`
        The stack that was made.
    summary : `StackQualitySummary`
        The stack's quality summary.
    """
    _point_target_at_stack(run, stacked_path)
    _record_provenance(run, stacked_path, summary)
    _save_registration_record(run, stacked_path, summary)
    _store_stack_quality(run, stacked_path, summary)
    _make_preview(run, stacked_path)  # run.preview_path
    _save_stack_inputs(run, stacked_path)
    _log_finished_stack(run)


def _point_target_at_stack(run: StackRun, stacked_path: str) -> None:
    """Make the target's stacked image the new stack, where that is right.

    Parameters
    ----------
    run : `StackRun`
        The run, with a stack made.
    stacked_path : `str`
        The stack that was made.
    """
    if run.has_spectral:
        run.target.spectral_stacking.stacked_image = stacked_path
    elif _record_configuration_stack(run.target, run.target_frames, stacked_path):
        run.target.stacking.stacked_image = stacked_path


def _record_provenance(run: StackRun, stacked_path: str, summary: StackQualitySummary) -> None:
    """Record this run as the provenance activity that made the stack.

    Parameters
    ----------
    run : `StackRun`
        The run, with a stack made.
    stacked_path : `str`
        The stack that was made.
    summary : `StackQualitySummary`
        The stack's quality summary.
    """
    from astrometricslib.models.provenance import DatasetEntity
    from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
    from astrometricslib.pipelines.shared.provenance_recording import (
        record_pipeline_run,
        stacked_image_entity_id,
    )

    entity_id = stacked_image_entity_id(run.target.id, stacked_path)
    record_pipeline_run(
        summary=summary,
        job_id=run.job_id,
        target_id=run.target.id,
        pipeline_name="stacking",
        pipeline_version=STACKING_PIPELINE_VERSION,
        generated_entities={
            entity_id: DatasetEntity(
                id=entity_id, location=stacked_path, entity_description="entitydesc:stacked-image"
            )
        },
    )


def _save_registration_record(run: StackRun, stacked_path: str, summary: StackQualitySummary) -> None:
    """Keep Siril's transform for each frame beside the stack.

    The astrometry run finds the target on each frame with it once the stack
    has a plate solution. A failure to write it never fails the stack.

    Parameters
    ----------
    run : `StackRun`
        The run, with a stack made.
    stacked_path : `str`
        The stack that was made.
    summary : `StackQualitySummary`
        The stack's quality summary.
    """
    from astrometricslib.pipelines.shared.registration_record import (
        build_registration_record,
        write_registration_record,
    )

    registration_record = build_registration_record(
        run.diagnostics,
        run.crop_offset,
        summary.stacking_metrics.registration_reference_frame,
        is_spectral=summary.stacking_metrics.is_spectral,
    )
    if registration_record is not None:
        write_registration_record(stacked_path, registration_record)


def _store_stack_quality(run: StackRun, stacked_path: str, summary: StackQualitySummary) -> None:
    """Store the summary, the stack's noise and its frames as measurements.

    A failure here never fails the stack. The flat-to-light match in it reads
    up to 16 flat, 24 light and 5 bias frames again from storage, which can
    take a while on a network mount (the job's time limit is in the
    configuration), so the job's progress says so first.

    Parameters
    ----------
    run : `StackRun`
        The run, with a stack made.
    stacked_path : `str`
        The stack that was made.
    summary : `StackQualitySummary`
        The stack's quality summary.
    """
    _report_stage(75, "Measuring the stack's quality (reads up to 16 flat, 24 light and 5 bias frames again)")
    _record_stack_quality(
        summary,
        run.target.id,
        stacked_path,
        run.target_frames,
        run.diagnostics,
        run.stack_weight,
        run.crop_offset,
        run.job_id,
        run.measured_at,
        run.quality_recording,
    )


def _make_preview(run: StackRun, stacked_path: str) -> None:
    """Make a picture of the stack for people to look at.

    It never changes the stack and a failure to make it is logged, not
    raised. The image viewer shows it through the target's processed image.
    Sets ``run.preview_path``.

    Parameters
    ----------
    run : `StackRun`
        The run, with a stack made.
    stacked_path : `str`
        The stack that was made.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
        record_preview_as_processed_image,
        write_stack_preview,
    )

    _report_stage(80, "Making the preview picture (GraXpert, Cosmic Clarity, star toning)")
    run.preview_path = write_stack_preview(stacked_path, run.preview_settings)
    if run.preview_path:
        record_preview_as_processed_image(run.target, run.has_spectral, stacked_path, run.preview_path)


def _save_stack_inputs(run: StackRun, stacked_path: str) -> None:
    """Save the inputs record beside the stack.

    This is done last, once the stack, its quality summary and its picture are
    done: a stack with a record is one the next run may skip, so a run that
    stopped before this point leaves a stack that is built again.

    Parameters
    ----------
    run : `StackRun`
        The run, with a stack made and pictured.
    stacked_path : `str`
        The stack that was made.
    """
    from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import write_stack_inputs

    if run.inputs_record is not None:
        write_stack_inputs(stacked_path, run.inputs_record)


def _log_finished_stack(run: StackRun) -> None:
    """Write one closing line that sums up the run.

    The preview step's line above says which cleanup programs ran.

    Parameters
    ----------
    run : `StackRun`
        The run, finished.
    """
    logger.info(
        "Stack of '%s' finished: stack %s; preview %s; %d frames submitted, %d set aside.",
        run.target.id,
        run.stacked_path,
        run.preview_path or "not made (see the warning above)",
        run.frames_submitted,
        len(run.excluded_frames),
    )


def _record_stack_quality(
    summary: StackQualitySummary,
    target_id: str,
    stacked_path: str,
    target_frames: list[Any],
    diagnostics: dict[str, Any],
    stack_weight: str | None,
    crop_offset: tuple[int, int],
    job_id: str | None,
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
) -> None:
    """Store the finished stack's quality measurements.

    This never stops a stack. The measurements are written next to the quality
    summary, which stays as it is. Nothing is stored when the caller gave no
    measurement time, because pipeline code does not read the clock.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The finished summary.
    target_id : `str`
        The target's id.
    stacked_path : `str`
        The finished stack.
    target_frames : `list`
        The frames given to the stacking program.
    diagnostics : `dict`
        What the stacking run reported.
    stack_weight : `str` or `None`
        The Siril weighting mode the stack used.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) trimmed from the stack's left and top edges.
    job_id : `str` or `None`
        The tracked job, if any.
    measured_at : `datetime` or `None`
        When the stack started, with a time zone, from the caller.
    quality_recording : `FrameQualityRecording` or `None`
        The caller's configuration, which says where the quality store lives.
    """
    from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
    from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
    from astrometricslib.pipelines.stacking.post_processing.record_quality import record_stacking_quality

    if measured_at is None:
        logger.warning(
            "No measurement time was given, so the quality measurements of the stack of '%s' are not stored.",
            target_id,
        )
        return
    context = PipelineRunContext.from_options(
        target_id, "stacking", STACKING_PIPELINE_VERSION, {"job_id": job_id, "measured_at": measured_at}
    )
    if context is None:
        return
    record_stacking_quality(
        summary,
        context,
        stacked_path,
        target_frames,
        diagnostics,
        stack_weight,
        crop_offset,
        config=None if quality_recording is None else quality_recording.configuration,
    )


def _record_unchanged_stack(
    target: Any, target_frames: list[Any], stacked_path: str, is_spectral: bool
) -> None:
    """Make sure the target points at a stack that was kept as it was.

    A skipped stack keeps its quality summary and its picture from the run
    that made it. Only the pointer to the file is set again, in case the
    target's record of it was lost.

    Parameters
    ----------
    target : `Target`
        The target whose stack was skipped.
    target_frames : `list`
        The frames the stack was made from.
    stacked_path : `str`
        Where the stack is.
    is_spectral : `bool`
        `True` for the target's spectral stack.
    """
    if is_spectral:
        target.spectral_stacking.stacked_image = stacked_path
    elif _record_configuration_stack(target, target_frames, stacked_path):
        target.stacking.stacked_image = stacked_path


def _expected_stack_path(target_id: str, output_file: str) -> str:
    """Give the path the stack will be written to.

    Parameters
    ----------
    target_id : `str`
        The target's id, which names its folder under the stacks path.
    output_file : `str`
        The stack's file name, or a full path.

    Returns
    -------
    path : `str`
        `output_file` when it is a full path, otherwise the file inside the
        target's folder under the stacks path.
    """
    import os

    from astrometricslib.foundation.config import get_configuration

    if os.path.isabs(output_file):
        return output_file
    return os.path.join(str(get_configuration().get_stacks_path()), "lights", target_id, output_file)


def _archive_stack_before_restack(target_id: str, output_file: str) -> str | None:
    """Move the current stack aside before a restack, if the setting is on.

    Parameters
    ----------
    target_id : `str`
        The target's id.
    output_file : `str`
        The stack's file name, or a full path.

    Returns
    -------
    staging : `str` or `None`
        The staging folder holding the old files, or `None` if the setting is
        off, there was no stack, or the files could not be moved. A failure to
        move is logged and does not stop the restack.
    """
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.stacking.post_processing.previous_stack import archive_current_stack

    if not get_configuration().get_keep_previous_stack_enabled():
        return None
    try:
        return archive_current_stack(_expected_stack_path(target_id, output_file))
    except OSError as error:
        logger.warning(
            "Could not keep the previous stack of '%s': %s. The restack goes on.", target_id, error
        )
        return None


def _finish_stack_archive(staging: str | None, output_file: str, target_id: str, *, kept: bool) -> None:
    """Make the staged stack the previous one, or put it back.

    Parameters
    ----------
    staging : `str` or `None`
        The folder `_archive_stack_before_restack` returned.
    output_file : `str`
        The stack's file name, or a full path.
    target_id : `str`
        The target's id.
    kept : `bool`
        `True` if the restack produced a new stack, so the old one becomes the
        previous stack. `False` if it failed, so the old one is restored.
    """
    from astrometricslib.pipelines.stacking.post_processing.previous_stack import (
        commit_archive,
        rollback_archive,
    )

    if staging is None:
        return
    stack_path = _expected_stack_path(target_id, output_file)
    try:
        if kept:
            commit_archive(stack_path, staging)
        else:
            rollback_archive(stack_path, staging)
    except OSError as error:
        logger.warning("Could not finish keeping the previous stack of '%s': %s", target_id, error)


def _disambiguating_configuration_tag(target: Target, target_frames: list[Any]) -> str:
    """Create a unique filename tag so we don't overwrite existing stacks.

    If we image the same target using different equipment setups (like changing
    the telescope focal length), the default name
    ``<target>_<filter>_Stacked.fits``
    would cause the new stack to overwrite the old one. This function adds a
    specific tag to the filename to keep them separate.

    We only add this tag if the target actually has multiple setups, so older
    targets keep their normal filenames and don't get lost in the catalog.

    Parameters
    ----------
    target : `Target`
        The target being checked.
    target_frames : `list` [`FrameRecord`]
        The specific images being stacked right now.

    Returns
    -------
    configuration_tag : `str`
        The extra tag to add to the filename, or "" if it's not needed.
    """
    from astrometricslib.pipelines.shared.frame_grouping import (
        frame_configuration_key,
        group_frames_by_configuration,
    )

    try:
        if len(group_frames_by_configuration(target)) < 2:
            return ""
    except (AstrometricsError, *DATA_ERRORS) as grouping_error:
        # A tag is only ever additive, so failing to decide costs
        # nothing beyond the collision this guards against.
        logger.debug("Could not group '%s' by configuration: %s", getattr(target, "id", "?"), grouping_error)
        return ""

    keys = {frame_configuration_key(frame) for frame in target_frames}
    keys.discard(None)
    if len(keys) != 1:
        # A blend of optics has no single configuration to name, and
        # naming it after one of them would assert something untrue.
        return ""

    configuration_key = next(iter(keys))
    return "".join(character if character.isalnum() else "_" for character in str(configuration_key)).strip(
        "_"
    )


def _record_configuration_stack(target: Target, target_frames: list[Any], stacked_path: str) -> bool:
    """Record a stack for one setup and say if it's the preferred one.

    This works alongside the older `stacked_image` property so that other
    parts of the program (like the UI) still work normally. This allows a
    target photographed through different telescopes to safely keep all of
    its separate, stacked images at the same time.

    Parameters
    ----------
    target : `Target`
        The target to update.
    target_frames : `list`
        The images used to create this stack.
    stacked_path : `str`
        Where the new stacked image is saved.

    Returns
    -------
    should_become_stacked_image : `bool`
        True if this new image should become the main, default image for the
        target.
    """
    from astrometricslib.models.target import StackConfigurationResult
    from astrometricslib.pipelines.shared.frame_grouping import frame_configuration_key

    keys = {frame_configuration_key(frame) for frame in target_frames}
    keys.discard(None)
    if len(keys) != 1:
        # Either no frame carried a focal length, or -- the case this
        # whole mechanism exists to prevent -- the stack blended several
        # optics. Recording it under a single key would assert something
        # untrue, but the stack itself is still the only one there is.
        logger.debug(
            "Not recording a per-configuration stack for '%s': %d configurations present.",
            getattr(target, "id", "?"),
            len(keys),
        )
        return True

    configuration_key = next(iter(keys))
    camera = (target_frames[0].camera or "") if target_frames else ""
    focal_length = next((frame.focal_length_mm for frame in target_frames if frame.focal_length_mm), None)

    primary_camera = None
    primary_focal_length = None
    try:
        from astrometricslib.foundation.config import get_configuration

        configuration = get_configuration()
        primary_camera = configuration.get_primary_camera_name()
        primary_focal_length = configuration.get_primary_focal_length_mm()
    except (AstrometricsError, OSError, ValueError) as configuration_error:
        logger.debug("Could not read the primary camera or optic: %s", configuration_error)

    # Both must match. Focal length alone would mark two cameras sharing
    # one focal length as equally preferred, and camera alone would do
    # the same for one camera used through two optics -- and this
    # library has both cases.
    optic_matches = bool(
        focal_length and primary_focal_length and round(focal_length) == round(primary_focal_length)
    )
    camera_matches = bool(primary_camera and camera and _camera_names_match(camera, primary_camera))
    is_preferred = optic_matches and camera_matches

    target.stacking.stacks_by_configuration[configuration_key] = StackConfigurationResult(
        configuration_key=configuration_key,
        camera=camera,
        focal_length_mm=focal_length,
        frames_stacked=len(target_frames),
        stacked_image=stacked_path,
        is_preferred=is_preferred,
    )

    if is_preferred:
        return True

    # Nothing preferred exists, so this stack is the best answer
    # available -- a target whose only optic is not the observatory's
    # primary must still have a stacked_image.
    return not any(
        recorded.is_preferred
        for key, recorded in target.stacking.stacks_by_configuration.items()
        if key != configuration_key
    )


def _camera_names_match(first: str, second: str) -> bool:
    """Check whether two camera names mean the same camera.

    Camera names in settings and image files are spelled differently (like
    "ZWO ASI533MM Pro" vs. "ZWO ASI 533MM Pro", or "Nikon D5300" vs. the
    header's "Nikon DSLR DSC D5300"). Case, spaces and punctuation are ignored,
    and the aliases listed in a camera's profile count as the same camera.

    Parameters
    ----------
    first, second : `str`
        Camera names to compare.

    Returns
    -------
    matches : `bool`
        True if the names mean the same camera.
    """
    return camera_identity(first) == camera_identity(second)


def _frames_in_combined_groups(target_frames: list[Any], diagnostics: dict) -> list[Any]:
    """Pick the frames that went into the final stack.

    A stack of several exposure groups can leave a group out (it failed to
    stack, could not be lined up, or was nonlinear). The stacking run lists
    the paths of the frames of the groups it combined in
    ``"combined_frame_paths"``. A stack of one group has no such list, and all
    its frames count.

    Parameters
    ----------
    target_frames : `list`
        The frames given to the stage.
    diagnostics : `dict`
        What the stacking run reported.

    Returns
    -------
    frames : `list`
        `target_frames` in their order, limited to the combined groups when
        the run reported them, otherwise all of them.
    """
    combined_paths = diagnostics.get("combined_frame_paths")
    if combined_paths is None:
        return target_frames
    combined = set(combined_paths)
    return [frame for frame in target_frames if frame.path in combined]


def _base_stack_quality_summary(
    target: Target,
    is_spectral: bool,
    frames_submitted: int,
    target_frames: list[Any],
    excluded_frames: list[Any],
    diagnostics: dict,
) -> StackQualitySummary:
    """Build the `StackQualitySummary` shell before any measured metrics.

    The summary's ``frames_stacked`` is the number of frames that went into
    the stack: the frames of the combined exposure groups, less the unreadable
    files and the frames Siril left out while registering (see
    `stack_membership.count_frames_stacked`).

    Returns
    -------
    summary : `StackQualitySummary`
        Carries the target's session breakdown and the resolved
        stacking parameters. Every measured metric (pixel fractions,
        FWHM, registration) is still at its default.
    """
    from astrometricslib.models.quality.quality_summary import (
        StackingPipelineQualityMetrics,
        StackQualitySummary,
    )
    from astrometricslib.pipelines.shared.target_sessions import (
        build_target_session_breakdown,
        derive_target_sessions,
    )
    from astrometricslib.pipelines.stacking.post_processing.stack_membership import count_frames_stacked

    # Only the frames of the groups that were combined count as stacked, less
    # the unreadable files and the frames Siril left out while registering.
    # The member rows use the same list (see `stack_membership`).
    combined_frames = _frames_in_combined_groups(target_frames, diagnostics)
    frames_stacked = count_frames_stacked(target_frames, diagnostics)

    target_sessions = derive_target_sessions(target.id, combined_frames)
    excluded_paths = {excluded_frame.path for excluded_frame in excluded_frames}
    target_session_breakdown = build_target_session_breakdown(target_sessions, excluded_paths)

    return StackQualitySummary(
        target_id=target.id,
        target_session_ids=[session.id for session in target_sessions],
        target_session_breakdown=target_session_breakdown,
        resolved_parameters={
            "rejection_sigma_low": diagnostics.get("rejection_sigma_low", 0.0),
            "rejection_sigma_high": diagnostics.get("rejection_sigma_high", 0.0),
            "rejection_sigma_mode": diagnostics.get("rejection_sigma_mode", "unknown"),
            "rejection_sigma_floor": diagnostics.get("rejection_sigma_floor"),
            "rejection_sigma_low_extra": diagnostics.get("rejection_sigma_low_extra"),
            "rejection_sigma_floor_applied": diagnostics.get("rejection_sigma_floor_applied", False),
            "filter_wfwhm_requested": diagnostics.get("filter_wfwhm_requested"),
            "filter_wfwhm_effective": diagnostics.get("filter_wfwhm_effective"),
            "filter_wfwhm_loosened": diagnostics.get("filter_wfwhm_loosened", False),
        },
        quality_processing_applied=frames_stacked > 1,
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=is_spectral,
            frames_submitted=frames_submitted,
            frames_stacked=frames_stacked,
            excluded_frames=excluded_frames,
            calibration_mismatch_flags=diagnostics.get("calibration_mismatch_flags", []),
            flat_frame_count=(diagnostics.get("flat_calibration") or {}).get("frame_count"),
            flat_noise_fraction=(diagnostics.get("flat_calibration") or {}).get("noise_fraction"),
            flat_smoothing_sigma_px=(diagnostics.get("flat_calibration") or {}).get("smoothing_sigma_pixels"),
            flat_calibration_issues=list((diagnostics.get("flat_calibration") or {}).get("issues", [])),
            stacking_duration_seconds=diagnostics.get("stacking_duration_seconds"),
            debayer_applied=diagnostics.get("debayer_applied"),
            stacking_engine=diagnostics.get("stacking_engine"),
            stacking_engine_version=diagnostics.get("stacking_engine_version"),
        ),
    )


def _measure_stacked_pixel_fractions(
    summary: StackQualitySummary, stacked_path: str, diagnostics: dict
) -> None:
    """Measure the stacked image's rejected and saturated pixel fractions.

    The rejected share comes from the engine's report (``diagnostics``);
    the saturated share is measured on the stacked file. Sets
    `summary.stacking_metrics`' pixel-fraction fields in place. The limits
    are applied later, by the output gates.
    """
    from astrometricslib.pipelines.shared.quality.quality_metrics import measure_saturated_pixel_fraction

    rejected_fraction = diagnostics.get("rejected_pixel_fraction")
    if rejected_fraction is not None:
        summary.stacking_metrics.rejected_pixel_fraction = rejected_fraction

    saturated_fraction = measure_saturated_pixel_fraction(stacked_path)
    if saturated_fraction is not None:
        summary.stacking_metrics.saturated_pixel_fraction = saturated_fraction


def _record_exposure_groups(summary: StackQualitySummary, diagnostics: dict) -> None:
    """Copy the exposure-group entries and recommended exposure from a run.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The summary being built; its metrics are set in place.
    diagnostics : `dict`
        What the stacking run reported (see `run_stack`).
    """
    from astrometricslib.models.quality.quality_summary import ExposureGroupSummary

    summary.stacking_metrics.exposure_groups = [
        ExposureGroupSummary(**entry) for entry in diagnostics.get("exposure_group_summaries", [])
    ]


def _finalize_stack_quality_flags(summary: StackQualitySummary) -> None:
    """Derive `summary.flagged` and `flag_reasons` from the two judgements.

    The input and output judgements (`summary.input_quality` and
    `summary.output_quality`) supply their own reasons. This adds the ones
    that belong to neither: exposure groups left out and single-frame stacks.
    """
    from astrometricslib.pipelines.stacking.post_processing.assess_output_quality import (
        assess_output_quality,
        output_quality_gates,
    )

    metrics = summary.stacking_metrics
    summary.output_quality = assess_output_quality(metrics, summary.quality_processing_applied)
    output_gates = output_quality_gates(metrics, summary.quality_processing_applied)
    flag_reasons = [*summary.input_quality.flag_reasons, *summary.output_quality.flag_reasons]
    for group in metrics.exposure_groups:
        # A nonlinear group's reason is recorded by the
        # `exposure_group_linearity` gate, which names both brightness
        # ratios, so it is not listed twice.
        if group.left_out_reason and not group.gain_nonlinear:
            flag_reasons.append(exposure_group_left_out_reason(group.exposure_seconds, group.left_out_reason))
    if not summary.quality_processing_applied:
        flag_reasons.append(single_frame_stack_reason())

    summary.flagged = bool(flag_reasons)
    summary.flag_reasons = flag_reasons
    for output_gate in output_gates:
        summary.record_gate(output_gate)


def _build_stack_quality_summary(
    target: Target,
    is_spectral: bool,
    frames_submitted: int,
    target_frames: list[Any],
    excluded_frames: list[Any],
    diagnostics: dict,
    background_split: dict | list[dict] | None,
    stacked_path: str | None,
    gate_results: list[GateResult] | None = None,
) -> StackQualitySummary:
    """Gather diagnostic data into a final `StackQualitySummary`.

    This checks the quality of the stack differently depending on the type
    of image. For regular images, it compares the overall sharpness (FWHM)
    of the final stack against the average sharpness of the original frames.
    For spectroscopy images, it uses a special tracking analysis because
    measuring the sharpness of stretched-out light spectrums doesn't work.

    Returns
    -------
    summary : `StackQualitySummary`
        A report card on how well the stacking went, with warnings if
        something looks wrong.
    """
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
        assess_input_quality,
        describe_background_splits,
    )

    summary = _base_stack_quality_summary(
        target, is_spectral, frames_submitted, target_frames, excluded_frames, diagnostics
    )
    summary.input_quality = assess_input_quality(
        frames_submitted, len(target_frames), excluded_frames, background_split, diagnostics
    )

    split_detail = describe_background_splits(background_split)
    if split_detail is not None:
        summary.stacking_metrics.background_split_detected = True
        summary.stacking_metrics.background_split_detail = split_detail

    _record_exposure_groups(summary, diagnostics)

    if stacked_path:
        _measure_stacked_pixel_fractions(summary, stacked_path, diagnostics)
        measure_calibration_health(summary, stacked_path, diagnostics)

        if not is_spectral and summary.quality_processing_applied:
            update_frame_registration_results(summary, stacked_path, target_frames, diagnostics)
            measure_fwhm_degradation(
                summary,
                stacked_path,
                _frames_in_combined_groups(target_frames, diagnostics),
                stacked_fwhm=diagnostics.get("stacked_fwhm_px"),
            )

        if is_spectral and summary.quality_processing_applied:
            check_spectral_registration_quality(summary, stacked_path, diagnostics)

    _finalize_stack_quality_flags(summary)
    # Recorded after the flags are rebuilt above, so a failed gate's reason is
    # not wiped by that rebuild.
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import (
        exposure_group_linearity_gate,
        exposure_groups_combined_gate,
        raw_frame_clipping_gate,
    )
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import calibration_gates

    linearity_gate = exposure_group_linearity_gate(
        diagnostics.get("exposure_group_summaries", []),
        get_configuration().get_exposure_group_gain_tolerance(),
    )
    # The raw-clipping gate carries the sentence that used to be appended to
    # the flag reasons here; `record_gate` adds it for a failed gate.
    for gate_result in [
        *(gate_results or []),
        *calibration_gates(diagnostics),
        linearity_gate,
        exposure_groups_combined_gate(diagnostics.get("exposure_group_summaries", [])),
        raw_frame_clipping_gate(diagnostics),
    ]:
        summary.record_gate(gate_result)

    from astrometricslib.pipelines.shared.applied_camera_profile import (
        most_common_camera_name,
        record_camera_profile,
    )

    record_camera_profile(summary, most_common_camera_name(target_frames))
    return summary
