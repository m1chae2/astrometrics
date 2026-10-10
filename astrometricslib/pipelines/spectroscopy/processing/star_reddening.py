"""Purpose: Find a star's catalog reddening for the dereddening step.

Description: The spectroscopy pipeline removes interstellar reddening from a
spectrum before it classifies the star, when a catalog gives the star's
colour excess E(B-V). This module connects a `StellarObject` to a
`ReddeningDriver`.

Only a Gaia DR3 source id can look the value up, so the module first reads
that id from the star. A star identified from Gaia carries it in its id and
name, in the form ``Gaia DR3 1328045433153485824``. A star identified from
SIMBAD carries only SIMBAD's main name (for example ``HD 172167``), which does
not hold the Gaia id, so no reddening is looked up for it and the
classification runs on the observed spectrum.
"""

import logging
import re
from collections.abc import Iterable

from astrometricslib.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.stellar_source import StellarObject

logger = logging.getLogger(__name__)

__all__ = ["gaia_source_id_of", "look_up_star_reddening"]

# "Gaia DR3 1328045433153485824". A position-only fallback name such as
# "Gaia DR3 J279.2347+38.7837" has no digits-only id and does not match.
_GAIA_DR3_NAME = re.compile(r"\bGaia\s+DR3\s+(\d+)\b", re.IGNORECASE)


def gaia_source_id_of(star: StellarObject) -> int | None:
    """Read a star's Gaia DR3 source id from its names.

    Parameters
    ----------
    star : `StellarObject`
        The star. Its ``id``, ``name`` and ``target_ids`` are searched, in
        that order.

    Returns
    -------
    gaia_source_id : `int` or `None`
        The id, or `None` when no name has the form ``Gaia DR3 <digits>``.
    """
    candidates: Iterable[str] = (star.id, star.name, *star.target_ids)
    for text in candidates:
        match = _GAIA_DR3_NAME.search(str(text or ""))
        if match is not None:
            return int(match.group(1))
    return None


def look_up_star_reddening(star: StellarObject, driver: ReddeningDriver) -> ReddeningEstimate | None:
    """Ask the driver for a star's colour excess E(B-V).

    Parameters
    ----------
    star : `StellarObject`
        The star to look up.
    driver : `ReddeningDriver`
        The reddening source.

    Returns
    -------
    estimate : `ReddeningEstimate` or `None`
        The catalog's value, or `None` when the star has no Gaia id, the
        catalog has no value for it, or the catalog could not be reached. A
        failed lookup is logged and never stops the pipeline.
    """
    gaia_source_id = gaia_source_id_of(star)
    if gaia_source_id is None:
        return None
    try:
        return driver.get_reddening(gaia_source_id)
    except ExternalServiceError:
        logger.warning("No reddening for %s: the catalog lookup failed.", star.name or star.id)
        return None
