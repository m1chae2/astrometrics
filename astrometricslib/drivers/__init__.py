"""Purpose: The storage and file tools that other apps may use.

Description: Storage and file helpers that the backend, the MCP servers and
`wayfindinglib` share: the data store (`Butler`, `AbstractButler`,
`DatasetSpec`, `connect_db`), file locks and work folders, the checks for a
mounted drive, and `stop_process_group`, which ends a program and the helpers
it started. The private driver code (Siril, catalog access, camera profiles)
lives in `astrometricslib._internal.drivers`. Each name here is imported from
the module where its code lives today.
"""

from astrometricslib.foundation.paths import (
    is_path_inside,
    locks_directory,
    resolve_mounted_path,
    scratch_directory,
)
from astrometricslib.foundation.run_abandonment import stop_process_group
from astrometricslib.foundation.storage.butler import AbstractButler, Butler, DatasetSpec
from astrometricslib.foundation.storage.local_database import connect_db
from astrometricslib.foundation.storage.mount import require_mounted_storage
from astrometricslib.foundation.storage.process_locks import file_lock

__all__ = [
    "AbstractButler",
    "Butler",
    "DatasetSpec",
    "connect_db",
    "file_lock",
    "is_path_inside",
    "locks_directory",
    "require_mounted_storage",
    "resolve_mounted_path",
    "scratch_directory",
    "stop_process_group",
]
