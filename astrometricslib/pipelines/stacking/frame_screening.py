"""Purpose: Screen the frames of a stack before the stacking program sees them.

Description: A stack is only as good as the frames that go into it. This
module runs the checks that leave out frames which would harm the stack: a
minority camera gain setting, frames an earlier run already set aside, clouds
and trailed stars (the quarantine step), and a different sky background. Each
check records which frames it left out and a `GateResult` that says what it
looked at. The checks run in a fixed order, and `screen_frames` is the one
entry point the stacking stage calls.

The module also holds the two helpers those checks share with the stage's
other users: `refresh_saturated_pixel_fraction` and `record_frame_quality`.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import AstrometricsError, ProcessingError
from astrometricslib.models.quality.gate_result import GateResult, unchecked_gate
from astrometricslib.models.target import Target
from astrometricslib.utilities.exceptions import DATA_ERRORS

if TYPE_CHECKING:
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

logger = logging.getLogger(__name__)

__all__ = [
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
    """

    frames: list[Any]
    excluded_frames: list[ExcludedFrame] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)
    background_split: dict | list[dict] | None = None


def screen_frames(
    target: Target,
    target_frames: list[Any],
    has_spectral: bool,
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
) -> FrameScreening:
    """Run the frame checks in order and give back the frames that pass.

    The order is: minority gain, frames already set aside, the quarantine
    step (clouds and trailed stars), and the sky background. The quarantine
    step moves files and stores frame quality rows, so the order and timing
    of these steps must not change.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    target_frames : `list`
        The frames offered to the stack.
    has_spectral : `bool`
        `True` when the frames are spectral. The quarantine step skips them,
        because their streaks are the spectra.
    measured_at : `datetime` or `None`
        When the stack started, passed on to the frame quality rows.
    quality_recording : `FrameQualityRecording` or `None`
        The configuration and calibration library for the frame quality rows.

    Returns
    -------
    screening : `FrameScreening`
        The kept frames, the frames left out with their reasons, the gate
        results and any background split.

    Notes
    -----
    A `ProcessingError` is raised when a check leaves no frames.
    """
    screening = FrameScreening(frames=target_frames)
    _drop_minority_gain(target, screening)
    _drop_frames_already_set_aside(target, screening)
    _quarantine_bad_frames(target, screening, has_spectral, measured_at, quality_recording)
    _drop_background_outliers(target, screening)
    return screening


def _drop_minority_gain(target: Target, screening: FrameScreening) -> None:
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

    Raises
    ------
    ProcessingError
        If no frames are left.
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
            ExcludedFrame(path=f.path, reason="minority gain setting") for f in excluded_by_gain
        )
    if not screening.frames:
        raise ProcessingError("Target has no frames available to stack after gain-homogeneity filtering.")


def _drop_frames_already_set_aside(target: Target, screening: FrameScreening) -> None:
    """Leave out frames that an earlier run moved into ``_excluded``.

    Such a frame can still be listed on the target, if that run's save of the
    target did not last. Count it as set aside, not as a frame that was kept.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    screening : `FrameScreening`
        The screening state. Updated in place.

    Raises
    ------
    ProcessingError
        If no frames are left.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
        drop_frames_already_set_aside,
    )

    screening.frames, already_set_aside = drop_frames_already_set_aside(
        target, screening.frames, str(get_configuration().get_frames_path())
    )
    if already_set_aside:
        logger.info(
            "Left out %d frame(s) that an earlier run moved into _excluded for target '%s'.",
            len(already_set_aside),
            target.id,
        )
        screening.excluded_frames.extend(
            ExcludedFrame(path=frame.path, reason="set aside by an earlier quarantine")
            for frame in already_set_aside
        )
    if not screening.frames:
        raise ProcessingError("Target has no frames available to stack after leaving out set-aside frames.")


def _quarantine_bad_frames(
    target: Target,
    screening: FrameScreening,
    has_spectral: bool,
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
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

    Raises
    ------
    ProcessingError
        If no frames are left.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
        GATE_NAME as QUARANTINE_GATE_NAME,
    )

    if has_spectral:
        screening.gate_results.append(
            unchecked_gate(
                QUARANTINE_GATE_NAME, "spectral frames are not checked: their streaks are the spectra"
            )
        )
        return
    if not get_configuration().get_quarantine_bad_frames_enabled():
        screening.gate_results.append(
            unchecked_gate(QUARANTINE_GATE_NAME, "frame quarantine is turned off in the settings")
        )
        return

    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
        quarantine_bad_frames,
        quarantine_gate,
    )

    frames_checked_by_quarantine = screening.frames
    screening.frames, quarantine_report = quarantine_bad_frames(target, screening.frames)
    record_frame_quality(
        target,
        screening.frames,
        quarantine_report,
        frames_checked_by_quarantine,
        measured_at,
        quality_recording,
    )
    screening.excluded_frames.extend(
        ExcludedFrame(path=path, reason=reason)
        for path, reason in quarantine_report.reasons_by_path().items()
    )
    for note in quarantine_report.notes:
        logger.info("Quarantine check for target '%s': %s", target.id, note)
    screening.gate_results.append(quarantine_gate(quarantine_report))
    if not screening.frames:
        raise ProcessingError("Target has no frames available to stack after quarantining bad frames.")


def _drop_background_outliers(target: Target, screening: FrameScreening) -> None:
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

    Raises
    ------
    ProcessingError
        If no frames are left.
    """
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
        background_homogeneity_gate,
    )

    background_check_enabled = get_configuration().get_background_homogeneity_check_enabled()
    frames_before_background_check = screening.frames
    if background_check_enabled:
        from astrometricslib.pipelines.shared.quality.background_measurement import (
            measure_frame_background_level,
        )
        from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
            find_dominant_background_subset_by_exposure,
        )

        for frame in screening.frames:
            try:
                # Computed once, recorded onto the FrameRecord (which
                # rides along with Target's normal save path) -- later
                # pipelines/runs read these instead of recomputing them.
                if frame.measurements.background_level is None:
                    frame.measurements.background_level = measure_frame_background_level(frame.path)
                refresh_saturated_pixel_fraction(frame)
            except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as exc:
                logger.debug("Skipping background/saturation measurement for '%s': %s", frame.path, exc)
                continue

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
                ExcludedFrame(path=f.path, reason="background-homogeneity split")
                for f in excluded_by_background
            )
        if not screening.frames:
            raise ProcessingError(
                "Target has no frames available to stack after background-homogeneity filtering."
            )

    screening.gate_results.append(
        background_homogeneity_gate(
            frames_before_background_check, screening.background_split, background_check_enabled
        )
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
            actions_by_path=quarantine_report.actions_by_path(),
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
