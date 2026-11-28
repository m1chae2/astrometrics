"""Purpose: Screen the frames of a stack before the stacking program sees them.

Description: A stack is only as good as the frames that go into it. This
module runs the checks that leave out frames which would harm the stack: a
minority camera gain setting, frames an earlier run already set aside, clouds
and trailed stars (the set-aside step), a different sky background, and
frames of an exposure length that has too few frames and no group near enough
to be folded into. Each
check records which frames it left out and a `GateResult` that says what it
looked at. The checks run in a fixed order, and `screen_frames` is the one
entry point the stacking stage calls.

The module also holds the two helpers those checks share with the stage's
other users: `refresh_saturated_pixel_fraction` and `record_frame_quality`.
"""

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import AstrometricsError, ProcessingError
from astrometricslib.foundation.run_abandonment import (
    RunAbandonedError,
    completed_unless_abandoned,
    raise_if_abandoned,
    stopping_on_abandonment,
)
from astrometricslib.models.quality.gate_result import GateResult, unchecked_gate
from astrometricslib.models.target import Target
from astrometricslib.pipelines.stacking.stage_progress import LoopProgress, stage_window
from astrometricslib.utilities.exceptions import DATA_ERRORS

if TYPE_CHECKING:
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

logger = logging.getLogger(__name__)

#: Where each long check sits inside the frame-check stage, as (start, span)
#: fractions of the stage. Rough guesses from which checks read every frame
#: (the cloud and trailing check reads all of them, the full measurement every
#: frame not yet measured, the sky check one pass); they only shape the job's
#: progress bar.
SET_ASIDE_WINDOW = (0.02, 0.43)
QUALITY_ROWS_WINDOW = (0.45, 0.35)
BACKGROUND_WINDOW = (0.80, 0.20)

# The reason recorded for a frame, by the check that left it out. The stack
# check (`predict_stack`) matches on these to say which check it was.
MINORITY_GAIN_REASON = "minority gain setting"
EARLIER_SET_ASIDE_REASON = "set aside by an earlier set-aside check"
BACKGROUND_SPLIT_REASON = "background-homogeneity split"
# The start of the reason recorded for a frame whose exposure length has no
# group to join; the rest of the sentence names the length, the nearest group
# and the frame count (see `plan_exposure_folding`).
EXPOSURE_NOT_FOLDABLE_REASON_PREFIX = "left out of every exposure group: "

__all__ = [
    "BACKGROUND_SPLIT_REASON",
    "EARLIER_SET_ASIDE_REASON",
    "EXPOSURE_NOT_FOLDABLE_REASON_PREFIX",
    "MINORITY_GAIN_REASON",
    "FrameScreening",
    "record_frame_quality",
    "refresh_saturated_pixel_fraction",
    "screen_frames",
]


@dataclass
class FrameScreening:
    """The frames left, and what the checks said, as screening runs.

    Parameters
    ----------
    frames : `list`
        The frames still in the stack. Each check replaces this list with
        the frames it kept.
    excluded_frames : `list` [`ExcludedFrame`]
        Every frame the checks left out so far, each with its reason, in the
        order the checks ran.
    gate_results : `list` [`GateResult`]
        One result for each check that has run, in the order they ran.
    background_split : `dict` or `list` [`dict`] or `None`
        What the background check found, when it found a split in the sky
        background; otherwise `None`.
    flagged_frames : `list` [`SetAsideDecision`]
        Frames the set-aside step kept with a warning (an uneven sky with
        normal stars), each with its reasons.
    set_aside_report : `SetAsideReport` or `None`
        What the set-aside step found, when it ran: the session notes, the
        warnings and the frames it could not read.
    """

    frames: list[Any]
    excluded_frames: list[ExcludedFrame] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)
    background_split: dict | list[dict] | None = None
    flagged_frames: list[Any] = field(default_factory=list)
    set_aside_report: Any = None


def screen_frames(
    target: Target,
    target_frames: list[Any],
    has_spectral: bool,
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
    dry_run: bool = False,
) -> FrameScreening:
    """Run the frame checks in order and give back the frames that pass.

    The order is: minority gain, frames already set aside, the set-aside check
    step (clouds and trailed stars), the sky background, and last the
    exposure lengths with no group to join (last, so that the frame counts
    that decide the groups are the final ones). The set-aside check
    step moves files and stores frame quality rows, so the order and timing
    of these steps must not change.

    With ``dry_run`` the same checks make the same decisions but change
    nothing: no file is moved, no frame quality row is stored, and the
    target is left as it was (the checks work on copies of the frames). A
    check that leaves no frames does not raise; the result simply holds no
    frames and the later checks are skipped. `check_stack` uses this, so a
    check and a real stack cannot disagree about which frames are set aside.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    target_frames : `list`
        The frames offered to the stack.
    has_spectral : `bool`
        `True` when the frames are spectral. The set-aside step skips them,
        because their streaks are the spectra.
    measured_at : `datetime` or `None`
        When the stack started, passed on to the frame quality rows.
    quality_recording : `FrameQualityRecording` or `None`
        The configuration and calibration library for the frame quality rows.
    dry_run : `bool`, optional
        Decide only. Change no file, row or record.

    Returns
    -------
    screening : `FrameScreening`
        The kept frames, the frames left out with their reasons, the gate
        results and any background split.

    Notes
    -----
    A `ProcessingError` is raised when a check leaves no frames, unless
    ``dry_run`` is set.
    """
    screening = FrameScreening(
        frames=[frame.model_copy(deep=True) for frame in target_frames] if dry_run else target_frames
    )
    steps = (
        lambda: _drop_minority_gain(target, screening, dry_run),
        lambda: _drop_frames_already_set_aside(target, screening, dry_run),
        lambda: _set_aside_bad_frames(
            target, screening, has_spectral, measured_at, quality_recording, dry_run
        ),
        lambda: _drop_background_outliers(target, screening, dry_run),
        lambda: _drop_unfoldable_exposures(target, screening, dry_run),
    )
    for step in steps:
        step()
        if dry_run and not screening.frames:
            break
    return screening


def _drop_minority_gain(target: Target, screening: FrameScreening, dry_run: bool = False) -> None:
    """Leave out frames whose camera gain is not the most common one.

    Stacking frames with different gains messes up the noise calculation,
    because each gain setting has a different amount of read noise and dark
    current. To protect the final image, we find the most common gain setting
    and throw out any frames that don't match it.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    screening : `FrameScreening`
        The screening state. Updated in place.
    dry_run : `bool`, optional
        Do not raise when no frames are left.

    Raises
    ------
    ProcessingError
        If no frames are left and this is not a dry run.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity import (
        find_dominant_gain_subset,
        gain_homogeneity_gate,
    )

    frames_before_gain_check = screening.frames
    screening.frames, excluded_by_gain = find_dominant_gain_subset(screening.frames)
    screening.gate_results.append(gain_homogeneity_gate(frames_before_gain_check, excluded_by_gain))
    if excluded_by_gain:
        logger.warning(
            "Excluding %s frame(s) with a minority gain setting from the stack for target '%s': %s",
            len(excluded_by_gain),
            target.id,
            [f.path for f in excluded_by_gain],
        )
        screening.excluded_frames.extend(
            ExcludedFrame(path=f.path, reason=MINORITY_GAIN_REASON) for f in excluded_by_gain
        )
    if not screening.frames and not dry_run:
        raise ProcessingError("Target has no frames available to stack after gain-homogeneity filtering.")


def _drop_frames_already_set_aside(target: Target, screening: FrameScreening, dry_run: bool = False) -> None:
    """Leave out frames that an earlier run moved into ``_excluded``.

    Such a frame can still be listed on the target, if that run's save of the
    target did not last. Count it as set aside, not as a frame that was kept.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    screening : `FrameScreening`
        The screening state. Updated in place.
    dry_run : `bool`, optional
        Leave the target's frame list as it is, and do not raise when no
        frames are left.

    Raises
    ------
    ProcessingError
        If no frames are left and this is not a dry run.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
        drop_frames_already_set_aside,
        find_frames_already_set_aside,
    )

    frames_path = str(get_configuration().get_frames_path())
    if dry_run:
        already_set_aside = find_frames_already_set_aside(target.id, screening.frames, frames_path)
        dropped_paths = {frame.path for frame in already_set_aside}
        screening.frames = [frame for frame in screening.frames if frame.path not in dropped_paths]
    else:
        screening.frames, already_set_aside = drop_frames_already_set_aside(
            target, screening.frames, frames_path
        )
    if already_set_aside:
        logger.info(
            "Left out %d frame(s) that an earlier run moved into _excluded for target '%s'.",
            len(already_set_aside),
            target.id,
        )
        screening.excluded_frames.extend(
            ExcludedFrame(path=frame.path, reason=EARLIER_SET_ASIDE_REASON) for frame in already_set_aside
        )
    if not screening.frames and not dry_run:
        raise ProcessingError("Target has no frames available to stack after leaving out set-aside frames.")


def _set_aside_bad_frames(
    target: Target,
    screening: FrameScreening,
    has_spectral: bool,
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
    dry_run: bool = False,
) -> None:
    """Move frames with clouds or trailed stars out of the target's folder.

    The background check that follows only catches one sudden jump in sky
    brightness, and the stacker's weighting only lowers the influence of a
    bad frame. Moving the frame aside keeps it out of this stack and every
    later one, and the internal `restore_excluded_frames` function puts it
    back. Spectral frames are skipped: their streaks are the spectra
    themselves. The step also stores the frame quality rows.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    screening : `FrameScreening`
        The screening state. Updated in place.
    has_spectral : `bool`
        `True` when the frames are spectral.
    measured_at : `datetime` or `None`
        When the stack started, for the frame quality rows.
    quality_recording : `FrameQualityRecording` or `None`
        The configuration and calibration library for the frame quality rows.
    dry_run : `bool`, optional
        Decide which frames would be set aside (`find_frames_to_set_aside`,
        the same decision the move uses) but move none and store no rows.

    Raises
    ------
    ProcessingError
        If no frames are left and this is not a dry run.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
        GATE_NAME as SET_ASIDE_GATE_NAME,
    )

    if has_spectral:
        logger.info("Skipping the cloud and trailing check: spectral frames are not checked.")
        screening.gate_results.append(
            unchecked_gate(
                SET_ASIDE_GATE_NAME, "spectral frames are not checked: their streaks are the spectra"
            )
        )
        return
    if not get_configuration().get_set_aside_bad_frames_enabled():
        logger.info("Skipping the cloud and trailing check: it is turned off in the settings.")
        screening.gate_results.append(
            unchecked_gate(SET_ASIDE_GATE_NAME, "set-aside check is turned off in the settings")
        )
        return

    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
        find_frames_to_set_aside,
        set_aside_bad_frames,
        set_aside_gate,
    )

    frames_checked_by_set_aside = screening.frames
    logger.info(
        "Checking %d frames for clouds and trailed stars (this reads every frame)\u2026",
        len(screening.frames),
    )
    if dry_run:
        light_frames = [frame for frame in screening.frames if str(frame.role).upper() == "LIGHT"]
        set_aside_report = find_frames_to_set_aside(light_frames, None, target.id)
        moved_paths = {decision.path for decision in set_aside_report.moved}
        screening.frames = [frame for frame in screening.frames if frame.path not in moved_paths]
    else:
        with stage_window(*SET_ASIDE_WINDOW):
            screening.frames, set_aside_report = set_aside_bad_frames(target, screening.frames)
        logger.info("Measuring the frames that have no stored quality numbers yet\u2026")
        with stage_window(*QUALITY_ROWS_WINDOW):
            record_frame_quality(
                target,
                screening.frames,
                set_aside_report,
                frames_checked_by_set_aside,
                measured_at,
                quality_recording,
            )
    screening.set_aside_report = set_aside_report
    screening.flagged_frames.extend(set_aside_report.flagged)
    screening.excluded_frames.extend(
        ExcludedFrame(path=path, reason=reason) for path, reason in set_aside_report.reasons_by_path().items()
    )
    for note in set_aside_report.notes:
        logger.info("Set-aside check for target '%s': %s", target.id, note)
    screening.gate_results.append(set_aside_gate(set_aside_report))
    if not screening.frames and not dry_run:
        why = "; ".join(set_aside_report.warnings) or f"{len(set_aside_report.moved)} frame(s) set aside"
        raise ProcessingError(
            f"Nothing was stacked: every frame failed a quality check and was set aside ({why})."
        )


def _background_is_current(frame: Any) -> bool:
    """Say whether a frame's stored background level is of the current method.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    current : `bool`
        `True` when the frame has a level measured with
        `BACKGROUND_LEVEL_METHOD`. A level with no method, or an older one,
        is not current: it must not be compared with a newer level.
    """
    from astrometricslib.pipelines.shared.quality.background_measurement import BACKGROUND_LEVEL_METHOD

    measurements = frame.measurements
    return (
        measurements.background_level is not None
        and measurements.background_level_method == BACKGROUND_LEVEL_METHOD
    )


def _measure_one_frame_background(frame: Any, dry_run: bool) -> None:
    """Measure one frame's sky background (and its saturated fraction).

    The result is written onto the `FrameRecord` (which rides along with
    Target's normal save path), so later pipelines and runs read it instead of
    computing it again. A frame whose level is already of the current method
    is not measured again. A failure leaves the frame without a level, so the
    background check skips it: an older level that could not be replaced is
    cleared, because it must never be compared with a newer one.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame. Its measurements are updated in place.
    dry_run : `bool`
        `True` skips the saturated fraction (the check does not use it).

    Raises
    ------
    RunAbandonedError
        If the job was cancelled.
    """
    from astrometricslib.pipelines.shared.quality.background_measurement import (
        BACKGROUND_LEVEL_METHOD,
        measure_frame_background_level,
    )

    raise_if_abandoned()
    try:
        if not _background_is_current(frame):
            frame.measurements.background_level = measure_frame_background_level(frame.path)
            frame.measurements.background_level_method = BACKGROUND_LEVEL_METHOD
        if not dry_run:
            refresh_saturated_pixel_fraction(frame)
    except RunAbandonedError:
        raise
    except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as exc:
        logger.debug("Skipping background/saturation measurement for '%s': %s", frame.path, exc)
        if not _background_is_current(frame):
            frame.measurements.background_level = None
            frame.measurements.background_level_method = None


def _measure_frame_backgrounds(frames: list[Any], dry_run: bool) -> None:
    """Measure the sky background of every frame, several frames at once.

    The frames are measured in the same number of worker threads as the
    set-aside step (`MEASUREMENT_THREADS`). Each frame's result goes onto
    that frame only, so the order of the frames and the values do not depend
    on the order the workers finish. Progress is reported as each frame
    finishes. A cancel is seen within half a second and stops the frames not
    yet started.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        The frames. Their measurements are updated in place.
    dry_run : `bool`
        Passed on to `_measure_one_frame_background`.

    Notes
    -----
    `RunAbandonedError` is raised if the job was cancelled.
    """
    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import MEASUREMENT_THREADS

    logger.info("Checking the sky background of %d frames\u2026", len(frames))
    background_progress = LoopProgress("Sky background measured for", len(frames))
    # Worker threads start without the caller's job context; each task gets
    # its own copy so its log lines go to this job's log.
    caller_context = contextvars.copy_context()
    with stopping_on_abandonment(ThreadPoolExecutor(max_workers=MEASUREMENT_THREADS)) as pool:
        futures = [
            pool.submit(caller_context.copy().run, _measure_one_frame_background, frame, dry_run)
            for frame in frames
        ]
        for finished in completed_unless_abandoned(futures):
            finished.result()
            with stage_window(*BACKGROUND_WINDOW):
                background_progress.tick()


def _drop_background_outliers(target: Target, screening: FrameScreening, dry_run: bool = False) -> None:
    """Leave out frames whose sky background differs from the rest.

    This catches sudden changes in the sky background (like clouds moving
    in). Standard calibration and pixel rejection aren't enough to catch
    these image-wide changes. Because checking every frame takes extra time,
    this check can be turned on or off in the settings with
    `get_background_homogeneity_check_enabled`. The gate result is recorded
    whether or not the check ran.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    screening : `FrameScreening`
        The screening state. Updated in place.
    dry_run : `bool`, optional
        Do not measure the saturated pixel fraction (the check does not use
        it), and do not raise when no frames are left.

    Raises
    ------
    ProcessingError
        If no frames are left and this is not a dry run.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
        background_homogeneity_gate,
    )

    background_check_enabled = get_configuration().get_background_homogeneity_check_enabled()
    frames_before_background_check = screening.frames
    if not background_check_enabled:
        logger.info("Skipping the sky background check: it is turned off in the settings.")
    if background_check_enabled:
        from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
            find_dominant_background_subset_by_exposure,
        )

        _measure_frame_backgrounds(screening.frames, dry_run)

        # Throw out frames with a different background level. If we
        # accidentally use a washed-out frame as the main reference for
        # aligning the images, the software won't be able to find any sharp
        # stars to lock onto. This would cause the alignment to fail and
        # crash the entire stacking process. The check is made within each
        # exposure length: the sky background grows with the exposure, so
        # frames of different lengths cannot be compared (all 14 of the 60 s
        # frames of NGC 2403 were once excluded because the 300 s frames were
        # brighter).
        screening.frames, excluded_by_background, screening.background_split = (
            find_dominant_background_subset_by_exposure(screening.frames)
        )
        if screening.background_split:
            logger.warning(
                "Background-homogeneity split detected for target '%s': %s",
                target.id,
                screening.background_split,
            )
        if excluded_by_background:
            logger.warning(
                "Excluding %s frame(s) from a different sky condition from the stack for target '%s': %s",
                len(excluded_by_background),
                target.id,
                [f.path for f in excluded_by_background],
            )
            screening.excluded_frames.extend(
                ExcludedFrame(path=f.path, reason=BACKGROUND_SPLIT_REASON) for f in excluded_by_background
            )
        if not screening.frames and not dry_run:
            raise ProcessingError(
                "Target has no frames available to stack after background-homogeneity filtering."
            )

    screening.gate_results.append(
        background_homogeneity_gate(
            frames_before_background_check, screening.background_split, background_check_enabled
        )
    )


def _drop_unfoldable_exposures(target: Target, screening: FrameScreening, dry_run: bool = False) -> None:
    """Leave out frames whose exposure length has no group to join.

    A length with too few frames to stack on its own is folded into the
    nearest group, but only when that group is within the fold ratio limit
    (`MAXIMUM_EXPOSURE_FOLD_RATIO`), because a folded frame's noise grows by
    the exposure ratio. A frame with no such group is not stacked. It is only
    left out of this stack: the file stays where it is, and the frame is
    recorded with its reason so the warning is not lost.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    screening : `FrameScreening`
        The screening state. Updated in place.
    dry_run : `bool`, optional
        Do not raise when no frames are left.

    Raises
    ------
    ProcessingError
        If no frames are left and this is not a dry run. This cannot happen
        for a real set of frames, because a left-out length is measured
        against a group that has enough frames, and that group stays.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.processing.exposure_groups import (
        find_frames_left_out_of_exposure_groups,
    )

    left_out = find_frames_left_out_of_exposure_groups(screening.frames)
    if left_out:
        left_out_paths = {frame.path for frame, _ in left_out}
        screening.frames = [frame for frame in screening.frames if frame.path not in left_out_paths]
        logger.warning(
            "Leaving %d frame(s) out of the stack for target '%s', exposure with no group near: %s",
            len(left_out),
            target.id,
            sorted({reason for _, reason in left_out}),
        )
        screening.excluded_frames.extend(
            ExcludedFrame(path=frame.path, reason=EXPOSURE_NOT_FOLDABLE_REASON_PREFIX + reason)
            for frame, reason in left_out
        )
    if not screening.frames and not dry_run:
        raise ProcessingError(
            "Target has no frames available to stack after leaving out unfoldable exposures."
        )


def refresh_saturated_pixel_fraction(frame: Any) -> bool:
    """Measure a frame's saturated pixel fraction when the stored one is stale.

    The fraction is stored with the level it was counted at
    (`saturated_pixel_fraction_limit_adu`). It is measured again when it is
    missing, when no level was stored (a record from before the level was
    kept), or when the level differs from the camera profile's current
    `pixel_saturation_level_adu`. That way a fix to a profile, such as a
    clip ceiling that lowers the level, reaches frames measured earlier.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame. Its measurements are updated in place.

    Returns
    -------
    measured : `bool`
        `True` when the fraction was measured again, `False` when the stored
        one was kept.
    """
    from astrometricslib.pipelines.shared.quality.background_measurement import (
        measure_frame_saturated_pixel_fraction,
    )

    saturation_level_adu = float(resolve_camera_profile(frame.camera).pixel_saturation_level_adu.value)
    measurements = frame.measurements
    if (
        measurements.saturated_pixel_fraction is not None
        and measurements.saturated_pixel_fraction_limit_adu == saturation_level_adu
    ):
        return False
    measurements.saturated_pixel_fraction = measure_frame_saturated_pixel_fraction(
        frame.path, saturation_level_adu
    )
    measurements.saturated_pixel_fraction_limit_adu = saturation_level_adu
    return True


def record_frame_quality(
    target: Target,
    kept_frames: list[Any],
    set_aside_report: Any,
    frames_checked: list[Any],
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
) -> None:
    """Store the set-aside step's frame numbers and any missing measurements.

    This never stops a stack. Any failure, whatever its cause (a storage
    error, or an unexpected error such as a ``RuntimeError`` or
    ``MemoryError`` from the image libraries), is logged with its traceback and
    the stack goes on, because the stack does not depend on these numbers.

    The hook runs only for imaging stacks with the set-aside step turned on,
    because it sits inside that step. It does not read the global
    configuration: the caller supplies the configuration and the shared
    calibration library in ``quality_recording``.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    kept_frames : `list`
        The frames that stay in the stack.
    set_aside_report : `SetAsideReport`
        What the set-aside step measured and moved.
    frames_checked : `list`
        Every frame given to the set-aside step, moved ones included.
    measured_at : `datetime` or `None`
        When the stack started, passed in by the caller because pipeline code
        does not read the clock. Nothing is stored when it is `None`.
    quality_recording : `FrameQualityRecording` or `None`
        The configuration and calibration library to store with. Nothing is
        stored when it is `None`.

    Raises
    ------
    RunAbandonedError
        If the job was cancelled. Any other failure is logged, not raised.
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
            set_aside_report.measurements,
            frames_checked,
            quality_recording,
            measured_at,
            actions_by_path=set_aside_report.actions_by_path(),
        )
    except RunAbandonedError:
        # A cancel is not a failure of the measurements.
        raise
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
