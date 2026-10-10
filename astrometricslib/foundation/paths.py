"""Purpose: Handle the two ways Linux spells the path of a mounted drive.

Description: Desktop Linux mounts a drive under ``/media/<user>/`` on some
systems and ``/run/media/<user>/`` on others. A configuration file written on
one system and read on the other names a path that does not exist. The
functions here treat the two spellings as the same place.
"""

import os


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
