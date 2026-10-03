"""Judges whether the inputs to a stack were sound.

The builder here looks only at what was known before the stacking engine
ran: the frames set aside by the pre-checks, whether the sky background
changed during the session, and how good the flat frames were. It returns a
`StackingInputQuality` (see `models/stacking_quality.py`) that carries its
own flag reasons.
"""

from typing import Any

from astrometricslib.models.stacking_quality import StackingInputQuality

# The reasons the pre-checks record on a frame they set aside. Stage code
# writes them; this module counts them.
GAIN_EXCLUSION_REASON = "minority gain setting"
BACKGROUND_EXCLUSION_REASON = "background-homogeneity split"
# A frame moved aside for clouds or trailed stars has a reason that starts
# with this text (see `frame_quarantine`), followed by what was measured.
QUARANTINE_EXCLUSION_REASON_PREFIX = "moved to _excluded"


def describe_background_splits(background_split: dict | list[dict] | None) -> str | None:
    """Describe the background splits in one line.

    Parameters
    ----------
    background_split : `dict`, `list` [`dict`] or `None`
        What the background check found. A list holds one split for each
        exposure length that showed one. A single dictionary is one split
        with no exposure length attached.

    Returns
    -------
    detail : `str` or `None`
        The splits in words, or `None` when there were none.
    """
    splits = [background_split] if isinstance(background_split, dict) else list(background_split or [])
    if not splits:
        return None
    details = []
    for split in splits:
        prefix = f"{split['exposure_seconds']:g} s frames: " if "exposure_seconds" in split else ""
        details.append(
            f"{prefix}{split['low_group_count']} frame(s) at background~"
            f"{split['low_group_median']:.0f} vs {split['high_group_count']} "
            f"frame(s) at ~{split['high_group_median']:.0f} (gap ratio "
            f"{split['gap_ratio']:.1f})"
        )
    return "; ".join(details)


def assess_input_quality(
    frames_submitted: int,
    frames_accepted: int,
    excluded_frames: list[Any],
    background_split: dict | list[dict] | None,
    diagnostics: dict[str, Any],
) -> StackingInputQuality:
    """Judge the frames and calibration data that went into a stack.

    Parameters
    ----------
    frames_submitted : `int`
        How many frames the stage was given.
    frames_accepted : `int`
        How many were left after the gain and background checks.
    excluded_frames : `list`
        The frames set aside, each with a ``reason``.
    background_split : `dict`, `list` [`dict`] or `None`
        What the background check found (see `describe_background_splits`).
    diagnostics : `dict`
        What the stacking run reported. This reads the flat assessment and
        the calibration mismatch flags from it.

    Returns
    -------
    quality : `StackingInputQuality`
        The input judgement, with a sentence for each problem found.
    """
    flat = diagnostics.get("flat_calibration") or {}
    mismatches = list(diagnostics.get("calibration_mismatch_flags", []))
    flat_issues = list(flat.get("issues", []))
    detail = describe_background_splits(background_split)

    quarantined = [
        frame for frame in excluded_frames if frame.reason.startswith(QUARANTINE_EXCLUSION_REASON_PREFIX)
    ]

    reasons = []
    if quarantined:
        reasons.append(
            f"{len(quarantined)} frame(s) with clouds or trailed stars moved to the _excluded folder"
        )
    if detail is not None:
        reasons.append(f"background split: {detail}")
    if mismatches:
        reasons.append(f"{len(mismatches)} calibration metadata mismatch(es)")
    reasons.extend(f"flat calibration: {issue}" for issue in flat_issues)

    return StackingInputQuality(
        frames_submitted=frames_submitted,
        frames_accepted=frames_accepted,
        frames_excluded_for_gain=sum(1 for frame in excluded_frames if frame.reason == GAIN_EXCLUSION_REASON),
        frames_excluded_for_background=sum(
            1 for frame in excluded_frames if frame.reason == BACKGROUND_EXCLUSION_REASON
        ),
        frames_quarantined=len(quarantined),
        background_split_detected=detail is not None,
        background_split_detail=detail,
        flat_frame_count=flat.get("frame_count"),
        flat_noise_fraction=flat.get("noise_fraction"),
        flat_smoothing_sigma_px=flat.get("smoothing_sigma_pixels"),
        flat_calibration_issues=flat_issues,
        calibration_mismatch_flags=mismatches,
        is_flagged=bool(reasons),
        flag_reasons=reasons,
    )
