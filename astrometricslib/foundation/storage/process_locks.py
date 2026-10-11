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
import logging
import os
import sys
import time
from collections.abc import Generator
from typing import TextIO

from astrometricslib.foundation.errors import ConflictError

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class DeviceInUseError(ConflictError):
    """Raised when a locked resource is already held by another process."""


# The resources whose slot the calling work already holds. A slot taken again
# by the same work is reused (see `acquire_resource_slot`).
_held_resource_names: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "held_resource_names", default=()
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
    if sys.platform != "win32":
        fcntl.flock(fh, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
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


def _unlock_file(fh: TextIO) -> None:
    """Release the lock taken by `_lock_file`.

    Parameters
    ----------
    fh : `io.TextIOWrapper`
        Open file that was locked.
    """
    if sys.platform != "win32":
        fcntl.flock(fh, fcntl.LOCK_UN)
        return
    with contextlib.suppress(OSError):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


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
) -> Generator[None]:
    """Acquire one of a limited number of OS-level slots for a resource.

    Round-robins over max_slots lock files (one per resource) under
    library/locks/, attempting a non-blocking file_lock() on each
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
        Application configuration object.
    resource_name : `str`
        Name of the shared resource being limited (e.g. "siril", "gpu").
    max_slots : `int`
        Maximum number of concurrent holders allowed for this resource.
    poll_interval_seconds : `float`
        How long to sleep between sweeps when every slot is held.

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
    if app_config is None:
        from astrometricslib.foundation.config import get_configuration

        app_config = get_configuration()

    locks_directory = os.path.join(str(app_config.get_library_path()), "locks")
    max_slots = max(1, max_slots)

    if resource_name in _held_resource_names.get():
        # This work already holds a slot of the resource. Asking again would
        # queue behind other work for a second slot, and two jobs that each
        # hold one slot and wait for another would wait for each other
        # forever.
        yield
        return

    while True:
        for slot_index in range(max_slots):
            lock_path = os.path.join(locks_directory, f"{resource_name}_slot_{slot_index}.lock")
            try:
                with file_lock(lock_path):
                    held_token = _held_resource_names.set((*_held_resource_names.get(), resource_name))
                    try:
                        yield
                    finally:
                        _held_resource_names.reset(held_token)
                return
            except DeviceInUseError:
                continue
        time.sleep(poll_interval_seconds)
