r"""Purpose: The Windows way to put a frame into a work folder (a hard link).

Description: Imported by `file_staging` only on Windows. It has the same
name and inputs as its Linux version in `_staging_posix`. A hard link needs
no special rights, but both names must be on one NTFS drive. When they are
not (the frames are on another drive, a Google Drive folder or a network
folder), it raises a `ConfigurationError` that says so, so the run stops
with a clear reason instead of skipping frames.
"""

import os

from astrometricslib.foundation.errors import ConfigurationError


def stage_file(source: str, destination: str) -> None:
    r"""Give a file a second name in a work folder, as a hard link.

    Parameters
    ----------
    source : `str`
        The original file. It is not changed.
    destination : `str`
        The new name in the work folder. It must not exist yet.

    Raises
    ------
    FileNotFoundError
        If `source` does not exist.
    FileExistsError
        If `destination` already exists.
    ConfigurationError
        If Windows cannot hard-link the file, for example because it is on
        another drive than the work folder (``%LOCALAPPDATA%``).
    """
    try:
        os.link(source, destination)
    except FileNotFoundError, FileExistsError:
        raise
    except OSError as link_error:
        source_drive = os.path.splitdrive(os.path.abspath(source))[0] or "?"
        work_drive = os.path.splitdrive(os.path.abspath(destination))[0] or "?"
        raise ConfigurationError(
            f"Cannot use {source} here: Windows reaches frames through hard links, which work only "
            f"when the frames are on the same NTFS drive as the work folder ({work_drive}). "
            f"The frame is on {source_drive}. Keep the frame library on {work_drive} "
            f"(not a Google Drive or network folder). ({link_error})"
        ) from link_error
