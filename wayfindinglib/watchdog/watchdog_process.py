"""Purpose: Out-Of-Process Watchdog.

Description: Reads the session runner's heartbeat and, if it has aged
past `watchdog_timeout_sec`, invokes the safe-state sequence itself,
per `Wayfinding_Library_Architecture.md` §2.5.7. Runs as a separate
process from whatever it watches -- "Watchdog Is Out Of Process" (§2.5.9):
imports only `control_tasks.safe_state` and the models it needs, never
the session runner, so a hang in the process being watched cannot also
disable the mechanism meant to catch it. Deliberately excluded from
the `Wayfinder` high-level interface for the same reason.
"""

import time
from collections.abc import Callable
from pathlib import Path

from wayfindinglib.models.session.safe_state import SafeStateOutcome
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps, execute
from wayfindinglib.watchdog.heartbeat import heartbeat_is_stale, read_heartbeat


def check_and_escalate(
    heartbeat_path: Path,
    watchdog_timeout_sec: int,
    now: float,
    steps: SafeStateSteps,
) -> SafeStateOutcome | None:
    """Check the heartbeat and escalate to safe state if it is stale.

    Parameters
    ----------
    heartbeat_path : `Path`
        File the session runner writes its heartbeat to.
    watchdog_timeout_sec : `int`
        Maximum age, in seconds, before a heartbeat is considered stale.
    now : `float`
        The current time, from `time.monotonic()`.
    steps : `SafeStateSteps`
        The hardware-facing operations `safe_state.execute` sequences.

    Returns
    -------
    outcome : `SafeStateOutcome` or `None`
        The safe-state outcome if escalation occurred; `None` if the
        heartbeat is still fresh and no action was taken.
    """
    last_heartbeat = read_heartbeat(heartbeat_path)
    if not heartbeat_is_stale(last_heartbeat, now, watchdog_timeout_sec):
        return None
    return execute("watchdog", steps)


def run_watchdog_loop(
    heartbeat_path: Path,
    watchdog_timeout_sec: int,
    steps_factory: Callable[[], SafeStateSteps],
    poll_interval_sec: float,
    stop_event: object | None = None,
    on_escalation: Callable[[SafeStateOutcome], None] | None = None,
) -> None:
    """Poll the heartbeat at a fixed interval, escalating on staleness.

    Parameters
    ----------
    heartbeat_path : `Path`
        File the session runner writes its heartbeat to.
    watchdog_timeout_sec : `int`
        Maximum age, in seconds, before a heartbeat is considered stale.
    steps_factory : callable
        Called to build a fresh `SafeStateSteps` for each check, since
        device availability may change between polls.
    poll_interval_sec : `float`
        How often to check the heartbeat.
    stop_event : `threading.Event`, optional
        When set, ends the loop after the current iteration. If `None`
        (default), the loop runs until `KeyboardInterrupt`.
    on_escalation : callable, optional
        Called with the `SafeStateOutcome` whenever escalation occurs
        (e.g. to log or alert); not called otherwise.
    """
    while stop_event is None or not stop_event.is_set():
        outcome = check_and_escalate(heartbeat_path, watchdog_timeout_sec, time.monotonic(), steps_factory())
        if outcome is not None and on_escalation is not None:
            on_escalation(outcome)
        time.sleep(poll_interval_sec)
