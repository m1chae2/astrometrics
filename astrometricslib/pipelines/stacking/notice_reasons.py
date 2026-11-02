"""The sentences of the stacking notices that are not gates.

The stacking stage stores these sentences as the reasons of its notice rows
(see `astrometricslib.pipelines.shared.quality.notices`). The functions here
are the one place that writes them.
"""

__all__ = [
    "exposure_group_left_out_reason",
    "flat_calibration_issue_reason",
    "frames_moved_to_excluded_reason",
    "single_frame_stack_reason",
    "uncovered_flat_calibration_issues",
]


def frames_moved_to_excluded_reason(frame_count: int) -> str:
    """Write the sentence for frames the quarantine step moved.

    Parameters
    ----------
    frame_count : `int`
        How many frames were moved to the excluded folder.

    Returns
    -------
    reason : `str`
        The sentence.
    """
    return f"{frame_count} frame(s) with clouds or trailed stars moved to the _excluded folder"


def single_frame_stack_reason() -> str:
    """Write the sentence for a stack made from one frame.

    Returns
    -------
    reason : `str`
        The sentence.
    """
    return "single-frame stack: no rejection/registration quality processing applied"


def exposure_group_left_out_reason(exposure_seconds: float, left_out_reason: str) -> str:
    """Write the sentence for an exposure group left out of the stack.

    Parameters
    ----------
    exposure_seconds : `float`
        The exposure length of the group.
    left_out_reason : `str`
        Why the group was left out, as the stacker wrote it.

    Returns
    -------
    reason : `str`
        The sentence.
    """
    return f"the {exposure_seconds:g} s exposure group {left_out_reason}"


def flat_calibration_issue_reason(issue: str) -> str:
    """Write the sentence for one flat calibration issue.

    Parameters
    ----------
    issue : `str`
        An issue sentence from the flat assessment.

    Returns
    -------
    reason : `str`
        The sentence.
    """
    return f"flat calibration: {issue}"


def uncovered_flat_calibration_issues(issues: list[str]) -> list[str]:
    """List the flat issues that no flat gate reports.

    The ``flat_level`` gate reports the first faint or bright issue and the
    ``flat_noise`` gate the first noisy one; an unreadable set fails the level
    gate. Any other issue, such as "not smoothed" for a color sensor, or a
    second level issue, is reported by no gate, so it needs a notice row.

    Parameters
    ----------
    issues : `list` [`str`]
        The issue sentences of the flat assessment.

    Returns
    -------
    uncovered : `list` [`str`]
        The issues no gate reports, in order.
    """
    from astrometricslib.pipelines.stacking.pre_processing.flat_calibration import (
        BRIGHT_FLAT_ISSUE_PREFIX,
        FAINT_FLAT_ISSUE_PREFIX,
        NOISY_FLAT_ISSUE_PREFIX,
        UNREADABLE_FLATS_ISSUE,
    )

    level_issues = [
        issue for issue in issues if issue.startswith((FAINT_FLAT_ISSUE_PREFIX, BRIGHT_FLAT_ISSUE_PREFIX))
    ]
    noise_issues = [issue for issue in issues if issue.startswith(NOISY_FLAT_ISSUE_PREFIX)]
    covered = {UNREADABLE_FLATS_ISSUE, *level_issues[:1], *noise_issues[:1]}
    return [issue for issue in issues if issue not in covered]
