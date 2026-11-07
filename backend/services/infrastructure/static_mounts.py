"""Purpose: Say which library folders the backend serves and at which URL.

Description: A client cannot guess where the backend serves a file. This
module holds the one table of served folders. The router that mounts the
folders and the ``system:static_mounts`` reply are both built from it, so
they cannot disagree.
"""

from astrometricslib import AppConfiguration


def static_mounts(configuration: AppConfiguration) -> list[dict[str, str]]:
    """List the folders the backend serves and the URL prefix of each.

    This is the one place that knows which folder is served where, so
    `mount_library_files` and the clients share it. A client turns a file
    path into a URL by finding the longest ``root`` that starts the path and
    swapping it for the ``urlPrefix``.

    Parameters
    ----------
    configuration : `AppConfiguration`
        The configuration that names the folders.

    Returns
    -------
    mounts : `list` [`dict`]
        One ``{"root": ..., "urlPrefix": ...}`` item per served folder. The
        stacks folder is listed only when the frames mount exists, because
        that mount is what serves it.
    """
    frames_path = configuration.get_frames_path()
    stacks_path = configuration.get_stacks_path()
    library_path = configuration.get_library_path()
    mounts = [{"root": str(library_path), "urlPrefix": "/static"}]
    if frames_path != library_path:
        mounts.append({"root": str(frames_path), "urlPrefix": "/static/frames"})
        if stacks_path != frames_path:
            mounts.append({"root": str(stacks_path), "urlPrefix": "/static/frames"})
    return mounts
