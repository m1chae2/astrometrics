"""Moves frames with clouds, trails or a bad pointing out of the stack.

A frame shot through cloud has few stars or an uneven sky, a frame shot while
the mount drifted has stars smeared into streaks, and a frame that points far
from the rest of the session forces the stack to be cut down to the area all
frames share. Stacking any of them adds noise or loses image area, and the
stacker's own weighting only reduces the damage. This
step finds those frames before the stack runs and moves them into a folder
named `_excluded`, next to where they were. The stack then uses only the
sound frames.

The step is built to be undone:

- It moves frames and never deletes them.
- It writes `excluded_frames.json` in the `_excluded` folder, listing where
  each frame came from and why it was moved (the rule, the frame's value and
  the limit, in plain words).
- `restore_quarantined_frames` moves the frames back.
- `quarantine_preview_report` shows which frames the step would move,
  without moving any.
- It judges each frame against the other frames of its own session, camera,
  filter and exposure, so a poor night is not mistaken for a few bad frames.
- It sets aside every frame that fails a check, however many that is. When
  more than a quarter of a batch is set aside, it still moves them and adds a
  warning (for example "35 of 123 frames set aside: 4 trailed, 12 off field,
  19 uneven sky") to the job's gate record, the log and each frame's entry in
  `excluded_frames.json`, so a limit that does not suit the equipment is
  easy to see.

The star count, sky level, sky grid and star positions come from
`raw_frame_check`, the same code that checks frames during an observing
session. Trailing is judged on the frame's stored star elongation
(`frame.star_elongation`), which the frame measurement makes.
"""

import contextvars
import json
import logging
import math
import os
import shutil
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.excluded_frames import QuarantinePreview, SetAsideFrame
from astrometricslib.models.quality.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.target import Target
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.quality import raw_frame_check
from astrometricslib.pipelines.shared.quality.frame_measurement import measure_image_star_profiles
from astrometricslib.pipelines.shared.quality.metrics import FRAME_STAR_ELONGATION
from astrometricslib.pipelines.shared.quality.star_profile_fitting import usable_top_level_adu
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    QUARANTINE_EXCLUSION_REASON_PREFIX,
)
from astrometricslib.pipelines.stacking.stage_progress import LoopProgress
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.frame_identity import frame_subject_id
from astrometricslib.utilities.iso_text import canonical_iso_text

logger = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_FILE_NAME",
    "QuarantineDecision",
    "QuarantineReport",
    "decision_to_set_aside_frame",
    "drop_frames_already_set_aside",
    "find_frames_already_set_aside",
    "find_frames_to_quarantine",
    "find_quarantine_folders",
    "judge_batch",
    "list_set_aside_frames",
    "quarantine_bad_frames",
    "quarantine_preview_report",
    "read_quarantine_manifest",
    "restore_quarantined_frames",
    "split_into_batches",
]

MANIFEST_FILE_NAME = "excluded_frames.json"

# The name the quarantine step goes by in a stack's gate record.
GATE_NAME = "frame_quarantine"
"""The record, inside each `_excluded` folder, of the frames moved there."""

MINIMUM_FRAMES_TO_JUDGE = TunedParameter(
    name="stacking.minimum_frames_to_judge",
    default=8,
    unit="frames",
    description="Fewest frames a batch needs before any frame is judged for quarantine.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-01",
)
"""A batch needs at least this many frames before any is judged.

The judgement compares each frame with the batch median. With fewer frames
one bad frame shifts the median enough to hide itself or to blame its
neighbours. In the clean frames of the 56-frame target of 2026-10-02, the
median star count of any eight consecutive frames stayed within 3.9% of the
median of all of them. In four older sessions of M 27 (18 to 60 frames) it
stayed within 5.8% to 18.3%. The star limit below (half the median) leaves
room for that spread.
"""

WARN_QUARANTINED_FRACTION = 0.25
"""The share of a batch above which setting frames aside draws a warning.

This is a documented constant, not a tuned parameter, and it blocks nothing:
every frame that fails a check is moved. Run on the real library without
moving anything, the step picked 4 of 56 frames (7%) of the 2026-10-02
target, 4 of 30 (13%) and 5 of 40 (12%) of two M 27 sessions, and none in the
other six batches. A quarter is about twice the largest of those, so a larger
share is unusual. It may mean a poor night, or a limit that does not suit the
equipment, and the person reading the warning should look at it.
"""

CLOUD_STAR_FRACTION = resolve(raw_frame_check.LOW_STAR_FRACTION).value
"""A frame with fewer stars than this share of its batch median is moved.

Reuses the raw-frame check's limit (0.5). On the 2026-10-02 target the
median was 3,348 detected stars and the clean frames held 3,076 to 3,651.
The two frames shot as cloud thickened held 1,245 (0.37 of the median) and
203 (0.06). The mildest cloud frame held 2,966 (0.89) with the sky 1.17
times normal. It stays in the stack, where the stacker's weighting lowers
its influence.
"""

CLOUD_SKY_MULTIPLE = TunedParameter(
    name="stacking.cloud_sky_multiple",
    default=1.15,
    unit="× median sky",
    description="Sky level, as a multiple of the batch median, above which a low-star frame is called cloud.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-01",
)
"""A low-star frame with sky this much above the batch median is called cloud.

Only the wording of the reason depends on it. Clean frames of the
2026-10-02 target stayed within 1.09 times the median sky level while the
moon rose. The two clouded frames reached 2.25 and 3.52 times.
"""

TRAILED_ELONGATION_LIMIT = TunedParameter(
    name="stacking.trailed_elongation_limit",
    default=1.32,
    unit="ratio",
    description="Star elongation above which a frame may have trailed stars.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="STK-01",
    allowed_range=(1.1, 2.0),
    # A sample is one session (target, night, capture setting).
    minimum_sample=5,
)
"""A frame whose star elongation is above this may have trailed stars.

Elongation is the long width of a star divided by its short width, 1.0 for
a circle. It is the stored `frame.star_elongation` of the frame (the median
over up to 196 isolated, unsaturated stars, from their full second moments,
so a trail at any angle counts). The earlier test used the median ratio of
the x and y widths alone (`frame.quarantine_roundness`). It missed trails at
an angle to the pixel axes: NGC 7331 L_037 (elongation 1.58, by eye a
trail) read 0.93.

On 21 sessions of the ZWO ASI 533MM Pro (675 frames, STK-01) the middle of
all frames is 1.096 and the spread of a frame about its own session's median
is 0.045 (robust standard deviation). The limit sits five of those spreads
above the library median, 1.32 (the repository's usual cut for an outlier,
see `quarantine_tuning.ELONGATION_OUTLIER_SIGMAS`). It is the floor under
the session-relative limit below, so a very round session is not cut at a
tiny rise.

A satellite or aircraft trail does not raise the median elongation, and the
stacker's pixel rejection removes it, so such a frame stays.
"""

ELONGATION_RISE_ABOVE_MEDIAN = TunedParameter(
    name="stacking.elongation_rise_above_median",
    default=0.22,
    unit="ratio",
    description="How far above the batch's median elongation a frame must be to count as trailed.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="STK-01",
    allowed_range=(0.05, 0.6),
    # A sample is one session (target, night, capture setting).
    minimum_sample=5,
)
"""A frame must also be this far above the batch's median elongation.

Some sessions are mildly elongated throughout. The M 27 session of
2026-09-24 had a median elongation of 1.28 and 15 of its 33 frames above
1.30. With the fixed limit alone most of them would move. With the rise of
0.22 the cut-off there is 1.50. On a round session such as NGC 7331 (median
1.096) the cut-off is the fixed 1.32.

The rise is five robust standard deviations of a frame's distance above its
own session's median, over the same 675 frames as the fixed limit.
"""

COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN = TunedParameter(
    name="stacking.colour_camera_elongation_rise_above_median",
    default=0.22,
    unit="ratio",
    description="Elongation rise above the median that counts as trailed for a colour camera.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.CAMERA}),
    register_entry="STK-02",
    allowed_range=(0.05, 0.8),
    # A sample is one session (target, night, capture setting).
    minimum_sample=5,
)
"""The rise above the median for a colour (Bayer) camera.

A colour sensor puts one red, green or blue filter over each pixel. At
405 mm the Nikon D5300 sees stars only 2 to 2.5 pixels wide, so each star
is sampled coarsely and its measured shape scatters more, even when the star
is round. The star shape of a raw mosaic is measured on 2 x 2 super-pixels
(see `frame_measurement`), which removes the filter pattern.

No stored `frame.star_elongation` rows exist for the colour camera in this
library, so the tuner cannot yet set this value. It was measured on 257
Nikon frames with the frame measurement's own code on 2026-11-20 (three
sessions, 2024). The 167 frames of NGC 6888 (one session) have a median of
1.16 and a robust spread of 0.019, so five spreads is 0.10, smaller than the
mono value. The two M 106 sessions (90 frames) scatter far more (medians 1.39
and 1.36, spreads 0.31): by roundness only two of their frames were judged
bad, so it is not known whether the scatter is trailing or measurement noise
in a sparse field. Pooling all three sessions gives 0.17. Until more colour
sessions are stored, the colour value equals the mono one (0.22), which is
above all three measurements of the clean session and cannot be tuned lower
without data (STK-02).
"""

UNEVEN_SKY_SIGMAS = TunedParameter(
    name="stacking.uneven_sky_sigmas",
    default=5.0,
    unit="× robust standard deviation",
    description="How many robust standard deviations above a session's usual unevenness is cloud.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D72",
)
"""A frame is uneven when it lies this many robust spreads above the session.

Five is the repository's usual cut for an outlier against the spread of its
own run (see `stacking.step_jump_sigmas`). The unevenness of the 95 clear
frames of NGC 7331 (before the sky began to change at frame 99) has a
median of 0.0023 and a robust spread of 0.0009, so the cut falls at 0.0068
before the floor below applies (STK-D72).
"""

UNEVEN_SKY_MINIMUM = TunedParameter(
    name="stacking.uneven_sky_minimum",
    default=0.01,
    unit="fraction of the sky level",
    description="Least sky unevenness that can set a frame aside, whatever the session's spread.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D72",
)
"""The sky must differ from the session pattern by 1% to count as uneven.

Unevenness is the root-mean-square difference, over a 4 x 4 grid of boxes,
between the frame's sky pattern and the session's median pattern, as a
fraction of the frame's own sky level (bias included, so a sky-only fraction
is about twice as large). On the clear frames of NGC 7331 it is 0.0005 to
0.0027, almost all of it the noise of the box medians. A session with an
unusually steady sky would give a tiny spread and a cut inside that noise,
so the cut never goes below four times the clear frames' largest value,
rounded to 0.01 (STK-D72).
"""

OFF_FIELD_POINTING_FRACTION = TunedParameter(
    name="stacking.off_field_pointing_fraction",
    default=0.10,
    unit="× frame width",
    description="How far from the main field a frame's centre may lie, as a share of frame width.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D73",
)
"""A frame centred farther than this from the main field is set aside.

The offset is found by matching the brightest stars of each frame with those
of one reference frame of the session, trying the frame as it is and turned
half a revolution (a meridian flip turns the camera, and a flipped frame of
the same field is not offset). The main field is the median of the offsets.
On NGC 7331 the frames of the same field sit within 0.020 of the width (a
slow drift of up to 75 pixels), the 12 frames after the flip that missed
the field sit at 0.243, and the 0.10 limit lies between them. A frame this
far off cuts the stack's overlap, so the stack is trimmed to its common area
(a 42% crop on NGC 7331). The M 31 session (shifts of 25 pixels or less)
sets nothing aside (STK-D73).
"""

SESSION_GAP_SECONDS = TunedParameter(
    name="stacking.session_gap_seconds",
    default=4 * 3600,
    unit="s",
    description="Gap between frames that starts a new session.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D13",
)
"""A gap this long between frames starts a new session.

Frames of one night are minutes apart; a night's exposures last seconds to
a few minutes each. Gaps between nights are many hours.
"""

MEASUREMENT_THREADS = 4
"""Frames measured at once. A frame takes about a second to measure."""


SET_ASIDE_ACTION_PREFIX = "frame moved to _excluded/"
"""Start of the ``action_taken`` text of a set-aside frame's rows.

The folder name is `QUARANTINE_FOLDER_NAME`. A reader finds the rows of
set-aside frames by this prefix (see `parse_set_aside_action`).
"""


def set_aside_action_text(kind: str, reason: str) -> str:
    """Write the ``action_taken`` sentence for a frame that was set aside.

    Parameters
    ----------
    kind : `str`
        ``"clouded"`` or ``"trailed"``.
    reason : `str`
        The one sentence saying what was measured.

    Returns
    -------
    text : `str`
        ``"frame moved to _excluded/: <kind> (<reason>)"``.
    """
    return f"{SET_ASIDE_ACTION_PREFIX}: {kind} ({reason})"


def parse_set_aside_action(action_taken: str | None) -> tuple[str, str] | None:
    """Read the kind and reason back out of a stored ``action_taken`` text.

    Parameters
    ----------
    action_taken : `str` or `None`
        The stored text of a measurement row.

    Returns
    -------
    parsed : `tuple` [`str`, `str`] or `None`
        The kind and the reason, or `None` when the text is not a set-aside
        action.
    """
    if not action_taken or not action_taken.startswith(f"{SET_ASIDE_ACTION_PREFIX}: "):
        return None
    remainder = action_taken[len(SET_ASIDE_ACTION_PREFIX) + 2 :]
    kind, separator, reason_part = remainder.partition(" (")
    if not separator:
        return remainder, ""
    return kind, reason_part[:-1] if reason_part.endswith(")") else reason_part


@dataclass(frozen=True)
class RuleFinding:
    """One rule a frame broke, and the plain words that say so.

    Attributes
    ----------
    rule : `str`
        ``"trailed"``, ``"few_stars"``, ``"uneven_sky"`` or ``"off_field"``.
    kind : `str`
        The stored kind: ``"clouded"``, ``"trailed"`` or ``"off_field"``.
    reason : `str`
        One sentence with the rule, the frame's value and the limit.
    """

    rule: str
    kind: str
    reason: str


@dataclass(frozen=True)
class QuarantineDecision:
    """One frame this step judged unfit to stack.

    Attributes
    ----------
    path : `str`
        Where the frame was before it was moved.
    kind : `str`
        ``"clouded"``, ``"trailed"`` or ``"off_field"``, from the first rule
        the frame broke.
    reason : `str`
        What was measured, in plain words: each rule the frame broke, its
        value and its limit, joined by semicolons.
    star_count : `int`
        Bright regions found in this frame.
    typical_star_count : `float`
        Median of the batch.
    star_elongation : `float` or `None`
        The frame's stored star elongation (long width over short width), or
        `None` when it has none.
    sky_median_adu : `float`
        Median pixel value of this frame.
    sky_unevenness : `float` or `None`
        How much the sky pattern of this frame differs from the session's.
    pointing_offset_fraction : `float` or `None`
        How far the frame's centre lies from the session's main field, as a
        share of the frame width.
    findings : `tuple` [`RuleFinding`, ...]
        Every rule the frame broke, in the order they are tried.
    """

    path: str
    kind: str
    reason: str
    star_count: int
    typical_star_count: float
    star_elongation: float | None
    sky_median_adu: float
    sky_unevenness: float | None = None
    pointing_offset_fraction: float | None = None
    findings: tuple[RuleFinding, ...] = ()


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
    batches_judged : `int`
        Batches with enough readable frames to be judged and acted on.
    batches_too_small : `int`
        Batches with too few frames, or too few readable ones, to judge.
    batches_with_many_set_aside : `int`
        Batches judged in which more than `WARN_QUARANTINED_FRACTION` of the
        frames were set aside. The frames are moved all the same.
    warnings : `list` [`str`]
        One sentence for each such batch, for example "35 of 123 frames set
        aside: 4 trailed, 12 off field, 19 uneven sky".
    warning_by_path : `dict` [`str`, `str`]
        The sentence of a batch, for each frame set aside in that batch.
        The frame's `excluded_frames.json` entry carries it.
    measurements : `list` [`dict`]
        What `raw_frame_check.measure_raw_frame` measured for every frame
        the step could read, whether the frame was moved or kept. A batch
        too small to judge is not measured, so it adds none. The stacking
        stage stores these as quality measurements.
    """

    moved: list[QuarantineDecision] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    # How each session (batch) of frames ended up. A batch is judged only if
    # it has enough readable frames; otherwise it is not checked at all, which
    # is different from being checked and found clean.
    batches_judged: int = 0
    batches_too_small: int = 0
    batches_with_many_set_aside: int = 0
    warnings: list[str] = field(default_factory=list)
    warning_by_path: dict[str, str] = field(default_factory=dict)
    measurements: list[dict[str, Any]] = field(default_factory=list)

    def actions_by_path(self) -> dict[str, str]:
        """Return the action text of each moved frame, for its stored rows.

        Returns
        -------
        actions : `dict` [`str`, `str`]
            Original frame path mapped to the sentence stored as the
            ``action_taken`` of the frame's quarantine measurements. See
            `set_aside_action_text`.
        """
        return {
            decision.path: set_aside_action_text(decision.kind, decision.reason) for decision in self.moved
        }

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

    Frames share a batch when they have the same capture setting (camera,
    optic, filter, exposure and gain) and were taken less than
    `SESSION_GAP_SECONDS` apart. Each gain setting is its own batch, because
    a frame's star count and sky level depend on the gain, so a frame is
    judged only against frames taken the same way.

    Parameters
    ----------
    frames : `Sequence`
        Frame records with ``camera``, ``filter``, ``exposure`` and
        ``timestamp``, and optionally ``iso`` (the gain) and
        ``focal_length_mm`` (the optic). A frame with no timestamp is placed
        by file name.

    Returns
    -------
    batches : `list` [`list`]
        The batches, each in time order.
    """
    session_gap_seconds = resolve(SESSION_GAP_SECONDS).value
    by_setup: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    for frame in frames:
        focal_length = getattr(frame, "focal_length_mm", None)
        setup = (
            str(frame.camera),
            str(round(float(focal_length))) if focal_length else "",
            str(getattr(frame.filter, "value", frame.filter)),
            str(frame.exposure),
            canonical_iso_text(getattr(frame, "iso", None)),
        )
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
                and frame.timestamp - previous_time > session_gap_seconds
            ):
                batches.append(current)
                current = []
            current.append(frame)
        batches.append(current)
    return batches


def _robust_spread(values: Sequence[float]) -> float:
    """Measure the spread of some numbers in a way a stray value cannot move.

    Returns
    -------
    spread : `float`
        The median absolute deviation about the median, times 1.4826, so it
        equals the standard deviation for normally spread numbers.
    """
    middle = statistics.median(values)
    return 1.4826 * statistics.median(abs(value - middle) for value in values)


def measure_sky_unevenness(measurements: Sequence[dict[str, Any]]) -> list[float | None]:
    """Measure how much each frame's sky pattern differs from the session's.

    Each frame carries the sky level in a grid of boxes (``sky_grid_adu``).
    The boxes are divided by the frame's own sky level (the median of its
    boxes), which removes the overall brightness. The session's typical
    pattern is the median of those fractions box by box. It holds what every
    frame shares: vignetting, the dust the flat does not remove and the
    steady gradient of the sky glow, which is the plane that best fits the
    pattern. A frame's unevenness is the root-mean-square difference between
    its fractions and that pattern. Thin cloud lights part of the frame, so
    it raises the number.

    Parameters
    ----------
    measurements : `Sequence` [`dict`]
        Frame measurements of one batch.

    Returns
    -------
    unevenness : `list` [`float` or `None`]
        One value per frame, `None` for a frame with no grid.
    """
    patterns: list[np.ndarray | None] = []
    for measurement in measurements:
        grid = measurement.get("sky_grid_adu")
        if grid is None:
            patterns.append(None)
            continue
        boxes = np.asarray(grid, dtype=float).ravel()
        level = float(np.median(boxes))
        patterns.append(boxes / level - 1.0 if level > 0 else None)
    usable = [pattern for pattern in patterns if pattern is not None]
    if not usable:
        return [None] * len(measurements)
    typical_pattern = np.median(np.array(usable), axis=0)
    return [
        None if pattern is None else float(np.sqrt(np.mean((pattern - typical_pattern) ** 2)))
        for pattern in patterns
    ]


def measure_pointing_offsets(measurements: Sequence[dict[str, Any]]) -> tuple[list[float | None], str]:
    """Measure how far each frame's centre lies from the session's main field.

    The brightest stars of the frame in the middle of the batch are the
    reference. Every frame is matched to it
    (`raw_frame_check.measure_field_offset`), which tries the frame as it
    is and turned half a revolution, so a meridian flip does not count as a
    move. The main field is the median centre of all the matched frames,
    and a frame's offset is its distance from it divided by the frame width.

    The star positions come from the frames themselves, not from the header
    pointing or from a stored stack's shifts. On NGC 7331 the header
    pointing did not show the 720-pixel miss after the flip, and stored shifts
    do not exist before the first stack.

    Parameters
    ----------
    measurements : `Sequence` [`dict`]
        Frame measurements of one batch, in time order, each with ``_stars``,
        ``width_px`` and ``height_px``.

    Returns
    -------
    offsets : `list` [`float` or `None`]
        One value per frame, `None` for a frame that could not be matched.
    note : `str`
        A sentence about frames left unjudged, or an empty string.
    """
    measured = [m for m in measurements if m.get("_stars") is not None and len(m["_stars"])]
    if len(measured) < len(measurements) / 2 or not measured:
        return [None] * len(measurements), (
            "Pointing was not judged: too few frames had stars to match." if measurements else ""
        )
    reference = measurements[len(measurements) // 2]
    if reference.get("_stars") is None or not len(reference["_stars"]):
        reference = measured[len(measured) // 2]
    centres: list[tuple[float, float] | None] = []
    for measurement in measurements:
        stars = measurement.get("_stars")
        if measurement is reference:
            centres.append((0.0, 0.0))
        elif stars is None or not len(stars):
            centres.append(None)
        else:
            found = raw_frame_check.measure_field_offset(
                reference["_stars"], stars, (measurement["height_px"], measurement["width_px"])
            )
            centres.append(None if found is None else (found[0], found[1]))
    matched = [centre for centre in centres if centre is not None]
    if len(matched) < len(measurements) / 2:
        return [None] * len(measurements), (
            f"Pointing was not judged: only {len(matched)} of {len(measurements)} frames could be "
            "matched to the reference frame's stars."
        )
    main_row = statistics.median(centre[0] for centre in matched)
    main_column = statistics.median(centre[1] for centre in matched)
    offsets = [
        None
        if centre is None
        else math.hypot(centre[0] - main_row, centre[1] - main_column) / measurement["width_px"]
        for centre, measurement in zip(centres, measurements, strict=True)
    ]
    unmatched = len(measurements) - len(matched)
    note = (
        f"Pointing could not be matched for {unmatched} of {len(measurements)} frames; "
        "those frames were not judged for it."
        if unmatched
        else ""
    )
    return offsets, note


def judge_batch_with_notes(
    measurements: Sequence[dict[str, Any]],
) -> tuple[list[QuarantineDecision], list[str]]:
    """Pick the frames of one batch that are clouded, trailed or off the field.

    Four rules are tried on every frame, and a frame that breaks several is
    reported for each (see `RuleFinding`):

    - Trailed stars: the frame's stored star elongation is above both
      `TRAILED_ELONGATION_LIMIT` and the batch median plus the rise
      (`ELONGATION_RISE_ABOVE_MEDIAN`, or the colour camera's).
    - Few stars: fewer than `CLOUD_STAR_FRACTION` of the batch median.
    - Uneven sky: the sky pattern differs from the session's by more than
      `UNEVEN_SKY_SIGMAS` robust spreads above the typical difference, and
      by at least `UNEVEN_SKY_MINIMUM` (see `measure_sky_unevenness`).
    - Off the field: the frame's centre is more than
      `OFF_FIELD_POINTING_FRACTION` of the frame width from the session's
      main field (see `measure_pointing_offsets`).

    The measurements are updated in place with ``sky_unevenness`` and
    ``pointing_offset_fraction``, so the caller can store them for kept
    frames too.

    Parameters
    ----------
    measurements : `Sequence` [`dict`]
        One entry per frame, in time order: ``path``, ``star_count``,
        ``star_elongation`` (or `None`), ``sky_median_adu``, ``sky_grid_adu``,
        ``_stars``, ``width_px``, ``height_px``, and optionally
        ``colour_camera`` (a colour camera gets the wider
        `COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN`).

    Returns
    -------
    decisions : `list` [`QuarantineDecision`]
        The frames to move, in the order given. Empty when the batch is
        too small to judge.
    notes : `list` [`str`]
        One sentence for each check that could not be made on some frames.
    """
    notes: list[str] = []
    if len(measurements) < resolve(MINIMUM_FRAMES_TO_JUDGE).value:
        return [], notes
    cloud_sky_multiple = resolve(CLOUD_SKY_MULTIPLE).value
    typical_stars = statistics.median(m["star_count"] for m in measurements)
    typical_sky = statistics.median(m["sky_median_adu"] for m in measurements)

    elongations = [m.get("star_elongation") for m in measurements]
    known_elongations = [value for value in elongations if value is not None]
    elongation_limit = None
    if known_elongations:
        rise = (
            resolve(COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN).value
            if any(m.get("colour_camera") for m in measurements)
            else resolve(ELONGATION_RISE_ABOVE_MEDIAN).value
        )
        elongation_limit = max(
            resolve(TRAILED_ELONGATION_LIMIT).value, statistics.median(known_elongations) + rise
        )
    if len(known_elongations) < len(measurements):
        missing = len(measurements) - len(known_elongations)
        notes.append(
            f"{missing} of {len(measurements)} frames have no star elongation, so trailing was not "
            "judged for them."
        )

    unevenness = measure_sky_unevenness(measurements)
    known_unevenness = [value for value in unevenness if value is not None]
    unevenness_limit = None
    if len(known_unevenness) >= resolve(MINIMUM_FRAMES_TO_JUDGE).value:
        unevenness_limit = max(
            resolve(UNEVEN_SKY_MINIMUM).value,
            statistics.median(known_unevenness)
            + resolve(UNEVEN_SKY_SIGMAS).value * _robust_spread(known_unevenness),
        )
    else:
        notes.append("The sky was not checked for unevenness: too few frames had a sky grid.")

    pointing_offsets, pointing_note = measure_pointing_offsets(measurements)
    if pointing_note:
        notes.append(pointing_note)
    pointing_limit = resolve(OFF_FIELD_POINTING_FRACTION).value

    decisions: list[QuarantineDecision] = []
    for measurement, elongation, uneven, offset in zip(
        measurements, elongations, unevenness, pointing_offsets, strict=True
    ):
        measurement["sky_unevenness"] = uneven
        measurement["pointing_offset_fraction"] = offset
        star_count = measurement["star_count"]
        sky = measurement["sky_median_adu"]
        findings: list[RuleFinding] = []
        if elongation is not None and elongation_limit is not None and elongation > elongation_limit:
            findings.append(
                RuleFinding(
                    "trailed",
                    "trailed",
                    f"Stars trailed: elongation {elongation:.2f}, limit {elongation_limit:.2f}",
                )
            )
        if star_count < CLOUD_STAR_FRACTION * typical_stars:
            cloudy_sky = typical_sky > 0 and sky > cloud_sky_multiple * typical_sky
            # Few stars under a normal sky is stored as "trailed" too. The
            # name is kept, because it is stored in the quality rows and in
            # `excluded_frames.json`; readers word it as "stars smeared, or far
            # fewer than usual".
            findings.append(
                RuleFinding(
                    "few_stars",
                    "clouded" if cloudy_sky else "trailed",
                    f"Few stars: {star_count} against a typical {typical_stars:.0f}"
                    + (f", sky {sky / typical_sky:.1f} times normal" if cloudy_sky else ", sky level normal"),
                )
            )
        if uneven is not None and unevenness_limit is not None and uneven > unevenness_limit:
            findings.append(
                RuleFinding(
                    "uneven_sky",
                    "clouded",
                    f"Uneven sky, likely cloud: {uneven:.3f}, limit {unevenness_limit:.3f}",
                )
            )
        if offset is not None and offset > pointing_limit:
            findings.append(
                RuleFinding(
                    "off_field",
                    "off_field",
                    f"Pointing off the main field: {offset:.2f} of the field width, "
                    f"limit {pointing_limit:.2f}",
                )
            )
        if findings:
            decisions.append(_decision_from_findings(measurement, findings, typical_stars))
    return decisions, notes


def _decision_from_findings(
    measurement: dict[str, Any], findings: Sequence[RuleFinding], typical_stars: float
) -> QuarantineDecision:
    """Build a decision from a frame's measurements and the rules it broke.

    Returns
    -------
    decision : `QuarantineDecision`
        The kind of the first finding, and every finding's reason.
    """
    return QuarantineDecision(
        path=measurement["path"],
        kind=findings[0].kind,
        reason="; ".join(finding.reason for finding in findings),
        star_count=int(measurement["star_count"]),
        typical_star_count=float(typical_stars),
        star_elongation=measurement.get("star_elongation"),
        sky_median_adu=float(measurement["sky_median_adu"]),
        sky_unevenness=measurement.get("sky_unevenness"),
        pointing_offset_fraction=measurement.get("pointing_offset_fraction"),
        findings=tuple(findings),
    )


def judge_batch(measurements: Sequence[dict[str, Any]]) -> list[QuarantineDecision]:
    """Pick the frames of one batch that are clouded, trailed or off the field.

    Parameters
    ----------
    measurements : `Sequence` [`dict`]
        See `judge_batch_with_notes`.

    Returns
    -------
    decisions : `list` [`QuarantineDecision`]
        The frames to move, in the order given. Empty when the batch is
        too small to judge.
    """
    return judge_batch_with_notes(measurements)[0]


RULE_WORDS = {
    "trailed": "trailed",
    "few_stars": "few stars",
    "uneven_sky": "uneven sky",
    "off_field": "off field",
}
"""The words that name each rule in a warning."""


def describe_many_set_aside(decisions: Sequence[QuarantineDecision], batch_size: int) -> str | None:
    """Write the warning for a batch in which many frames were set aside.

    Parameters
    ----------
    decisions : `Sequence` [`QuarantineDecision`]
        What `judge_batch_with_notes` found for the batch.
    batch_size : `int`
        Frames judged in the batch.

    Returns
    -------
    warning : `str` or `None`
        For example "35 of 123 frames set aside: 4 trailed, 12 off field, 19
        uneven sky". A frame that broke two rules counts under each. `None`
        when no more than `WARN_QUARANTINED_FRACTION` of the batch was set
        aside.
    """
    if len(decisions) <= WARN_QUARANTINED_FRACTION * batch_size:
        return None
    counts: dict[str, int] = defaultdict(int)
    for decision in decisions:
        for finding in decision.findings:
            counts[finding.rule] += 1
    parts = ", ".join(f"{count} {RULE_WORDS.get(rule, rule)}" for rule, count in counts.items())
    return f"{len(decisions)} of {batch_size} frames set aside: {parts}"


def stored_star_elongations(target_id: str) -> dict[str, float]:
    """Read the stored star elongation of each frame of a target.

    The frame measurement stores ``frame.star_elongation`` for every frame it
    measures. A frame stored by colour plane has one row per plane, and the
    median of the planes is used. The store is only read.

    Parameters
    ----------
    target_id : `str`
        The target's id.

    Returns
    -------
    elongations : `dict` [`str`, `float`]
        The newest stored value of each frame, by the frame's subject id
        (see `frame_subject_id`). Empty when the library has no such rows or
        no quality database.
    """
    try:
        store = QualityStore.from_configuration(get_configuration())
        rows = store.query(
            metric=FRAME_STAR_ELONGATION, level=SubjectLevel.FRAME, target_id=target_id, library_only=True
        )
    except (AstrometricsError, sqlite3.Error, OSError) as error:
        logger.debug("Could not read the stored star elongations of '%s': %s", target_id, error)
        return {}
    by_frame: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.value is not None and math.isfinite(row.value):
            by_frame[row.subject.subject_id].append(row.value)
    return {subject_id: statistics.median(values) for subject_id, values in by_frame.items()}


def measure_star_elongation(path: str, camera: str | None) -> float | None:
    """Measure a frame's star elongation, for a frame with no stored value.

    This runs the frame measurement's own star-shape step
    (`measure_image_star_profiles`), so the number has the same definition as
    the stored one. It is slow (seconds per frame), which is why stored values
    are read first.

    Parameters
    ----------
    path : `str`
        The frame's FITS file.
    camera : `str` or `None`
        The camera name from the header, for the pixel level above which a
        pixel is not trusted.

    Returns
    -------
    elongation : `float` or `None`
        The median over the frame's colour planes, or `None` if no star
        could be measured.
    """
    try:
        top_level = usable_top_level_adu(resolve_camera_profile(camera))
    except AstrometricsError:
        top_level = None
    values = [
        result.elongation
        for _, result, _ in measure_image_star_profiles(path, top_level)
        if result.elongation is not None and math.isfinite(result.elongation)
    ]
    return float(statistics.median(values)) if values else None


def make_quarantine_measurer(target_id: str | None) -> Callable[[str], dict[str, Any]]:
    """Build the function that measures one frame for the quarantine step.

    The raw-frame check gives the star count, sky level, sky grid and star
    positions. The star elongation is the frame's stored
    ``frame.star_elongation``, or, for a frame with none, measured with the
    same code (`measure_star_elongation`). The stored values of the target are
    read once, when this function is built.

    Parameters
    ----------
    target_id : `str` or `None`
        The target's id. `None` reads no stored values.

    Returns
    -------
    measure : `Callable`
        Takes a frame path and returns the measurement dict, with
        ``star_elongation`` and ``elongation_source`` (``"stored"``,
        ``"measured"`` or `None`) added.
    """
    stored = stored_star_elongations(target_id) if target_id else {}

    def measure(path: str) -> dict[str, Any]:
        """Measure one frame.

        Returns
        -------
        measurement : `dict`
            The raw-frame measurement plus the star elongation.
        """
        measurement = raw_frame_check.measure_raw_frame(path)
        elongation = stored.get(frame_subject_id(path, target_id)) if target_id else None
        source = "stored" if elongation is not None else None
        if elongation is None:
            elongation = measure_star_elongation(path, measurement.get("camera"))
            source = "measured" if elongation is not None else None
        measurement["star_elongation"] = elongation
        measurement["elongation_source"] = source
        return measurement

    return measure


def _measure_frames(
    frames: Sequence[Any],
    measure: Callable[[str], dict[str, Any]],
    progress: LoopProgress | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Measure frames in parallel, keeping the ones that can be read.

    Parameters
    ----------
    frames : `Sequence`
        The frames to measure.
    measure : `Callable`
        Measures one frame file.
    progress : `LoopProgress` or `None`, optional
        Told about each frame as its result comes in, to write progress lines.

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
        except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as error:
            logger.debug("Cannot measure '%s' for the quarantine check: %s", frame.path, error)
            return None
        measurement["path"] = frame.path
        return measurement

    # Worker threads start without the caller's job context. Each task gets
    # its own copy, so the messages it logs go to this job's log and not to
    # the log of every job that is running.
    caller_context = contextvars.copy_context()

    def measure_in_job_context(frame: Any) -> dict[str, Any] | None:
        """Measure one frame inside a copy of the caller's job context.

        Returns
        -------
        measurement : `dict` or `None`
            The frame's measurements, or `None` if it could not be read.
        """
        return caller_context.copy().run(safe_measure, frame)

    with ThreadPoolExecutor(max_workers=MEASUREMENT_THREADS) as pool:
        results = []
        for result in pool.map(measure_in_job_context, frames):
            results.append(result)
            if progress is not None:
                progress.tick()
    unreadable = [frame.path for frame, result in zip(frames, results, strict=True) if result is None]
    if unreadable:
        # One line, not one per frame: a folder of missing frames would
        # otherwise bury the rest of the log.
        logger.warning(
            "Could not measure %d of %d frames for the quarantine check (first: '%s'). "
            "Run with debug logging to see each reason.",
            len(unreadable),
            len(frames),
            unreadable[0],
        )
    return [result for result in results if result is not None], unreadable


def find_frames_to_quarantine(
    frames: Sequence[Any],
    measure: Callable[[str], dict[str, Any]] | None = None,
    target_id: str | None = None,
) -> QuarantineReport:
    """Decide which frames to move, without moving any.

    Parameters
    ----------
    frames : `Sequence`
        Light frame records of the target.
    measure : `Callable`, optional
        Measures one frame file. Defaults to `make_quarantine_measurer`;
        tests pass a stand-in.
    target_id : `str`, optional
        The target's id, to read the stored star elongations of its frames
        when `measure` is not given.

    Returns
    -------
    report : `QuarantineReport`
        ``moved`` holds the decisions (nothing has been moved yet), ``notes``
        the batches left alone, ``unreadable`` the frames that could not be
        measured.
    """
    minimum_frames_to_judge = resolve(MINIMUM_FRAMES_TO_JUDGE).value
    if measure is None:
        measure = make_quarantine_measurer(target_id)
    report = QuarantineReport()
    batches = split_into_batches(frames)
    # Only batches big enough to judge are measured, so only they are counted.
    progress = LoopProgress(
        "Checked",
        sum(len(batch) for batch in batches if len(batch) >= minimum_frames_to_judge),
    )
    for batch in batches:
        if len(batch) < minimum_frames_to_judge:
            report.batches_too_small += 1
            report.notes.append(
                f"{len(batch)} frame(s) of one session are too few to judge "
                f"(at least {minimum_frames_to_judge} needed); none moved."
            )
            continue
        measurements, unreadable = _measure_frames(batch, measure, progress)
        report.unreadable.extend(unreadable)
        report.measurements.extend(measurements)
        if len(measurements) < minimum_frames_to_judge:
            report.batches_too_small += 1
            report.notes.append(
                f"Only {len(measurements)} of {len(batch)} frame(s) of one session could be read "
                f"(at least {minimum_frames_to_judge} needed to judge); none moved."
            )
            continue
        decisions, judging_notes = judge_batch_with_notes(measurements)
        report.notes.extend(judging_notes)
        for measurement in measurements:
            # The star positions were needed only to match the pointing.
            measurement.pop("_stars", None)
        warning = describe_many_set_aside(decisions, len(measurements))
        if warning:
            report.batches_with_many_set_aside += 1
            report.warnings.append(warning)
            report.warning_by_path.update({decision.path: warning for decision in decisions})
        report.batches_judged += 1
        report.moved.extend(decisions)
    return report


def quarantine_gate(report: QuarantineReport) -> GateResult:
    """Turn a quarantine report into the stack's gate record.

    The three outcomes are kept apart on purpose:

    - ``failed``: more than a quarter of a batch was set aside. The frames
      were moved all the same; the gate says how many and why, so a limit that
      does not suit the equipment is easy to see.
    - ``not_checked``: no batch had enough readable frames to judge, so
      nothing was checked.
    - ``passed``: at least one batch was judged and none had many frames set
      aside. Batches too small to judge are named in the detail.

    Parameters
    ----------
    report : `QuarantineReport`
        What `find_frames_to_quarantine` found.

    Returns
    -------
    gate : `GateResult`
        The result, named ``frame_quarantine``.
    """
    source = (
        f"at least {resolve(MINIMUM_FRAMES_TO_JUDGE).value} frames per session; "
        f"warns above {WARN_QUARANTINED_FRACTION:.0%} set aside"
    )
    if report.batches_with_many_set_aside:
        return failed_gate(
            GATE_NAME,
            "; ".join(report.warnings),
            limit=WARN_QUARANTINED_FRACTION,
            limit_source=source,
        )
    if report.batches_judged == 0:
        return unchecked_gate(
            GATE_NAME,
            "no session had enough readable frames to check for clouds or trails",
            limit_source=source,
        )
    detail = f"{report.batches_judged} session(s) checked, {len(report.moved)} frame(s) moved"
    if report.batches_too_small:
        detail += f"; {report.batches_too_small} session(s) too small to check"
    return passed_gate(GATE_NAME, limit=WARN_QUARANTINED_FRACTION, limit_source=source, detail=detail)


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


def _move_to_quarantine(decision: QuarantineDecision, warning: str | None = None) -> str:
    """Move one frame into the `_excluded` folder beside it and record it.

    Parameters
    ----------
    decision : `QuarantineDecision`
        The frame to move and why.
    warning : `str`, optional
        The batch warning to store in the frame's manifest entry, when many
        frames of its batch were set aside.

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
        "star_elongation": decision.star_elongation,
        "sky_median_adu": decision.sky_median_adu,
        "sky_unevenness": decision.sky_unevenness,
        "pointing_offset_fraction": decision.pointing_offset_fraction,
        "rules": [finding.rule for finding in decision.findings],
        "moved_at": datetime.now(UTC).isoformat(timespec="seconds"),
    })
    if warning:
        entries[-1]["warning"] = warning
    _write_manifest(manifest_path, entries)
    return new_path


def quarantine_bad_frames(
    target: Any,
    frames: Sequence[Any],
    measure: Callable[[str], dict[str, Any]] | None = None,
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
        Measures one frame file; tests pass a stand-in. Defaults to
        `make_quarantine_measurer`.

    Returns
    -------
    kept_frames : `list`
        `frames` without the moved ones.
    report : `QuarantineReport`
        What was moved and what was left alone.
    """
    light_frames = [frame for frame in frames if str(frame.role).upper() == "LIGHT"]
    report = find_frames_to_quarantine(light_frames, measure, getattr(target, "id", None))

    moved_paths: set[str] = set()
    for decision in list(report.moved):
        try:
            _move_to_quarantine(decision, report.warning_by_path.get(decision.path))
        except OSError as error:
            logger.warning("Could not move '%s' into %s: %s", decision.path, QUARANTINE_FOLDER_NAME, error)
            report.moved.remove(decision)
            report.notes.append(f"Could not move {os.path.basename(decision.path)}: {error}")
            continue
        moved_paths.add(decision.path)

    for warning in report.warnings:
        logger.warning("Frame quarantine for target '%s': %s", getattr(target, "id", "?"), warning)
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


def drop_frames_already_set_aside(
    target: Any, frames: Sequence[Any], frames_path: str
) -> tuple[list[Any], list[Any]]:
    """Drop frames that an earlier run moved into `_excluded`.

    The quarantine step removes the frames it moves from the target's
    records, but that change only lasts if the target was saved afterwards.
    When it was not, the target still lists files that are no longer in
    their folder, and the stack would count them as frames it kept. A frame
    is dropped only when the `_excluded` manifest lists its original path
    and no file lies there now, so a frame that was restored stays.

    The target is changed in memory; the caller saves it.

    Parameters
    ----------
    target : `Target`
        The target being stacked.
    frames : `Sequence`
        The frames the stack is about to use.
    frames_path : `str`
        The library's frames folder, where the `_excluded` folders are found.

    Returns
    -------
    kept_frames : `list`
        `frames` without the ones already set aside.
    set_aside_frames : `list`
        The frames that were dropped.
    """
    set_aside_frames = find_frames_already_set_aside(getattr(target, "id", ""), frames, frames_path)
    if not set_aside_frames:
        return list(frames), []
    dropped_paths = {frame.path for frame in set_aside_frames}
    target.frames = [frame for frame in target.frames if frame.path not in dropped_paths]
    target.recalculate_total_exposure()
    kept_frames = [frame for frame in frames if frame.path not in dropped_paths]
    return kept_frames, set_aside_frames


def find_frames_already_set_aside(target_id: str, frames: Sequence[Any], frames_path: str) -> list[Any]:
    """Find the frames that an earlier run moved into `_excluded`.

    A frame counts only when the `_excluded` manifest lists its original path
    and no file lies there now, so a frame that was restored does not count.
    Nothing is changed. The stacker (through `drop_frames_already_set_aside`)
    and the quarantine preview both use this one rule.

    Parameters
    ----------
    target_id : `str`
        The target's id.
    frames : `Sequence`
        The frames to look through.
    frames_path : `str`
        The library's frames folder, where the `_excluded` folders are found.

    Returns
    -------
    set_aside_frames : `list`
        The frames of `frames` that are already set aside, in their order.
    """
    set_aside_paths = {
        os.path.normpath(entry.original_path) for entry in list_set_aside_frames(frames_path, target_id)
    }
    if not set_aside_paths:
        return []
    return [
        frame
        for frame in frames
        if os.path.normpath(frame.path) in set_aside_paths and not os.path.exists(frame.path)
    ]


def restore_quarantined_frames(directory: str, only_files: Collection[str] | None = None) -> list[str]:
    """Move quarantined frames back to where they came from.

    Parameters
    ----------
    directory : `str`
        A folder holding an `_excluded` folder, or the `_excluded` folder
        itself.
    only_files : `Collection` [`str`], optional
        The file names (as in the manifest's ``file`` field) to move back.
        The other frames stay in `_excluded`. Every frame is moved back when
        this is left out.

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
        if only_files is not None and entry["file"] not in only_files:
            remaining.append(entry)
            continue
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
        sky_median_adu=decision.sky_median_adu,
    )


def quarantine_preview_report(
    target: Target, limit: int, quarantine_enabled: bool, frames_path: str
) -> QuarantinePreview:
    """Show which frames a stack would set aside, without moving any.

    It judges every non-spectral light frame of the target, each capture
    setting (camera, optic, filter, exposure and gain) in its own sessions
    (`split_into_batches`). A target imaged with two cameras is stacked one
    camera at a time, so every camera's frames are judged. Frames an earlier
    run already set aside are left out, as a stack leaves them out
    (`find_frames_already_set_aside`). The stacker also leaves out the
    frames of a minority gain setting of a camera before its check
    (`find_dominant_gain_subset`); the preview still judges them, in their
    own sessions, and says how many there are. It measures each frame
    (about a second each), so a large target takes a while. It changes
    neither the target nor any file.

    Parameters
    ----------
    target : `Target`
        The target to check.
    limit : `int`
        Most frames to list. ``would_move_total`` gives the full count.
    quarantine_enabled : `bool`
        The ``quarantine_bad_frames_enabled`` setting. When it is off, a
        stack moves nothing, so the preview measures nothing.
    frames_path : `str`
        The library's frames folder, where the `_excluded` folders are found.

    Returns
    -------
    preview : `QuarantinePreview`
        The frames the check would move, with the measurements behind each,
        the batches it would leave alone, and any frame it could not read.
        ``frames_checked`` counts the frames judged: every light frame that
        is not already set aside.
    """
    from astrometricslib.pipelines.shared.frame_grouping import frame_configuration_key
    from astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity import (
        find_dominant_gain_subset,
    )

    if not quarantine_enabled:
        return QuarantinePreview(
            target_id=target.id,
            quarantine_enabled=False,
            frames_checked=0,
            notes=[
                "Frame quarantine is turned off in the settings "
                "(quarantine_bad_frames_enabled), so a stack would move no frames."
            ],
        )
    lights = [
        frame
        for frame in target.frames
        if str(frame.role).upper() == "LIGHT" and not frame_is_spectral(frame)
    ]
    by_configuration: dict[str, list[Any]] = defaultdict(list)
    for frame in lights:
        by_configuration[frame_configuration_key(frame) or str(frame.camera)].append(frame)
    minority_gain = [
        frame for frames in by_configuration.values() for frame in find_dominant_gain_subset(frames)[1]
    ]
    already_set_aside = {
        frame.path for frame in find_frames_already_set_aside(target.id, lights, frames_path)
    }
    judged = [frame for frame in lights if frame.path not in already_set_aside]
    found = find_frames_to_quarantine(judged, target_id=target.id)
    notes = [*found.notes, *found.warnings]
    if minority_gain:
        notes.append(
            f"{len(minority_gain)} frame(s) have a minority gain setting for their camera. They were "
            "judged in their own sessions, but a stack of that camera leaves them out before this check."
        )
    if already_set_aside:
        notes.append(
            f"{len(already_set_aside)} frame(s) that an earlier run set aside are still listed on "
            "the target; a stack leaves them out before this check."
        )
    would_move = [decision_to_set_aside_frame(decision) for decision in found.moved]
    return QuarantinePreview(
        target_id=target.id,
        frames_checked=len(judged),
        would_move_total=len(would_move),
        would_move=would_move[:limit],
        notes=notes,
        unreadable=found.unreadable,
    )


def target_folder_names(target_id: str) -> list[str]:
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
    for name in target_folder_names(target_id):
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
