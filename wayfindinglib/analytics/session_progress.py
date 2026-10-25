"""Purpose: Work out how far a night plan got, from the frames that arrived.

Description: The queue cards show "12 of 20 frames", which entry is being
imaged now, and whether a night that passed ran at all. This module works
those out. It counts frames with the same function that decides whether a
package is complete (`wayfindinglib.analytics.frame_counting`), so the card,
the package and the carry-over always agree.

The app does not run the queue (Ekos does), so "imaging now" is read from the
frames: an entry is being imaged when its newest counted frame arrived within
`IMAGING_NOW_WINDOW_SECONDS`, or when the entry is marked running. A night is
over once the current observing night is a later one (the observing night
changes at local noon, see `astrometricslib.observing_night_id`).

Register row WAY-D84 holds the window.
"""

from datetime import UTC, date, datetime

from astrometricslib import observing_night_id
from wayfindinglib.analytics.frame_counting import (
    EquipmentNames,
    RequestCount,
    TargetFrameReader,
    count_request_frames,
)
from wayfindinglib.models.planning.observation_package import FrameType
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    RunState,
)
from wayfindinglib.models.session.session_progress import EntryProgress, RowProgress, SessionProgress

IMAGING_NOW_WINDOW_SECONDS = 600.0
"""An entry is being imaged now when its newest frame arrived this recently.

600 s covers the longest sub-exposure the library plans for (300 s) plus the
download, dither settle and autofocus time that follows it (about 150 s, see
`wayfindinglib.analytics.result_quality.capture_overheads`), with a margin of
about one frame. Design estimate, register row WAY-D84.
"""


def current_night(now: datetime) -> date:
    """Give the local date of the observing night that contains a moment.

    Parameters
    ----------
    now : `datetime`
        The moment.

    Returns
    -------
    night : `datetime.date`
        The date the night began (the night changes at local noon).
    """
    return date.fromisoformat(observing_night_id(now.timestamp()))


def build_session_progress(
    session: ObservationSession,
    reader: TargetFrameReader,
    equipment: EquipmentNames,
    now: datetime | None = None,
) -> SessionProgress:
    """Count the frames that arrived for each entry of a session.

    Parameters
    ----------
    session : `ObservationSession`
        The session.
    reader : `TargetFrameReader`
        Reads each target's frames and judgments.
    equipment : `EquipmentNames`
        The names of the session's camera and telescope.
    now : `datetime`, optional
        The moment to judge against. Now when omitted.

    Returns
    -------
    progress : `SessionProgress`
        One `EntryProgress` per queue entry, the entry being imaged now, and
        the session's run state.
    """
    now = now or datetime.now(UTC)
    night_over = session.night_date < current_night(now)
    entries = [_entry_progress(session, entry, reader, equipment, now, night_over) for entry in session.queue]
    imaging = _pick_imaging_entry(entries)
    for entry_progress in entries:
        if entry_progress.entry_id == imaging:
            entry_progress.imaging_now = True
            entry_progress.state = RunState.IMAGING
    return SessionProgress(
        session_id=session.id,
        entries=entries,
        imaging_now_entry_id=imaging,
        run_state=_session_state(entries, night_over),
        as_of=now,
    )


def run_state_of_session(
    session: ObservationSession,
    reader: TargetFrameReader,
    equipment: EquipmentNames,
    now: datetime | None = None,
) -> RunState:
    """Give only the run state of a session (for a list of sessions).

    Parameters
    ----------
    session : `ObservationSession`
        The session.
    reader : `TargetFrameReader`
        Reads each target's frames and judgments.
    equipment : `EquipmentNames`
        The names of the session's camera and telescope.
    now : `datetime`, optional
        The moment to judge against. Now when omitted.

    Returns
    -------
    state : `RunState`
        The session's run state.
    """
    return build_session_progress(session, reader, equipment, now).run_state


def _entry_progress(
    session: ObservationSession,
    entry: QueuedObservationPackage,
    reader: TargetFrameReader,
    equipment: EquipmentNames,
    now: datetime,
    night_over: bool,
) -> EntryProgress:
    """Count one entry's light rows and name its state.

    Parameters
    ----------
    session : `ObservationSession`
        The session.
    entry : `QueuedObservationPackage`
        The entry.
    reader : `TargetFrameReader`
        Reads the frames.
    equipment : `EquipmentNames`
        The session's camera and telescope names.
    now : `datetime`
        The moment judged against.
    night_over : `bool`
        Whether the session's night has passed.

    Returns
    -------
    progress : `EntryProgress`
        The entry's progress. Its state is never `IMAGING` here; the caller
        picks the one entry being imaged.
    """
    target_frames = reader.read(entry.target_id)
    fallback_start = entry.actual_start_time or session.created_at
    light_rows = [row for row in entry.exposure_requests if row.frame_type == FrameType.LIGHT]
    counts: list[tuple[RowProgress, RequestCount]] = []
    for row in light_rows:
        count = count_request_frames(row, target_frames, equipment, fallback_start)
        counts.append((
            RowProgress(
                source_key=row.source_key,
                filter_name=str(row.filter.value),
                exposure_sec=row.exposure_sec,
                requested=count.requested,
                captured=count.captured,
                judged=count.judged,
                flagged=count.flagged,
                not_judged=count.not_judged,
                met=count.met,
            ),
            count,
        ))
    requested = sum(count.requested for _row, count in counts)
    captured = sum(min(count.captured, count.requested) for _row, count in counts)
    newest = max((count.newest_frame_time for _row, count in counts if count.newest_frame_time), default=None)
    progress = EntryProgress(
        entry_id=entry.id,
        target_id=entry.target_id,
        requested=requested,
        captured=captured,
        flagged=sum(count.flagged for _row, count in counts),
        not_judged=sum(count.not_judged for _row, count in counts),
        rows=[row for row, _count in counts],
        newest_frame_time=newest,
        state=RunState.UPCOMING,
        note=target_frames.note,
    )
    progress.state = _entry_state(entry, progress, counts, night_over)
    if (
        not night_over
        and newest is not None
        and (now - newest).total_seconds() <= IMAGING_NOW_WINDOW_SECONDS
        and progress.state != RunState.COMPLETE
    ):
        progress.imaging_now = True
    elif entry.status == QueueEntryStatus.RUNNING and progress.state != RunState.COMPLETE:
        progress.imaging_now = True
    return progress


def _entry_state(
    entry: QueuedObservationPackage,
    progress: EntryProgress,
    counts: list[tuple[RowProgress, RequestCount]],
    night_over: bool,
) -> RunState:
    """Name an entry's state from its counts.

    Parameters
    ----------
    entry : `QueuedObservationPackage`
        The entry.
    progress : `EntryProgress`
        The entry's totals.
    counts : `list` [`tuple`]
        The light rows and their counts.
    night_over : `bool`
        Whether the session's night has passed.

    Returns
    -------
    state : `RunState`
        `UNKNOWN` when the frames could not be read; `COMPLETE` when every
        light row is met; otherwise by whether anything was captured and
        whether the night is over.
    """
    if progress.note and progress.captured == 0:
        return RunState.UNKNOWN
    if not counts:
        # A calibration-only entry has no frames to count; trust its status.
        if entry.status == QueueEntryStatus.COMPLETED:
            return RunState.COMPLETE
        return RunState.NOT_RUN if night_over else RunState.UPCOMING
    if all(count.met for _row, count in counts):
        return RunState.COMPLETE
    if progress.captured > 0:
        return RunState.PARTLY_DONE if night_over else RunState.IN_PROGRESS
    return RunState.NOT_RUN if night_over else RunState.UPCOMING


def _pick_imaging_entry(entries: list[EntryProgress]) -> str | None:
    """Pick the one entry being imaged now.

    Parameters
    ----------
    entries : `list` [`EntryProgress`]
        The entries, with `imaging_now` set on every candidate.

    Returns
    -------
    entry_id : `str` or `None`
        The candidate whose newest frame is the newest; with no frames at
        all, the first candidate (an entry marked running). `None` when
        there is no candidate.
    """
    candidates = [entry for entry in entries if entry.imaging_now]
    for entry in entries:
        entry.imaging_now = False
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda entry: entry.newest_frame_time or datetime.min.replace(tzinfo=UTC),
    )
    return best.entry_id


def _session_state(entries: list[EntryProgress], night_over: bool) -> RunState:
    """Name a session's state from its entries.

    Parameters
    ----------
    entries : `list` [`EntryProgress`]
        The entries.
    night_over : `bool`
        Whether the night has passed.

    Returns
    -------
    state : `RunState`
        See `RunState`. A session with no entries is `NOT_RUN` once its
        night is over, else `UPCOMING`.
    """
    if not entries:
        return RunState.NOT_RUN if night_over else RunState.UPCOMING
    states = [entry.state for entry in entries]
    if any(state == RunState.IMAGING for state in states):
        return RunState.IMAGING
    if all(state == RunState.COMPLETE for state in states):
        return RunState.COMPLETE
    if all(state == RunState.UNKNOWN for state in states):
        return RunState.UNKNOWN
    any_captured = any(entry.captured > 0 for entry in entries)
    if night_over:
        return RunState.PARTLY_DONE if any_captured else RunState.NOT_RUN
    return RunState.IN_PROGRESS if any_captured else RunState.UPCOMING
