"""Purpose: Target Session derivation.

Description: Defines TargetSession, a computed (not persisted) grouping of a
target's frames by observing night and gain/offset configuration, plus the
pure functions that derive it from frame metadata alone. TargetSession is
orthogonal to Target -- Target is "what" (identity/coordinates), TargetSession
is "when/under what conditions" -- and it is always reconstructable from
Target.frames, so it carries no persistence of its own.
"""

from datetime import date, datetime, timedelta

from pydantic import BaseModel

from astrometricslib.models.target import FrameRecord

# A session night runs noon-to-noon local time rather than
# midnight-to-midnight, so a single night's observing run (which crosses
# midnight) stays in one bucket.
SESSION_NIGHT_BOUNDARY_HOUR = 12


class TargetSession(BaseModel):
    """A target's frames from one night under one gain/offset config."""

    id: str
    target_id: str
    night_date: date
    gain: str
    offset: str
    frame_paths: list[str]


def compute_session_night(frame_timestamp: float) -> date:
    """Return the local calendar date of the noon-to-noon session-night window.

    Parameters
    ----------
    frame_timestamp : `float`
        Unix timestamp of a frame's capture time (``FrameRecord.timestamp``),
        interpreted in the system's local time zone (no observatory-site
        time zone configuration exists yet in this repo).

    Returns
    -------
    night_date : `date`
        The calendar date of the night the frame belongs to. Timestamps
        before local noon belong to the previous calendar date's night.
    """
    local_capture_time = datetime.fromtimestamp(frame_timestamp)
    if local_capture_time.hour < SESSION_NIGHT_BOUNDARY_HOUR:
        local_capture_time -= timedelta(days=1)
    return local_capture_time.date()


def derive_target_sessions(target_id: str, frames: list[FrameRecord]) -> list[TargetSession]:
    """Group a target's frames into per-night, per-gain/offset sessions.

    Frames need not be temporally contiguous: repeated identical gain/offset
    configuration within the same session night rejoins the same session
    rather than starting a new one. Filter and exposure changes do not split
    a session (see the architecture discussion this implements).

    Parameters
    ----------
    target_id : `str`
        Identifier of the owning `Target`.
    frames : `list` [`FrameRecord`]
        Frames to bucket, drawn from ``Target.frames``. Frames with no
        `timestamp` are skipped, since a session night can't be computed
        without a capture time.

    Returns
    -------
    sessions : `list` [`TargetSession`]
        One `TargetSession` per distinct (night, gain, offset) bucket found
        in `frames`, sorted by `night_date`. Pure function: no I/O, no
        persistence.
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
