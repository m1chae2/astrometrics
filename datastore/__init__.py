"""Shared, generic storage infrastructure.

Used by both astrometricslib and wayfindinglib. Provides two unrelated
kinds of plumbing: `local_database` opens and writes SQLite files
(`connect_db`, `safe_json_dumps`, `NumpyEncoder`), while `process_locks`
stops two programs from grabbing the same resource at once (`file_lock`,
`acquire_resource_slot`). On top of the database side sits a concrete,
generic keyed-model `Butler` that both libraries instantiate directly
for their catalog/record data. Domain-specific concerns -- FITS file
path resolution, `FrameSelector` -- stay in the consuming libraries;
this package only knows about "table of pydantic models keyed by id".
"""

from datastore.butler import AbstractButler, Butler, DatasetSpec
from datastore.exceptions import DeviceInUseError
from datastore.local_database import NumpyEncoder, connect_db, safe_json_dumps
from datastore.process_locks import acquire_resource_slot, file_lock

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
