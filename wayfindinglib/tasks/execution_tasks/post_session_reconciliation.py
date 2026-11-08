"""Purpose: Post-Session Reconciliation.

Description: Runs once a session reaches `COMPLETED` or `ABORTED`, per
`Wayfinding_Library_Architecture.md` Section 5.7: attaches
`target_session_ids`. It is idempotent on session identity -- it does
not accumulate onto prior state; it recomputes its result from source
data (a target's frame records) every time, so re-running produces the
same output rather than double-counting.

Calibration advice reads the calibration index, so this module no longer
writes a per-camera `CalibrationStats` record. `target_session_ids` are
attached by re-deriving each
imaged target's `TargetSession`s from its actual captured frames
(`astrometricslib.pipelines.shared.target_sessions
.derive_target_sessions`) for the session's night -- Execution
computes nothing here that isn't already derivable from what the
science library recorded, since a `TargetSession` id encodes the
gain/offset actually read from a frame's FITS header, not anything a
queue entry carries.
"""

from typing import Any

from astrometricslib.exceptions import ConflictError
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueueEntryStatus,
    SessionStatus,
)

_TERMINAL_SESSION_STATUSES = (SessionStatus.COMPLETED, SessionStatus.ABORTED)


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
    target_ids = {entry.target_id for entry in session.queue if entry.status == QueueEntryStatus.COMPLETED}

    session_ids: set[str] = set()
    for target_id in target_ids:
        target = astrometrics.targets.get(target_id)
        if target is None:
            continue
        for target_session in astrometrics.targets.sessions(target, night_date=session.night_date):
            session_ids.add(target_session.id)

    session.target_session_ids = sorted(session_ids)
    return session


def reconcile_session(
    butler: DiskButler, session: ObservationSession, astrometrics: Any
) -> ObservationSession:
    """Reconcile a terminal session and record the result.

    Parameters
    ----------
    butler : `DiskButler`
        Storage layer: writes the reconciled session.
    session : `ObservationSession`
        The just-terminated session to reconcile.
    astrometrics : `Any`
        The `Astrometrics` astrometrics, passed through to
        `attach_target_session_ids`.

    Returns
    -------
    session : `ObservationSession`
        The session with `target_session_ids` attached and recorded.

    Raises
    ------
    ConflictError
        If `session.status` is not `COMPLETED` or `ABORTED` --
        reconciliation runs only once a session has actually ended.
    """
    if session.status not in _TERMINAL_SESSION_STATUSES:
        raise ConflictError(
            f"Session {session.id} is not terminal (status={session.status}); cannot reconcile"
        )

    attach_target_session_ids(session, astrometrics)
    butler.put_session_from_execution(session)
    return session
