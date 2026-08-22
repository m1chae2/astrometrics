"""Purpose: Cross-Process Heartbeat Protocol.

Description: The file-based heartbeat protocol the watchdog and its
writer (Observation Execution's session runner, at each checkpoint)
share, per `Wayfinding_Library_Architecture.md` §2.5.7. Uses
`time.monotonic()` rather than wall-clock time: on POSIX, the
monotonic clock is measured from boot, not process start, so it is
comparable across two separate processes on the same machine and is
immune to wall-clock adjustments (NTP steps, DST, manual changes) that
could otherwise make a live process look falsely stale or a dead one
look falsely alive.

Deliberately has no dependency on the session runner or any other
Execution module -- the writer and the watchdog share only this file
format, not any code path, so a hang in the session runner's process
cannot also break the mechanism meant to detect it.
"""

from pathlib import Path


def write_heartbeat(heartbeat_path: Path, monotonic_time: float) -> None:
    """Write the current heartbeat timestamp, replacing any prior value.

    Parameters
    ----------
    heartbeat_path : `Path`
        File the heartbeat is written to.
    monotonic_time : `float`
        The timestamp to write, from `time.monotonic()`.
    """
    heartbeat_path.write_text(repr(monotonic_time))


def read_heartbeat(heartbeat_path: Path) -> float | None:
    """Read the last-written heartbeat timestamp.

    Returns
    -------
    monotonic_time : `float` or `None`
        The last-written timestamp, or `None` if the file is missing
        or does not contain a parseable float -- treated by callers as
        equivalent to a stale heartbeat, never as a live one.
    """
    try:
        return float(heartbeat_path.read_text())
    except OSError, ValueError:
        return None


def heartbeat_is_stale(last_heartbeat: float | None, now: float, watchdog_timeout_sec: int) -> bool:
    """Return whether a heartbeat has aged past the configured timeout.

    A missing heartbeat (`None`) is always stale -- there is no
    configuration under which absence of a heartbeat is treated as a
    live process, the same fail-closed posture the safety assessment
    and enclosure interlock already apply.

    Returns
    -------
    is_stale : `bool`
        `True` if `last_heartbeat` is `None` or older than
        `watchdog_timeout_sec`.
    """
    if last_heartbeat is None:
        return True
    return (now - last_heartbeat) > watchdog_timeout_sec
