"""Purpose: Build the stack reports that the processing API returns.

Description: Three reads about a finished stack live here, so the API
methods only check their arguments:

* `stack_path_of` finds the file of a target's imaging or spectral stack.
* `stack_quality_report` measures one stack file: the star width (FWHM), the
  share of pixels rejected (from the rejection map Siril wrote beside the
  stack), and the frame registration (from the ``_Registration.seq`` file
  beside it). It can also compare the stack with the previous stack the
  last restack kept, or with any other stack file.
* `summarize_stack` reads the quality summary the stacking stage saved with
  a target's stack, so nothing is measured again.

Nothing here writes a file.
"""

import os
from collections.abc import Collection
from typing import Literal

from astrometricslib.foundation.errors import NotFoundError
from astrometricslib.models.quality_reports import StackQualityReport, StackSummary
from astrometricslib.models.target import Target

__all__ = ["STACK_QUALITY_SECTIONS", "stack_path_of", "stack_quality_report", "summarize_stack"]

STACK_QUALITY_SECTIONS = ("fwhm", "rejected_fraction", "registration")
"""The sections `stack_quality_report` can measure."""

StackKind = Literal["imaging", "spectral"]


def stack_path_of(target: Target, kind: StackKind) -> str:
    """Find the file of a target's current stack.

    Parameters
    ----------
    target : `Target`
        The target.
    kind : {"imaging", "spectral"}
        Which stack.

    Returns
    -------
    path : `str`
        The path of the stack.

    Raises
    ------
    NotFoundError
        If the target has no stack of that kind.
    """
    stacking = target.spectral_stacking if kind == "spectral" else target.stacking
    path = getattr(stacking, "stacked_image", None)
    if not path:
        raise NotFoundError(
            f"Target '{target.id}' has no {'spectral ' if kind == 'spectral' else ''}stack.",
            details={"target": target.id, "kind": kind},
        )
    return str(path)


def stack_quality_report(
    stack_path: str,
    target_id: str | None,
    include: Collection[str],
    compare_to: str | None,
) -> StackQualityReport:
    """Measure a stack and, if asked, compare it with another stack.

    Parameters
    ----------
    stack_path : `str`
        The stack to measure.
    target_id : `str` or `None`
        The target the stack belongs to, if known.
    include : `Collection` [`str`]
        The sections to measure, from `STACK_QUALITY_SECTIONS`.
    compare_to : `str` or `None`
        ``"previous"`` compares with the stack the last restack kept in the
        ``_previous`` folder. Any other text is the path of a stack to
        compare with. The other stack is ``before``; this one is ``after``.

    Returns
    -------
    report : `StackQualityReport`
        The measurements asked for. A section that could not be measured
        is `None`, with a sentence in ``notes``.

    Raises
    ------
    NotFoundError
        If the stack file, or the stack to compare with, does not exist.
    """
    from astrometricslib.drivers.siril_output_parsing import parse_seq_file
    from astrometricslib.pipelines.shared.quality.quality_metrics import measure_rejected_fraction
    from astrometricslib.pipelines.stacking.post_processing.previous_stack import previous_stack_path
    from astrometricslib.pipelines.stacking.post_processing.stack_comparison import (
        compare_stacks,
        measure_stack_fwhm,
    )

    if not os.path.isfile(stack_path):
        raise NotFoundError(f"There is no stack file at {stack_path}.", details={"path": stack_path})
    report = StackQualityReport(stack_path=stack_path, target_id=target_id, included=sorted(include))
    notes: list[str] = []
    if "fwhm" in include:
        report.fwhm_px = measure_stack_fwhm(stack_path)
        if report.fwhm_px is None:
            notes.append("The star width could not be measured: too few stars could be fitted.")
    if "rejected_fraction" in include:
        report.rejected_fraction = measure_rejected_fraction(stack_path)
        if report.rejected_fraction is None:
            notes.append("No rejection map was written beside this stack.")
    if "registration" in include:
        seq_path = os.path.splitext(stack_path)[0] + "_Registration.seq"
        if os.path.isfile(seq_path):
            report.registration = parse_seq_file(seq_path)
        else:
            notes.append("No _Registration.seq file was kept beside this stack.")
    if compare_to == "previous":
        previous = previous_stack_path(stack_path)
        if previous is None:
            notes.append("No previous stack is kept, so there is nothing to compare with.")
        else:
            report.comparison = compare_stacks(previous, stack_path)
    elif compare_to:
        if not os.path.isfile(compare_to):
            raise NotFoundError(f"There is no stack file at {compare_to}.", details={"path": compare_to})
        report.comparison = compare_stacks(compare_to, stack_path)
    report.notes = notes
    return report


def summarize_stack(target: Target, kind: StackKind) -> StackSummary:
    """Describe a target's current stack from its saved quality summary.

    Parameters
    ----------
    target : `Target`
        The target.
    kind : {"imaging", "spectral"}
        Which stack.

    Returns
    -------
    summary : `StackSummary`
        Frames stacked and set aside, the rejected share, the star width
        against what the inputs predict, the flags and each exposure group.

    Raises
    ------
    NotFoundError
        If the target has no saved summary for that stack.
    """
    stacking = target.spectral_stacking if kind == "spectral" else target.stacking
    quality = getattr(stacking, "quality_summary", None)
    metrics = getattr(quality, "stacking_metrics", None)
    if quality is None or metrics is None:
        label = "spectral stack" if kind == "spectral" else "stack"
        raise NotFoundError(
            f"Target '{target.id}' has no saved summary for its {label}.",
            details={"target": target.id, "kind": kind},
        )
    excluded = metrics.excluded_frames
    return StackSummary(
        target_id=target.id,
        kind=kind,
        stack_path=getattr(stacking, "stacked_image", None),
        made_at=quality.created_at.isoformat(),
        frames_submitted=metrics.frames_submitted,
        frames_stacked=metrics.frames_stacked,
        frames_skipped=len(excluded),
        skipped_reasons=[
            {"file": frame.path.rsplit("/", 1)[-1], "reason": frame.reason} for frame in excluded[:10]
        ],
        frames_set_aside_before_stacking=quality.input_quality.frames_quarantined,
        sessions=[
            {
                "session": session.session_id,
                "frames": session.frames_contributed,
                "clipped": session.frames_clipped,
            }
            for session in quality.target_session_breakdown
        ],
        rejected_pixel_fraction=metrics.rejected_pixel_fraction,
        rejected_fraction_flagged=metrics.rejected_fraction_flagged,
        star_width_px={
            "stack": metrics.stacked_fwhm_px,
            "expected_from_inputs": metrics.expected_stack_fwhm_px,
            "median_of_inputs": metrics.median_input_fwhm_px,
            "degraded": metrics.fwhm_degraded,
        },
        saturated_pixel_fraction=metrics.saturated_pixel_fraction,
        zero_pixel_fraction=metrics.zero_pixel_fraction,
        exposure_groups=[
            {
                "exposure_seconds": group.exposure_seconds,
                "frames_submitted": group.frames_submitted,
                "frames_stacked": group.frames_stacked,
                "saturated": group.saturated,
                "clipped_at_zero": group.clipped_at_zero,
                "alignment_shift_pixels": group.alignment_shift_pixels,
                "left_out_reason": group.left_out_reason,
            }
            for group in metrics.exposure_groups
        ],
        flagged=quality.flagged,
        flag_reasons=quality.flag_reasons,
        calibration_mismatches=len(metrics.calibration_mismatch_flags),
    )
