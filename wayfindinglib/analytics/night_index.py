"""Purpose: Index the recorded nights so the night list is kept in two parts.

Description: The list of nights (`review.query(kind="nights")`) says, for
each observing night, which targets were imaged, how many light frames the
active equipment took, and whether the night has guiding, Ekos and sky
background data. Building it from scratch reads every frame of the library,
every stored background measurement and every Ekos session, which takes about
half a second.

During a night a new frame arrives about once a minute, and it changes only
the entry of the night in progress. The entries of finished nights stay as
they are. So the list is built in two parts, kept separately: the finished
nights, and the night in progress (see `wayfindinglib.tasks.control_tasks.
night_history`). This module reads the cheap part that both need, the
`NightIndex`: which night and target each frame belongs to, and which nights
have an Ekos session record or a guiding run. It also works out a digest of
what the finished nights contain, and a signature of what tonight contains.
A part is rebuilt only when its own digest or signature changes.

Nights are named as `astrometricslib.reports.observing_night_id` names them,
from local noon to local noon. A night is finished when it is older than the
night of the moment asked about.

Nothing here writes. For exact behavior, read the code.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from functools import cached_property
from itertools import chain
from typing import TYPE_CHECKING, Any

from astrometricslib.drivers import get_target_document_signs
from astrometricslib.exceptions import FITS_READ_ERRORS, AstrometricsError
from astrometricslib.reports import observing_night_id
from wayfindinglib.analytics import capture_history
from wayfindinglib.analytics.data_versions import nights_context_signs
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog

if TYPE_CHECKING:
    from collections.abc import Collection, Hashable, Iterable

logger = logging.getLogger(__name__)

__all__ = ["NightFrame", "NightIndex", "TargetFrameMemo", "build_night_index"]


@dataclass(frozen=True)
class NightFrame:
    """One light frame of the active equipment, placed in a night.

    Attributes
    ----------
    night : `str`
        The observing night the frame was taken on.
    target_id : `str`
        The library target the frame belongs to.
    subject_id : `str`
        The name the frame's stored measurements carry
        (`Target.frame_subject_id`).
    """

    night: str
    target_id: str
    subject_id: str


def _digest(lines: Iterable[str]) -> str:
    """Make a short fixed-size fingerprint of some lines of text.

    Returns
    -------
    digest : `str`
        A hash of the lines.
    """
    hasher = hashlib.blake2b(digest_size=16)
    for line in lines:
        hasher.update(line.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


@dataclass(frozen=True)
class NightIndex:
    """Where each frame and each recorded session sits in the nights.

    Attributes
    ----------
    tonight : `str`
        The night in progress. It and any later night are not finished.
    frames : `tuple` [`NightFrame`, ...]
        The active equipment's light frames, sorted by night, target and
        name.
    ekos_nights : `frozenset` [`str`]
        The nights that have an Ekos session record.
    guiding_nights : `frozenset` [`str`]
        The nights that have a guiding run.
    """

    tonight: str
    frames: tuple[NightFrame, ...]
    ekos_nights: frozenset[str]
    guiding_nights: frozenset[str]

    @cached_property
    def finished_frames(self) -> tuple[NightFrame, ...]:
        """The frames of the finished nights.

        Returns
        -------
        frames : `tuple` [`NightFrame`, ...]
            The frames taken before tonight.
        """
        return tuple(frame for frame in self.frames if frame.night < self.tonight)

    @cached_property
    def tonight_frames(self) -> tuple[NightFrame, ...]:
        """The frames of the night in progress (and any later night).

        Returns
        -------
        frames : `tuple` [`NightFrame`, ...]
            The frames taken tonight or later.
        """
        return tuple(frame for frame in self.frames if frame.night >= self.tonight)

    @cached_property
    def finished_subject_ids(self) -> frozenset[str]:
        """The measurement names of the finished nights' frames.

        Returns
        -------
        subject_ids : `frozenset` [`str`]
            The subject ids.
        """
        return frozenset(frame.subject_id for frame in self.finished_frames)

    @cached_property
    def tonight_subject_ids(self) -> frozenset[str]:
        """The measurement names of tonight's frames.

        Returns
        -------
        subject_ids : `frozenset` [`str`]
            The subject ids.
        """
        return frozenset(frame.subject_id for frame in self.tonight_frames)

    @cached_property
    def finished_digest(self) -> str:
        """Fingerprint everything the finished nights' entries are built from.

        Returns
        -------
        digest : `str`
            Changes when a finished night gains, loses or moves a frame, or
            gains or loses an Ekos session record or a guiding run.
        """
        return _digest(
            chain(
                (f"{frame.night}|{frame.target_id}|{frame.subject_id}" for frame in self.finished_frames),
                ("ekos", *sorted(night for night in self.ekos_nights if night < self.tonight)),
                ("guiding", *sorted(night for night in self.guiding_nights if night < self.tonight)),
            )
        )

    @cached_property
    def tonight_signature(self) -> tuple[object, ...]:
        """Describe what tonight's entry is built from, except measurements.

        Returns
        -------
        signature : `tuple`
            Changes when tonight gains or loses a frame, an Ekos session
            record or a guiding run.
        """
        return (
            self.tonight_frames,
            tuple(sorted(night for night in self.ekos_nights if night >= self.tonight)),
            tuple(sorted(night for night in self.guiding_nights if night >= self.tonight)),
        )


class TargetFrameMemo:
    """Remember each target's frames until its stored document changes.

    Reading every target's document takes about 0.4 s. A new frame changes
    one target's document only. So the frames found for each target are kept
    with the size and hash of the document they were read from, and only a
    target whose document changed is read again.

    Attributes
    ----------
    context_signs : `~collections.abc.Hashable` or `None`
        The equipment and configuration signs the frames were found with.
        The frames are found again when they change.
    frames : `dict` [`str`, `tuple`]
        For each target id: the document's signs, then the target's frames.
    """

    def __init__(self) -> None:
        """Start with nothing remembered."""
        self.context_signs: Hashable | None = None
        self.frames: dict[str, tuple[tuple[int, str], tuple[NightFrame, ...]]] = {}


def read_night_frames(context: Any) -> list[NightFrame]:
    """Read the active equipment's light frames and place each in a night.

    The frames are those the full list has always used: light frames of the
    active telescope and camera that have a capture time
    (`capture_history.equipment_light_frames`). With no telescope and camera
    active there are none. A library that cannot be read gives no frames, as
    the full read does.

    Only the targets whose stored document changed since the last read are
    read again (see `TargetFrameMemo`). The first read, or one after the
    equipment or configuration changed, reads them all.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration, the science library (``astrometrics``)
        and the memo of each target's frames (``night_frame_memo``).

    Returns
    -------
    frames : `list` [`NightFrame`]
        The frames, in no particular order.
    """
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return []
    memo = context.night_frame_memo
    context_signs = nights_context_signs(context.config)
    document_signs = get_target_document_signs(context.config)
    if memo.context_signs != context_signs or document_signs is None:
        memo.context_signs, memo.frames = context_signs, {}
    memo.frames = {
        target_id: held for target_id, held in memo.frames.items() if target_id in (document_signs or {})
    }
    try:
        astrometrics = context.astrometrics
        if document_signs is None:
            targets = astrometrics.targets.list()
        else:
            changed = [
                target_id
                for target_id, signs in document_signs.items()
                if target_id not in memo.frames or memo.frames[target_id][0] != signs
            ]
            if len(changed) == len(document_signs):
                targets = astrometrics.targets.list()
            else:
                targets = [astrometrics.targets.get(target_id, refresh=True) for target_id in changed]
        targets = [target for target in targets if target is not None]
        found: dict[str, list[NightFrame]] = {target.id: [] for target in targets}
        for target, frame in capture_history.equipment_light_frames(
            astrometrics, telescope.name, camera.name, targets
        ):
            found[target.id].append(
                NightFrame(
                    night=observing_night_id(float(frame.timestamp)),
                    target_id=target.id,
                    subject_id=target.frame_subject_id(frame),
                )
            )
    except (AstrometricsError, sqlite3.Error, *FITS_READ_ERRORS) as error:
        logger.warning("Could not read this equipment's frames from the frame library: %s", error)
        memo.frames = {}
        return []
    if document_signs is None:
        return [frame for frames in found.values() for frame in frames]
    for target_id, frames in found.items():
        if target_id in document_signs:
            memo.frames[target_id] = (document_signs[target_id], tuple(frames))
    return [frame for _, frames in memo.frames.values() for frame in frames]


def build_night_index(context: Any, tonight: str) -> NightIndex | None:
    """Read where every frame and recorded session sits in the nights.

    The frames come from `read_night_frames`. With no telescope and camera
    active there are none, and the sessions still give nights.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration, the science library (``astrometrics``)
        and the record store (``records``).
    tonight : `str`
        The night in progress, as `observing_night_id` names it.

    Returns
    -------
    index : `NightIndex` or `None`
        The index, or `None` if the recorded sessions could not be read.
    """
    sessions = context.records.recorded_session_nights()
    if sessions is None:
        return None
    frames = read_night_frames(context)
    return NightIndex(
        tonight=tonight,
        frames=tuple(sorted(frames, key=lambda frame: (frame.night, frame.target_id, frame.subject_id))),
        ekos_nights=sessions[0],
        guiding_nights=sessions[1],
    )


def subjects_with_background(context: Any, target_ids: Collection[str] | None) -> frozenset[str]:
    """Name the frames that have a stored sky background measurement.

    The rule is the one the full read uses: the newest stored row at the
    pinned definition version, for the whole frame, that holds a value.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library (``astrometrics``).
    target_ids : `~collections.abc.Collection` [`str`] or `None`
        Look only at these targets' frames, or at every target's when `None`.

    Returns
    -------
    subject_ids : `frozenset` [`str`]
        The measurement names of the frames that have a background value.
        Empty if the library's measurements cannot be read.
    """
    metrics = {
        capture_history.BACKGROUND_METRIC: capture_history.PINNED_FRAME_METRICS[
            capture_history.BACKGROUND_METRIC
        ]
    }
    try:
        if target_ids is None:
            found = capture_history.read_frame_measurements(context.astrometrics, metrics)
        else:
            found = {}
            for target_id in sorted(target_ids):
                found.update(
                    capture_history.read_frame_measurements(context.astrometrics, metrics, target_id)
                )
    except (AstrometricsError, sqlite3.Error, *FITS_READ_ERRORS) as error:
        logger.warning("Could not read the frames' background measurements: %s", error)
        return frozenset()
    return frozenset(found)
