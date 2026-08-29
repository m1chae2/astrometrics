"""Opening and writing to a SQLite database file on this computer.

SQLite keeps a whole database in a single ordinary file, so "connecting"
to one means opening that file rather than talking to a server. This
module holds the small amount of setup every such connection needs, plus
the JSON encoder used to store values SQLite has no column type for.

Both astrometricslib and wayfindinglib share this module, so nothing
here knows anything about telescopes, targets, or stars -- only about
databases. Coordinating separate programs is a different job and lives
in `datastore.process_locks`.
"""

import json
import logging
import os
import sqlite3
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
