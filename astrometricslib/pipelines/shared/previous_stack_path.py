"""Where the stacking pipeline keeps the stack before the current one.

Before a restack replaces a stack, the pipeline moves the old stack and its
pictures into a folder named `_previous`, next to the new one. Code that walks
a target's folder must skip that folder, or it would treat the old stack as
one more input. This module holds the folder name so every caller agrees.
"""

import os

__all__ = ["PREVIOUS_STACK_FOLDER_NAME", "previous_directory_for"]

# Starts with an underscore, like `_excluded`, so it sorts first and is easy to
# spot in a file manager. The name is a convention, not a setting.
PREVIOUS_STACK_FOLDER_NAME = "_previous"


def previous_directory_for(stack_path: str) -> str:
    """Name the folder that holds the previous version of a stack.

    Parameters
    ----------
    stack_path : `str`
        Path of the current stack's FITS file.

    Returns
    -------
    directory : `str`
        The `_previous` folder inside the stack's own folder.
    """
    return os.path.join(os.path.dirname(stack_path), PREVIOUS_STACK_FOLDER_NAME)
