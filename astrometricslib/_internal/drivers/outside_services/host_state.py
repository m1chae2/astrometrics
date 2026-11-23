"""Purpose: Keep each outside host's request times and pauses.

Description: Two rules have to hold across every process of the app (the
backend, its job workers and any script), not just inside one: no more than a
host's requests per second, and no requests to a host that is paused. Both
live in a small SQLite file, ``outside_services.db``, next to the stored
catalog answers. SQLite locks the file for each change, so two processes
cannot both take the same time slot.

The rate limit gives out time slots. The file keeps the next free slot of each
host. A caller takes the slot, or the present moment when that is later, moves
the next free slot one interval on, and waits until its slot arrives. The
host's rate is therefore never exceeded, however many callers ask at once.

A pause is a time before which the host is not asked. A failure in a row grows
it (see ``services.py``); one good answer clears it.

Times are wall-clock seconds (`time.time`), because the processes must agree.
"""

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.foundation.paths import catalog_cache_directory
from astrometricslib.foundation.storage.local_database import connect_db

_STATE_FILENAME = "outside_services.db"

# The time source. Tests replace it to move time without waiting.
_clock: Callable[[], float] = time.time


def outside_state_path() -> Path:
    """Find the file that holds the hosts' times.

    Returns
    -------
    path : `pathlib.Path`
        The file, in the catalog cache folder. It may not exist yet.
    """
    return Path(catalog_cache_directory()) / _STATE_FILENAME


def _open() -> sqlite3.Connection:
    """Open the state file, creating the table when it is missing.

    Returns
    -------
    connection : `sqlite3.Connection`
        An open connection. The caller closes it.
    """
    connection = connect_db(str(outside_state_path()))
    connection.execute(
        "CREATE TABLE IF NOT EXISTS hosts ("
        "host TEXT PRIMARY KEY, next_slot REAL NOT NULL DEFAULT 0, "
        "paused_until REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, "
        "last_error TEXT)"
    )
    return connection


@dataclass(frozen=True)
class HostState:
    """What the file holds about one host.

    Attributes
    ----------
    paused_until : `float`
        The time before which the host is not asked; 0 when it is not paused.
    failures : `int`
        Failures in a row since the last good answer.
    last_error : `str` or `None`
        The text of the last failure.
    """

    paused_until: float = 0.0
    failures: int = 0
    last_error: str | None = None


def read_host_state(host: str) -> HostState:
    """Read what is known about a host.

    Parameters
    ----------
    host : `str`
        The host name.

    Returns
    -------
    state : `HostState`
        The host's state; the default (never failed) when it is not listed.
    """
    with closing(_open()) as connection:
        row = connection.execute(
            "SELECT paused_until, failures, last_error FROM hosts WHERE host = ?", (host,)
        ).fetchone()
    return HostState() if row is None else HostState(row[0], row[1], row[2])


def pause_remaining(host: str) -> float:
    """Say how long a host stays paused.

    Parameters
    ----------
    host : `str`
        The host name.

    Returns
    -------
    seconds : `float`
        Seconds until the host may be asked again; 0 when it is not paused.
    """
    return max(0.0, read_host_state(host).paused_until - _clock())


def reserve_slot(host: str, requests_per_second: float) -> float:
    """Take the host's next free time slot.

    Parameters
    ----------
    host : `str`
        The host name.
    requests_per_second : `float`
        The most requests per second the host may be sent.

    Returns
    -------
    wait_seconds : `float`
        How long the caller must wait before sending; 0 when the slot is now.
        The caller does the waiting, outside the file lock.
    """
    interval = 1.0 / requests_per_second
    with closing(_open()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _clock()
        row = connection.execute("SELECT next_slot FROM hosts WHERE host = ?", (host,)).fetchone()
        slot = max(now, row[0] if row else 0.0)
        connection.execute(
            "INSERT INTO hosts (host, next_slot) VALUES (?, ?) "
            "ON CONFLICT(host) DO UPDATE SET next_slot = excluded.next_slot",
            (host, slot + interval),
        )
        connection.commit()
    return max(0.0, slot - now)


def record_success(host: str) -> None:
    """Clear a host's failures and pause after a good answer.

    Parameters
    ----------
    host : `str`
        The host name.
    """
    with closing(_open()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO hosts (host) VALUES (?) "
            "ON CONFLICT(host) DO UPDATE SET failures = 0, paused_until = 0, last_error = NULL",
            (host,),
        )
        connection.commit()


def record_failure(
    host: str,
    *,
    error_text: str,
    immediate: bool,
    retry_after_seconds: float | None,
    failures_before_backoff: int,
    backoff_base_seconds: float,
    backoff_cap_seconds: float,
) -> float:
    """Count one failure and start or lengthen a pause when it is due.

    Parameters
    ----------
    host : `str`
        The host name.
    error_text : `str`
        What went wrong, for the log.
    immediate : `bool`
        `True` for a 429 or 503 reply, which starts a pause at once. Other
        failures (timeouts, refused connections, server errors) start one
        only after `failures_before_backoff` in a row.
    retry_after_seconds : `float` or `None`
        The pause the host asked for; used instead of the growing pause.
    failures_before_backoff, backoff_base_seconds, backoff_cap_seconds
        The service's rules (see `OutsideService`).

    Returns
    -------
    pause_seconds : `float`
        The length of the pause just started; 0 when the failure started none.
    """
    with closing(_open()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _clock()
        row = connection.execute(
            "SELECT failures, paused_until FROM hosts WHERE host = ?", (host,)
        ).fetchone()
        failures = (row[0] if row else 0) + 1
        paused_until = row[1] if row else 0.0
        pause = 0.0
        if retry_after_seconds is not None:
            pause = retry_after_seconds
        elif immediate:
            pause = min(backoff_cap_seconds, backoff_base_seconds * 2 ** (failures - 1))
        elif failures >= failures_before_backoff:
            pause = min(backoff_cap_seconds, backoff_base_seconds * 2 ** (failures - failures_before_backoff))
        if pause > 0:
            paused_until = max(paused_until, now + pause)
        connection.execute(
            "INSERT INTO hosts (host, failures, paused_until, last_error) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(host) DO UPDATE SET failures = excluded.failures, "
            "paused_until = excluded.paused_until, last_error = excluded.last_error",
            (host, failures, paused_until, error_text[:300]),
        )
        connection.commit()
    return pause
