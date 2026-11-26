"""Keeps the stars astrometry named on a stack in a file next to the stack.

The star catalog saves only stars this library measured (a light curve or a
spectrum; see `catalog_access.holds_measurements`). Astrometry measures
neither, so the stars it names on a stack are not saved there. Spectroscopy
still needs them: it names the zero-order stars of a spectral stack by
matching them against the named stars of the same target's imaging stack
(see `spectroscopy/runner.py`, `_registration_reference_candidates`).

So astrometry writes its named stars next to the stack it solved
(``<stack>_solved_stars.json``), and spectroscopy reads them back. Each run
of astrometry on the stack writes the file again. A restack makes the
astrometry result out of date, and the stage order then runs astrometry again
before spectroscopy (see `tasks.STAGE_PREREQUISITES`). Only a stack gets a
file: a single frame belongs to the raw folders, where nothing is written.
"""

import json
import logging
import os
from collections.abc import Iterable
from typing import Any

from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib.models.stellar_source import StellarObject

__all__ = ["SOLVED_STARS_SIDECAR_SUFFIX", "read_solved_stars", "save_solved_stars"]

logger = logging.getLogger(__name__)

# What follows a stack's own name (without .fits) in the name of the file,
# like the other files kept next to a stack.
SOLVED_STARS_SIDECAR_SUFFIX = "_solved_stars.json"

# Raise this when the file's layout changes, so an old file is not read as a
# new one.
SOLVED_STARS_FILE_VERSION = 1


def _sidecar_path(stack_path: str) -> str:
    """Give the path of the file kept next to a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack's FITS file.

    Returns
    -------
    sidecar : `str`
        ``<stack>_solved_stars.json``.
    """
    return os.path.splitext(stack_path)[0] + SOLVED_STARS_SIDECAR_SUFFIX


def save_solved_stars(stack_path: str, stellar_objects: Iterable[Any]) -> bool:
    """Write the stars astrometry named on a stack next to the stack.

    A failure is logged and ignored: spectroscopy then names its stars from
    the saved catalog stars alone.

    Parameters
    ----------
    stack_path : `str`
        The solved stack.
    stellar_objects : `Iterable` [`StellarObject`]
        The run's named stars.

    Returns
    -------
    saved : `bool`
        `True` when the file was written.
    """
    if not stack_path:
        return False
    try:
        if int(read_header(stack_path).get("STACKCNT", 0) or 0) <= 1:
            return False
        record = {
            "version": SOLVED_STARS_FILE_VERSION,
            "stars": [
                star.model_dump(
                    mode="json", by_alias=True, exclude_defaults=True, exclude_computed_fields=True
                )
                for star in stellar_objects
            ],
        }
        saved_path = _sidecar_path(stack_path)
        temporary_path = f"{saved_path}.{os.getpid()}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
        os.replace(temporary_path, saved_path)
    except (OSError, ValueError, TypeError) as error:
        logger.debug("Could not save the solved star list of %s: %s", stack_path, error)
        return False
    return True


def read_solved_stars(stack_path: str) -> list[StellarObject]:
    """Read the stars astrometry named on a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The named stars, or an empty list when the stack has no readable
        file of the current version.
    """
    if not stack_path:
        return []
    try:
        with open(_sidecar_path(stack_path), encoding="utf-8") as handle:
            record = json.load(handle)
        if record.get("version") != SOLVED_STARS_FILE_VERSION:
            return []
        return [StellarObject.model_validate(payload) for payload in record.get("stars", [])]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError, AttributeError) as error:
        logger.debug("Could not read the solved star list of %s: %s", stack_path, error)
        return []
