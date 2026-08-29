"""Locks that stop two programs from using the same thing at once.

Some resources on this computer can only safely be used by one program
at a time -- a telescope mount, a camera, or a copy of Siril that would
otherwise fight over the same scratch files. These helpers hand out
permission slips for those resources.

The locks live in real files on disk and are enforced by the operating
system, which matters: a lock kept only in memory would be invisible to
a second program. Because these use POSIX advisory locks, the batch
script and the backend service can both ask for "the Siril slot" and
the operating system will make one of them wait.

This module is about coordinating programs, not about storing data --
the storage side lives in `datastore.local_database`.
"""

import contextlib
import fcntl
import logging
import os
import time

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def file_lock(lock_path: str, blocking: bool = False):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Acquire a POSIX advisory process-safe file lock via native fcntl.

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
        if blocking:
            fcntl.flock(fh, fcntl.LOCK_EX)
        else:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fh
    except BlockingIOError as e:
        from datastore.exceptions import DeviceInUseError

        raise DeviceInUseError(f"Resource descriptor is locked by another active process: {lock_path}") from e
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


@contextlib.contextmanager
def acquire_resource_slot(  # ruff: ignore[missing-return-type-undocumented-public-function]
    app_config=None,  # ruff: ignore[missing-type-function-argument]
    resource_name: str = "",
    max_slots: int = 1,
    poll_interval_seconds: float = 0.5,
):
    """Acquire one of a limited number of OS-level slots for a resource.

    Round-robins over max_slots lock files (one per resource) under
    libraryIndex/locks/, attempting a non-blocking file_lock() on each
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

    Yields
    ------
    None
        The caller's code runs while holding one of the resource slots.
    """
    from datastore.exceptions import DeviceInUseError

    if app_config is None:
        from astrometricslib import get_configuration

        app_config = get_configuration()

    locks_directory = os.path.join(str(app_config.get_library_path()), "locks")
    max_slots = max(1, max_slots)

    while True:
        for slot_index in range(max_slots):
            lock_path = os.path.join(locks_directory, f"{resource_name}_slot_{slot_index}.lock")
            try:
                with file_lock(lock_path):
                    yield
                return
            except DeviceInUseError:
                continue
        time.sleep(poll_interval_seconds)
