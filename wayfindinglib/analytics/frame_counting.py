"""Purpose: Count the light frames that answer an exposure request.

Description: One function decides which captured frames count toward a
requested exposure row. Four things use it, so they always agree: whether an
observation package is complete, which rows a later session still has to ask
for (carry-over), the progress shown on the queue cards, and whether a past
session ran at all ("not run").

A frame counts toward a row when all of these hold:

* It is a light frame, taken with the session's camera and telescope.
* Its filter is the row's filter. Names are compared after normalizing, so
  "L", "Lum" and "Luminance" are the same filter.
* Its gain, offset and binning equal the row's. A row that leaves one of
  them unset (`None`) matches any value.
* Its exposure length is within 0.5 s of the row's (the same match width
  as the calibration frames, register row WAY-D63).
* It is not set aside. The stacker moves cloudy and trailed frames to an
  `_excluded` folder and drops them from the target record, so a set-aside
  frame is not in the target's frame list (register row WAY-D60). A frame
  that lies in an `_excluded` folder is skipped here as well.
* It was taken at or after the row's `counted_from` time, so frames from
  before the row was last changed or promoted do not count. A reopened
  package also subtracts a stored baseline count.

A frame with a quality flag still counts, and carries the flag. A frame with
no stored quality judgment yet is counted as captured but does not make the
row met: only judged frames complete it.

The frames come from the science library's root, `Astrometrics.targets.get`
(the target record's frame list), not from `quality.query`, which holds only
the frames that were measured. The judgments come from `quality.query`.

The counting functions are pure. `TargetFrameReader` is the one place that
reads the science library.
"""

import logging
import os
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from astrometricslib import (
    Astrometrics,
    AstrometricsError,
    FilterType,
    FrameRecord,
    NotFoundError,
    QualityMeasurement,
    SubjectLevel,
    frame_subject_id,
)
from wayfindinglib.analytics.result_quality.capture_overheads import EXPOSURE_MATCH_SECONDS
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType

logger = logging.getLogger(__name__)

EXCLUDED_FOLDER_MARKER = "_excluded"
"""A frame in a folder with this name is set aside (register row WAY-D60)."""


@dataclass(frozen=True)
class FrameJudgment:
    """What the quality store says about one frame.

    Parameters
    ----------
    judged : `bool`
        `True` when the frame has at least one stored frame-level row.
    flags : `tuple` [`str`]
        The metrics whose self-check failed for this frame. Empty when none.
    """

    judged: bool
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EquipmentNames:
    """The names a session's camera and telescope go by in frame records.

    A frame record holds the camera and telescope as names. A session holds
    their ids. Both are matched, after folding case and underscores.

    Parameters
    ----------
    camera_names : `frozenset` [`str`]
        Accepted names of the session's camera.
    telescope_names : `frozenset` [`str`]
        Accepted names of the session's telescope.
    """

    camera_names: frozenset[str]
    telescope_names: frozenset[str]

    @classmethod
    def from_names(cls, camera_names: Iterable[str], telescope_names: Iterable[str]) -> EquipmentNames:
        """Build the accepted names from plain names or ids.

        Parameters
        ----------
        camera_names : `Iterable` [`str`]
            The camera's id and configured name.
        telescope_names : `Iterable` [`str`]
            The telescope's id and configured name.

        Returns
        -------
        names : `EquipmentNames`
            The folded names.
        """
        return cls(
            camera_names=frozenset(fold_name(name) for name in camera_names) - {""},
            telescope_names=frozenset(fold_name(name) for name in telescope_names) - {""},
        )


@dataclass
class TargetFrames:
    """One target's frames and the quality judgments of those frames.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        The target's frame records.
    judgments : `dict` [`str`, `FrameJudgment`]
        The judgment of each frame, by frame path.
    note : `str`
        A plain sentence when the frames could not be read. Empty otherwise.
    """

    frames: list[FrameRecord] = field(default_factory=list)
    judgments: dict[str, FrameJudgment] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class RequestCount:
    """How many frames answer one exposure row.

    Parameters
    ----------
    requested : `int`
        The row's frame count.
    captured : `int`
        Matching frames, judged or not, after the baseline is taken off.
    judged : `int`
        Matching frames that have a judgment, after the baseline.
    flagged : `int`
        Judged frames that carry a flag.
    not_judged : `int`
        Matching frames with no judgment yet.
    newest_frame_time : `datetime` or `None`
        When the newest matching frame was taken.
    """

    requested: int
    captured: int
    judged: int
    flagged: int
    not_judged: int
    newest_frame_time: datetime | None = None

    @property
    def met(self) -> bool:
        """Whether the judged frames reach the requested count.

        Returns
        -------
        met : `bool`
            `True` when `judged` is at least `requested`.
        """
        return self.judged >= self.requested

    @property
    def missing(self) -> int:
        """How many more frames the row still needs.

        Returns
        -------
        missing : `int`
            The requested count less the judged frames, never below 0.
        """
        return max(self.requested - self.judged, 0)


def fold_name(name: str | None) -> str:
    """Fold a camera or telescope name so spelling differences do not matter.

    Parameters
    ----------
    name : `str` or `None`
        The name or id.

    Returns
    -------
    folded : `str`
        The name in lower case, underscores as spaces, ends trimmed.
    """
    return (name or "").replace("_", " ").strip().casefold()


def normalize_filter(value: Any) -> str:
    """Name a filter the same way however it was spelled.

    "L", "Lum", "Luminance" and `FilterType.L` all give the same text.

    Parameters
    ----------
    value : `Any`
        A `FilterType`, or the filter's name or value as text.

    Returns
    -------
    filter_text : `str`
        The folded name of the matching `FilterType` value, or the folded
        text when no `FilterType` matches. ``"none"`` for no filter.
    """
    if value is None:
        return str(FilterType.NONE.value).casefold()
    if isinstance(value, FilterType):
        return str(value.value).casefold()
    text = str(value).strip()
    for lookup in (
        lambda: FilterType[text],
        lambda: FilterType(text),
        lambda: FilterType[text.upper()],
        lambda: FilterType(text.title()),
    ):
        try:
            return str(lookup().value).casefold()
        except KeyError, ValueError:
            continue
    if text.upper() in {"LUM", "LUMINANCE"}:
        return str(FilterType.L.value).casefold()
    return text.casefold()


def _number(text: Any) -> float | None:
    """Read a number from a frame field kept as text.

    Parameters
    ----------
    text : `Any`
        The field, such as ``"120"`` or ``"120.0"``.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when the text is not one.
    """
    try:
        return float(str(text).strip())
    except TypeError, ValueError:
        return None


def frame_is_set_aside(frame: FrameRecord) -> bool:
    """Whether a frame lies in an ``_excluded`` folder (register row WAY-D60).

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    set_aside : `bool`
        `True` when any folder of its path is named ``_excluded``.
    """
    return EXCLUDED_FOLDER_MARKER in os.path.normpath(frame.path).split(os.sep)[:-1]


def _frame_time(frame: FrameRecord) -> datetime | None:
    """Give the time a frame was taken, with a time zone.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    taken : `datetime` or `None`
        The time (UTC), or `None` when the record has no timestamp.
    """
    if frame.timestamp is None:
        return None
    return datetime.fromtimestamp(frame.timestamp, tz=UTC)


def frame_matches_request(frame: FrameRecord, request: ExposureRequest, equipment: EquipmentNames) -> bool:
    """Whether one frame has the equipment and settings a row asks for.

    The time, set-aside and judgment checks are separate (see
    `count_request_frames`).

    Parameters
    ----------
    frame : `FrameRecord`
        The captured frame.
    request : `ExposureRequest`
        The requested row.
    equipment : `EquipmentNames`
        The session's camera and telescope names.

    Returns
    -------
    matches : `bool`
        `True` for a light frame of the right camera, telescope, filter,
        gain, offset, binning and length (within 0.5 s).
    """
    if not str(frame.role).upper().endswith("LIGHT"):
        return False
    if fold_name(frame.camera) not in equipment.camera_names:
        return False
    if fold_name(frame.telescope) not in equipment.telescope_names:
        return False
    if normalize_filter(frame.filter) != normalize_filter(request.filter):
        return False
    length = _number(frame.exposure)
    if length is None or abs(length - request.exposure_sec) > EXPOSURE_MATCH_SECONDS:
        return False
    for wanted, found in (
        (request.gain_setting, _number(frame.iso)),
        (request.offset, _number(frame.offset)),
        (request.binning, None if frame.binning is None else float(frame.binning)),
    ):
        if wanted is not None and (found is None or abs(found - wanted) > 1e-6):
            return False
    return True


def count_request_frames(
    request: ExposureRequest,
    target_frames: TargetFrames,
    equipment: EquipmentNames,
    counted_from: datetime,
) -> RequestCount:
    """Count the frames that answer one exposure row.

    Parameters
    ----------
    request : `ExposureRequest`
        The row. Only a light row is counted; any other frame type gives a
        count of zero requested frames that is always met.
    target_frames : `TargetFrames`
        The target's frames and judgments.
    equipment : `EquipmentNames`
        The session's camera and telescope names.
    counted_from : `datetime`
        Only frames taken at or after this time count. The row's own
        `counted_from` wins when it is set.

    Returns
    -------
    count : `RequestCount`
        The counts. The row's `baseline_captured` is taken off `captured`
        (and off `judged` first, since the oldest frames are the baseline).
    """
    if request.frame_type != FrameType.LIGHT:
        return RequestCount(requested=0, captured=0, judged=0, flagged=0, not_judged=0)
    start = request.counted_from or counted_from
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    matching: list[tuple[datetime, FrameRecord]] = []
    for frame in target_frames.frames:
        if frame_is_set_aside(frame) or not frame_matches_request(frame, request, equipment):
            continue
        taken = _frame_time(frame)
        if taken is None or taken < start:
            continue
        matching.append((taken, frame))
    matching.sort(key=lambda pair: pair[0])
    counted = matching[request.baseline_captured :]
    judged = [frame for _taken, frame in counted if _judgment(target_frames, frame).judged]
    flagged = [frame for frame in judged if _judgment(target_frames, frame).flags]
    return RequestCount(
        requested=request.count,
        captured=len(counted),
        judged=len(judged),
        flagged=len(flagged),
        not_judged=len(counted) - len(judged),
        newest_frame_time=counted[-1][0] if counted else None,
    )


def _judgment(target_frames: TargetFrames, frame: FrameRecord) -> FrameJudgment:
    """Look up a frame's judgment.

    Parameters
    ----------
    target_frames : `TargetFrames`
        The target's frames and judgments.
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    judgment : `FrameJudgment`
        The stored judgment, or "not judged" when there is none.
    """
    return target_frames.judgments.get(frame.path, FrameJudgment(judged=False))


def package_is_complete(counts: Iterable[RequestCount]) -> bool:
    """Whether every light row of a package is met.

    Parameters
    ----------
    counts : `Iterable` [`RequestCount`]
        The counts of the package's light rows.

    Returns
    -------
    complete : `bool`
        `True` when there is at least one light row and all are met.
    """
    counts = [count for count in counts if count.requested > 0]
    return bool(counts) and all(count.met for count in counts)


def missing_requests(
    requests: Iterable[ExposureRequest],
    target_frames: TargetFrames,
    equipment: EquipmentNames,
    counted_from: datetime,
) -> list[ExposureRequest]:
    """List the rows a later session still has to ask for.

    A light row that is met is dropped. A light row that is partly met
    keeps only the missing frames. A row that is not a light row is kept
    as it is.

    Parameters
    ----------
    requests : `Iterable` [`ExposureRequest`]
        The package's rows.
    target_frames : `TargetFrames`
        The target's frames and judgments.
    equipment : `EquipmentNames`
        The camera and telescope names of the session the rows go into.
    counted_from : `datetime`
        The time frames count from when a row has none of its own.

    Returns
    -------
    missing : `list` [`ExposureRequest`]
        The rows with the counts still missing.
    """
    kept: list[ExposureRequest] = []
    for request in requests:
        if request.frame_type != FrameType.LIGHT:
            kept.append(request)
            continue
        count = count_request_frames(request, target_frames, equipment, counted_from)
        if count.met:
            continue
        kept.append(request.model_copy(update={"count": count.missing}))
    return kept


class TargetFrameReader:
    """Read each target's frames and judgments from the science library once.

    The reader keeps what it read, so one operation that looks at the same
    target several times reads it once. Make a new reader for each
    operation so the numbers are fresh.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle. Only its root reads are used:
        `targets.get` for the frames and `quality.query` for the judgments.
    """

    def __init__(self, astrometrics: Astrometrics) -> None:
        self._astrometrics = astrometrics
        self._cache: dict[str, TargetFrames] = {}

    def read(self, target_id: str) -> TargetFrames:
        """Read one target's frames and their judgments.

        Parameters
        ----------
        target_id : `str`
            The target.

        Returns
        -------
        target_frames : `TargetFrames`
            The frames and judgments. When the target is missing or a read
            fails, the frame list is empty and `note` says why, so a caller
            never mistakes "could not read" for "no frames" without being
            able to tell.
        """
        if target_id not in self._cache:
            self._cache[target_id] = self._read_uncached(target_id)
        return self._cache[target_id]

    def _read_uncached(self, target_id: str) -> TargetFrames:
        """Read the frames and judgments without using the cache.

        Parameters
        ----------
        target_id : `str`
            The target.

        Returns
        -------
        target_frames : `TargetFrames`
            See `read`.
        """
        try:
            target = self._astrometrics.targets.get(target_id)
        except NotFoundError:
            target = None
        except (AstrometricsError, OSError) as error:
            logger.warning("Could not read the frames of %s: %s", target_id, error)
            return TargetFrames(note=f"The frames of {target_id} could not be read: {error}")
        if target is None:
            return TargetFrames(note=f"No target {target_id!r} in the library.")
        try:
            rows = self._astrometrics.quality.query(
                target_id=target_id, level=SubjectLevel.FRAME.value, latest_only=True
            )
        except (AstrometricsError, OSError) as error:
            logger.warning("Could not read the frame judgments of %s: %s", target_id, error)
            return TargetFrames(
                frames=list(target.frames),
                note=f"The quality judgments of {target_id} could not be read: {error}",
            )
        return TargetFrames(
            frames=list(target.frames), judgments=judgments_by_path(target_id, target.frames, rows)
        )


_FAILED = "failed"


def judgments_by_path(
    target_id: str, frames: Collection[FrameRecord], rows: Iterable[QualityMeasurement]
) -> dict[str, FrameJudgment]:
    """Join a target's frame-level quality rows to its frames.

    Parameters
    ----------
    target_id : `str`
        The target.
    frames : `Collection` [`FrameRecord`]
        The target's frames.
    rows : `Iterable` [`QualityMeasurement`]
        The target's stored frame-level rows.

    Returns
    -------
    judgments : `dict` [`str`, `FrameJudgment`]
        The judgment of each frame that has at least one row, by frame path.
    """
    flags_by_subject: dict[str, set[str]] = {}
    for row in rows:
        if row.subject.level != SubjectLevel.FRAME:
            continue
        flags = flags_by_subject.setdefault(row.subject.subject_id, set())
        outcome = getattr(row.check_outcome, "value", row.check_outcome)
        if outcome == _FAILED:
            flags.add(row.metric)
    judgments: dict[str, FrameJudgment] = {}
    for frame in frames:
        subject_id = frame_subject_id(frame.path, target_id)
        if subject_id in flags_by_subject:
            judgments[frame.path] = FrameJudgment(
                judged=True, flags=tuple(sorted(flags_by_subject[subject_id]))
            )
    return judgments
