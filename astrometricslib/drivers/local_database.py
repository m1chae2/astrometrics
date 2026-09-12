"""The local SQLite database holding the target and stellar catalogs.

This is where astrometrics.db is actually read and written. The generic
plumbing -- opening the file, encoding values as JSON -- comes from the
shared `datastore.local_database` module; what lives here is everything
that knows what a target or a stellar object actually is.
"""

import json
import logging
import os
from typing import Any

from datastore.local_database import connect_db as _connect_db
from datastore.local_database import safe_json_dumps as _safe_json_dumps

__all__ = [
    "load_targets",
    "save_target",
]

logger = logging.getLogger(__name__)


def load_targets(app_config=None) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
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
        from astrometricslib.utilities.config_loader import get_configuration

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
    except Exception as e:
        logger.error(f"Error loading targets from SQLite: {e}")
    finally:
        conn.close()

    return targets


def save_target(app_config=None, target=None) -> str:  # ruff: ignore[missing-type-function-argument]
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
        from astrometricslib.utilities.config_loader import get_configuration

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
    except Exception as e:
        logger.error(f"Error saving target to SQLite: {e}")
        raise e
    finally:
        conn.close()
    return db_path
