"""Passive observation-session telemetry recorder.

Description: Passively records PHD2 guiding telemetry and periodic INDI
weather/status snapshots into an ObservationSession for the duration of
an Ekos-run night, until interrupted, per
`Wayfinding_Library_Architecture.md` Sections 5.7 and 5.8. Attaches to an
existing, already-planned session where one exists (created by
Observation Planning); otherwise creates a minimal session against the
active telescope/camera and the recorded site, so this
script remains usable standalone without a prior planning step.
"""

import argparse
import threading
from datetime import UTC, datetime

from wayfindinglib import ObservationSession, Wayfinder
from wayfindinglib.drivers.phd2.phd2_client import DEFAULT_PHD2_PORT, PHD2Client
from wayfindinglib.drivers.phd2.phd2_guiding_service import PHD2GuidingService


def _ensure_session(wayfinder: Wayfinder, session_id: str) -> ObservationSession:
    """Return the session for `session_id`, creating an empty one if absent.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The library entry point.
    session_id : `str`
        The id of a planned session, or, to start a new one, the observing
        night as a date such as ``"2026-09-24"``.

    Returns
    -------
    session : `ObservationSession`
        The existing session, or a new empty one for that night (recorded
        for the active telescope and camera at the recorded site).

    Notes
    -----
    The library raises `InvalidArgumentError` if no session has this id and
    `session_id` is not a date.
    """
    existing = wayfinder.planning.get_plan(session_id)
    if existing is not None:
        return existing
    return wayfinder.planning.create_session(session_id)


def run_observation_session_recorder() -> None:
    """Connect to PHD2 and INDI, recording a session until interrupted.

    Raises
    ------
    SystemExit
        With code 1 if the recording fails.
    """
    parser = argparse.ArgumentParser(description="Astrometrics Passive Observation Session Recorder")
    parser.add_argument("--phd2-host", type=str, default="localhost", help="PHD2 event-server hostname.")
    parser.add_argument("--phd2-port", type=int, default=DEFAULT_PHD2_PORT, help="PHD2 event-server port.")
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Identifier for the ObservationSession being recorded (defaults to today's date).",
    )
    parser.add_argument(
        "--snapshot-interval-seconds",
        type=int,
        default=300,
        help="How often to take a weather snapshot and persist a checkpoint (default 300).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Connect, attach/create the session, and record one telemetry "
            "sample, then return immediately instead of looping until "
            "interrupted."
        ),
    )
    args = parser.parse_args()

    session_id = args.session_id or datetime.now(UTC).date().isoformat()

    print("Initializing Wayfinder...")
    wayfinder = Wayfinder()

    print(f"Connecting to PHD2 at {args.phd2_host}:{args.phd2_port}...")
    guiding_service = PHD2GuidingService(PHD2Client(host=args.phd2_host, port=args.phd2_port))

    print("Connecting to observatory INDI driver...")
    try:
        wayfinder.control.equipment.connect()
        session_id = _ensure_session(wayfinder, session_id).id

        recorder = wayfinder.execution.create_recorder(
            guiding_service,
            snapshot_interval_seconds=args.snapshot_interval_seconds,
        )

        if args.dry_run:
            # An already-set stop_event ends the loop after the first
            # telemetry poll instead of running until interrupted --
            # exercises the same connect/attach/record/persist path as a
            # real run without blocking.
            print(f"[Dry Run] Recording one telemetry sample for ObservationSession '{session_id}'...")
            stop_event = threading.Event()
            stop_event.set()
            session = recorder.run(session_id, stop_event=stop_event)
        else:
            print(f"Recording ObservationSession '{session_id}' -- press Ctrl+C to stop...")
            session = recorder.run(session_id)

        print("\n==========================================")
        print("OBSERVATION SESSION RECORDING COMPLETE")
        print("==========================================")
        print(f"Session ID: {session.id}")
        print(f"Guiding samples recorded: {len(session.guiding_samples)}")
        print(f"Weather snapshots recorded: {len(session.weather_samples)}")

    except Exception as err:
        # This is the top of the script. The error is shown and the script
        # exits with a failure code, keeping the original error attached.
        print(f"Error: Observation session recording failed: {err}")
        raise SystemExit(1) from err


if __name__ == "__main__":
    run_observation_session_recorder()
