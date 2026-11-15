"""Purpose: The Linux way to put a frame into a work folder (a symbolic link).

Description: Imported by `file_staging` only when the program does not run
on Windows. It has the same name, inputs and errors as its Windows version
in `_staging_windows`.
"""

import os


def stage_file(source: str, destination: str) -> None:
    """Give a file a second name in a work folder, as a symbolic link.

    Parameters
    ----------
    source : `str`
        The original file. It is not changed.
    destination : `str`
        The new name in the work folder. It must not exist yet; if it does,
        or the link cannot be made, the `OSError` from ``os.symlink`` comes
        through.
    """
    os.symlink(source, destination)
