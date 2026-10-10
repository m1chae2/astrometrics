"""Purpose: Find a star's catalog reddening for the dereddening step.

Description: The spectroscopy pipeline removes interstellar reddening from a
spectrum before it classifies the star, when a catalog gives the star's
colour excess E(B-V). This module connects a `StellarObject` to a
`ReddeningDriver`.

Only a Gaia DR3 source id can look the value up, so the module first reads
that id from the star with `catalog_star_identity.gaia_dr3_source_id_of`. A
star identified from SIMBAD carries the id taken from SIMBAD's list of names;
a star identified from Gaia carries it in its id and name. A star with no
Gaia id (the brightest stars are not in Gaia DR3) gets no reddening, and the
classification runs on the observed spectrum.
"""

import logging

from astrometricslib.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import gaia_dr3_source_id_of

logger = logging.getLogger(__name__)

__all__ = ["look_up_star_reddening"]


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
    gaia_source_id = gaia_dr3_source_id_of(star)
    if gaia_source_id is None:
        return None
    try:
        return driver.get_reddening(gaia_source_id)
    except ExternalServiceError:
        logger.warning("No reddening for %s: the catalog lookup failed.", star.name or star.id)
        return None
