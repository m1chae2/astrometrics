"""Deep-star catalog driver: faint Gaia DR3 stars from a copy kept on disk.

The Planetarium draws faint stars from a copy of the Gaia DR3 catalog that
was downloaded once with ``python -m
astrometricslib.scripts.build_deep_star_catalog``. Looking stars up in it is
a local database read, so it takes milliseconds and never touches the
internet.

Until that download has been run, this driver simply returns no stars.

REQ: PLN-3.2
"""

import logging
from typing import Protocol

from astrometricslib import StellarObject
from wayfindinglib.drivers.catalog.base_catalog_driver import CatalogDriver

logger = logging.getLogger(__name__)

# The most stars one lookup returns, brightest first.
#
# Derivation: the map draws every star it is given as a point, and a
# lookup of about 19,000 stars (a 5 degree view at magnitude 15 on a
# synthetic 10 million star catalog) took 47 ms and produced about a
# megabyte of JSON. 20,000 is just above that, so a normal view is never cut
# short, while an unusually dense field (the Milky Way) cannot make one
# response grow without bound. When it does hit the limit, the brightest
# stars still arrive first and only the faintest are dropped. Not yet
# measured on the real catalog, whose dense fields hold far more stars.
_MAXIMUM_STARS_PER_QUERY = 20000

# Used when the caller gives no magnitude limit: fainter than any star in
# the catalog, so nothing is left out by the limit.
_NO_MAGNITUDE_LIMIT = 99.0


class DeepStarSource(Protocol):
    """Where the driver reads the downloaded stars from.

    `astrometricslib.api.stars.StellarCatalog` provides this, so the driver
    reaches the catalog through the `Astrometrics` facade.
    """

    def find_deep_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float, maximum_stars: int | None = None
    ) -> list[tuple[int, float, float, float]] | None:
        """Return ``(source_id, ra, dec, magnitude)`` for stars in a circle."""


class DeepStarCatalogDriver(CatalogDriver):
    """Query driver for the Gaia DR3 stars downloaded to this computer.

    Parameters
    ----------
    star_source : `DeepStarSource`, optional
        Where to read the downloaded stars from. Without one, no stars are
        ever returned.

    REQ: PLN-3.2
    """

    def __init__(self, star_source: DeepStarSource | None = None) -> None:
        self._star_source = star_source

    @property
    def driver_name(self) -> str:
        """The registry key for this driver ("deep_stars")."""
        return "deep_stars"

    @property
    def display_name(self) -> str:
        """The human-readable catalog name."""
        return "Gaia DR3 (downloaded)"

    @property
    def maximum_query_radius_degrees(self) -> float:
        """The largest radius, in degrees, this driver accepts (whole sky)."""
        return 180.0

    def query_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
        magnitude_limit: float | None = None,
    ) -> list[StellarObject]:
        """Look up the downloaded stars in a circular sky region.

        Parameters
        ----------
        ra_degrees : float
            Center Right Ascension in degrees (ICRS).
        dec_degrees : float
            Center Declination in degrees (ICRS).
        radius_degrees : float
            Search radius in degrees.
        magnitude_limit : float, optional
            Faintest Gaia G magnitude wanted.

        Returns
        -------
        List[StellarObject]
            Transient StellarObject instances, brightest first, at most
            _MAXIMUM_STARS_PER_QUERY of them. Empty if the catalog has not
            been downloaded. Their IDs are ``Gaia DR3 <source_id>``, the same
            names the star library gives the same stars, so a star that is
            both in the library and in this catalog is only drawn once.
        """
        if self._star_source is None:
            return []
        limit = _NO_MAGNITUDE_LIMIT if magnitude_limit is None else magnitude_limit
        stars = self._star_source.find_deep_stars(
            ra_degrees, dec_degrees, radius_degrees, limit, maximum_stars=_MAXIMUM_STARS_PER_QUERY
        )
        if stars is None:
            return []
        return [
            StellarObject(
                id=f"Gaia DR3 {source_id}",
                name=f"Gaia DR3 {source_id}",
                ra=star_ra,
                dec=star_dec,
                magnitude=magnitude,
                spectralType="",
            )
            for source_id, star_ra, star_dec, magnitude in stars
        ]
