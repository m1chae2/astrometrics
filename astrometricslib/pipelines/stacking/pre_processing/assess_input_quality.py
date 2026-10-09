"""Judges whether the inputs to a stack were sound.

The builder here looks only at what was known before the stacking engine
ran: the frames set aside by the pre-checks, whether the sky background
changed during the session, and how good the flat frames were. It returns a
`StackingInputQuality` (see `models/stacking_quality.py`) that carries its
own flag reasons.
"""

from typing import Any

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.stacking_quality import StackingInputQuality
from astrometricslib.pipelines.stacking.pre_processing.flat_calibration import (
    BRIGHT_FLAT_ISSUE_PREFIX,
    FAINT_FLAT_ISSUE_PREFIX,
    MAXIMUM_FLAT_LEVEL_FRACTION,
    MAXIMUM_FLAT_NOISE_FRACTION,
    MINIMUM_FLAT_LEVEL_FRACTION,
    NOISY_FLAT_ISSUE_PREFIX,
    UNREADABLE_FLATS_ISSUE,
)

# The reasons the pre-checks record on a frame they set aside. Stage code
# writes them; this module counts them.
GAIN_EXCLUSION_REASON = "minority gain setting"
BACKGROUND_EXCLUSION_REASON = "background-homogeneity split"
# A frame moved aside for clouds or trailed stars has a reason that starts
# with this text (see `frame_quarantine`), followed by what was measured.
QUARANTINE_EXCLUSION_REASON_PREFIX = "moved to _excluded"


FLAT_LEVEL_GATE_NAME = "flat_level"
FLAT_NOISE_GATE_NAME = "flat_noise"
CALIBRATION_METADATA_GATE_NAME = "calibration_metadata"


def calibration_gates(diagnostics: dict[str, Any]) -> list[GateResult]:
    """Build the gates for the flat frames and the calibration metadata.

    A stack that used no flats cannot fail the flat checks, and that is not
    the same as passing them, so those gates are ``not_checked`` with the
    reason. The same goes for the metadata check when no calibration frame
    was applied. A failed gate's sentence is the same text the input quality
    lists as a flag reason, so the two never disagree.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported. Read: ``flat_calibration``,
        ``calibration_applied`` and ``calibration_mismatch_flags``.

    Returns
    -------
    gates : `list` [`GateResult`]
        The ``flat_level``, ``flat_noise`` and ``calibration_metadata`` gates.
    """
    gates: list[GateResult] = []
    flat = diagnostics.get("flat_calibration") or {}
    issues = list(flat.get("issues", []))
    level_source = (
        f"{MINIMUM_FLAT_LEVEL_FRACTION:.0%} to {MAXIMUM_FLAT_LEVEL_FRACTION:.0%} "
        "of full scale (design estimate)"
    )
    noise_source = f"master flat noise at most {MAXIMUM_FLAT_NOISE_FRACTION:.1%} (derived from stack noise)"
    if not flat:
        no_flats = "the stack used no flat frames"
        gates.append(unchecked_gate(FLAT_LEVEL_GATE_NAME, no_flats, level_source))
        gates.append(unchecked_gate(FLAT_NOISE_GATE_NAME, no_flats, noise_source))
    else:
        level_issues = [
            i for i in issues if i.startswith((FAINT_FLAT_ISSUE_PREFIX, BRIGHT_FLAT_ISSUE_PREFIX))
        ]
        noise_issues = [i for i in issues if i.startswith(NOISY_FLAT_ISSUE_PREFIX)]
        unreadable = UNREADABLE_FLATS_ISSUE in issues
        measured_level = flat.get("level_fraction")
        measured_noise = flat.get("noise_fraction")
        if unreadable:
            gates.append(
                failed_gate(
                    FLAT_LEVEL_GATE_NAME,
                    f"flat calibration: {UNREADABLE_FLATS_ISSUE}",
                    limit_source=level_source,
                )
            )
            gates.append(unchecked_gate(FLAT_NOISE_GATE_NAME, UNREADABLE_FLATS_ISSUE, noise_source))
        else:
            if level_issues:
                gates.append(
                    failed_gate(
                        FLAT_LEVEL_GATE_NAME,
                        f"flat calibration: {level_issues[0]}",
                        measured_value=measured_level,
                        limit_source=level_source,
                    )
                )
            else:
                gates.append(passed_gate(FLAT_LEVEL_GATE_NAME, measured_level, limit_source=level_source))
            if noise_issues:
                gates.append(
                    failed_gate(
                        FLAT_NOISE_GATE_NAME,
                        f"flat calibration: {noise_issues[0]}",
                        measured_value=measured_noise,
                        limit=MAXIMUM_FLAT_NOISE_FRACTION,
                        limit_source=noise_source,
                    )
                )
            elif measured_noise is None:
                gates.append(
                    unchecked_gate(
                        FLAT_NOISE_GATE_NAME, "the flat noise could not be estimated", noise_source
                    )
                )
            else:
                gates.append(
                    passed_gate(
                        FLAT_NOISE_GATE_NAME, measured_noise, MAXIMUM_FLAT_NOISE_FRACTION, noise_source
                    )
                )

    applied = diagnostics.get("calibration_applied") or {}
    mismatches = list(diagnostics.get("calibration_mismatch_flags", []))
    if not any(applied.values()):
        gates.append(
            unchecked_gate(
                CALIBRATION_METADATA_GATE_NAME,
                "no calibration frame was applied, so there was nothing to compare",
            )
        )
    elif mismatches:
        gates.append(
            failed_gate(
                CALIBRATION_METADATA_GATE_NAME,
                f"{len(mismatches)} calibration metadata mismatch(es)",
                measured_value=float(len(mismatches)),
                limit=0.0,
            )
        )
    else:
        gates.append(passed_gate(CALIBRATION_METADATA_GATE_NAME, 0.0, 0.0))
    return gates


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
