"""Purpose: Put a frame into a work folder without changing the original.

Description: A stacking or plate-solving run works in a scratch folder and
must not touch the library's frames. This module gives the frame a second
name in that folder. It tries the cheapest way first and falls back when the
system refuses, so no part of the program asks which system it runs on:

1. A symbolic link. Free and instant. Linux always allows it; Windows allows
   it only with Developer Mode or administrator rights.
2. A hard link (the same file under a second name, also free and instant).
   It needs no special rights, but both names must be on one drive, so a
   Google Drive or network folder cannot hold one.
3. A copy. Always possible, but it takes time and disk space.
"""

import logging
import os
import shutil

logger = logging.getLogger(__name__)

STAGED_BY_LINK = "link"
"""`stage_file` made a link (symbolic or hard)."""

STAGED_BY_COPY = "copy"
"""`stage_file` copied the file, because no link was possible."""


def stage_file(source: str, destination: str) -> str:
    """Put a file into a work folder without changing the original.

    Parameters
    ----------
    source : `str`
        The original file.
    destination : `str`
        The new name in the work folder. It must not exist yet.

    Returns
    -------
    method : `str`
        `STAGED_BY_LINK` or `STAGED_BY_COPY`. An `OSError` comes through
        when the file can be neither linked nor copied.
    """
    try:
        os.symlink(source, destination)
        return STAGED_BY_LINK
    except OSError as symlink_error:
        logger.debug("No symbolic link for %s (%s); trying a hard link", source, symlink_error)
    try:
        os.link(source, destination)
        return STAGED_BY_LINK
    except OSError as link_error:
        logger.debug("No hard link for %s (%s); copying it", source, link_error)
    shutil.copy2(source, destination)
    return STAGED_BY_COPY
