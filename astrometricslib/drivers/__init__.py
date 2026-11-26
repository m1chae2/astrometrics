"""Purpose: The storage and file tools that other apps may use.

Description: Storage and file helpers that the backend, the MCP servers and
`wayfindinglib` share: the data store (`Butler`, `AbstractButler`,
`DatasetSpec`, `connect_db`), the library's write counters
(`get_library_data_version`, `get_target_data_version`,
`get_camera_data_versions`, `get_frame_metric_signs`,
`get_stack_quality_data_version`,
`get_target_camera_names`, `get_target_document_signs`,
`LIBRARY_DATA_KINDS`,
and `bump_data_version` and `read_data_versions` for a database that keeps
its own counters) and the
cache that uses them (`VersionedCache`), file locks and work folders, the
checks for a mounted drive, the launch identity (`LaunchIdentity`,
`read_launch_identity`) and the offline switch it carries (`require_online`,
`require_local_host`, `is_loopback_host` and the ``ASTROMETRICS_OFFLINE``
variable name `OFFLINE_VARIABLE`), the folders a settings file names
(`library_folders`), the stack file a target record names, found again where
the stacks folder moved it (`existing_stack_path`),
and `stop_process_group`, which ends a program and the helpers it started,
and the HEALPix sky-tile helpers (`tile_of_source_id`, `tiles_for_cone`,
`tiles_for_footprint`, `pixels_near_circles`, `healpix_pixels_of_points`,
`pixel_source_id_range`, `build_pixel_query`), and `get_simbad_driver`, the
SIMBAD driver every caller shares: it answers from the library's stored
answers first and asks SIMBAD only when it must, and `get_gaia_archive`, the
ESA Gaia archive connection (``launch_job`` and ``launch_job_async``). Both
send every request through the outside-service gateway, which applies one rate
limit and one pause rule to each service host. `header_is_plate_solved` says
whether a FITS header's sky map came from a plate solve, not from the place
the mount was told to point; the library, the backend and the Planetarium all
use this one test.
The private driver code (Siril, catalog
access, camera profiles) lives in `astrometricslib._internal.drivers`.
Each name here is imported from the module where its code lives today.
"""

from astrometricslib._internal.drivers.cached_simbad_driver import get_simbad_driver
from astrometricslib._internal.drivers.fits_access import header_is_plate_solved
from astrometricslib._internal.drivers.outside_services import get_gaia_archive
from astrometricslib._internal.drivers.sky_tiles import (
    build_pixel_query,
    healpix_pixels_of_points,
    pixel_source_id_range,
    pixels_near_circles,
    tile_of_source_id,
    tiles_for_cone,
    tiles_for_footprint,
)
from astrometricslib.foundation.launch_identity import (
    LaunchIdentity,
    launch_identity_from_settings,
    read_launch_identity,
)
from astrometricslib.foundation.offline import (
    OFFLINE_VARIABLE,
    is_loopback_host,
    network_is_offline,
    require_local_host,
    require_online,
)
from astrometricslib.foundation.paths import (
    LibraryFolders,
    existing_stack_path,
    is_path_inside,
    library_folders,
    locks_directory,
    resolve_mounted_path,
    scratch_directory,
)
from astrometricslib.foundation.run_abandonment import stop_process_group
from astrometricslib.foundation.storage.butler import AbstractButler, Butler, DatasetSpec
from astrometricslib.foundation.storage.data_versions import ALL_KINDS as LIBRARY_DATA_KINDS
from astrometricslib.foundation.storage.data_versions import (
    bump_data_version,
    get_camera_data_versions,
    get_frame_metric_signs,
    get_library_data_version,
    get_stack_quality_data_version,
    get_target_camera_names,
    get_target_data_version,
    get_target_document_signs,
    read_data_versions,
)
from astrometricslib.foundation.storage.local_database import connect_db
from astrometricslib.foundation.storage.mount import require_mounted_storage
from astrometricslib.foundation.storage.process_locks import file_lock
from astrometricslib.foundation.versioned_cache import VersionedCache

__all__ = [
    "LIBRARY_DATA_KINDS",
    "OFFLINE_VARIABLE",
    "AbstractButler",
    "Butler",
    "DatasetSpec",
    "LaunchIdentity",
    "LibraryFolders",
    "VersionedCache",
    "build_pixel_query",
    "bump_data_version",
    "connect_db",
    "existing_stack_path",
    "file_lock",
    "get_camera_data_versions",
    "get_frame_metric_signs",
    "get_gaia_archive",
    "get_library_data_version",
    "get_simbad_driver",
    "get_stack_quality_data_version",
    "get_target_camera_names",
    "get_target_data_version",
    "get_target_document_signs",
    "header_is_plate_solved",
    "healpix_pixels_of_points",
    "is_loopback_host",
    "is_path_inside",
    "launch_identity_from_settings",
    "library_folders",
    "locks_directory",
    "network_is_offline",
    "pixel_source_id_range",
    "pixels_near_circles",
    "read_data_versions",
    "read_launch_identity",
    "require_local_host",
    "require_mounted_storage",
    "require_online",
    "resolve_mounted_path",
    "scratch_directory",
    "stop_process_group",
    "tile_of_source_id",
    "tiles_for_cone",
    "tiles_for_footprint",
]
