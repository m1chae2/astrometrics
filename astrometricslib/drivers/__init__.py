"""Purpose: The storage and file tools that other apps may use.

Description: Storage and file helpers that the backend, the MCP servers and
`wayfindinglib` share: the data store (`Butler`, `AbstractButler`,
`DatasetSpec`, `connect_db`), the library's write counters
(`get_library_data_version`, `LIBRARY_DATA_KINDS`, and `bump_data_version`
and `read_data_versions` for a database that keeps its own counters) and the
cache that uses them (`VersionedCache`), file locks and work folders, the
checks for a mounted drive, and `stop_process_group`, which ends a program
and the helpers it started. The private driver code (Siril, catalog
access, camera profiles) lives in `astrometricslib._internal.drivers`.
Each name here is imported from the module where its code lives today.
"""

from astrometricslib.foundation.paths import (
    SIMULATOR_FOLDER_NAME,
    is_path_inside,
    locks_directory,
    real_folders,
    resolve_mounted_path,
    scratch_directory,
)
from astrometricslib.foundation.run_abandonment import stop_process_group
from astrometricslib.foundation.storage.butler import AbstractButler, Butler, DatasetSpec
from astrometricslib.foundation.storage.data_versions import ALL_KINDS as LIBRARY_DATA_KINDS
from astrometricslib.foundation.storage.data_versions import (
    bump_data_version,
    get_library_data_version,
    read_data_versions,
)
from astrometricslib.foundation.storage.local_database import connect_db
from astrometricslib.foundation.storage.mount import require_mounted_storage
from astrometricslib.foundation.storage.process_locks import file_lock
from astrometricslib.foundation.versioned_cache import VersionedCache

__all__ = [
    "LIBRARY_DATA_KINDS",
    "SIMULATOR_FOLDER_NAME",
    "AbstractButler",
    "Butler",
    "DatasetSpec",
    "VersionedCache",
    "bump_data_version",
    "connect_db",
    "file_lock",
    "get_library_data_version",
    "is_path_inside",
    "locks_directory",
    "read_data_versions",
    "real_folders",
    "require_mounted_storage",
    "resolve_mounted_path",
    "scratch_directory",
    "stop_process_group",
]
