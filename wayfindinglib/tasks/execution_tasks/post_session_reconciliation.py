"""Purpose: Post-Session Reconciliation.

Description: Runs once a session reaches `COMPLETED` or `ABORTED`, per
`Wayfinding_Library_Architecture.md` §2.4.8: folds captured
calibration frames into `CalibrationStats`, and attaches
`target_session_ids`. Both are idempotent on session identity --
neither incrementally accumulates onto prior state; each recomputes
its result from source data (persisted sessions, a target's frame
records) every time, so re-running produces the same output rather
than double-counting.

`CalibrationEntry`/`CalibrationStats` had no writer anywhere in the
codebase before this module (`models/calibration.py`); this closes
that gap. `target_session_ids` are attached by re-deriving each
imaged target's `TargetSession`s from its actual captured frames
(`astrometricslib.tasks.target_tasks.target_session_tasks
.derive_target_sessions`) for the session's night -- Execution
computes nothing here that isn't already derivable from what the
science library recorded, since a `TargetSession` id encodes the
gain/offset actually read from a frame's FITS header, not anything a
queue entry carries.
"""

from typing import Any

from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.calibration import CalibrationEntry, CalibrationStats
from wayfindinglib.models.planning.observation_package import FrameType
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueueEntryStatus,
    SessionStatus,
)

_TERMINAL_SESSION_STATUSES = (SessionStatus.COMPLETED, SessionStatus.ABORTED)


def compute_calibration_stats(camera_id: str, sessions: list[ObservationSession]) -> CalibrationStats:
    """Recompute a camera's full calibration inventory from terminal sessions.

    Counts calibration-type (`DARK`/`BIAS`/`FLAT`) exposure requests on
    every `COMPLETED` queue entry across every terminal session for
    `camera_id`. Recomputed from scratch each call, so re-running
    after a new session completes -- or re-running on the same input
    -- always yields the count that input actually supports.

    Parameters
    ----------
    camera_id : `str`
        The camera to compute inventory for.
    sessions : `list` [`ObservationSession`]
        Every persisted session to consider (e.g. from
        `butler.get_all("observation_session")`); sessions for other
        cameras, and non-terminal sessions, are ignored.

    Returns
    -------
    stats : `CalibrationStats`
        The full recomputed inventory for `camera_id`.
    """
    counts: dict[tuple[FrameType, float | None, Any], int] = {}

    for session in sessions:
        if session.camera_id != camera_id or session.status not in _TERMINAL_SESSION_STATUSES:
            continue
        for entry in session.queue:
            if entry.status != QueueEntryStatus.COMPLETED:
                continue
            for request in entry.exposure_requests:
                if request.frame_type == FrameType.LIGHT:
                    continue
                key = (request.frame_type, request.exposure_sec, request.filter)
                counts[key] = counts.get(key, 0) + request.count

    darks, biases, flats = [], [], []
    bucket_by_frame_type = {FrameType.DARK: darks, FrameType.BIAS: biases, FrameType.FLAT: flats}
    for (frame_type, exposure_sec, filter_value), count in counts.items():
        entry = CalibrationEntry(
            camera_id=camera_id,
            frame_type=frame_type,
            exposure_sec=exposure_sec,
            filter=filter_value,
            count=count,
        )
        bucket_by_frame_type[frame_type].append(entry)

    return CalibrationStats(camera_id=camera_id, darks=darks, biases=biases, flats=flats)


def attach_target_session_ids(session: ObservationSession, astrometrics: Any) -> ObservationSession:
    """Attach the science-side `TargetSession` ids for every imaged target.

    Parameters
    ----------
    session : `ObservationSession`
        The session to attach ids to. Mutated and returned.
    astrometrics : `Any`
        The `Astrometrics` astrometrics, used to resolve each imaged
        target's frame records.

    Returns
    -------
    session : `ObservationSession`
        The same session, with `target_session_ids` set to every
        `TargetSession` derived for `session.night_date` across every
        target with at least one `COMPLETED` queue entry.
    """
    from astrometricslib import derive_target_sessions

    target_ids = {entry.target_id for entry in session.queue if entry.status == QueueEntryStatus.COMPLETED}

    session_ids: set[str] = set()
    for target_id in target_ids:
        target = astrometrics.targets.get(target_id)
        if target is None:
            continue
        for target_session in derive_target_sessions(target_id, target.frames):
            if target_session.night_date == session.night_date:
                session_ids.add(target_session.id)

    session.target_session_ids = sorted(session_ids)
    return session


def reconcile_session(
    butler: DiskButler, session: ObservationSession, astrometrics: Any
) -> ObservationSession:
    """Run both reconciliations for a terminal session and persist the results.

    Parameters
    ----------
    butler : `DiskButler`
        Persistence layer: reads every session to recompute
        `CalibrationStats`, and writes both results.
    session : `ObservationSession`
        The just-terminated session to reconcile.
    astrometrics : `Any`
        The `Astrometrics` astrometrics, passed through to
        `attach_target_session_ids`.

    Returns
    -------
    session : `ObservationSession`
        The session with `target_session_ids` attached and persisted.

    Raises
    ------
    ValueError
        If `session.status` is not `COMPLETED` or `ABORTED` --
        reconciliation runs only once a session has actually ended.
    """
    if session.status not in _TERMINAL_SESSION_STATUSES:
        raise ValueError(f"Session {session.id} is not terminal (status={session.status}); cannot reconcile")

    all_sessions = butler.get_all("observation_session")
    stats = compute_calibration_stats(session.camera_id, all_sessions)
    butler.put(stats, "calibration_stats", {"camera_id": stats.camera_id})

    attach_target_session_ids(session, astrometrics)
    butler.put(session, "observation_session", {"session_id": session.id})
    return session
