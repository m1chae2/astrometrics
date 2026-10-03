"""Keeps the stack that a restack replaced, so the two can be compared.

A restack overwrites the stack file. Without a copy of the old one, there is
no way to tell whether a change (new flats, a new setting, a fix) made the
stack better or worse. This module keeps exactly one previous version:

1. Before a restack, `archive_current_stack` moves the current stack and its
   companion files into a staging folder.
2. If the restack works, `commit_archive` makes the staging folder the new
   `_previous` folder and drops the older version.
3. If the restack fails, `rollback_archive` moves everything back and the
   older `_previous` folder is untouched.

A person decides when the old version has done its job:
`discard_previous_stack` deletes it, and `swap_with_previous_stack` puts it
back as the current stack (and the current one becomes the previous one, so
nothing is lost by swapping). The pipeline never deletes a previous stack by
itself except by replacing it with a newer one.

Companion files are the pictures and tables made with a stack: the rejection
map, the registration table, the preview and the stretched FITS.
"""

import logging
import os
import shutil

from astrometricslib.pipelines.shared.previous_stack_path import previous_directory_for

logger = logging.getLogger(__name__)

__all__ = [
    "STACK_COMPANION_SUFFIXES",
    "archive_current_stack",
    "commit_archive",
    "discard_previous_stack",
    "previous_stack_path",
    "rollback_archive",
    "stack_files",
    "swap_with_previous_stack",
]

# What follows the stack's own name (without the .fits extension) in the name
# of each companion file. They match what the stacking engine and
# `stack_preview.py` write; a name that is not on this list belongs to
# another stack and is never moved.
STACK_COMPANION_SUFFIXES = ("_RejMap.fits", "_Registration.seq", "_preview.jpg", "_processed.fits")

# Where a restack's old files wait until the new stack is known to be good.
_STAGING_FOLDER_NAME = "_previous.staging"


def stack_files(stack_path: str) -> list[str]:
    """List the files that make up one stack and exist on disk.

    Parameters
    ----------
    stack_path : `str`
        Path of the stack's FITS file.

    Returns
    -------
    files : `list` [`str`]
        The stack itself, then each companion file that exists.
    """
    stem, _ = os.path.splitext(stack_path)
    candidates = [stack_path] + [stem + suffix for suffix in STACK_COMPANION_SUFFIXES]
    return [path for path in candidates if os.path.isfile(path)]


def _move_all(paths: list[str], destination_directory: str) -> list[str]:
    """Move files into a folder, keeping their names.

    Returns
    -------
    moved : `list` [`str`]
        The paths the files were moved from.
    """
    os.makedirs(destination_directory, exist_ok=True)
    moved = []
    for path in paths:
        shutil.move(path, os.path.join(destination_directory, os.path.basename(path)))
        moved.append(path)
    return moved


def archive_current_stack(stack_path: str) -> str | None:
    """Move the current stack and its companions aside before a restack.

    Parameters
    ----------
    stack_path : `str`
        Path the new stack will be written to, which is also where the current
        stack lies.

    Returns
    -------
    staging : `str` or `None`
        The staging folder holding the old files, to pass to `commit_archive`
        or `rollback_archive`. `None` if there was no stack to keep.
    """
    files = stack_files(stack_path)
    if not files or not os.path.isfile(stack_path):
        return None
    staging = os.path.join(os.path.dirname(stack_path), _STAGING_FOLDER_NAME)
    shutil.rmtree(staging, ignore_errors=True)
    _move_all(files, staging)
    return staging


def commit_archive(stack_path: str, staging: str) -> None:
    """Make the staged files the previous stack, replacing any older one.

    Parameters
    ----------
    stack_path : `str`
        Path of the new stack.
    staging : `str`
        The folder `archive_current_stack` returned.
    """
    previous = previous_directory_for(stack_path)
    shutil.rmtree(previous, ignore_errors=True)
    os.replace(staging, previous)


def rollback_archive(stack_path: str, staging: str) -> None:
    """Move the staged files back, after a restack that did not finish.

    Files the failed restack wrote under the same names are replaced by the
    old ones. The older `_previous` folder is left as it was.

    Parameters
    ----------
    stack_path : `str`
        Path of the stack that was being replaced.
    staging : `str`
        The folder `archive_current_stack` returned.
    """
    directory = os.path.dirname(stack_path)
    for name in os.listdir(staging):
        os.replace(os.path.join(staging, name), os.path.join(directory, name))
    shutil.rmtree(staging, ignore_errors=True)


def previous_stack_path(stack_path: str) -> str | None:
    """Find the previous version of a stack.

    Parameters
    ----------
    stack_path : `str`
        Path of the current stack's FITS file.

    Returns
    -------
    path : `str` or `None`
        Path of the previous stack's FITS file, or `None` if none is kept.
    """
    candidate = os.path.join(previous_directory_for(stack_path), os.path.basename(stack_path))
    return candidate if os.path.isfile(candidate) else None


def discard_previous_stack(stack_path: str) -> list[str]:
    """Delete the previous version of a stack and its companion files.

    Parameters
    ----------
    stack_path : `str`
        Path of the current stack's FITS file.

    Returns
    -------
    removed : `list` [`str`]
        The files deleted. Empty if no previous version was kept.
    """
    previous = previous_directory_for(stack_path)
    if not os.path.isdir(previous):
        return []
    removed = [os.path.join(previous, name) for name in sorted(os.listdir(previous))]
    shutil.rmtree(previous)
    return removed


def swap_with_previous_stack(stack_path: str) -> list[str]:
    """Put the previous stack back as the current one.

    The current stack and its companions move into `_previous` in the same
    step, so calling this again undoes it.

    Parameters
    ----------
    stack_path : `str`
        Path of the current stack's FITS file.

    Returns
    -------
    restored : `list` [`str`]
        The files now in the stack's folder that came from `_previous`. Empty
        if no previous version was kept, in which case nothing moves.
    """
    previous = previous_directory_for(stack_path)
    previous_files = (
        [os.path.join(previous, name) for name in sorted(os.listdir(previous))]
        if os.path.isdir(previous)
        else []
    )
    if not previous_files:
        return []
    directory = os.path.dirname(stack_path)
    exchange = os.path.join(directory, "_previous.exchange")
    shutil.rmtree(exchange, ignore_errors=True)
    _move_all(stack_files(stack_path), exchange)
    restored = []
    for path in previous_files:
        target = os.path.join(directory, os.path.basename(path))
        os.replace(path, target)
        restored.append(target)
    shutil.rmtree(previous, ignore_errors=True)
    os.replace(exchange, previous)
    return restored
