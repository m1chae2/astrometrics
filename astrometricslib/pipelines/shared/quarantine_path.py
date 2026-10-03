"""Where the stacking pipeline keeps frames it has set aside.

The stacking pipeline moves frames with clouds or trailed stars into a
folder named `_excluded`, inside the folder the frame came from. The
frame scanner and any other code that walks a target's folder must skip
that folder, or it would add the frames straight back to the target.
This module holds the folder name and the one test for "is this path in
it", so every caller agrees.
"""

import os

__all__ = ["QUARANTINE_FOLDER_NAME", "is_quarantined_path"]

# Starts with an underscore so it sorts first and is easy to spot in a file
# manager. The name is a convention, not a setting: the manifest and the
# restore script both find the folder by this name.
QUARANTINE_FOLDER_NAME = "_excluded"


def is_quarantined_path(path: str) -> bool:
    """Say whether a path lies inside a quarantine folder.

    Parameters
    ----------
    path : `str`
        A file or folder path.

    Returns
    -------
    is_quarantined : `bool`
        `True` if any folder in the path is named `_excluded`.
    """
    return QUARANTINE_FOLDER_NAME in os.path.normpath(path).split(os.sep)
