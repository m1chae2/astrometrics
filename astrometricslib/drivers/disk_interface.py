"""Domain-specific disk recording for the targets and stellar catalogs.

The generic SQLite connection, file-locking, and JSON-serialization
primitives this module used to define now live in the shared
`datastore` package; they're re-exported here under their historical
names for backward compatibility with existing callers.


"""

import json
import logging
import os
import sqlite3
from typing import Any

from datastore.disk_interface import (
    NumpyEncoder,
    acquire_resource_slot,
    connect_db,
    file_lock,
    safe_json_dumps,
)

_connect_db = connect_db
_safe_json_dumps = safe_json_dumps

__all__ = [
    "NumpyEncoder",
    "acquire_resource_slot",
    "file_lock",
    "get_stellar_objects_by_ids",
    "get_targets_by_ids",
    "load_stellar_objects",
    "load_targets",
    "query_database",
    "save_or_update_stellar_objects",
    "save_or_update_targets",
    "save_stellar_objects",
    "save_target",
    "save_targets",
    "verify_and_upgrade_database",
]

logger = logging.getLogger(__name__)


def load_targets(app_config=None) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Load targets from the SQLite database.

    Migrates individual JSON shards if they exist in
    libraryIndex/targets/ during the first boot.

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
    shards_dir = os.path.join(str(app_config.get_library_path()), "targets")

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

    # 2. Check for migration from JSON shards
    if os.path.exists(shards_dir) and any(f.endswith(".json") for f in os.listdir(shards_dir)):
        logger.info("Migrating JSON shards into targets SQLite database...")
        for filename in os.listdir(shards_dir):
            if filename.endswith(".json"):
                path = os.path.join(shards_dir, filename)
                try:
                    with open(path, encoding="utf8") as fh:
                        data = json.load(fh)
                        to = Target.model_validate(data)

                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO targets (id, name, ra, dec, data_json)
                            VALUES (?, ?, ?, ?, ?)
                        """,
                            (to.id, to.common_name, to.ra, to.dec, _safe_json_dumps(to.serialize())),
                        )

                    os.remove(path)
                except Exception as e:
                    logger.error(f"Error migrating target shard {filename}: {e}")
        conn.commit()

        try:
            if not os.listdir(shards_dir):
                os.rmdir(shards_dir)
        except Exception as exc:
            logger.debug("Could not remove empty shard directory '%s': %s", shards_dir, exc)

    # 3. Load all targets from SQLite
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


def load_stellar_objects(app_config=None) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Load stellar objects from the SQLite database.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.

    Returns
    -------
    stellar_objects : `list` of `StellarObject`
        List of loaded stellar objects.
    """
    from astrometricslib.models.stellar_source import StellarObject

    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()
    objects = []
    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")

    try:
        if os.path.exists(db_path):
            conn = _connect_db(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stellar_objects (
                    id TEXT PRIMARY KEY,
                    target_id TEXT,
                    name TEXT,
                    ra REAL,
                    dec REAL,
                    magnitude REAL,
                    data_json TEXT
                )
            """)
            cursor.execute("SELECT data_json FROM stellar_objects")
            rows = cursor.fetchall()
            for row in rows:
                data = json.loads(row["data_json"])
                objects.append(StellarObject.model_validate(data))
            conn.close()
            if objects:
                return objects
        return objects

    except Exception as e:
        logger.error(f"Error loading stellar objects: {e}")

    return []


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


def save_targets(app_config=None, targets_list=None) -> str:  # ruff: ignore[missing-type-function-argument]
    """Save targets list to the database, clearing any no longer present.

    Parameters
    ----------
    app_config
        Application configuration object.
    targets_list
        List of targets to record.

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

        for target in targets_list:
            cursor.execute(
                """
                INSERT OR REPLACE INTO targets (id, name, ra, dec, data_json)
                VALUES (?, ?, ?, ?, ?)
            """,
                (target.id, target.common_name, target.ra, target.dec, _safe_json_dumps(target.serialize())),
            )

        active_ids = [target.id for target in targets_list]
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            cursor.execute(f"DELETE FROM targets WHERE id NOT IN ({placeholders})", active_ids)
        else:
            cursor.execute("DELETE FROM targets")

        conn.commit()
    except Exception as e:
        logger.error(f"Error saving targets list to SQLite: {e}")
        raise e
    finally:
        conn.close()
    return db_path


def get_targets_by_ids(app_config=None, target_ids: list[str] | None = None) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Load only the targets matching the given ids from the SQLite database.

    A targeted counterpart to load_targets, which reads the entire table.
    Used by CatalogAccess.merge_and_record so a caller only reads the
    rows it is about to update, rather than the whole catalog.

    Args:
        app_config: Application configuration object.
        target_ids: List of target ids to retrieve.

    Returns
    -------
        List[Target]: Targets matching the given ids.
    """
    from astrometricslib.models.target import Target

    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()

    if not target_ids:
        return []

    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")
    if not os.path.exists(db_path):
        return []

    targets = []
    try:
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
        placeholders = ",".join("?" for _ in target_ids)
        cursor.execute(f"SELECT data_json FROM targets WHERE id IN ({placeholders})", target_ids)
        rows = cursor.fetchall()
        for row in rows:
            data = json.loads(row["data_json"])
            targets.append(Target.model_validate(data))
        conn.close()
    except Exception as e:
        logger.error(f"Error loading targets by id: {e}")

    return targets


def save_or_update_targets(app_config=None, targets_list=None) -> str:  # ruff: ignore[missing-type-function-argument]
    """Insert new targets or update existing ones by id.

    Unlike save_targets, which deletes any row not present in the given
    list, this only touches the rows for the given targets. Safe to call
    with a partial subset of the catalog from a concurrent process, since
    it never removes rows it wasn't given.

    Args:
        app_config: Application configuration object.
        targets_list: List of targets to insert or update.

    Returns
    -------
        str: Database path saved to.
    """
    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()
    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")

    try:
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

        for target in targets_list:
            cursor.execute(
                """
                INSERT OR REPLACE INTO targets (id, name, ra, dec, data_json)
                VALUES (?, ?, ?, ?, ?)
            """,
                (target.id, target.common_name, target.ra, target.dec, _safe_json_dumps(target.serialize())),
            )

        conn.commit()
        conn.close()
        return db_path
    except Exception as e:
        logger.error(f"Error saving or updating targets to SQLite: {e}")
        raise e


def save_stellar_objects(app_config=None, stellar_list=None) -> str:  # ruff: ignore[missing-type-function-argument]
    """Save stellar objects to SQLite database.

    Args:
        app_config: Application configuration object.
        stellar_list: List of stellar objects to record.

    Returns
    -------
        str: Database path saved to.
    """
    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()
    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")

    try:
        conn = _connect_db(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stellar_objects (
                id TEXT PRIMARY KEY,
                target_id TEXT,
                name TEXT,
                ra REAL,
                dec REAL,
                magnitude REAL,
                data_json TEXT
            )
        """)

        cursor.execute("DELETE FROM stellar_objects")

        for s in stellar_list:
            data = s.serialize()
            ra_val = None
            if s.right_ascension not in ("", None):
                try:
                    ra_val = float(s.right_ascension)
                except ValueError, TypeError:
                    pass

            dec_val = None
            if s.declination not in ("", None):
                try:
                    dec_val = float(s.declination)
                except ValueError, TypeError:
                    pass

            mag_val = None
            mag_attr = getattr(s, "magnitude", None)
            if mag_attr not in ("", None):
                try:
                    mag_val = float(mag_attr)
                except ValueError, TypeError:
                    pass

            cursor.execute(
                """
                INSERT INTO stellar_objects (id, target_id, name, ra, dec, magnitude, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    s.id,
                    ",".join(s.target_ids) if getattr(s, "target_ids", None) else None,
                    s.name,
                    ra_val,
                    dec_val,
                    mag_val,
                    _safe_json_dumps(data),
                ),
            )

        conn.commit()
        conn.close()
        return db_path
    except Exception as e:
        logger.error(f"Error saving stellar objects to SQLite: {e}")
        raise e


_database_verified = False

# Recorded in the database itself via PRAGMA user_version, so the
# stellar_objects migration pass in verify_and_upgrade_database can tell
# whether it has already brought every row up to this format -- see
# that function's comments for why this exists and what it does not
# protect against.
STELLAR_OBJECT_DATA_VERSION = 2
# v1 -> v2: backfill the has_spectra/has_photometry columns
# (astrometricslib.data_access.catalog_access._stellar_extra_columns) for rows
# written before those columns existed, from the same hydrated object
# this pass already builds -- see the UPDATE below.


def verify_and_upgrade_database(app_config=None) -> None:  # ruff: ignore[missing-type-function-argument]
    """Validate and upgrade all recorded rows in the SQLite tables.

    Hydrates rows in the targets and stellar_objects tables using the
    new core Pydantic models, and rewrites them in the updated
    serialization format. Runs row-by-row in try-except with manually
    patched defaults. Ensures this verification runs exactly once per
    process to prevent SQLite lock contention.
    """
    global _database_verified
    if _database_verified:
        return

    from astrometricslib.models.stellar_source import StellarObject
    from astrometricslib.models.target import Target

    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()

    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")
    if not os.path.exists(db_path):
        return

    logger.info("Initializing database integrity verification and upgrade...")
    _database_verified = True
    conn = _connect_db(db_path)
    try:
        cursor = conn.cursor()

        # 1. Targets verification
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS targets (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    ra TEXT,
                    dec TEXT,
                    data_json TEXT
                )
            """)
            cursor.execute("SELECT id, data_json FROM targets")
            rows = cursor.fetchall()
            for row in rows:
                target_id = row["id"]
                data = json.loads(row["data_json"])

                # Map legacy exposureTime key if present
                if "exposureTime" in data and "exposure_sec" not in data:
                    data["exposure_sec"] = data.pop("exposureTime")
                if "exposureTime" in data:
                    data["exposure_sec"] = data["exposureTime"]

                try:
                    target_obj = Target.model_validate(data)
                except Exception as val_err:
                    logger.warning(f"Target {target_id} hydration failed, attempting recovery: {val_err}")
                    # Attempt safe recovery patching
                    if "id" not in data:
                        data["id"] = target_id
                    if "frames" not in data:
                        data["frames"] = []
                    target_obj = Target.model_validate(data)

                # Re-serialize to verify structure parity
                cursor.execute(
                    "UPDATE targets SET data_json = ? WHERE id = ?",
                    (_safe_json_dumps(target_obj.serialize()), target_id),
                )
            logger.info(f"Verified and upgraded {len(rows)} targets.")
        except Exception as e:
            logger.error(f"Failed target database verification: {e}")

        # 2. StellarObjects verification
        #
        # This step upgrades the database format for stars when we change how
        # they are stored.
        # Checking and rewriting every star in a large database takes a lot
        # of time and can make the program slow to start. To fix this, we
        # only run the upgrade if the database version
        # (STELLAR_OBJECT_DATA_VERSION) is older than the current code expects.
        #
        # If you change the StellarObject format, increase
        # STELLAR_OBJECT_DATA_VERSION so the database knows to update itself
        # on the next launch. After that, it skips the upgrade again until
        # the next version change.
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stellar_objects (
                    id TEXT PRIMARY KEY,
                    target_id TEXT,
                    name TEXT,
                    ra REAL,
                    dec REAL,
                    magnitude REAL,
                    data_json TEXT
                )
            """)
            stored_version = cursor.execute("PRAGMA user_version").fetchone()[0]
            if stored_version >= STELLAR_OBJECT_DATA_VERSION:
                logger.info(
                    "Stellar object catalog already verified at the current schema version; skipping."
                )
            else:
                # This table is also reachable through CatalogAccess, whose
                # _ensure_table adds any column a DatasetSpec declares
                # that isn't on disk yet -- but this pass writes via a
                # raw cursor, not through CatalogAccess, so it can't assume
                # that has already run. Bring the two new v2 columns
                # in the same way _ensure_table does, so the UPDATE
                # below has somewhere to write.
                existing_columns = {row[1] for row in cursor.execute("PRAGMA table_info(stellar_objects)")}
                for column in ("has_spectra", "has_photometry"):
                    if column not in existing_columns:
                        cursor.execute(f"ALTER TABLE stellar_objects ADD COLUMN {column} INTEGER")

                cursor.execute("SELECT id, data_json FROM stellar_objects")
                rows = cursor.fetchall()
                for row in rows:
                    obj_id = row["id"]
                    data = json.loads(row["data_json"])

                    try:
                        stellar_obj = StellarObject.model_validate(data)
                    except Exception as val_err:
                        logger.warning(
                            f"StellarObject {obj_id} hydration failed, attempting recovery: {val_err}"
                        )
                        # Attempt safe recovery patching
                        if "id" not in data:
                            data["id"] = obj_id
                        stellar_obj = StellarObject.model_validate(data)

                    # Re-serialize to verify structure parity, and
                    # backfill has_spectra/has_photometry from the same
                    # hydrated object -- these mirror StellarObject's
                    # own computed properties of the same name, kept in
                    # sync going forward by
                    # data_access.catalog_access._stellar_extra_columns
                    # on every write through CatalogAccess.
                    cursor.execute(
                        "UPDATE stellar_objects SET data_json = ?, has_spectra = ?, has_photometry = ? "
                        "WHERE id = ?",
                        (
                            _safe_json_dumps(stellar_obj.serialize()),
                            int(stellar_obj.has_spectra),
                            int(stellar_obj.has_photometry),
                            obj_id,
                        ),
                    )
                cursor.execute(f"PRAGMA user_version = {STELLAR_OBJECT_DATA_VERSION}")
                logger.info(f"Verified and upgraded {len(rows)} stellar objects.")
        except Exception as e:
            logger.error(f"Failed stellar objects database verification: {e}")

        conn.commit()
    except Exception as outer_err:
        logger.error(f"Outer transaction error during database upgrade: {outer_err}")
        conn.rollback()
    finally:
        conn.close()


def query_database(
    app_config=None,  # ruff: ignore[missing-type-function-argument]
    query: str | None = None,
    database_name: str = "astrometrics.db",
) -> list[dict[str, Any]]:
    """Execute a read-only SQL query against a library database safely.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.
    query : `str`, optional
        SQL SELECT query to execute.
    database_name : `str`, optional
        Target library database name. Default `"astrometrics.db"`.

    Returns
    -------
    results : `list` [`dict` [`str`, `Any`]]
        Serialized row results.

    Raises
    ------
    ValueError
        Raised if `database_name` is not a recognized library
        database.
    FileNotFoundError
        Raised if the target database file does not exist.
    PermissionError
        Raised if `query` contains a non-SELECT statement.
    """
    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()

    if database_name not in ["astrometrics.db", "astrometrics_log.db"]:
        raise ValueError(f"Invalid database name: {database_name}")

    db_path = os.path.join(str(app_config.get_library_path()), database_name)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "GRANT", "PRAGMA"]
    if any(cmd in query.upper() for cmd in forbidden):
        raise PermissionError("Only SELECT queries are allowed for security reasons.")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()

        results = [dict(row) for row in rows]
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Database query error: {e}")
        raise e


def get_stellar_objects_by_ids(app_config=None, stellar_object_ids: list[str] | None = None) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Load only the stellar objects matching the given ids.

    A targeted counterpart to load_stellar_objects, which reads the entire
    table. Used by CatalogAccess.merge_and_record so a caller only
    reads the rows it is about to update, rather than the whole catalog.

    Args:
        app_config: Application configuration object.
        stellar_object_ids: List of stellar object ids to retrieve.

    Returns
    -------
        List[StellarObject]: Stellar objects matching the given ids.
    """
    from astrometricslib.models.stellar_source import StellarObject

    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()

    if not stellar_object_ids:
        return []

    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")
    if not os.path.exists(db_path):
        return []

    objects = []
    try:
        conn = _connect_db(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stellar_objects (
                id TEXT PRIMARY KEY,
                target_id TEXT,
                name TEXT,
                ra REAL,
                dec REAL,
                magnitude REAL,
                data_json TEXT
            )
        """)
        placeholders = ",".join("?" for _ in stellar_object_ids)
        cursor.execute(
            f"SELECT data_json FROM stellar_objects WHERE id IN ({placeholders})", stellar_object_ids
        )
        rows = cursor.fetchall()
        for row in rows:
            data = json.loads(row["data_json"])
            objects.append(StellarObject.model_validate(data))
        conn.close()
    except Exception as e:
        logger.error(f"Error loading stellar objects by id: {e}")

    return objects


def save_or_update_stellar_objects(app_config=None, stellar_list=None) -> str:  # ruff: ignore[missing-type-function-argument]
    """Insert new stellar objects or update existing ones by id.

    Unlike save_stellar_objects, which deletes the entire table before
    reinserting the full given list, this only touches the rows for the
    given objects. Safe to call with a partial subset of the catalog from a
    concurrent process, since it never removes rows it wasn't given.

    Args:
        app_config: Application configuration object.
        stellar_list: List of stellar objects to insert or update.

    Returns
    -------
        str: Database path saved to.
    """
    if app_config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        app_config = get_configuration()
    db_path = os.path.join(str(app_config.get_library_path()), "astrometrics.db")

    conn = _connect_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stellar_objects (
                id TEXT PRIMARY KEY,
                target_id TEXT,
                name TEXT,
                ra REAL,
                dec REAL,
                magnitude REAL,
                data_json TEXT
            )
        """)

        for stellar_object in stellar_list:
            data = stellar_object.serialize()
            right_ascension_value = None
            if stellar_object.right_ascension not in ("", None):
                try:
                    right_ascension_value = float(stellar_object.right_ascension)
                except ValueError, TypeError:
                    pass

            declination_value = None
            if stellar_object.declination not in ("", None):
                try:
                    declination_value = float(stellar_object.declination)
                except ValueError, TypeError:
                    pass

            magnitude_value = None
            magnitude_attribute = getattr(stellar_object, "magnitude", None)
            if magnitude_attribute not in ("", None):
                try:
                    magnitude_value = float(magnitude_attribute)
                except ValueError, TypeError:
                    pass

            cursor.execute(
                """
                INSERT OR REPLACE INTO stellar_objects (id, target_id, name, ra, dec, magnitude, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    stellar_object.id,
                    ",".join(stellar_object.target_ids)
                    if getattr(stellar_object, "target_ids", None)
                    else None,
                    stellar_object.name,
                    right_ascension_value,
                    declination_value,
                    magnitude_value,
                    _safe_json_dumps(data),
                ),
            )

        conn.commit()
        return db_path
    except Exception as e:
        logger.error(f"Error saving or updating stellar objects to SQLite: {e}")
        raise e
    finally:
        try:
            conn.close()
        except Exception as exc:
            logger.debug("Error closing SQLite connection during cleanup: %s", exc)
