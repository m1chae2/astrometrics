"""Purpose: The work behind `execution`: run, abort and record a session.

Description: `run_session` advances an observing session's queue with the
imaging steps built on the control work (`imaging_queue`), one cycle or
cycle after cycle. `abort_and_save` closes a session and saves it.
`build_recorder` makes the recorder that saves guiding and weather
telemetry during a night. Reconciling a session after the night is
`post_session_reconciliation.reconcile_session`.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from wayfindinglib.tasks.execution_tasks.imaging_queue import imaging_dependencies
from wayfindinglib.tasks.execution_tasks.queue_runner import run_queue
from wayfindinglib.tasks.execution_tasks.session_recorder import ObservationSessionRecorder
from wayfindinglib.tasks.execution_tasks.session_runner import abort_session, advance_session

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.drivers.butler import DiskButler
    from wayfindinglib.models.session.observation_session import ObservationSession

__all__ = ["abort_and_save", "build_recorder", "run_session"]


def _latest(butler: DiskButler, session: ObservationSession) -> ObservationSession:
    """Read the latest saved copy of a session.

    Parameters
    ----------
    butler : `DiskButler`
        The storage.
    session : `ObservationSession`
        The session.

    Returns
    -------
    session : `ObservationSession`
        The saved copy, or `session` itself when none is saved.
    """
    return butler.get("observation_session", {"session_id": session.id}) or session


def run_session(
    context: ControlContext,
    butler: DiskButler,
    session: ObservationSession,
    stop: threading.Event | None = None,
) -> ObservationSession | None:
    """Run a session's queue: one cycle, or cycle after cycle.

    Parameters
    ----------
    context : `ControlContext`
        Supplies safety, the policy, the mount and the camera.
    butler : `DiskButler`
        Saves the session after each cycle.
    session : `ObservationSession`
        The session.
    stop : `threading.Event`, optional
        Given, keep running cycles, each on the latest saved copy, until
        the queue has nothing left to run, the session ends or is
        suspended, or `stop` is set. This blocks.

    Returns
    -------
    session : `ObservationSession` or `None`
        The session after the last cycle (`None` if `stop` was already
        set).
    """
    deps = imaging_dependencies(context, butler)
    if stop is None:
        return advance_session(_latest(butler, session), deps)
    return run_queue(lambda: advance_session(_latest(butler, session), deps), stop)


def abort_and_save(butler: DiskButler, session: ObservationSession, reason: str = "") -> ObservationSession:
    """Abort a session: skip its pending entries, close it and save it.

    Parameters
    ----------
    butler : `DiskButler`
        The storage.
    session : `ObservationSession`
        The session.
    reason : `str`, optional
        Why it was aborted, recorded on the session and on each skipped
        entry.

    Returns
    -------
    session : `ObservationSession`
        The aborted, saved session.
    """
    aborted = abort_session(_latest(butler, session), reason, datetime.now(UTC))
    butler.put_session_from_execution(aborted)
    return aborted


def build_recorder(
    context: ControlContext,
    butler: DiskButler,
    phd2_host: str | None = None,
    phd2_port: int | None = None,
    snapshot_interval_s: int = 300,
) -> ObservationSessionRecorder:
    """Build the recorder that saves a night's telemetry.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the INDI session and the configured PHD2 location.
    butler : `DiskButler`
        Where the snapshots are saved.
    phd2_host : `str`, optional
        The computer PHD2 runs on. The configured PHD2 location when
        omitted.
    phd2_port : `int`, optional
        The PHD2 event-server port. 4400 when omitted.
    snapshot_interval_s : `int`, optional
        How often a snapshot is saved, in seconds.

    Returns
    -------
    recorder : `ObservationSessionRecorder`
        The recorder, not yet running.
    """
    from wayfindinglib.drivers.phd2.phd2_client import DEFAULT_PHD2_PORT, PHD2Client
    from wayfindinglib.drivers.phd2.phd2_guiding_service import PHD2GuidingService

    if phd2_host is None:
        client = context._build_phd2_client()
    else:
        client = PHD2Client(host=phd2_host, port=DEFAULT_PHD2_PORT if phd2_port is None else phd2_port)
    return ObservationSessionRecorder(PHD2GuidingService(client), context.driver, butler, snapshot_interval_s)
