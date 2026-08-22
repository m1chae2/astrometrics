"""Shared, generic persistence infrastructure.

Used by both astrometricslib and wayfindinglib. Provides the
SQLite/file-locking primitives (`connect_db`, `file_lock`,
`safe_json_dumps`, `acquire_resource_slot`) and a concrete, generic
keyed-model `Butler` that both libraries instantiate directly for
their catalog/record data. Domain-specific concerns -- FITS file path
resolution, `DataCoordinate` -- stay in the consuming libraries; this
package only knows about "table of pydantic models keyed by id".
"""

from datastore.butler import AbstractButler, Butler, DatasetSpec
from datastore.disk_interface import (
    NumpyEncoder,
    acquire_resource_slot,
    connect_db,
    file_lock,
    safe_json_dumps,
)
from datastore.exceptions import DeviceInUseError

__all__ = [
    "AbstractButler",
    "Butler",
    "DatasetSpec",
    "DeviceInUseError",
    "NumpyEncoder",
    "acquire_resource_slot",
    "connect_db",
    "file_lock",
    "safe_json_dumps",
]
