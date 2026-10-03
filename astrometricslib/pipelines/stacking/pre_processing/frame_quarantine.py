"""Moves frames with clouds or trailed stars out of a target's folder.

A frame shot through cloud has few stars, and a frame shot while the mount
drifted has stars smeared into streaks. Stacking either one adds noise to
the result, and the stacker's own weighting only reduces the damage. This
step finds those frames before the stack runs and moves them into a folder
named `_excluded`, next to where they were. The stack then uses only the
sound frames.

The step is built to be undone:

- It moves frames and never deletes them.
- It writes `excluded_frames.json` in the `_excluded` folder, listing where
  each frame came from and why it was moved.
- `restore_quarantined_frames` moves the frames back.
- It judges each frame against the other frames of its own session, camera,
  filter and exposure, so a poor night is not mistaken for a few bad frames.
- It refuses to move more than a quarter of any batch. If a limit were
  wrong for some equipment, the step would flag the batch and move nothing.

The measurements come from `raw_frame_check`, the same code that checks
frames during an observing session.
"""

import json
import logging
import os
import shutil
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from astrometricslib.models.excluded_frames import SetAsideFrame
from astrometricslib.pipelines.shared.quality import raw_frame_check
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    QUARANTINE_EXCLUSION_REASON_PREFIX,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_FILE_NAME",
    "QuarantineDecision",
    "QuarantineReport",
    "decision_to_set_aside_frame",
    "find_frames_to_quarantine",
    "find_quarantine_folders",
    "judge_batch",
    "list_set_aside_frames",
    "quarantine_bad_frames",
    "read_quarantine_manifest",
    "restore_quarantined_frames",
    "split_into_batches",
]

MANIFEST_FILE_NAME = "excluded_frames.json"
"""The record, inside each `_excluded` folder, of the frames moved there."""

MINIMUM_FRAMES_TO_JUDGE = 8
"""A batch needs at least this many frames before any is judged.

The judgement compares each frame with the batch median. With fewer frames
one bad frame shifts the median enough to hide itself or to blame its
neighbours. In the clean frames of the 56-frame target of 2026-10-02, the
median star count of any eight consecutive frames stayed within 3.9% of the
median of all of them. In four older sessions of M 27 (18 to 60 frames) it
stayed within 5.8% to 18.3%. The star limit below (half the median) leaves
room for that spread.
"""

MAXIMUM_QUARANTINED_FRACTION = 0.25
"""The most of one batch this step will move; above it, the step moves none.

Run on the real library without moving anything, the step picked 4 of 56
frames (7%) of the 2026-10-02 target, 4 of 30 (13%) and 5 of 40 (12%) of two
M 27 sessions, and none in the other six batches. A quarter is about twice
the largest of those. A larger share means the batch itself is the problem
(poor seeing all night, or a limit that does not suit the equipment), and
the stacker's weighting is the better tool.
"""

CLOUD_STAR_FRACTION = raw_frame_check.LOW_STAR_FRACTION
"""A frame with fewer stars than this share of its batch median is moved.

Reuses the raw-frame check's limit (0.5). On the 2026-10-02 target the
median was 3,348 detected stars and the clean frames held 3,076 to 3,651.
The two frames shot as cloud thickened held 1,245 (0.37 of the median) and
203 (0.06). The mildest cloud frame held 2,966 (0.89) with the sky 1.17
times normal. It stays in the stack, where the stacker's weighting lowers
its influence.
"""

CLOUD_SKY_MULTIPLE = 1.15
"""A low-star frame with sky this much above the batch median is called cloud.

Only the wording of the reason depends on it. Clean frames of the
2026-10-02 target stayed within 1.09 times the median sky level while the
moon rose. The two clouded frames reached 2.25 and 3.52 times.
"""

TRAILED_ROUNDNESS_LIMIT = 0.80
"""A frame whose median star roundness is below this may have trailed stars.

Roundness is the narrow width divided by the wide width of a star, 1.0 for
a circle. On the 2026-10-02 target the clean frames measured 0.87 to 0.99,
and the two frames shot during the mount drift measured 0.44 and 0.65. A
frame with one 4 arcsec guiding spike (0.90) stayed in the stack. The limit
sits between those groups. It is stricter than the 0.88 used to flag a frame
for a person to look at.

A satellite or aircraft trail does not lower the median roundness, and
the stacker's pixel rejection removes it, so such a frame stays.
"""

ROUNDNESS_DROP_BELOW_MEDIAN = 0.15
"""A frame must also be this far below the batch's median roundness.

Some sessions are mildly elongated throughout. The M 27 session of
2026-09-24 had a median of 0.90 and a long tail: 0.47, 0.49, 0.55, then 0.71,
0.74, 0.76, 0.76, 0.78, 0.79 and 0.80. With the fixed limit alone, nine of
its 40 frames moved. The drop of 0.15 below the median (a cut-off of 0.75
there) moves the five clearly trailed frames and keeps the four that are
only mildly elongated. On the 2026-10-02 target (median 0.98) the cut-off
stays at the fixed 0.80.
"""

SESSION_GAP_SECONDS = 4 * 3600
"""A gap this long between frames starts a new session.

Frames of one night are minutes apart; a night's exposures last seconds to
a few minutes each. Gaps between nights are many hours.
"""

MEASUREMENT_THREADS = 4
"""Frames measured at once. A frame takes about a second to measure."""


@dataclass(frozen=True)
class QuarantineDecision:
    """One frame this step judged unfit to stack.

    Attributes
    ----------
    path : `str`
        Where the frame was before it was moved.
    kind : `str`
        ``"clouded"`` or ``"trailed"``.
    reason : `str`
        One sentence saying what was measured.
    star_count : `int`
        Bright regions found in this frame.
    typical_star_count : `float`
        Median of the batch.
    roundness : `float` or `None`
        Median star roundness of this frame.
    sky_median_adu : `float`
        Median pixel value of this frame.
    """

    path: str
    kind: str
    reason: str
    star_count: int
    typical_star_count: float
    roundness: float | None
    sky_median_adu: float


@dataclass
class QuarantineReport:
    """What the quarantine step did.

    Attributes
    ----------
    moved : `list` [`QuarantineDecision`]
        The frames moved into `_excluded`.
    notes : `list` [`str`]
        One sentence for each batch the step left alone, with the reason.
    unreadable : `list` [`str`]
        Frames that could not be measured. They stay where they are.
    """

    moved: list[QuarantineDecision] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def reasons_by_path(self) -> dict[str, str]:
        """Return the recorded reason for each moved frame.

        Returns
        -------
        reasons : `dict` [`str`, `str`]
            Original frame path mapped to the reason, starting with
            `QUARANTINE_EXCLUSION_REASON_PREFIX`.
        """
        return {
            decision.path: f"{QUARANTINE_EXCLUSION_REASON_PREFIX}: {decision.kind} ({decision.reason})"
            for decision in self.moved
        }


def split_into_batches(frames: Sequence[Any]) -> list[list[Any]]:
    """Group frames into batches that can be judged against each other.

    Frames share a batch when they have the same camera, filter and
    exposure and were taken less than `SESSION_GAP_SECONDS` apart.

    Parameters
    ----------
    frames : `Sequence`
        Frame records with ``camera``, ``filter``, ``exposure`` and
        ``timestamp``. A frame with no timestamp is placed by file name.

    Returns
    -------
    batches : `list` [`list`]
        The batches, each in time order.
    """
    by_setup: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for frame in frames:
        setup = (str(frame.camera), str(getattr(frame.filter, "value", frame.filter)), str(frame.exposure))
        by_setup[setup].append(frame)

    batches: list[list[Any]] = []
    for setup_frames in by_setup.values():
        ordered = sorted(setup_frames, key=lambda frame: (frame.timestamp or 0.0, frame.path))
        current = [ordered[0]]
        for frame in ordered[1:]:
            previous_time = current[-1].timestamp
            if (
                frame.timestamp is not None
                and previous_time is not None
                and frame.timestamp - previous_time > SESSION_GAP_SECONDS
            ):
                batches.append(current)
                current = []
            current.append(frame)
        batches.append(current)
    return batches


def judge_batch(measurements: Sequence[dict[str, Any]]) -> list[QuarantineDecision]:
    """Pick the frames of one batch that have clouds or trailed stars.

    Parameters
    ----------
    measurements : `Sequence` [`dict`]
        One entry per frame from `raw_frame_check.measure_raw_frame`:
        ``path``, ``star_count``, ``roundness`` and ``sky_median_adu``.

    Returns
    -------
    decisions : `list` [`QuarantineDecision`]
        The frames to move, in the order given. Empty when the batch is
        too small to judge.
    """
    if len(measurements) < MINIMUM_FRAMES_TO_JUDGE:
        return []
    typical_stars = statistics.median(m["star_count"] for m in measurements)
    typical_sky = statistics.median(m["sky_median_adu"] for m in measurements)
    roundness_values = [m["roundness"] for m in measurements if m["roundness"] is not None]
    roundness_limit = TRAILED_ROUNDNESS_LIMIT
    if roundness_values:
        roundness_limit = min(
            TRAILED_ROUNDNESS_LIMIT, statistics.median(roundness_values) - ROUNDNESS_DROP_BELOW_MEDIAN
        )

    decisions: list[QuarantineDecision] = []
    for measurement in measurements:
        roundness = measurement["roundness"]
        star_count = measurement["star_count"]
        sky = measurement["sky_median_adu"]
        kind = reason = None
        if roundness is not None and roundness < roundness_limit:
            kind = "trailed"
            reason = f"star roundness {roundness:.2f}, below {roundness_limit:.2f}"
        elif star_count < CLOUD_STAR_FRACTION * typical_stars:
            cloudy_sky = typical_sky > 0 and sky > CLOUD_SKY_MULTIPLE * typical_sky
            kind = "clouded" if cloudy_sky else "trailed"
            reason = f"{star_count} stars against a typical {typical_stars:.0f}" + (
                f", sky {sky / typical_sky:.1f} times normal" if cloudy_sky else ", sky level normal"
            )
        if kind is not None:
            decisions.append(
                QuarantineDecision(
                    path=measurement["path"],
                    kind=kind,
                    reason=reason,
                    star_count=int(star_count),
                    typical_star_count=float(typical_stars),
                    roundness=roundness,
                    sky_median_adu=float(sky),
                )
            )
    return decisions


def _measure_frames(
    frames: Sequence[Any], measure: Callable[[str], dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Measure frames in parallel, keeping the ones that can be read.

    Returns
    -------
    measurements : `list` [`dict`]
        One entry per readable frame, in the order given.
    unreadable : `list` [`str`]
        Paths that raised an error.
    """

    def safe_measure(frame: Any) -> dict[str, Any] | None:
        try:
            measurement = measure(frame.path)
        except Exception as error:
            logger.warning("Cannot measure '%s' for the quarantine check: %s", frame.path, error)
            return None
        measurement.pop("_stars", None)
        measurement["path"] = frame.path
        return measurement

    with ThreadPoolExecutor(max_workers=MEASUREMENT_THREADS) as pool:
        results = list(pool.map(safe_measure, frames))
    unreadable = [frame.path for frame, result in zip(frames, results, strict=True) if result is None]
    return [result for result in results if result is not None], unreadable


def find_frames_to_quarantine(
    frames: Sequence[Any],
    measure: Callable[[str], dict[str, Any]] = raw_frame_check.measure_raw_frame,
) -> QuarantineReport:
    """Decide which frames to move, without moving any.

    Parameters
    ----------
    frames : `Sequence`
        Light frame records of the target.
    measure : `Callable`, optional
        Measures one frame file. Defaults to
        `raw_frame_check.measure_raw_frame`; tests pass a stand-in.

    Returns
    -------
    report : `QuarantineReport`
        ``moved`` holds the decisions (nothing has been moved yet), ``notes``
        the batches left alone, ``unreadable`` the frames that could not be
        measured.
    """
    report = QuarantineReport()
    for batch in split_into_batches(frames):
        if len(batch) < MINIMUM_FRAMES_TO_JUDGE:
            report.notes.append(
                f"{len(batch)} frame(s) of one session are too few to judge "
                f"(at least {MINIMUM_FRAMES_TO_JUDGE} needed); none moved."
            )
            continue
        measurements, unreadable = _measure_frames(batch, measure)
        report.unreadable.extend(unreadable)
        decisions = judge_batch(measurements)
        allowed = int(MAXIMUM_QUARANTINED_FRACTION * len(measurements))
        if len(decisions) > allowed:
            report.notes.append(
                f"{len(decisions)} of {len(measurements)} frames in one session look bad, more than the "
                f"{allowed} allowed; none moved. The batch itself may be poor."
            )
            continue
        report.moved.extend(decisions)
    return report


def _unused_destination(directory: str, file_name: str) -> str:
    """Return a free path in `directory` for `file_name`.

    Returns
    -------
    destination : `str`
        ``directory/file_name``, or that name with ``_1``, ``_2`` and so on
        added when it is taken.
    """
    destination = os.path.join(directory, file_name)
    stem, extension = os.path.splitext(file_name)
    counter = 1
    while os.path.exists(destination):
        destination = os.path.join(directory, f"{stem}_{counter}{extension}")
        counter += 1
    return destination


def read_quarantine_manifest(manifest_path: str) -> list[dict[str, Any]]:
    """Read a quarantine manifest.

    Returns
    -------
    entries : `list` [`dict`]
        One entry per moved frame, or an empty list if there is no manifest.
    """
    if not os.path.exists(manifest_path):
        return []
    with open(manifest_path, encoding="utf-8") as manifest_file:
        return json.load(manifest_file)


def _write_manifest(manifest_path: str, entries: list[dict[str, Any]]) -> None:
    """Write a quarantine manifest.

    The new copy is written beside the old one and then replaces it, so a
    crash never leaves a half-written manifest.
    """
    temporary_path = manifest_path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as manifest_file:
        json.dump(entries, manifest_file, indent=2)
    os.replace(temporary_path, manifest_path)


def _move_to_quarantine(decision: QuarantineDecision) -> str:
    """Move one frame into the `_excluded` folder beside it and record it.

    Returns
    -------
    new_path : `str`
        Where the frame now is.
    """
    quarantine_directory = os.path.join(os.path.dirname(decision.path), QUARANTINE_FOLDER_NAME)
    os.makedirs(quarantine_directory, exist_ok=True)
    new_path = _unused_destination(quarantine_directory, os.path.basename(decision.path))
    shutil.move(decision.path, new_path)

    manifest_path = os.path.join(quarantine_directory, MANIFEST_FILE_NAME)
    entries = read_quarantine_manifest(manifest_path)
    entries.append({
        "file": os.path.basename(new_path),
        "original_path": decision.path,
        "kind": decision.kind,
        "reason": decision.reason,
        "star_count": decision.star_count,
        "typical_star_count": decision.typical_star_count,
        "roundness": decision.roundness,
        "sky_median_adu": decision.sky_median_adu,
        "moved_at": datetime.now(UTC).isoformat(timespec="seconds"),
    })
    _write_manifest(manifest_path, entries)
    return new_path


def quarantine_bad_frames(
    target: Any,
    frames: Sequence[Any],
    measure: Callable[[str], dict[str, Any]] = raw_frame_check.measure_raw_frame,
) -> tuple[list[Any], QuarantineReport]:
    """Move the frames with clouds or trailed stars out of the target's folder.

    Only light frames are judged. The moved frames are removed from
    ``target.frames`` too, so the target no longer lists them, and its total
    exposure is recalculated. The target is changed in memory; the caller
    saves it.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    frames : `Sequence`
        The frames the stack is about to use.
    measure : `Callable`, optional
        Measures one frame file; tests pass a stand-in.

    Returns
    -------
    kept_frames : `list`
        `frames` without the moved ones.
    report : `QuarantineReport`
        What was moved and what was left alone.
    """
    light_frames = [frame for frame in frames if str(frame.role).upper() == "LIGHT"]
    report = find_frames_to_quarantine(light_frames, measure)

    moved_paths: set[str] = set()
    for decision in list(report.moved):
        try:
            _move_to_quarantine(decision)
        except OSError as error:
            logger.warning("Could not move '%s' into %s: %s", decision.path, QUARANTINE_FOLDER_NAME, error)
            report.moved.remove(decision)
            report.notes.append(f"Could not move {os.path.basename(decision.path)}: {error}")
            continue
        moved_paths.add(decision.path)

    if moved_paths:
        logger.info(
            "Moved %d frame(s) with clouds or trailed stars into %s for target '%s'.",
            len(moved_paths),
            QUARANTINE_FOLDER_NAME,
            getattr(target, "id", "?"),
        )
        target.frames = [frame for frame in target.frames if frame.path not in moved_paths]
        target.recalculate_total_exposure()
    kept_frames = [frame for frame in frames if frame.path not in moved_paths]
    return kept_frames, report


def restore_quarantined_frames(directory: str) -> list[str]:
    """Move quarantined frames back to where they came from.

    Parameters
    ----------
    directory : `str`
        A folder holding an `_excluded` folder, or the `_excluded` folder
        itself.

    Returns
    -------
    restored : `list` [`str`]
        The original paths of the frames moved back. A frame whose original
        place is now taken stays in `_excluded`.
    """
    quarantine_directory = (
        directory
        if os.path.basename(os.path.normpath(directory)) == QUARANTINE_FOLDER_NAME
        else os.path.join(directory, QUARANTINE_FOLDER_NAME)
    )
    manifest_path = os.path.join(quarantine_directory, MANIFEST_FILE_NAME)
    entries = read_quarantine_manifest(manifest_path)

    restored: list[str] = []
    remaining: list[dict[str, Any]] = []
    for entry in entries:
        current_path = os.path.join(quarantine_directory, entry["file"])
        original_path = entry["original_path"]
        if not os.path.exists(current_path) or os.path.exists(original_path):
            remaining.append(entry)
            continue
        os.makedirs(os.path.dirname(original_path), exist_ok=True)
        shutil.move(current_path, original_path)
        restored.append(original_path)

    if remaining:
        _write_manifest(manifest_path, remaining)
    elif os.path.exists(manifest_path):
        os.remove(manifest_path)
        if not os.listdir(quarantine_directory):
            os.rmdir(quarantine_directory)
    return restored


def decision_to_set_aside_frame(decision: QuarantineDecision) -> SetAsideFrame:
    """Describe a decision in the shape the public API returns.

    Returns
    -------
    frame : `SetAsideFrame`
        The frame's measurements, with no move time and no folder yet.
    """
    return SetAsideFrame(
        file=os.path.basename(decision.path),
        original_path=decision.path,
        kind=decision.kind,
        reason=decision.reason,
        star_count=decision.star_count,
        typical_star_count=decision.typical_star_count,
        roundness=decision.roundness,
        sky_median_adu=decision.sky_median_adu,
    )


def _target_folder_names(target_id: str) -> list[str]:
    """List the folder names a target's lights may be stored under.

    Returns
    -------
    names : `list` [`str`]
        The id as written, then with spaces and underscores swapped, then
        with the spaces removed. Each name appears once.
    """
    return list(
        dict.fromkeys([
            target_id,
            target_id.replace(" ", "_"),
            target_id.replace("_", " "),
            target_id.replace(" ", ""),
        ])
    )


def find_quarantine_folders(frames_path: str, target_id: str) -> list[str]:
    """Find the `_excluded` folders that hold set-aside frames of a target.

    Parameters
    ----------
    frames_path : `str`
        The library's frames folder.
    target_id : `str`
        The target's id.

    Returns
    -------
    folders : `list` [`str`]
        Paths of the `_excluded` folders below the target's ``lights``
        folder that hold a manifest.
    """
    folders = []
    for name in _target_folder_names(target_id):
        root = os.path.join(frames_path, "lights", name)
        if not os.path.isdir(root):
            continue
        folders.extend(
            directory
            for directory, _, files in os.walk(root)
            if os.path.basename(directory) == QUARANTINE_FOLDER_NAME and MANIFEST_FILE_NAME in files
        )
    return folders


def list_set_aside_frames(frames_path: str, target_id: str) -> list[SetAsideFrame]:
    """List the frames set aside for a target, as the manifests record them.

    Parameters
    ----------
    frames_path : `str`
        The library's frames folder.
    target_id : `str`
        The target's id.

    Returns
    -------
    frames : `list` [`SetAsideFrame`]
        One entry per frame still in an `_excluded` folder, with the folder
        it is in.
    """
    frames = []
    for folder in find_quarantine_folders(frames_path, target_id):
        for entry in read_quarantine_manifest(os.path.join(folder, MANIFEST_FILE_NAME)):
            frames.append(
                SetAsideFrame(
                    file=entry["file"],
                    original_path=entry["original_path"],
                    kind=entry["kind"],
                    reason=entry["reason"],
                    star_count=entry["star_count"],
                    typical_star_count=entry["typical_star_count"],
                    roundness=entry.get("roundness"),
                    sky_median_adu=entry["sky_median_adu"],
                    moved_at=entry.get("moved_at"),
                    folder=folder,
                )
            )
    return frames
