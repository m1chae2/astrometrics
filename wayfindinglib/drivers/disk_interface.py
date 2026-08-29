"""Purpose: wayfindinglib's own disk recording, for ObservationSession.

Description: A physically separate SQLite database from astrometricslib's
astrometrics.db -- "separate recording per library" from the architecture
discussion means a distinct database file and schema, not a distinct WAL
connection helper. Reuses the generic, target-agnostic low-level primitives
already in astrometricslib/drivers/disk_interface.py (WAL-mode connection,
JSON encoding) rather than re-deriving them, since those carry no
Target-specific logic and cross-library imports are already the norm
throughout this codebase in both directions.
"""

import configparser
import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datastore.disk_interface import connect_db as _connect_db
from datastore.disk_interface import safe_json_dumps as _safe_json_dumps

if TYPE_CHECKING:
    from wayfindinglib.observationlib.observation_session import ObservationSession

logger = logging.getLogger(__name__)

_TABLE_NAME = "observation_sessions"


def _wayfinding_library_path(app_config) -> Path:  # ruff: ignore[missing-type-function-argument]
    """Return wayfindinglib's own libraryIndex directory path.

    Physically separate from astrometricslib's own libraryIndex --
    wayfindinglib records its own data (e.g. ObservationSession) in
    its own database file, not astrometricslib's.

    Returns
    -------
    wayfinding_library_path : `Path`
        Absolute path to wayfindinglib's libraryIndex directory,
        created if it did not already exist.
    """
    try:
        path_str = app_config.app_config.get("Wayfinding Library", "path")
        path = Path(path_str)
        if not path.is_absolute():
            path = (app_config.get_project_root() / path).absolute()
    except configparser.NoSectionError, configparser.NoOptionError, KeyError:
        path = app_config.get_project_root() / "wayfindinglib" / "libraryIndex"
    path.mkdir(parents=True, exist_ok=True)
    return path.absolute()


def _db_path(app_config=None) -> str:  # ruff: ignore[missing-type-function-argument]
    """Return the absolute path to wayfindinglib's own SQLite database file.

    Returns
    -------
    path : `str`
        Absolute path to ``wayfinding.db`` under the configured
        wayfinding library directory.
    """
    if app_config is None:
        from astrometricslib import get_configuration

        app_config = get_configuration()
    return os.path.join(str(_wayfinding_library_path(app_config)), "wayfinding.db")


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the observation_sessions table if it doesn't already exist."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
            id TEXT PRIMARY KEY,
            target_session_id TEXT,
            created_at TEXT,
            data_json TEXT
        )
    """)
    conn.commit()


def save_observation_session(app_config=None, session=None) -> str:  # ruff: ignore[missing-type-function-argument]
    """Save a single ObservationSession to wayfindinglib's SQLite database.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.
    session : `ObservationSession`
        The session instance to save.

    Returns
    -------
    db_path : `str`
        Database path saved to.
    """
    db_path = _db_path(app_config)
    conn = _connect_db(db_path)
    try:
        _ensure_table(conn)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_TABLE_NAME} (id, target_session_id, created_at, data_json)
            VALUES (?, ?, ?, ?)
        """,
            (
                session.id,
                session.target_session_id,
                session.created_at,
                _safe_json_dumps(session.model_dump(mode="python", by_alias=True)),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving ObservationSession to SQLite: {e}")
        raise e
    finally:
        conn.close()
    return db_path


def load_observation_sessions(app_config=None) -> list[ObservationSession]:  # ruff: ignore[missing-type-function-argument]
    """Load every recorded ObservationSession.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.

    Returns
    -------
    sessions : `list` of `ObservationSession`
        Every session currently recorded.
    """
    from wayfindinglib.observationlib.observation_session import ObservationSession

    db_path = _db_path(app_config)
    conn = _connect_db(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute(f"SELECT data_json FROM {_TABLE_NAME}").fetchall()
    finally:
        conn.close()
    return [ObservationSession.model_validate_json(row["data_json"]) for row in rows]


def get_observation_session(app_config=None, session_id: str = "") -> ObservationSession | None:  # ruff: ignore[missing-type-function-argument]
    """Load a single recorded ObservationSession by ID.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.
    session_id : `str`
        The ID of the session to load.

    Returns
    -------
    session : `Optional[ObservationSession]`
        The matching session, or `None` if no session with that ID exists.
    """
    from wayfindinglib.observationlib.observation_session import ObservationSession

    db_path = _db_path(app_config)
    conn = _connect_db(db_path)
    try:
        _ensure_table(conn)
        row = conn.execute(f"SELECT data_json FROM {_TABLE_NAME} WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return ObservationSession.model_validate_json(row["data_json"]) if row else None


_WAYFINDING_SESSION_TABLE_NAME = "wayfinding_observation_sessions"
"""Deliberately distinct from `_TABLE_NAME` ("observation_sessions") above.

The three-function redesign's `wayfindinglib.models.session.observation_session
.ObservationSession` (queue, telescope_id, camera_id, divergence_records,
...) supersedes the deprecated single-target telemetry-only
`observationlib.observation_session.ObservationSession` this module's
`_TABLE_NAME` functions above already serve -- the two are not
schema-compatible. Rather than rewrite those functions (which the
not-yet-relocated `observationlib.session_recorder.ObservationSessionRecorder`
still calls directly, bypassing `DiskButler` entirely), the new model gets
its own table and functions; `DiskButler`'s public "observation_session"
dataset type routes to these. The old table/functions are retired only
once the recorder itself is relocated onto the new model (a separate,
not-yet-done milestone).
"""


def save_wayfinding_session(app_config, session: Any) -> str:  # ruff: ignore[missing-type-function-argument]
    """Record an `ObservationSession` (three-function redesign model).

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None`, the process-wide
        singleton from `get_configuration` is used.
    session : `ObservationSession`
        The session instance to record.

    Returns
    -------
    db_path : `str`
        Database path saved to.
    """
    return save_model(app_config, _WAYFINDING_SESSION_TABLE_NAME, session.id, session)


def load_wayfinding_sessions(app_config, model_class: type) -> list:  # ruff: ignore[missing-type-function-argument]
    """Load every recorded `ObservationSession` of the redesigned model.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None`, the process-wide
        singleton from `get_configuration` is used.
    model_class : `type`
        The `ObservationSession` class to validate each row against,
        passed explicitly to avoid this Foundation-adjacent module
        importing a `models/` type at module level.

    Returns
    -------
    sessions : `list`
        Every recorded session.
    """
    return load_models(app_config, _WAYFINDING_SESSION_TABLE_NAME, model_class)


def get_wayfinding_session(app_config, model_class: type, session_id: str) -> Any:  # ruff: ignore[missing-type-function-argument]
    """Load a single recorded `ObservationSession` of the redesigned model.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None`, the process-wide
        singleton from `get_configuration` is used.
    model_class : `type`
        The `ObservationSession` class to validate the row against.
    session_id : `str`
        The session's `id` field value.

    Returns
    -------
    session : `Any` or `None`
        The matching session, or `None` if no session with that id exists.
    """
    return get_model(app_config, _WAYFINDING_SESSION_TABLE_NAME, model_class, session_id)


# ---------------------------------------------------------------------------
# Generic per-dataset-type recording, added for the Foundation models
# introduced by the three-function redesign (site_profile, enclosure,
# guider_calibration, focus_model, delegation_policy, safety_rule_set,
# commissioning_run -- Wayfinding_Library_Architecture.md Table 3). Each
# gets its own table (id TEXT PRIMARY KEY, data_json TEXT), the same on-disk
# shape as observation_sessions above, without duplicating a hand-written
# CRUD function per dataset type.
#
# Deliberately does not touch
# _TABLE_NAME/_ensure_table/save_observation_session/
# load_observation_sessions/get_observation_session above: those already
# record real data and their exact behavior (including the extra indexed
# target_session_id/created_at columns) is left unchanged.
#
# Uses model_dump(mode="json") rather than mode="python" (which
# save_observation_session uses): the new models carry `date`/`datetime`/
# StrEnum fields the existing NumpyEncoder does not fully cover -- it
# handles `datetime` but not the bare `date` type ObservationSession.night_date
# uses -- and mode="json" converts everything to JSON-safe primitives before
# json.dumps ever sees it, sidestepping the gap rather than extending the
# encoder for one specific type.
# ---------------------------------------------------------------------------


def _ensure_generic_table(conn: sqlite3.Connection, table_name: str) -> None:
    """Create a generic (id, data_json) table if it doesn't already exist."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id TEXT PRIMARY KEY,
            data_json TEXT
        )
    """)
    conn.commit()


def save_model(app_config, table_name: str, model_id: str, model: Any) -> str:  # ruff: ignore[missing-type-function-argument]
    """Record a Pydantic model to its own table under the given id.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None`, the process-wide
        singleton from `get_configuration` is used.
    table_name : `str`
        The table this dataset type is stored under, e.g.
        ``"site_profiles"``.
    model_id : `str`
        The primary key to store the row under. Passed explicitly
        rather than read from a hardcoded `model.id` attribute, since
        not every dataset type's natural key is named `id` (e.g.
        `CalibrationStats.camera_id`).
    model : `Any`
        The Pydantic model instance to record.

    Returns
    -------
    db_path : `str`
        Database path saved to.
    """
    db_path = _db_path(app_config)
    conn = _connect_db(db_path)
    try:
        _ensure_generic_table(conn, table_name)
        conn.execute(
            f"INSERT OR REPLACE INTO {table_name} (id, data_json) VALUES (?, ?)",
            (model_id, _safe_json_dumps(model.model_dump(mode="json", by_alias=True))),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving {table_name} record to SQLite: {e}")
        raise e
    finally:
        conn.close()
    return db_path


def load_models(app_config, table_name: str, model_class: type) -> list:  # ruff: ignore[missing-type-function-argument]
    """Load every recorded instance of one dataset type.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None`, the process-wide
        singleton from `get_configuration` is used.
    table_name : `str`
        The table this dataset type is stored under.
    model_class : `type`
        The Pydantic model class to validate each row against.

    Returns
    -------
    models : `list`
        Every recorded instance, hydrated from its stored JSON.
    """
    db_path = _db_path(app_config)
    conn = _connect_db(db_path)
    try:
        _ensure_generic_table(conn, table_name)
        rows = conn.execute(f"SELECT data_json FROM {table_name}").fetchall()
    finally:
        conn.close()
    return [model_class.model_validate_json(row["data_json"]) for row in rows]


def get_model(app_config, table_name: str, model_class: type, model_id: str) -> Any:  # ruff: ignore[missing-type-function-argument]
    """Load a single recorded instance of one dataset type by id.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None`, the process-wide
        singleton from `get_configuration` is used.
    table_name : `str`
        The table this dataset type is stored under.
    model_class : `type`
        The Pydantic model class to validate the row against.
    model_id : `str`
        The `id` field value to look up.

    Returns
    -------
    model : `Any` or `None`
        The matching instance, or `None` if no row with that id exists.
    """
    db_path = _db_path(app_config)
    conn = _connect_db(db_path)
    try:
        _ensure_generic_table(conn, table_name)
        row = conn.execute(f"SELECT data_json FROM {table_name} WHERE id = ?", (model_id,)).fetchone()
    finally:
        conn.close()
    return model_class.model_validate_json(row["data_json"]) if row else None
