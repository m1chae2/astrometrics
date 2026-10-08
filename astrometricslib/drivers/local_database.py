"""The local SQLite database holding the target and stellar catalogs.

This is where astrometrics.db is actually read and written. The generic
plumbing -- opening the file, encoding values as JSON -- comes from the
shared storage module in `astrometricslib.foundation.storage`; what lives here
is everything that knows what a target or a stellar object actually is.
"""

import json
import logging
import os
import shutil
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from astrometricslib.foundation.storage.local_database import connect_db as _connect_db
from astrometricslib.foundation.storage.local_database import safe_json_dumps as _safe_json_dumps

__all__ = [
    "backup_catalog_database",
    "load_targets",
    "save_target",
]

if TYPE_CHECKING:
    from astrometricslib.foundation.config import AppConfiguration
    from astrometricslib.models.target import Target

logger = logging.getLogger(__name__)


def load_targets(app_config: AppConfiguration | None = None) -> list[Any]:
    """Load targets from the SQLite database.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.

    Returns
    -------
    targets : `list` of `Target`
        List of loaded targets.
    """
    from astrometricslib.models.target import Target

    if app_config is None:
        from astrometricslib.foundation.config import get_configuration

        app_config = get_configuration()

    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")

    # 1. Ensure table exists in SQLite
    conn = _connect_db(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS targets (
            id TEXT PRIMARY KEY,
            name TEXT,
            ra TEXT,
            dec TEXT,
            data_json TEXT
        )
    """)
    conn.commit()

    # 2. Load all targets from SQLite
    targets = []
    try:
        cursor.execute("SELECT data_json FROM targets")
        rows = cursor.fetchall()
        for row in rows:
            data = json.loads(row["data_json"])
            targets.append(Target.model_validate(data))
    except sqlite3.Error, ValueError:
        # ValueError covers bad JSON and a stored target that no longer
        # passes validation. The targets read so far are still returned.
        logger.exception("Error loading targets from SQLite")
    finally:
        conn.close()

    return targets


def save_target(app_config: AppConfiguration | None = None, target: Target | None = None) -> str:
    """Save a single target to the SQLite database.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.
    target : `Target`, optional
        Target instance to save.

    Returns
    -------
    db_path : `str`
        Database path saved to.
    """
    if app_config is None:
        from astrometricslib.foundation.config import get_configuration

        app_config = get_configuration()
    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")
    conn = _connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id TEXT PRIMARY KEY,
                name TEXT,
                ra TEXT,
                dec TEXT,
                data_json TEXT
            )
        """)
        cursor.execute(
            """
            INSERT OR REPLACE INTO targets (id, name, ra, dec, data_json)
            VALUES (?, ?, ?, ?, ?)
        """,
            (target.id, target.common_name, target.ra, target.dec, _safe_json_dumps(target.serialize())),
        )
        conn.commit()
    except Exception:
        logger.exception("Error saving target to SQLite")
        raise
    finally:
        conn.close()
    return db_path


def backup_catalog_database(app_config: AppConfiguration) -> str | None:
    """Copy the catalog database aside before a script writes to it.

    Parameters
    ----------
    app_config : `AppConfiguration`
        Provides the library path the database lives under.

    Returns
    -------
    backup_path : `str` or `None`
        Where the copy was written, or `None` if the source database
        doesn't exist yet (nothing to back up) or the copy failed.
    """
    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")
    if not os.path.exists(db_path):
        return None
    backup_path = f"{db_path}.{time.strftime('%Y%m%d_%H%M%S')}.bak"
    try:
        shutil.copy2(db_path, backup_path)
    except OSError:
        logger.exception("Could not back up %s before writing", db_path)
        return None
    return backup_path
