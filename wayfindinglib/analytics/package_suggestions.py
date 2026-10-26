"""Purpose: The shared pieces of the library's package suggestions.

Description: The library suggests observation packages in two ways. A quality
rule that asks for more frames becomes an exposure row of a suggested package
while the target's noise is above one default goal. Tonight's ranking puts its
top few targets in suggested packages with no rows. This module holds what
both ways share: the noise goal, the number of ranking suggestions, the source
key and the evidence fingerprint (a short code for the evidence, so a dismissal
lasts until the evidence changes), and the reading of a target's noise.

The refresh itself, which creates, updates and deletes the packages, is
`wayfindinglib.tasks.planning_tasks.suggestion_tasks`.

Register rows WAY-D83 (the noise goal) and WAY-D85 (the ranking suggestions)
list the tuned values.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from astrometricslib import Astrometrics, AstrometricsError, CaptureSetting, SubjectLevel
from wayfindinglib.analytics.result_quality.forecast import NORMALIZED_NOISE_METRIC, PINNED_STACK_METRICS
from wayfindinglib.models.quality.rule import QualityRule
from wayfindinglib.models.quality.statements import ProposedAction

DEFAULT_NOISE_GOAL = 1.9e-4
"""The one noise goal for every target (no setting on screen).

The noise is the library stack's empty-aperture noise as a fraction of the
reference stars' brightness (`stacking.empty_aperture_noise_normalized`; it
has no unit). The one stack measured so far, M 57 with 61 frames, gave
2.65e-4 (STK-36). The goal is that value divided by sqrt(2) (1.9e-4): one
more doubling of the effective time, the same step the "take N more" rule
uses (WAY-D30). A target below the goal gets no "more frames" suggestion.
Design estimate, register row WAY-D83.
"""

NOISE_GOAL_ENTRY = "WAY-D83"
"""Register row behind the default noise goal."""

RANKING_SUGGESTIONS_PER_USE = 3
"""How many targets of each use tonight's ranking turns into suggestions.

Three is about the number of targets one night's session holds (a target
needs a few hours for a useful gain). Design estimate, register row WAY-D85.
"""

RANKING_ENTRY = "WAY-D85"
"""Register row behind the ranking suggestions."""

RANKING_FINGERPRINT_VERSION = 1
"""Changes when a ranking suggestion changes meaning (old dismissals lapse)."""

STOP_RULES = frozenset({"more_frames_will_not_help"})
"""Rules that say more frames will not help. A suggestion for more frames is
deleted when one of them speaks."""

LIBRARY_STACK_EXCLUDED_MARKERS = ("#group-", "/groups/")
"""Parts of a stack id that mark an exposure-group stack."""


@dataclass(frozen=True)
class TargetNoise:
    """A target's measured noise and the row it came from.

    Parameters
    ----------
    value : `float`
        The normalized noise (a fraction of the reference stars' brightness).
    row_id : `int` or `None`
        The stored measurement row.
    measured_at : `datetime`
        When the noise was measured.
    """

    value: float
    row_id: int | None
    measured_at: datetime


def read_target_noise(astrometrics: Astrometrics, target_id: str) -> TargetNoise | None:
    """Read the noise of a target's newest library stack.

    Only rows of the pinned definition version count (a row of another
    version means something else). Exposure-group stacks and subset stacks
    are not the library stack and are left out.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle (`quality.query`).
    target_id : `str`
        The target.

    Returns
    -------
    noise : `TargetNoise` or `None`
        The newest value, or `None` when the target has no usable row or the
        read failed. `None` means "unknown", never "below the goal".
    """
    try:
        rows = astrometrics.quality.query(
            metric=NORMALIZED_NOISE_METRIC,
            level=SubjectLevel.STACK.value,
            target_id=target_id,
            library_only=True,
            latest_only=True,
        )
    except AstrometricsError, OSError:
        return None
    pinned = PINNED_STACK_METRICS[NORMALIZED_NOISE_METRIC]
    usable = [
        row
        for row in rows
        if row.value is not None
        and row.definition_version == pinned
        and not any(marker in row.subject.subject_id for marker in LIBRARY_STACK_EXCLUDED_MARKERS)
    ]
    if not usable:
        return None
    newest = max(usable, key=lambda row: row.measured_at)
    return TargetNoise(value=float(newest.value), row_id=newest.row_id, measured_at=newest.measured_at)


def capture_setting_key(setting: CaptureSetting | None) -> str:
    """Name a capture setting in a source key.

    Parameters
    ----------
    setting : `CaptureSetting` or `None`
        The setting a statement is about.

    Returns
    -------
    key : `str`
        The camera, telescope, filter, exposure, gain, offset and binning
        joined with ``/``, ``-`` for a value that is not set. ``"-"`` for no
        setting.
    """
    if setting is None:
        return "-"
    parts = (
        setting.camera,
        setting.telescope,
        setting.filter_name,
        None if setting.exposure_seconds is None else f"{setting.exposure_seconds:g}",
        setting.gain_setting,
        setting.offset,
        setting.binning,
    )
    return "/".join("-" if part is None else str(part) for part in parts)


def row_source_key(rule_name: str, target_id: str, setting: CaptureSetting | None) -> str:
    """Name what a suggested row came from.

    Parameters
    ----------
    rule_name : `str`
        The rule.
    target_id : `str`
        The target.
    setting : `CaptureSetting` or `None`
        The capture setting the statement is about.

    Returns
    -------
    key : `str`
        ``rule|target|capture setting``.
    """
    return f"{rule_name}|{target_id}|{capture_setting_key(setting)}"


def ranking_source_key(target_id: str, use: str, night_id: str) -> str:
    """Name what a ranking suggestion came from.

    Parameters
    ----------
    target_id : `str`
        The target.
    use : `str`
        The ranked use (imaging, photometry or spectroscopy).
    night_id : `str`
        The observing night, such as ``"2026-10-25"``.

    Returns
    -------
    key : `str`
        ``ranking|target|use|night``.
    """
    return f"ranking|{target_id}|{use}|{night_id}"


def _round_action(action: ProposedAction | None) -> dict[str, Any]:
    """Round the values of a proposed action for a fingerprint.

    The exposure is rounded to whole seconds and the frame count to two
    significant figures, so a count of 118 becoming 121 is the same
    evidence while 118 becoming 200 is not.

    Parameters
    ----------
    action : `ProposedAction` or `None`
        The action.

    Returns
    -------
    values : `dict`
        The rounded values that are set. The note and the time window are
        left out.
    """
    if action is None:
        return {}
    values: dict[str, Any] = {}
    for name in (
        "exposure_seconds",
        "frame_count",
        "gain_setting",
        "offset",
        "binning",
        "filter_name",
        "dither_every_frames",
        "refocus_every_degc",
        "calibration_frame_type",
    ):
        value = getattr(action, name)
        if value is None:
            continue
        if name == "exposure_seconds":
            value = round(float(value))
        elif name == "frame_count":
            value = int(float(f"{value:.2g}"))
        elif name == "refocus_every_degc":
            value = round(float(value), 1)
        values[name] = value
    return values


def evidence_fingerprint(
    rule: QualityRule | None, rule_name: str, kind: str, action: ProposedAction | None
) -> str:
    """Make the short code for the evidence behind one suggested row.

    It is a hash of the rule, the statement kind, the rounded values of the
    proposed action and the definition versions of the metrics the rule reads.
    It does not use row ids or library-wide overheads, which change without
    the evidence changing.

    Parameters
    ----------
    rule : `QualityRule` or `None`
        The rule, for its metric versions. `None` when the rule is unknown.
    rule_name : `str`
        The rule's name.
    kind : `str`
        The statement kind (such as ``"recommendation"``).
    action : `ProposedAction` or `None`
        The action the statement proposes.

    Returns
    -------
    fingerprint : `str`
        Sixteen hexadecimal characters.
    """
    versions = sorted(
        (requirement.name, requirement.definition_version) for requirement in (rule.requires if rule else [])
    )
    payload = {"rule": rule_name, "kind": kind, "action": _round_action(action), "versions": versions}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def package_fingerprint(row_fingerprints: Iterable[str]) -> str:
    """Combine the fingerprints of a package's rows.

    Parameters
    ----------
    row_fingerprints : `Iterable` [`str`]
        The rows' fingerprints, in any order.

    Returns
    -------
    fingerprint : `str`
        Sixteen hexadecimal characters.
    """
    return hashlib.sha256("|".join(sorted(row_fingerprints)).encode()).hexdigest()[:16]


def ranking_fingerprint(target_id: str, use: str, night_id: str) -> str:
    """Make the fingerprint of a ranking suggestion.

    A ranking suggestion is keyed by target, use and night, so its
    fingerprint does not change within the night and a dismissal lasts
    until the night ends.

    Parameters
    ----------
    target_id : `str`
        The target.
    use : `str`
        The ranked use.
    night_id : `str`
        The observing night.

    Returns
    -------
    fingerprint : `str`
        Sixteen hexadecimal characters.
    """
    payload = {
        "rule": "ranking",
        "kind": "ranking",
        "target": target_id,
        "use": use,
        "night": night_id,
        "version": RANKING_FINGERPRINT_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
