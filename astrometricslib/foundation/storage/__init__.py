"""Purpose: Generic storage plumbing that both libraries share.

Description: This package provides three kinds of plumbing. `local_database`
opens and writes SQLite files (`connect_db`, `safe_json_dumps`,
`NumpyEncoder`). `process_locks` stops two programs from grabbing the same
resource at once (`file_lock`, `acquire_resource_slot`). `mount` checks that a
drive that should hold the frames is mounted (`require_mounted_storage`). On
top of the database side sits a concrete, generic keyed-model `Butler` that
both libraries instantiate for their catalog and record data. Domain-specific
concerns, such as FITS file path resolution, stay in the libraries. This
package only knows about "a table of Pydantic models keyed by id".
"""

from astrometricslib.foundation.storage.butler import AbstractButler, Butler, DatasetSpec
from astrometricslib.foundation.storage.local_database import NumpyEncoder, connect_db, safe_json_dumps
from astrometricslib.foundation.storage.mount import StorageNotMountedError, require_mounted_storage
from astrometricslib.foundation.storage.process_locks import (
    DeviceInUseError,
    acquire_resource_slot,
    file_lock,
)

__all__ = [
    "AbstractButler",
    "Butler",
    "DatasetSpec",
    "DeviceInUseError",
    "NumpyEncoder",
    "StorageNotMountedError",
    "acquire_resource_slot",
    "connect_db",
    "file_lock",
    "require_mounted_storage",
    "safe_json_dumps",
]
