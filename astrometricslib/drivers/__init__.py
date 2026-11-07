"""Purpose: The storage and file tools that other apps may use.

Description: Storage and file helpers that the backend, the MCP servers and
`wayfindinglib` share: the data store (`Butler`, `AbstractButler`,
`DatasetSpec`, `connect_db`), the library's write counters
(`get_library_data_version`, `get_target_data_version`,
`get_camera_data_versions`, `get_target_camera_names`, `LIBRARY_DATA_KINDS`,
and `bump_data_version` and `read_data_versions` for a database that keeps
its own counters) and the
cache that uses them (`VersionedCache`), file locks and work folders, the
checks for a mounted drive, the launch identity (`LaunchIdentity`,
`read_launch_identity`) and the offline switch it carries (`require_online`,
`require_local_host`), the folders a settings file names (`library_folders`),
and `stop_process_group`, which ends a program and the helpers it started.
The private driver code (Siril, catalog
access, camera profiles) lives in `astrometricslib._internal.drivers`.
Each name here is imported from the module where its code lives today.
"""

from astrometricslib.foundation.launch_identity import (
    LaunchIdentity,
    launch_identity_from_settings,
    read_launch_identity,
)
from astrometricslib.foundation.offline import (
    OfflineModeError,
    is_loopback_host,
    network_is_offline,
    require_local_host,
    require_online,
)
from astrometricslib.foundation.paths import (
    SIMULATOR_FOLDER_NAME,
    LibraryFolders,
    is_path_inside,
    library_folders,
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
    get_camera_data_versions,
    get_library_data_version,
    get_target_camera_names,
    get_target_data_version,
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
    "LaunchIdentity",
    "LibraryFolders",
    "OfflineModeError",
    "VersionedCache",
    "bump_data_version",
    "connect_db",
    "file_lock",
    "get_camera_data_versions",
    "get_library_data_version",
    "get_target_camera_names",
    "get_target_data_version",
    "is_loopback_host",
    "is_path_inside",
    "launch_identity_from_settings",
    "library_folders",
    "locks_directory",
    "network_is_offline",
    "read_data_versions",
    "read_launch_identity",
    "real_folders",
    "require_local_host",
    "require_mounted_storage",
    "require_online",
    "resolve_mounted_path",
    "scratch_directory",
    "stop_process_group",
]
