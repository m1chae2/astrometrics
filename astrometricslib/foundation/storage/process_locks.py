"""Locks that stop two programs from using the same thing at once.

Some resources on this computer can only safely be used by one program
at a time -- a telescope mount, a camera, or a copy of Siril that would
otherwise fight over the same scratch files. These helpers hand out
permission slips for those resources.

The locks live in real files on disk and are enforced by the operating
system, which matters: a lock kept only in memory would be invisible to
a second program. Because these use the operating system's file locks
(POSIX advisory locks, or `msvcrt` on Windows), the batch script and the
backend service can both ask for "the Siril slot" and the operating system
will make one of them wait.

This module is about coordinating programs, not about storing data --
the storage side lives in `astrometricslib.foundation.storage.local_database`.
"""

import contextlib
import contextvars
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Generator
from typing import TextIO

from astrometricslib.foundation.errors import ConflictError
from astrometricslib.foundation.paths import locks_directory

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


# How often a work item that is waiting for a slot reports who it is waiting
# behind. Five minutes: a normal stack or analysis takes minutes, so a shorter
# period would fill the log during an ordinary queue, while a holder that has
# stopped making progress shows up well inside an hour. A chosen value, not a
# measured one.
HOLDER_REPORT_INTERVAL_SECONDS = 300.0


class DeviceInUseError(ConflictError):
    """Raised when a locked resource is already held by another process."""


# The resources whose slot the calling work already holds. A slot taken again
# by the same work is reused (see `acquire_resource_slot`).
_held_resource_names: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "held_resource_names", default=()
)


def _holder_file_path(lock_path: str) -> str:
    """Give the path of the file that says who holds a slot.

    Parameters
    ----------
    lock_path : `str`
        Path of the slot's lock file.

    Returns
    -------
    holder_path : `str`
        The lock path with ``.lock`` replaced by ``.holder.json``.
    """
    return os.path.splitext(lock_path)[0] + ".holder.json"


def _write_holder_file(lock_path: str, work_name: str) -> str | None:
    """Record that this process now holds the slot, without ever failing.

    The file is information for people reading logs. It does not decide who
    holds the slot (the operating system lock does), so any error here is
    logged at debug level and ignored.

    Parameters
    ----------
    lock_path : `str`
        Path of the slot's lock file.
    work_name : `str`
        Name of the work holding the slot.

    Returns
    -------
    holder_path : `str` or `None`
        The file written, or `None` if it could not be written.
    """
    holder_path = _holder_file_path(lock_path)
    temporary_path = f"{holder_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        record = {"process_id": os.getpid(), "work_name": work_name, "started_at": time.time()}
        with open(temporary_path, "w", encoding="utf-8") as holder_file:
            json.dump(record, holder_file)
        # Replace in one step, so a reader never sees half a record.
        os.replace(temporary_path, holder_path)
    except (OSError, TypeError, ValueError) as write_error:
        logger.debug("Could not record the holder of %s: %s", lock_path, write_error)
        with contextlib.suppress(OSError):
            os.unlink(temporary_path)
        return None
    return holder_path


def _remove_holder_file(holder_path: str | None) -> None:
    """Delete a holder file when its slot is given back, ignoring errors.

    Parameters
    ----------
    holder_path : `str` or `None`
        File returned by `_write_holder_file`.
    """
    if holder_path is None:
        return
    with contextlib.suppress(OSError):
        os.unlink(holder_path)


def _process_is_alive(process_id: int) -> bool:
    """Say whether a process with this id exists on this computer.

    Parameters
    ----------
    process_id : `int`
        The process id to look for.

    Returns
    -------
    alive : `bool`
        `False` only when the system says no such process exists. A process
        we may not signal still counts as alive.
    """
    if process_id <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) would terminate the process on Windows; skip the
        # check there and treat the record as live.
        return True
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def list_slot_holders(resource_name: str, max_slots: int = 1) -> list[dict]:
    """List the live holders recorded for a resource's slots.

    Missing, unreadable or half-written holder files are skipped, and so is a
    file left by a process that no longer exists. Never raises: this is
    information only.

    Parameters
    ----------
    resource_name : `str`
        Name of the shared resource (for example "job").
    max_slots : `int`
        How many slots the resource has.

    Returns
    -------
    holders : `list` [`dict`]
        One entry per live holder with keys ``slot_index``,
        ``process_id``, ``work_name`` and ``held_seconds``.
    """
    holders: list[dict] = []
    try:
        lock_folder = locks_directory()
    except OSError:
        return holders
    for slot_index in range(max(1, max_slots)):
        lock_path = os.path.join(lock_folder, f"{resource_name}_slot_{slot_index}.lock")
        try:
            with open(_holder_file_path(lock_path), encoding="utf-8") as holder_file:
                record = json.load(holder_file)
            process_id = int(record["process_id"])
            started_at = float(record["started_at"])
            work_name = str(record.get("work_name", ""))
        except OSError, ValueError, TypeError, KeyError, AttributeError:
            continue
        if not _process_is_alive(process_id):
            continue
        holders.append({
            "slot_index": slot_index,
            "process_id": process_id,
            "work_name": work_name,
            "held_seconds": max(0.0, time.time() - started_at),
        })
    return holders


def describe_slot_holders(resource_name: str, max_slots: int = 1) -> str:
    """Write one line saying who holds the slots of a resource.

    Parameters
    ----------
    resource_name : `str`
        Name of the shared resource.
    max_slots : `int`
        How many slots the resource has.

    Returns
    -------
    description : `str`
        Holders joined by semicolons, each as name, process id and minutes
        held, or a sentence saying no live holder is recorded.
    """
    holders = list_slot_holders(resource_name, max_slots)
    if not holders:
        return "no live holder recorded"
    return "; ".join(
        f"{holder['work_name'] or 'unnamed work'} (process {holder['process_id']}, "
        f"{holder['held_seconds'] / 60.0:.1f} min)"
        for holder in holders
    )


def _lock_file(fh: TextIO, blocking: bool) -> None:
    """Take an exclusive lock on an open file.

    Parameters
    ----------
    fh : `io.TextIOWrapper`
        Open file to lock.
    blocking : `bool`
        If `True`, wait for the lock; otherwise fail at once.

    Raises
    ------
    BlockingIOError
        Raised if the lock is held elsewhere and `blocking` is `False`.
    """
    if sys.platform == "win32":
        # Windows has no flock. msvcrt locks one byte at the file's start.
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as e:
                if not blocking:
                    raise BlockingIOError(str(e)) from e
                time.sleep(0.05)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh: TextIO) -> None:
    """Release the lock taken by `_lock_file`.

    Parameters
    ----------
    fh : `io.TextIOWrapper`
        Open file that was locked.
    """
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(lock_path: str, blocking: bool = False) -> Generator[TextIO]:
    """Acquire a process-safe file lock (`fcntl` or `msvcrt`).

    Parameters
    ----------
    lock_path : `str`
        Absolute path to the descriptor lock file.
    blocking : `bool`, optional
        If `True`, wait for the lock instead of failing immediately.
        Default `False`. Intended for calls expected to briefly
        overlap themselves (e.g. rapid-fire jog start/stop), where the
        hold time is negligible and failing fast would surface as a
        spurious user-facing error rather than a real conflict.

    Yields
    ------
    lock_file_handle : `io.TextIOWrapper`
        Open file handle for the held lock, for the duration of the
        `with` block.

    Raises
    ------
    DeviceInUseError
        Raised if the target descriptor lock file is held by another
        process and `blocking` is `False`.
    """
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fh = open(lock_path, "w")
    try:
        _lock_file(fh, blocking)
        yield fh
    except BlockingIOError as e:
        raise DeviceInUseError(
            f"Resource descriptor is locked by another active process: {lock_path}",
            details={"lock_path": lock_path},
        ) from e
    finally:
        _unlock_file(fh)
        fh.close()


@contextlib.contextmanager
def acquire_resource_slot(
    app_config=None,  # ruff: ignore[missing-type-function-argument]
    resource_name: str = "",
    max_slots: int = 1,
    poll_interval_seconds: float = 0.5,
    work_name: str = "",
    wait_logger: logging.Logger | None = None,
) -> Generator[None]:
    """Acquire one of a limited number of OS-level slots for a resource.

    Round-robins over max_slots lock files (one per resource) under
    the locks folder (see `locks_directory`), attempting a non-blocking
    file_lock() on each
    in turn, and sleeps briefly between full sweeps if every slot is
    currently held elsewhere. Unlike an in-process
    multiprocessing.Semaphore, this is respected by any process on the
    machine invoking the same resource (e.g. the offline batch script
    and the backend service both running Siril), since it is backed by
    POSIX advisory file locks rather than in-memory state scoped to one
    process tree.

    Parameters
    ----------
    app_config
        Unused. Kept so existing callers keep working; the lock files live
        in `locks_directory`, not in the library.
    resource_name : `str`
        Name of the shared resource being limited (e.g. "siril", "gpu").
    max_slots : `int`
        Maximum number of concurrent holders allowed for this resource.
    poll_interval_seconds : `float`
        How long to sleep between sweeps when every slot is held.
    work_name : `str`, optional
        Name of the work taking the slot (a target or job), written next to
        the lock file so a waiter can say who it is behind. Defaults to the
        name of the calling thread.
    wait_logger : `logging.Logger`, optional
        Extra logger that receives the "still waiting" reports, besides this
        module's logger.

    Waiting has no time limit. Every `HOLDER_REPORT_INTERVAL_SECONDS` a
    waiter logs which holders it is behind and for how long. The holder
    record is information only; the operating system lock alone decides who
    holds a slot, and a failure to read or write the record never delays it.

    A slot the calling work already holds is reused, not taken again: a
    nested request for the same resource runs at once inside the slot already
    held. Without this, two jobs that each hold one slot and each ask for a
    second one wait for each other forever (the cause of two stacks hanging
    together). The work must be running in the same thread, or in a copy of
    its context (see `contextvars.copy_context`), for this to apply.

    Yields
    ------
    None
        The caller's code runs while holding one of the resource slots.
    """
    lock_folder = locks_directory()
    max_slots = max(1, max_slots)

    if resource_name in _held_resource_names.get():
        # This work already holds a slot of the resource. Asking again would
        # queue behind other work for a second slot, and two jobs that each
        # hold one slot and wait for another would wait for each other
        # forever.
        yield
        return

    work_name = work_name or threading.current_thread().name
    wait_started_at = time.monotonic()
    next_report_at = wait_started_at + HOLDER_REPORT_INTERVAL_SECONDS
    while True:
        for slot_index in range(max_slots):
            lock_path = os.path.join(lock_folder, f"{resource_name}_slot_{slot_index}.lock")
            try:
                with file_lock(lock_path):
                    holder_path = _write_holder_file(lock_path, work_name)
                    held_token = _held_resource_names.set((*_held_resource_names.get(), resource_name))
                    try:
                        yield
                    finally:
                        _held_resource_names.reset(held_token)
                        _remove_holder_file(holder_path)
                return
            except DeviceInUseError:
                continue
        time.sleep(poll_interval_seconds)
        if time.monotonic() >= next_report_at:
            next_report_at = time.monotonic() + HOLDER_REPORT_INTERVAL_SECONDS
            _report_wait(resource_name, max_slots, work_name, time.monotonic() - wait_started_at, wait_logger)


def _report_wait(
    resource_name: str,
    max_slots: int,
    work_name: str,
    waited_seconds: float,
    wait_logger: logging.Logger | None,
) -> None:
    """Log that work is still waiting for a slot, and behind whom.

    Never raises.

    Parameters
    ----------
    resource_name : `str`
        Name of the shared resource.
    max_slots : `int`
        How many slots the resource has.
    work_name : `str`
        Name of the waiting work.
    waited_seconds : `float`
        How long it has waited so far.
    wait_logger : `logging.Logger` or `None`
        Extra logger for the report.
    """
    try:
        message = (
            f"{work_name} has waited {waited_seconds / 60.0:.1f} min for a '{resource_name}' slot; "
            f"behind: {describe_slot_holders(resource_name, max_slots)}"
        )
        logger.info(message)
        if wait_logger is not None:
            wait_logger.info(message)
    except Exception:  # information only: must never stop a slot being taken
        logger.debug("Could not report slot holders", exc_info=True)
