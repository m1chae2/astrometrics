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
before spectroscopy (see `tasks.STAGE_PREREQUISITES`). The file carries the
stack's stamp (`stack_sidecar`), so a file older than the stack is never
read. A file written before the stamp was added has none; it is still read
when it was last changed no earlier than the stack, so the overlays keep
their stars until astrometry runs again. Only a stack gets a file: a single
frame belongs to the raw folders, where nothing is written.
"""

import logging
from collections.abc import Iterable
from typing import Any

from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.stack_sidecar import (
    read_stamped_record,
    sidecar_path,
    write_stamped_record,
)

__all__ = ["SOLVED_STARS_SIDECAR_SUFFIX", "read_solved_stars", "save_solved_stars"]

logger = logging.getLogger(__name__)

# What follows a stack's own name (without .fits) in the name of the file,
# like the other files kept next to a stack.
SOLVED_STARS_SIDECAR_SUFFIX = "_solved_stars.json"

# Raise this when the file's layout changes, so an old file is not read as a
# new one. A file of version 1 written before the stack's stamp was added has
# no stamp; it is read when it is not older than the stack.
SOLVED_STARS_FILE_VERSION = 1

# The key that holds the stack's stamp (size and modification time).
_STAMP_KEY = "stack_stamp"


def save_solved_stars(stack_path: str, stellar_objects: Iterable[Any]) -> bool:
    """Write the stars astrometry named on a stack next to the stack.

    The file carries the stack's stamp (`stack_sidecar`), so call this after
    the run has finished changing the stack (astrometry writes its solution
    into the header first). A failure is logged and ignored: spectroscopy
    then names its stars from the saved catalog stars alone.

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
        stars = [
            star.model_dump(mode="json", by_alias=True, exclude_defaults=True, exclude_computed_fields=True)
            for star in stellar_objects
        ]
    except (OSError, ValueError, TypeError) as error:
        logger.debug("Could not save the solved star list of %s: %s", stack_path, error)
        return False
    return write_stamped_record(
        sidecar_path(stack_path, SOLVED_STARS_SIDECAR_SUFFIX),
        stack_path,
        SOLVED_STARS_FILE_VERSION,
        _STAMP_KEY,
        {"stars": stars},
    )


def read_solved_stars(stack_path: str) -> list[StellarObject]:
    """Read the stars astrometry named on a stack, if the stack is unchanged.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The named stars, or an empty list when the stack has no readable
        file of the current version, or the stack changed since the file
        was written (a restack that astrometry has not solved yet). A file
        with a stamp must match the stack's stamp. A file from before stamps
        were added has none; it is read when its modification time is not
        older than the stack's.
    """
    if not stack_path:
        return []
    record = read_stamped_record(
        sidecar_path(stack_path, SOLVED_STARS_SIDECAR_SUFFIX),
        stack_path,
        SOLVED_STARS_FILE_VERSION,
        _STAMP_KEY,
        accept_unstamped_newer=True,
    )
    if record is None:
        return []
    try:
        return [StellarObject.model_validate(payload) for payload in record.get("stars", [])]
    except (ValueError, TypeError, AttributeError) as error:
        logger.debug("Could not read the solved star list of %s: %s", stack_path, error)
        return []
