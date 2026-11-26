"""Check that an image path the app sends is inside a folder the app may read.

The viewer sends file paths with many calls: draw a FITS file, read its
header, measure a star, list its detections, follow a pixel to another image.
Each of these reads only files inside the library folder, the frames and
stacks folders the settings name, and the app's scratch folder. This module
is the one place that rule lives. A path from another computer (a mounted
share) is first turned into the path on this computer, and links are
followed, so a link cannot lead out of the allowed folders.
"""

import os
from pathlib import Path
from typing import Any

from astrometricslib.drivers import is_path_inside, resolve_mounted_path, scratch_directory
from astrometricslib.exceptions import InvalidArgumentError

__all__ = ["OUTSIDE_THE_LIBRARY", "PROCESSED_PICTURE_ENDING", "allowed_image_roots", "resolve_allowed_image"]

PROCESSED_PICTURE_ENDING = "_processed"
"""The ending of the picture the app shows for a stack, saved next to it."""

OUTSIDE_THE_LIBRARY = "The image must be inside the library folders."
"""The message of the refusal for a path outside the allowed folders."""


def allowed_image_roots(config: Any) -> list[str]:
    """List the folders the app may read images from.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings. ``get_frames_path`` and ``get_stacks_path`` are used
        when the settings object has them.

    Returns
    -------
    roots : `list` [`str`]
        The real paths of the library folder, the frames and stacks folders,
        and the scratch folder.
    """
    roots = [Path(config.get_library_path()), Path(scratch_directory())]
    for getter in ("get_frames_path", "get_stacks_path"):
        method = getattr(config, getter, None)
        if callable(method):
            roots.append(Path(method()))
    return [os.path.realpath(root) for root in roots]


def resolve_allowed_image(path: str, config: Any) -> str:
    """Turn a path the app sent into a real path inside an allowed folder.

    Parameters
    ----------
    path : `str`
        The path as the app sent it.
    config : `AppConfiguration`
        The settings that name the allowed folders.

    Returns
    -------
    resolved : `str`
        The real path on this computer. The file may not exist; the caller
        reports a missing file.

    Raises
    ------
    InvalidArgumentError
        If the path is outside every allowed folder.
    """
    resolved = os.path.realpath(resolve_mounted_path(path))
    if not any(is_path_inside(root, resolved) for root in allowed_image_roots(config)):
        raise InvalidArgumentError(OUTSIDE_THE_LIBRARY, details={"path": path})
    return resolved
