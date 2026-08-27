"""Low-level SQLite, file-locking, and JSON serialization primitives.

Shared, domain-agnostic infrastructure used by both astrometricslib
and wayfindinglib's Butler implementations.
"""

import contextlib
import fcntl
import json
import logging
import os
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)


class NumpyEncoder(json.JSONEncoder):
    """Custom JSON encoder for numpy data types.

    Handles types such as ``np.int64`` and ``np.float64`` that are
    commonly returned from astrometry and source detection packages.
    """

    def default(self, obj):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Serialize numpy datatypes and datetimes to plain Python types.

        Parameters
        ----------
        obj : `Any`
            The object being serialized by the JSON encoder.

        Returns
        -------
        serializable : `Any`
            A JSON-serializable representation of `obj` if it is a
            recognized numpy or datetime type; otherwise delegates to
            the superclass implementation.
        """
        from datetime import datetime

        import numpy as np

        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def safe_json_dumps(obj: Any) -> str:
    """Serialize an object to a JSON string using `NumpyEncoder`.

    Returns
    -------
    serialized : `str`
        JSON string representation of `obj`.
    """
    return json.dumps(obj, cls=NumpyEncoder)


def connect_db(db_path: str, timeout: float = 30.0) -> sqlite3.Connection:
    """Connect to a SQLite database with WAL mode enabled.

    Ensures the parent directory exists before creating/opening the database.

    Parameters
    ----------
    db_path : `str`
        Absolute path to the SQLite database file.
    timeout : `float`, optional
        Connection timeout in seconds, by default 30.0.

    Returns
    -------
    connection : `sqlite3.Connection`
        Database connection instance with WAL mode active.
    """
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=60000;")
    return conn


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
