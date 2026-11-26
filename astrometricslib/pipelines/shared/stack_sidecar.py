"""Saves a JSON file next to a stack, valid only for the stack as it is.

Several steps keep a result next to the stack they measured, so the next
request can read it instead of measuring again: the stars the star finder
detected (`astrometry/processing/detections.py`), the names astrometry gave
them (`astrometry/processing/stack_identification.py`), and the stars
astrometry named for spectroscopy (`shared/solved_star_list.py`). Such a file
(a sidecar) must never outlive the stack it describes: after a restack it
would describe a different picture.

So every sidecar carries the stack's stamp, its size in bytes and its
modification time in nanoseconds. A read gives the record back only when
the file's version and the stamp still match the stack. A write goes to a
temporary file first and is moved over the old file in one step, so a reader
never sees half a file. A temporary file left by a failed write is removed.
A failure to write is logged as a warning and otherwise ignored: the file is
only a shortcut, and the caller measures again next time.

A caller may also accept a file written before stamps were added (it has
none) while the file is not older than the stack; the solved star list does.

The layout of each file is the caller's: this module adds only the
``"version"`` key and the stamp key (each caller keeps the key name its files
already use).
"""

import json
import logging
import os
from typing import Any

__all__ = ["file_stamp", "read_stamped_record", "sidecar_path", "write_stamped_record"]

logger = logging.getLogger(__name__)


def sidecar_path(stack_path: str, suffix: str) -> str:
    """Give the path of a file kept next to a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack's FITS file.
    suffix : `str`
        What follows the stack's own name, without its extension, such as
        ``"_identification.json"``.

    Returns
    -------
    path : `str`
        ``<stack without extension><suffix>``.
    """
    return os.path.splitext(stack_path)[0] + suffix


def file_stamp(path: str) -> list[int] | None:
    """Describe a file by its size and modification time, to see a change.

    Parameters
    ----------
    path : `str`
        The file.

    Returns
    -------
    stamp : `list` [`int`] or `None`
        ``[size in bytes, modification time in nanoseconds]``, or `None` when
        the file cannot be read.
    """
    try:
        status = os.stat(path)
    except OSError:
        return None
    return [int(status.st_size), int(status.st_mtime_ns)]


def read_stamped_record(
    saved_path: str, stack_path: str, version: int, stamp_key: str, *, accept_unstamped_newer: bool = False
) -> dict[str, Any] | None:
    """Read a saved record, if it is for the stack as the stack is now.

    A record with a stamp is read only when the stamp matches the stack.

    Parameters
    ----------
    saved_path : `str`
        The saved file.
    stack_path : `str`
        The stack (or image) the record describes.
    version : `int`
        The layout version the caller reads.
    stamp_key : `str`
        The key that holds the stack's stamp in the record.
    accept_unstamped_newer : `bool`, optional
        Also read a record of the right version that has no stamp (written
        before stamps were added) when the saved file was last changed no
        earlier than the stack: the stack has not changed since it was
        written. False by default, so such a record counts as out of date.

    Returns
    -------
    record : `dict` or `None`
        The whole record, or `None` when there is no file, it cannot be read,
        or its version or stamp differ from now.
    """
    stamp = file_stamp(stack_path)
    if stamp is None or not os.path.isfile(saved_path):
        return None
    try:
        with open(saved_path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError) as error:
        logger.debug("Could not read the saved file %s: %s", saved_path, error)
        return None
    if not isinstance(record, dict) or record.get("version") != version:
        return None
    if stamp_key not in record and accept_unstamped_newer:
        saved_stamp = file_stamp(saved_path)
        return record if saved_stamp is not None and saved_stamp[1] >= stamp[1] else None
    return record if record.get(stamp_key) == stamp else None


def write_stamped_record(
    saved_path: str, stack_path: str, version: int, stamp_key: str, contents: dict[str, Any]
) -> bool:
    """Save a record with the stack's stamp, in one step.

    Parameters
    ----------
    saved_path : `str`
        The file to write. Its folder is made when missing.
    stack_path : `str`
        The stack (or image) the record describes.
    version : `int`
        The layout version, saved under ``"version"``.
    stamp_key : `str`
        The key the stack's stamp is saved under.
    contents : `dict`
        The rest of the record, as plain data JSON can hold.

    Returns
    -------
    saved : `bool`
        `True` when the file was written. A failure is logged as a warning.
    """
    stamp = file_stamp(stack_path)
    if stamp is None:
        return False
    record = {"version": version, stamp_key: stamp, **contents}
    temporary_path = f"{saved_path}.{os.getpid()}.tmp"
    try:
        folder = os.path.dirname(saved_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
        os.replace(temporary_path, saved_path)
    except (OSError, ValueError, TypeError) as error:
        logger.warning("Could not save %s: %s", saved_path, error)
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        return False
    return True
