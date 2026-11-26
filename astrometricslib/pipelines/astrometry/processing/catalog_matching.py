"""The matching step both star identification paths share.

An astrometry run (`star_identifier`) and a stack's own star names
(`stack_identification`) both place each detected star on the sky through
the plate solution and take the nearest catalog star (SIMBAD or Gaia)
within `CATALOG_MATCH_RADIUS_ARCSEC`. Both do it with the two functions
here, so the two paths cannot drift apart. The naming of a matched star is
shared too: `catalog_star_identity.name_simbad_star` for a SIMBAD star and
`gaia_record_ids.gaia_record_id` for a Gaia star.
"""

from typing import Any

import numpy as np
from astropy.coordinates import SkyCoord

__all__ = ["nearest_catalog_entries", "pixels_to_sky"]


def pixels_to_sky(wcs: Any, x: Any, y: Any) -> tuple[np.ndarray, np.ndarray]:
    """Place pixel positions on the sky through a plate solution.

    The full solution is used, including any distortion terms (the SIP
    polynomial a plate solver adds for a wide field), so stars near the
    corners land where the catalog has them.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The plate solution.
    x, y : `float` or array-like
        Columns and rows, 0-based (the center of the first pixel is 0, 0).

    Returns
    -------
    ra, dec : `numpy.ndarray`
        Right ascension and declination in degrees, one per position.
    """
    ra, dec = wcs.all_pix2world(np.asarray(x, dtype=float), np.asarray(y, dtype=float), 0)
    return np.atleast_1d(np.asarray(ra, dtype=float)), np.atleast_1d(np.asarray(dec, dtype=float))


def nearest_catalog_entries(coords: SkyCoord, catalog: SkyCoord | None) -> tuple[np.ndarray, np.ndarray]:
    """Find each position's nearest catalog star and its distance.

    Parameters
    ----------
    coords : `SkyCoord`
        The positions (one or more).
    catalog : `SkyCoord` or `None`
        The catalog stars.

    Returns
    -------
    index : `numpy.ndarray` [`int`]
        The catalog index of each position's nearest star (0 when the
        catalog is empty).
    separation_arcsec : `numpy.ndarray` [`float`]
        The distance to it in arcseconds; infinite when the catalog is empty
        or missing, so no match radius accepts it.
    """
    count = 1 if coords.isscalar else len(coords)
    if catalog is None or catalog.isscalar or len(catalog) == 0 or count == 0:
        return np.zeros(count, dtype=int), np.full(count, np.inf)
    index, separation, _ = coords.match_to_catalog_sky(catalog)
    return np.atleast_1d(np.asarray(index, dtype=int)), np.atleast_1d(
        np.asarray(separation.arcsec, dtype=float)
    )
