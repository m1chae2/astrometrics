"""Grouping images into observing sessions.

This file defines a 'TargetSession', which groups a target's images by the
night they were taken and the camera settings used (gain/offset). We do this
because taking pictures on different nights usually means the telescope is
pointing slightly differently, so they need to be handled in separate batches.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from astrometricslib.models.quality_summary import TargetSessionContribution
from astrometricslib.models.target import FrameRecord

# A session night runs noon-to-noon local time rather than
# midnight-to-midnight, so a single night's observing run (which crosses
# midnight) stays in one bucket.
SESSION_NIGHT_BOUNDARY_HOUR = 12


class TargetSession(BaseModel):
    """A group of images taken on the same night with the same settings."""

    id: str
    target_id: str
    night_date: date
    gain: str
    offset: str
    frame_paths: list[str]


def compute_session_night(frame_timestamp: float) -> date:
    """Figure out which observing night an image belongs to.

    Astronomers work through the night, so a "night" runs from noon to noon,
    not midnight to midnight. This keeps pictures taken at 11 PM and 1 AM
    together in the same session.

    Parameters
    ----------
    frame_timestamp : `float`
        The exact time the picture was taken (Unix timestamp).

    Returns
    -------
    night_date : `date`
        The calendar date of the night the image belongs to. Images taken
        before noon count as the previous night's session.
    """
    local_capture_time = datetime.fromtimestamp(frame_timestamp)
    if local_capture_time.hour < SESSION_NIGHT_BOUNDARY_HOUR:
        local_capture_time -= timedelta(days=1)
    return local_capture_time.date()


def derive_target_sessions(target_id: str, frames: list[FrameRecord]) -> list[TargetSession]:
    """Group a list of images into separate sessions.

    Images go into the same session if they were taken on the same night
    and have the exact same camera gain and offset settings. Changing the
    filter or exposure time doesn't start a new session.

    Parameters
    ----------
    target_id : `str`
        The ID of the target these images belong to.
    frames : `list` of `FrameRecord`
        The list of images to group. Images missing a timestamp are ignored.

    Returns
    -------
    sessions : `list` of `TargetSession`
        A list of grouped sessions, ordered by date.
    """
    frame_paths_by_bucket: dict = {}
    bucket_order: list[tuple[date, str, str]] = []

    for frame in frames:
        if frame.timestamp is None:
            continue
        night_date = compute_session_night(frame.timestamp)
        bucket_key = (night_date, frame.iso, frame.offset)
        if bucket_key not in frame_paths_by_bucket:
            frame_paths_by_bucket[bucket_key] = []
            bucket_order.append(bucket_key)
        frame_paths_by_bucket[bucket_key].append(frame.path)

    sessions = []
    for night_date, gain, offset in sorted(bucket_order):
        session_id = f"{target_id}:{night_date.isoformat()}:{gain}:{offset}"
        sessions.append(
            TargetSession(
                id=session_id,
                target_id=target_id,
                night_date=night_date,
                gain=gain,
                offset=offset,
                frame_paths=frame_paths_by_bucket[night_date, gain, offset],
            )
        )
    return sessions


def build_target_session_breakdown(
    sessions: Sequence[TargetSession], excluded_paths: set[str] | None = None
) -> list[TargetSessionContribution]:
    """Summarize each session's contribution for a pipeline's quality report.

    Every pipeline that groups frames into sessions (stacking, photometry,
    spectroscopy, asteroid recovery) reports the same two numbers per
    session: how many frames it contributed, and how many of those were
    later excluded. Only the definition of "excluded" differs -- rejected
    by outlier filtering, missing a plate solve, etc. -- so that's the one
    thing callers supply.

    Parameters
    ----------
    sessions : `Sequence` of `TargetSession`
        The sessions to summarize, in the order they should appear.
    excluded_paths : `set` of `str`, optional
        Frame paths that were excluded from this pipeline's output. Frames
        not in this set count as contributed and not clipped. Omit it (or
        pass an empty set) for a pipeline that doesn't track per-frame
        exclusions -- every session then reports zero frames clipped.

    Returns
    -------
    breakdown : `list` of `TargetSessionContribution`
        One entry per session, in the same order as `sessions`.
    """
    excluded_paths = excluded_paths or set()
    return [
        TargetSessionContribution(
            session_id=session.id,
            frames_contributed=len(session.frame_paths),
            frames_clipped=sum(1 for path in session.frame_paths if path in excluded_paths),
        )
        for session in sessions
    ]
