"""Purpose: Name the folders for working files, and handle mounted-drive paths.

Description: Two groups of paths live here.

* Where temporary working files go. On many Linux systems ``/tmp`` is kept in
  RAM, so large scratch files there use up memory. These functions point to
  folders under ``~/.cache`` on the local disk instead.
* The two ways Linux spells the path of a mounted drive. Desktop Linux mounts
  a drive under ``/media/<user>/`` on some systems and ``/run/media/<user>/``
  on others. A configuration file written on one system and read on the other
  names a path that does not exist. The functions here treat the two spellings
  as the same place.
"""

import os


def _cache_root() -> str:
    """Give the folder under which this program keeps its working files.

    Returns
    -------
    cache_root : `str`
        ``$XDG_CACHE_HOME`` if it is set, otherwise ``~/.cache``.
    """
    return os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")


def scratch_directory() -> str:
    """Give the folder for Python's temporary working files, creating it.

    This is ``~/.cache/astrometrics`` on the local disk. It is used instead of
    the system temporary folder, which is often held in RAM.

    Returns
    -------
    scratch_path : `str`
        The absolute path of the folder, which exists when this returns.
    """
    scratch_path = os.path.join(_cache_root(), "astrometrics")
    os.makedirs(scratch_path, exist_ok=True)
    return scratch_path


def siril_work_directory() -> str:
    """Give the folder where Siril keeps its working files, creating it.

    This is ``~/.cache/astrometrics/siril`` on the local disk, inside the
    one folder that holds everything disposable. Siril runs in a Flatpak
    sandbox with access to the home folder, so it can use this path. The
    system temporary folder is not used because the sandbox cannot see it.

    Returns
    -------
    work_path : `str`
        The absolute path of the folder, which exists when this returns.
    """
    work_path = os.path.join(scratch_directory(), "siril")
    os.makedirs(work_path, exist_ok=True)
    return work_path


def data_directory() -> str:
    """Give the folder for this program's durable local state, creating it.

    This is ``~/.local/share/astrometrics``. Unlike the cache folder, nothing
    here may be deleted to free space.

    Returns
    -------
    data_path : `str`
        ``$XDG_DATA_HOME/astrometrics`` if ``XDG_DATA_HOME`` is set,
        otherwise ``~/.local/share/astrometrics``. The folder exists when
        this returns.
    """
    data_root = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    data_path = os.path.join(data_root, "astrometrics")
    os.makedirs(data_path, exist_ok=True)
    return data_path


def locks_directory() -> str:
    """Give the folder for lock files, creating it.

    Lock files stop two programs from using one device or resource at the
    same time. They hold no data and are safe to delete when nothing runs.
    They live in the cache folder on the local disk, so every program on
    this computer finds the same locks whichever library it uses.

    Returns
    -------
    locks_path : `str`
        ``<scratch folder>/locks``, which exists when this returns.
    """
    locks_path = os.path.join(scratch_directory(), "locks")
    os.makedirs(locks_path, exist_ok=True)
    return locks_path


def catalog_cache_directory() -> str:
    """Give the folder for downloaded catalog data that can be fetched again.

    Returns
    -------
    catalog_path : `str`
        ``<scratch folder>/catalogs``, which exists when this returns.
    """
    catalog_path = os.path.join(scratch_directory(), "catalogs")
    os.makedirs(catalog_path, exist_ok=True)
    return catalog_path


# The folder on the backup remote, below the remote's name, that holds one
# subfolder per database snapshot.
REMOTE_BACKUP_FOLDER = "Library/_backup"


def remote_snapshot_path(remote: str, snapshot_name: str = "") -> str:
    """Build the rclone path of a database snapshot folder on the backup.

    Parameters
    ----------
    remote : `str`
        Remote name including the colon, for example ``drive:``.
    snapshot_name : `str`, optional
        Snapshot folder name. Empty gives the folder that holds all snapshots.

    Returns
    -------
    remote_path : `str`
        The rclone path.
    """
    suffix = f"/{snapshot_name}" if snapshot_name else ""
    return f"{remote}{REMOTE_BACKUP_FOLDER}{suffix}"


def mount_path_variants(path: str) -> set[str]:
    """List the ways one drive's path can be written on Linux.

    Parameters
    ----------
    path : `str`
        An absolute path.

    Returns
    -------
    variants : `set` [`str`]
        The path and its other ``/media`` or ``/run/media`` spelling, with
        no trailing slash.
    """
    normalised = os.path.normpath(path)
    variants = {normalised}
    if normalised.startswith("/run/media/"):
        variants.add(normalised.replace("/run/media/", "/media/", 1))
    elif normalised.startswith("/media/"):
        variants.add(normalised.replace("/media/", "/run/media/", 1))
    return variants


def resolve_mounted_path(path: str) -> str:
    """Give the spelling of a path that exists on this computer.

    Parameters
    ----------
    path : `str`
        A candidate filesystem path.

    Returns
    -------
    resolved : `str`
        `path` if it exists or has no other spelling that exists. Otherwise
        the other ``/media`` or ``/run/media`` spelling.
    """
    if not path or os.path.exists(path):
        return path
    for variant in sorted(mount_path_variants(path)):
        if variant != os.path.normpath(path) and os.path.exists(variant):
            return variant
    return path


def is_path_inside(root: str, path: str) -> bool:
    """Say whether a resolved path is `root` or lies below it.

    Both paths may be spelled either way (``/media`` or ``/run/media``).

    Parameters
    ----------
    root : `str`
        A resolved directory path.
    path : `str`
        A resolved path to test.

    Returns
    -------
    inside : `bool`
        `True` if `path` is `root` or inside it. `False` if it is not, or if
        the two cannot be compared (for example, paths on different drives).
    """
    for path_variant in mount_path_variants(path):
        for root_variant in mount_path_variants(root):
            try:
                if os.path.commonpath([root_variant, path_variant]) == root_variant:
                    return True
            except ValueError:
                continue
    return False
