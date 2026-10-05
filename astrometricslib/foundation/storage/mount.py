"""Check that the drive holding the raw frames is really attached.

The raw frames can live on a drive that is not always there: a Windows
partition, a USB disk or a network share (NAS). When such a drive is not
mounted, its mount point is just an empty folder on the computer's own disk.
Without a check, a download or a file sort would create folders there and
fill the local disk with frames that belong on the drive.

The ``frames_mount_point`` setting names the mount point. When it is set,
`require_mounted_storage` refuses to write below it unless something is
mounted there. When it is not set, nothing is checked.
"""

import os
from pathlib import Path

from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.errors import StorageError
from astrometricslib.foundation.paths import is_path_inside, mount_path_variants


class StorageNotMountedError(StorageError):
    """Raised when a drive that should hold the frames is not mounted."""


def require_mounted_storage(destination: str | Path, config: AppConfiguration | None = None) -> None:
    """Stop with an error if `destination` is on a drive that is not mounted.

    Call this before creating folders or writing frames. It does nothing
    unless ``frames_mount_point`` is set, and nothing for a path outside that
    mount point.

    Parameters
    ----------
    destination : `str` or `pathlib.Path`
        The folder or file about to be written.
    config : `AppConfiguration`, optional
        The settings to read. Defaults to the shared application settings.

    Raises
    ------
    StorageNotMountedError
        Raised if `destination` is below the frames mount point and nothing
        is mounted there. The message says which folder to check.
    """
    settings = config or get_configuration()
    mount_point = settings.get_frames_mount_point()
    if mount_point is None:
        return
    if not is_path_inside(str(mount_point), str(destination)):
        return
    if any(os.path.ismount(variant) for variant in mount_path_variants(str(mount_point))):
        return
    raise StorageNotMountedError(
        f"Nothing is mounted at {mount_point}, so {destination} was not written. "
        "Attach the drive or mount the share, then try again.",
        details={"mount_point": str(mount_point), "destination": str(destination)},
    )
