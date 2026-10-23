"""Tools for combining (stacking) many images into one.

This file handles running the external stacking software (Siril)
and checking the quality of the final stacked images.
"""

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrometricslib.drivers.camera_profile_store import camera_identity
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import AstrometricsError, ConflictError, ProcessingError
from astrometricslib.foundation.jobs.runner import get_current_job
from astrometricslib.models.quality.gate_result import GateResult, unchecked_gate
from astrometricslib.models.target import Target
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
    filter_type: Any | None = None,
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
    filter_type : `Any` or `None`, optional
        Only stack images taken with this specific filter (like 'L' or 'HA').
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

    Raises
    ------
    ConflictError
        If the target has no frames to stack, or mixes spectral and
        standard imaging frames. `ensure_single_camera` raises it too
        when the frames come from more than one camera.
    ProcessingError
        If no usable frames are left after filtering out mismatched or
        bad frames.
    """
    if frames_to_stack is not None:
        target_frames = frames_to_stack
    elif filter_type is not None:
        from astrometricslib.models.target import FrameRecord

        norm_filter = FrameRecord.normalize_filter(filter_type)
        if not isinstance(norm_filter, FilterType):
            for ft in FilterType:
                if (
                    ft.name.upper() == str(filter_type).upper()
                    or ft.value.upper() == str(filter_type).upper()
                ):
                    norm_filter = ft
                    break
        target_frames = [
            f
            for f in target.frames
            if f.filter == norm_filter
            and not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]
    else:
        target_frames = [
            f
            for f in target.frames
            if not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]

    if not target_frames:
        raise ConflictError("Target has no frames available to stack.")

    # Frames from different cameras cannot be stacked together. Without this
    # check the gain filter below would keep whichever camera had more frames.
    from astrometricslib.pipelines.stacking.pre_processing.camera_selection import ensure_single_camera

    ensure_single_camera(target_frames)

    _report_stage(5, f"Checking {len(target_frames)} frames")

    # Validate that only homogeneous frame types are stacked (no mixed
    # spectral/standard frames)
    from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral

    has_spectral = any(frame_is_spectral(f) for f in target_frames)
    has_standard = any(not frame_is_spectral(f) for f in target_frames)
    if has_spectral and has_standard:
        raise ConflictError(
            "Target contains a mixed set of spectral ('SPEC') and standard imaging frames. "
            "Stacking mixed frame types is not permitted."
        )

    if output_file is None:
        safe_target_id = target.id.replace(" ", "_")
        filter_tag = None
        if has_spectral:
            filter_tag = "SPEC"
        elif filter_type is not None:
            if hasattr(filter_type, "name"):
                filter_tag = filter_type.name.upper()
            else:
                filter_tag = str(filter_type).upper()
        else:
            filters = {getattr(f, "filter", None) for f in target_frames}
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

        configuration_tag = _disambiguating_configuration_tag(target, target_frames)
        if configuration_tag:
            output_file = output_file.removesuffix(".fits") + f"_{configuration_tag}.fits"

    frames_submitted = len(target_frames)
    from astrometricslib.models.quality.quality_summary import ExcludedFrame

    excluded_frames: list[ExcludedFrame] = []
    gate_results: list[GateResult] = []

    # Ensure all frames have the same camera gain setting. Stacking frames
    # with different gains messes up the noise calculation, because each
    # gain setting has a different amount of read noise and dark current.
    # To protect the final image, we find the most common gain setting
    # and throw out any frames that don't match it.
    from astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity import (
        find_dominant_gain_subset,
        gain_homogeneity_gate,
    )

    frames_before_gain_check = target_frames
    target_frames, excluded_by_gain = find_dominant_gain_subset(target_frames)
    gate_results.append(gain_homogeneity_gate(frames_before_gain_check, excluded_by_gain))
    if excluded_by_gain:
        logger.warning(
            "Excluding %s frame(s) with a minority gain setting from the stack for target '%s': %s",
            len(excluded_by_gain),
            target.id,
            [f.path for f in excluded_by_gain],
        )
        excluded_frames.extend(
            ExcludedFrame(path=f.path, reason="minority gain setting") for f in excluded_by_gain
        )
    if not target_frames:
        raise ProcessingError("Target has no frames available to stack after gain-homogeneity filtering.")

    # A frame an earlier run moved into `_excluded` can still be listed on the
    # target, if that run's save of the target did not last. Count it as set
    # aside, not as a frame that was kept.
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
        drop_frames_already_set_aside,
    )

    target_frames, already_set_aside = drop_frames_already_set_aside(
        target, target_frames, str(get_configuration().get_frames_path())
    )
    if already_set_aside:
        logger.info(
            "Left out %d frame(s) that an earlier run moved into _excluded for target '%s'.",
            len(already_set_aside),
            target.id,
        )
        excluded_frames.extend(
            ExcludedFrame(path=frame.path, reason="set aside by an earlier quarantine")
            for frame in already_set_aside
        )
    if not target_frames:
        raise ProcessingError("Target has no frames available to stack after leaving out set-aside frames.")

    # Move frames with clouds or trailed stars out of the target's folder.
    # The background check below only catches one sudden jump in sky
    # brightness, and the stacker's weighting only lowers the influence of a
    # bad frame. Moving the frame aside keeps it out of this stack and every
    # later one, and `ProcessingPipelines.restore_excluded_frames` puts it
    # back. Spectral frames are skipped: their streaks are the spectra
    # themselves.
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
        GATE_NAME as QUARANTINE_GATE_NAME,
    )

    if has_spectral:
        gate_results.append(
            unchecked_gate(
                QUARANTINE_GATE_NAME, "spectral frames are not checked: their streaks are the spectra"
            )
        )
    elif not get_configuration().get_quarantine_bad_frames_enabled():
        gate_results.append(
            unchecked_gate(QUARANTINE_GATE_NAME, "frame quarantine is turned off in the settings")
        )
    if not has_spectral and get_configuration().get_quarantine_bad_frames_enabled():
        from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
            quarantine_bad_frames,
            quarantine_gate,
        )

        frames_checked_by_quarantine = target_frames
        target_frames, quarantine_report = quarantine_bad_frames(target, target_frames)
        _record_frame_quality(
            target,
            target_frames,
            quarantine_report,
            frames_checked_by_quarantine,
            measured_at,
            quality_recording,
        )
        excluded_frames.extend(
            ExcludedFrame(path=path, reason=reason)
            for path, reason in quarantine_report.reasons_by_path().items()
        )
        for note in quarantine_report.notes:
            logger.info("Quarantine check for target '%s': %s", target.id, note)
        gate_results.append(quarantine_gate(quarantine_report))
        if not target_frames:
            raise ProcessingError("Target has no frames available to stack after quarantining bad frames.")

    # Check for sudden changes in the sky background (like clouds moving in).
    # Standard calibration and pixel rejection aren't enough to catch these
    # massive, image-wide changes. Because checking every frame takes extra
    # time, this feature can be turned on or off in the settings using
    # `get_background_homogeneity_check_enabled`.
    from astrometricslib.foundation.config import get_configuration

    background_split = None
    background_check_enabled = get_configuration().get_background_homogeneity_check_enabled()
    frames_before_background_check = target_frames
    if background_check_enabled:
        from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
        from astrometricslib.pipelines.shared.quality.background_measurement import (
            measure_frame_background_level,
            measure_frame_saturated_pixel_fraction,
        )
        from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
            find_dominant_background_subset_by_exposure,
        )

        for frame in target_frames:
            try:
                # Computed once, recorded onto the FrameRecord (which
                # rides along with Target's normal save path) -- later
                # pipelines/runs read these instead of recomputing them.
                if frame.measurements.background_level is None:
                    frame.measurements.background_level = measure_frame_background_level(frame.path)
                if frame.measurements.saturated_pixel_fraction is None:
                    camera_profile = resolve_camera_profile(frame.camera)
                    frame.measurements.saturated_pixel_fraction = measure_frame_saturated_pixel_fraction(
                        frame.path, camera_profile.saturation_threshold_adu.value
                    )
            except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as exc:
                logger.debug("Skipping background/saturation measurement for '%s': %s", frame.path, exc)
                continue

        # Throw out frames with a different background level. If we
        # accidentally
        # use a washed-out frame as the main reference for aligning the images,
        # the software won't be able to find any sharp stars to lock onto. This
        # would cause the alignment to fail and crash the entire stacking
        # process. The check is made within each exposure length: the sky
        # background grows with the exposure, so frames of different lengths
        # cannot be compared (all 14 of the 60 s frames of NGC 2403 were once
        # excluded because the 300 s frames were brighter).
        target_frames, excluded_by_background, background_split = find_dominant_background_subset_by_exposure(
            target_frames
        )
        if background_split:
            logger.warning(
                "Background-homogeneity split detected for target '%s': %s", target.id, background_split
            )
        if excluded_by_background:
            logger.warning(
                "Excluding %s frame(s) from a different sky condition from the stack for target '%s': %s",
                len(excluded_by_background),
                target.id,
                [f.path for f in excluded_by_background],
            )
            excluded_frames.extend(
                ExcludedFrame(path=f.path, reason="background-homogeneity split")
                for f in excluded_by_background
            )
        if not target_frames:
            raise ProcessingError(
                "Target has no frames available to stack after background-homogeneity filtering."
            )

    from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
        background_homogeneity_gate,
    )

    gate_results.append(
        background_homogeneity_gate(
            frames_before_background_check, background_split, background_check_enabled
        )
    )

    _report_stage(15, f"Frame checks done, {len(target_frames)} frames kept")

    from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
        record_preview_as_processed_image,
        write_stack_preview,
    )
    from astrometricslib.pipelines.stacking.stack_runner import run_stack

    engine = (drivers or Drivers()).stacking_or_default()
    from astrometricslib.pipelines.stacking.pre_processing.exposure_weighting import choose_stack_weight

    stack_weight = choose_stack_weight(
        [float(frame.exposure) for frame in target_frames if frame.exposure],
        stack_weight if stack_weight is not None else get_configuration().get_stack_weight(),
        is_spectral=has_spectral,
    )

    # Skip a stack that would come out the same: the same frames, the same
    # calibration frames, the same settings and the same stacking code as when
    # the stack on disk was made (see `stack_inputs.py`). Adding frames to one
    # filter then leaves the target's other stacks alone.
    from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import (
        build_stack_inputs_record,
        decide_whether_to_restack,
        write_stack_inputs,
    )

    expected_path = _expected_stack_path(target.id, output_file)
    inputs_record = build_stack_inputs_record(
        target_frames,
        get_configuration(),
        {
            "rejection_sigma": rejection_sigma,
            "filter_wfwhm": filter_wfwhm,
            "filter_round": filter_round,
            "stack_weight": stack_weight,
            "generate_rejmap": generate_rejmap,
            "is_spectral": has_spectral,
        },
    )
    decision = decide_whether_to_restack(
        expected_path,
        inputs_record,
        force=force,
        enabled=get_configuration().get_skip_unchanged_stacks_enabled(),
    )
    if decision.skip:
        logger.info(
            "Skipping the stack of '%s' at %s: its frames, calibration frames, settings and the "
            "stacking code are the same as when it was made. Pass force=True to rebuild it.",
            target.id,
            expected_path,
        )
        _record_unchanged_stack(target, target_frames, expected_path, has_spectral)
        return expected_path
    if os.path.isfile(expected_path):
        logger.info("Rebuilding the stack of '%s': %s.", target.id, "; ".join(decision.reasons))

    # `run_stack` adds two safeguards for spectroscopy: frames of
    # different exposure lengths are stacked one length at a time and then
    # combined, and a registration that loses too many frames is retried with
    # a different star detection.
    # A restack overwrites the stack file. The old stack and its pictures wait
    # in a staging folder until the new stack exists; if the restack fails,
    # they go back where they were.
    staging = _archive_stack_before_restack(target.id, output_file)
    _report_stage(20, f"Calibrating, aligning and stacking {len(target_frames)} frames in Siril")
    try:
        stacked_path, diagnostics = run_stack(
            engine,
            target_frames,
            target.id,
            output_file,
            log_file,
            has_spectral,
            job_id=job_id,
            rejection_sigma=rejection_sigma,
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
            stack_weight=stack_weight,
            generate_rejmap=generate_rejmap,
        )
    except BaseException:
        _finish_stack_archive(staging, output_file, target.id, kept=False)
        raise
    _finish_stack_archive(staging, output_file, target.id, kept=bool(stacked_path))
    _report_stage(70, "Siril stack finished" if stacked_path else "Siril produced no stack")

    # Frames shift between nights, so the edges of a stack of several nights
    # are covered by fewer frames and are noisier. Trim them off. The check
    # leaves a stack with clean edges as it is. It runs before the stack is
    # measured and pictured, so both describe the trimmed stack.
    crop_offset = (0, 0)
    if stacked_path and not has_spectral and get_configuration().get_trim_noisy_stack_edges_enabled():
        from astrometricslib.pipelines.stacking.post_processing.stack_crop import crop_stack_edges

        crop_box = crop_stack_edges(stacked_path)
        if crop_box is not None:
            crop_offset = (crop_box.x0, crop_box.y0)
    excluded_frames.extend(
        ExcludedFrame(path=path, reason="corrupt or unreadable FITS file")
        for path in diagnostics.get("corrupt_frames_skipped", [])
    )

    summary = _build_stack_quality_summary(
        target=target,
        is_spectral=has_spectral,
        frames_submitted=frames_submitted,
        target_frames=target_frames,
        excluded_frames=excluded_frames,
        diagnostics=diagnostics,
        background_split=background_split,
        stacked_path=stacked_path,
        gate_results=gate_results,
    )

    if has_spectral:
        target.spectral_stacking.quality_summary = summary
    else:
        target.stacking.quality_summary = summary

    if stacked_path:
        if has_spectral:
            target.spectral_stacking.stacked_image = stacked_path
        elif _record_configuration_stack(target, target_frames, stacked_path):
            target.stacking.stacked_image = stacked_path

        from astrometricslib.models.provenance import DatasetEntity
        from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
        from astrometricslib.pipelines.shared.provenance_recording import (
            record_pipeline_run,
            stacked_image_entity_id,
        )

        entity_id = stacked_image_entity_id(target.id, stacked_path)
        record_pipeline_run(
            summary=summary,
            job_id=job_id,
            target_id=target.id,
            pipeline_name="stacking",
            pipeline_version=STACKING_PIPELINE_VERSION,
            generated_entities={
                entity_id: DatasetEntity(
                    id=entity_id, location=stacked_path, entity_description="entitydesc:stacked-image"
                )
            },
        )
        # The same summary, the stack's noise in empty sky and the frames
        # that went in, stored as measurements. A failure here never fails
        # the stack.
        _record_stack_quality(
            summary,
            target.id,
            stacked_path,
            target_frames,
            diagnostics,
            stack_weight,
            crop_offset,
            job_id,
            measured_at,
            quality_recording,
        )
        # A picture for people to look at. It never changes the stack and a
        # failure to make it is logged, not raised. The image viewer shows it
        # through the target's processed image.
        _report_stage(80, "Making the preview picture (GraXpert, Cosmic Clarity, star toning)")
        preview_path = write_stack_preview(stacked_path, preview_settings)
        if preview_path:
            record_preview_as_processed_image(target, has_spectral, stacked_path, preview_path)
        # Saved last, once the stack, its quality summary and its picture are
        # done: a stack with a record is one the next run may skip, so a run
        # that stopped before this point leaves a stack that is built again.
        if inputs_record is not None:
            write_stack_inputs(stacked_path, inputs_record)
        # One closing line, so a reader of the log does not have to piece the
        # outcome together. The preview steps line above says which cleanup
        # programs ran.
        logger.info(
            "Stack of '%s' finished: stack %s; preview %s; %d frames submitted, %d set aside.",
            target.id,
            stacked_path,
            preview_path or "not made (see the warning above)",
            frames_submitted,
            len(excluded_frames),
        )

    return stacked_path


def _record_frame_quality(
    target: Target,
    kept_frames: list[Any],
    quarantine_report: Any,
    frames_checked: list[Any],
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
) -> None:
    """Store the quarantine step's frame numbers and any missing measurements.

    This never stops a stack. Any failure, whatever its cause (a storage
    error, or an unexpected error such as a ``RuntimeError`` or
    ``MemoryError`` from the image libraries), is logged with its traceback and
    the stack goes on, because the stack does not depend on these numbers.

    The hook runs only for imaging stacks with the quarantine step turned on,
    because it sits inside that step. It does not read the global
    configuration: the caller supplies the configuration and the shared
    calibration library in ``quality_recording``.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    kept_frames : `list`
        The frames that stay in the stack.
    quarantine_report : `QuarantineReport`
        What the quarantine step measured and moved.
    frames_checked : `list`
        Every frame given to the quarantine step, moved ones included.
    measured_at : `datetime` or `None`
        When the stack started, passed in by the caller because pipeline code
        does not read the clock. Nothing is stored when it is `None`.
    quality_recording : `FrameQualityRecording` or `None`
        The configuration and calibration library to store with. Nothing is
        stored when it is `None`.
    """
    from astrometricslib.pipelines.stacking.frame_measurement_run import record_stage_frame_quality

    if measured_at is None or quality_recording is None:
        logger.warning(
            "No measurement time or recording context was given, so frame quality of '%s' is not stored. "
            "A caller of the stacking stage should pass measured_at and quality_recording.",
            target.id,
        )
        return
    try:
        summary = record_stage_frame_quality(
            target,
            kept_frames,
            quarantine_report.measurements,
            frames_checked,
            quality_recording,
            measured_at,
        )
    except Exception:  # frame quality is a by-product: no failure here may fail the stack
        logger.exception("Frame quality measurements of target '%s' were not stored", target.id)
        return
    if summary.rows_written or summary.frames_failed or summary.already_running:
        logger.info(
            "Stored %d frame quality row(s) for target '%s' (%d frame(s) measured, %d failed%s).",
            summary.rows_written,
            target.id,
            summary.frames_measured,
            summary.frames_failed,
            "; the full measurement was skipped because another run has the turn"
            if summary.already_running
            else "",
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


def _disambiguating_configuration_tag(target, target_frames) -> str:  # ruff: ignore[missing-type-function-argument]
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


def _record_configuration_stack(target, target_frames: list[Any], stacked_path: str) -> bool:  # ruff: ignore[missing-type-function-argument]
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

    # Only the frames of the groups that were combined count as stacked.
    combined_frames = _frames_in_combined_groups(target_frames, diagnostics)
    frames_stacked = len(combined_frames) - len(diagnostics.get("corrupt_frames_skipped", []))

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


def _measure_stacked_pixel_fractions(summary, stacked_path: str, diagnostics: dict) -> None:  # ruff: ignore[missing-type-function-argument]
    """Measure the stacked image's rejected and saturated pixel fractions.

    The rejected share comes from the engine's report (``diagnostics``);
    the saturated share is measured on the stacked file. Sets
    `summary.stacking_metrics`' pixel-fraction fields and their threshold
    flags in place.
    """
    from astrometricslib.pipelines.shared.quality.quality_metrics import measure_saturated_pixel_fraction
    from astrometricslib.pipelines.shared.quality.saturation import is_saturation_significant
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
        is_rejected_fraction_significant,
    )

    rejected_fraction = diagnostics.get("rejected_pixel_fraction")
    if rejected_fraction is not None:
        summary.stacking_metrics.rejected_pixel_fraction = rejected_fraction
        summary.stacking_metrics.rejected_fraction_flagged = is_rejected_fraction_significant(
            rejected_fraction
        )

    saturated_fraction = measure_saturated_pixel_fraction(stacked_path)
    if saturated_fraction is not None:
        summary.stacking_metrics.saturated_pixel_fraction = saturated_fraction
        summary.stacking_metrics.saturation_flagged = is_saturation_significant(saturated_fraction)


def _update_frame_registration_results(
    summary,  # ruff: ignore[missing-type-function-argument]
    stacked_path: str,
    target_frames: list[Any],
    diagnostics: dict,
) -> None:
    """Record Siril's per-frame registration facts, find the reference frame.

    Per-frame registration facts (Siril's own findstar pass, computed
    for free during registration) are recorded onto each `FrameRecord`
    for later pipelines/runs to read, not used for any verdict here.
    Frame order in the .seq file follows Siril's own symlink submission
    order, which can differ from `target_frames`' order (corrupt/wrong-
    camera frames get filtered during symlinking) --
    `symlinked_light_paths` is the authoritative alignment reference,
    same discipline the spectral registration check uses.

    Also identifies the registration reference frame -- the one Siril
    aligned every other frame to, i.e. the frame whose transform is the
    identity (no shift of its own) -- and records it onto
    `summary.stacking_metrics`.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
        registration_reference_from_diagnostics,
    )

    registration_frames = diagnostics.get("registration_frames", [])
    registration_frame_paths = diagnostics.get("symlinked_light_paths", [])

    # Siril aligns every frame to one reference, so a poor reference fails the
    # whole stack -- M 42 aborted with "Found 0 stars in reference" and
    # nothing recorded which frame that was. The reference is the frame whose
    # transform is the identity; `registration_reference_from_diagnostics`
    # names it only when it is certain.
    reference = registration_reference_from_diagnostics(diagnostics)
    if reference is not None:
        summary.stacking_metrics.registration_reference_frame = reference[0]
        summary.stacking_metrics.registration_reference_star_count = reference[1]

    if len(registration_frame_paths) == len(registration_frames):
        frame_by_path = {frame.path: frame for frame in target_frames}
        for frame_path, registration_facts in zip(
            registration_frame_paths, registration_frames, strict=False
        ):
            frame = frame_by_path.get(frame_path)
            if frame is None:
                continue
            # Normally the first run's measurements win, so a
            # target stacked twice (standard then spectral) does
            # not have its facts clobbered by the second pass.
            # The exception is a frame carrying shifts that are
            # known to be degenerate: runs before the
            # registration-sequence fix preserved the *registered*
            # sequence, whose frames are already aligned and so
            # recorded dx=dy=0 for every frame. A non-zero shift
            # now available for that same frame is real data
            # replacing a known-bad zero, so it is allowed
            # through. A genuine reference frame also has
            # dx=dy=0, but this run offers 0 for it too, so it is
            # never rewritten.
            has_existing_facts = frame.measurements.registration_fwhm_x_px is not None
            stored_shift_is_degenerate = (
                not frame.measurements.registration_dx_px and not frame.measurements.registration_dy_px
            )
            run_offers_real_shift = bool(registration_facts["dx"] or registration_facts["dy"])
            if has_existing_facts and not (stored_shift_is_degenerate and run_offers_real_shift):
                continue
            # `registration_fwhm_y_px` and `registration_rmse` are no longer
            # filled: their old values were the weighted FWHM and the sky
            # background level (see `FrameMeasurements`). The field named
            # `..._fwhm_x_px` holds Siril's plain FWHM.
            frame.measurements.registration_fwhm_x_px = registration_facts["fwhm_px"]
            frame.measurements.registration_weighted_fwhm_px = registration_facts["weighted_fwhm_px"]
            frame.measurements.registration_roundness = registration_facts["roundness"]
            frame.measurements.registration_quality = registration_facts["quality"]
            frame.measurements.registration_background_level = registration_facts["background_level"]
            frame.measurements.registration_rotation_deg = registration_facts["rotation_deg"]
            frame.measurements.registration_star_count = registration_facts["star_count"]
            frame.measurements.registration_dx_px = registration_facts["dx"]
            frame.measurements.registration_dy_px = registration_facts["dy"]


def _measure_one_frame_fwhm(path: str) -> float | None:
    """Measure one frame's star width, or give `None`.

    A module-level function so a worker process can run it. A frame that
    cannot be read or measured is logged and skipped, not raised.

    Parameters
    ----------
    path : `str`
        Path of the frame to measure.

    Returns
    -------
    fwhm : `float` or `None`
        The median star width in pixels, or `None` if there is no width.
    """
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_image_fwhm

    try:
        return measure_image_fwhm(path)
    except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as exc:
        logger.debug("Skipping FWHM measurement for '%s': %s", path, exc)
        return None


def _fwhm_worker_count(sample_size: int) -> int:
    """Choose how many processes measure the sample frames at once.

    Uses the configured inner worker count (``photometry_workers``, the
    same setting the other per-target parallel steps use), kept within the
    available memory by `resolve_worker_counts`. One measurement of a 24
    megapixel frame was measured to use about 1 GB; the helper's default
    budget of 1.5 GB per worker covers that with margin. The count never
    exceeds the sample size. A process that is itself a daemon (such as a
    `multiprocessing.Pool` worker) may not have child processes, so it
    measures serially.

    Parameters
    ----------
    sample_size : `int`
        Number of frames to measure.

    Returns
    -------
    worker_count : `int`
        Number of worker processes; 1 means measure in this process.
    """
    import multiprocessing

    if sample_size < 2 or multiprocessing.current_process().daemon:
        return 1
    try:
        from astrometricslib.foundation.config import get_configuration
        from astrometricslib.utilities.concurrency import resolve_worker_counts

        counts = resolve_worker_counts("1", get_configuration().get_photometry_workers())
    except (AstrometricsError, OSError, ValueError) as configuration_error:
        logger.debug("Measuring FWHM serially; worker count unavailable: %s", configuration_error)
        return 1
    return max(1, min(counts.inner_worker_count, sample_size))


def _measure_sample_fwhms(paths: list[str], worker_count: int) -> list[float | None]:
    """Measure the star width of each frame, keeping the order of `paths`.

    Parameters
    ----------
    paths : `list` [`str`]
        Frame paths to measure.
    worker_count : `int`
        Number of worker processes; 1 measures in this process.

    Returns
    -------
    fwhms : `list` [`float` or `None`]
        One width per path, in the same order; `None` where a frame was
        skipped.
    """
    if worker_count > 1 and len(paths) > 1:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        try:
            # 'fork' like the photometry pool: 'spawn' re-imports the entry
            # script, which fails for scripts run as `python script.py`.
            with ProcessPoolExecutor(
                max_workers=worker_count, mp_context=multiprocessing.get_context("fork")
            ) as executor:
                return list(executor.map(_measure_one_frame_fwhm, paths))
        except (BrokenProcessPool, OSError) as pool_error:
            logger.warning("Parallel FWHM measurement failed (%s); measuring serially.", pool_error)
    return [_measure_one_frame_fwhm(path) for path in paths]


def _measure_fwhm_degradation(
    summary: Any, stacked_path: str, target_frames: list[Any], stacked_fwhm: float | None = None
) -> None:
    """Compare the stacked image's sharpness against its input frames.

    Measured with the same `measure_image_fwhm` function on both sides. The
    input sample is spread evenly over all the frames, so a stack of several
    nights is judged against all of them and not only the first night. The
    stack is compared with the width its inputs predict (see
    `expected_stack_fwhm`), not with their median.

    A stack combined from exposure groups arrives with its width already
    measured, away from the cores that the combine patched (see
    `stack_runner._measure_combined_fwhm`); that value is used instead of
    measuring the file, which would pick those patched cores as its
    brightest stars.

    Sets `summary.stacking_metrics`' FWHM fields and the degradation
    flag in place.
    """
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import (
        FWHM_MEASUREMENT_STAR_COUNT,
        measure_image_fwhm,
    )
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
        expected_stack_fwhm,
        is_stacked_fwhm_degraded,
    )

    # A sample of at most 15 frames (matching FWHM_MEASUREMENT_STAR_COUNT),
    # spaced evenly over the whole set. A median or an RMS only needs a
    # representative sample, unlike the background-split check, which needs
    # every frame to avoid missing a split.
    step = max(1, len(target_frames) // FWHM_MEASUREMENT_STAR_COUNT)
    sample_paths = [frame.path for frame in target_frames[::step][:FWHM_MEASUREMENT_STAR_COUNT]]
    input_fwhms = [
        fwhm
        for fwhm in _measure_sample_fwhms(sample_paths, _fwhm_worker_count(len(sample_paths)))
        if fwhm is not None
    ]
    expected = expected_stack_fwhm(input_fwhms)
    if input_fwhms:
        import statistics as _statistics

        summary.stacking_metrics.median_input_fwhm_px = _statistics.median(input_fwhms)
        summary.stacking_metrics.expected_stack_fwhm_px = expected

    if stacked_fwhm is None:
        stacked_fwhm = measure_image_fwhm(stacked_path)
    if stacked_fwhm is not None:
        summary.stacking_metrics.stacked_fwhm_px = stacked_fwhm
        if expected is not None:
            summary.stacking_metrics.fwhm_degraded = is_stacked_fwhm_degraded(stacked_fwhm, expected)


def _check_spectral_registration_quality(summary, stacked_path: str, diagnostics: dict) -> None:  # ruff: ignore[missing-type-function-argument]
    """Flag frames with spectral zero-order-star tracking concerns.

    Checks matched star count, fit RMSE, and position/brightness
    stability for the zero-order star -- not a whole-field FWHM
    comparison -- see `registration_quality.py`'s module docstring for
    why. Requires `frame_paths`, `seq_frames`, and `zero_order_stars`
    to be index-aligned; all three are populated by
    `siril_interface.py` during the same registration pass the real
    stack already ran (`parse_seq_file` reads the preserved .seq file,
    mirroring the standard-imaging FWHM check; `zero_order_stars` comes
    from `diagnostics` since spectral .lst files aren't preserved as
    files, only parsed in place before the scratch directory is
    cleaned up).

    Sets `summary.stacking_metrics.spectral_registration_flags` in place.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.spectroscopy.utilities.registration_quality import (
        evaluate_spectral_registration_quality,
    )

    seq_frames = diagnostics.get("registration_frames", [])
    zero_order_stars = diagnostics.get("zero_order_stars", [])
    frame_paths = diagnostics.get("symlinked_light_paths", [])

    if len(frame_paths) == len(seq_frames) == len(zero_order_stars) and frame_paths:
        flagged = evaluate_spectral_registration_quality(frame_paths, seq_frames, zero_order_stars)
        summary.stacking_metrics.spectral_registration_flags = [ExcludedFrame(**entry) for entry in flagged]
        summary.stacking_metrics.spectral_registration_checked = True


def _record_exposure_groups(summary, diagnostics: dict) -> None:  # ruff: ignore[missing-type-function-argument]
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
    summary.stacking_metrics.recommended_exposure_seconds = diagnostics.get("recommended_exposure_seconds")


def _measure_calibration_health(
    summary,  # ruff: ignore[missing-type-function-argument]
    stacked_path: str,
    is_spectral: bool,
    diagnostics: dict,
) -> None:
    """Record whether calibration left the stack mostly zeros.

    Two signs are used: the share of exactly-zero pixels in the stack (for a
    colour stack, the share in its worst channel), and the
    worst "many negative pixels" percentage Siril printed after subtracting the
    dark. Both are recorded for every stack. The zero share is flagged only for
    images, because the sky of a spectral stack is legitimately at or below
    zero.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The summary being built; its metrics are set in place.
    stacked_path : `str`
        The stacked image to measure.
    is_spectral : `bool`
        Whether the stack is spectral.
    diagnostics : `dict`
        What the stacking run reported.
    """
    import numpy as np

    from astrometricslib.drivers.fits_access import read_data
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
        is_negative_pixel_percent_significant,
        is_zero_fraction_significant,
        zero_fraction_threshold,
    )

    metrics = summary.stacking_metrics
    try:
        data = np.asarray(read_data(stacked_path))
    except OSError as read_error:
        logger.warning("Could not read '%s' to check for a blank stack: %s", stacked_path, read_error)
    else:
        # A colour stack is judged by its worst channel: one dead channel
        # (the red of a Nikon stack whose bias was subtracted twice) gives a
        # strongly tinted picture even when the other channels are fine.
        planes = list(data) if data.ndim == 3 else [data]
        metrics.zero_pixel_fraction = max(
            float(np.count_nonzero(plane == 0) / plane.size) for plane in planes
        )
        metrics.zero_fraction_flagged = is_zero_fraction_significant(
            metrics.zero_pixel_fraction, zero_fraction_threshold(is_spectral)
        )
    negative_percent = diagnostics.get("negative_pixel_max_percent")
    if negative_percent is not None:
        metrics.negative_pixel_max_percent = int(negative_percent)
        metrics.negative_pixels_flagged = is_negative_pixel_percent_significant(negative_percent)


def _finalize_stack_quality_flags(summary) -> None:  # ruff: ignore[missing-type-function-argument]
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
            flag_reasons.append(f"the {group.exposure_seconds:g} s exposure group {group.left_out_reason}")
    if not summary.quality_processing_applied:
        flag_reasons.append("single-frame stack: no rejection/registration quality processing applied")

    summary.flagged = bool(flag_reasons)
    summary.flag_reasons = flag_reasons
    for output_gate in output_gates:
        summary.record_gate(output_gate)


def _build_stack_quality_summary(  # ruff: ignore[missing-return-type-private-function]
    target,  # ruff: ignore[missing-type-function-argument]
    is_spectral: bool,
    frames_submitted: int,
    target_frames: list[Any],
    excluded_frames: list[Any],
    diagnostics: dict,
    background_split: dict | list[dict] | None,
    stacked_path: str | None,
    gate_results: list[GateResult] | None = None,
):
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
        _measure_calibration_health(summary, stacked_path, is_spectral, diagnostics)

        if not is_spectral and summary.quality_processing_applied:
            _update_frame_registration_results(summary, stacked_path, target_frames, diagnostics)
            _measure_fwhm_degradation(
                summary,
                stacked_path,
                _frames_in_combined_groups(target_frames, diagnostics),
                stacked_fwhm=diagnostics.get("stacked_fwhm_px"),
            )

        if is_spectral and summary.quality_processing_applied:
            _check_spectral_registration_quality(summary, stacked_path, diagnostics)

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
