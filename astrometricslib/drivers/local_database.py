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
