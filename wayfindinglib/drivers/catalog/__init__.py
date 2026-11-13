"""Online astronomical catalog query drivers.

Provides a driver interface and concrete implementations for querying online
star catalogs (SIMBAD, GAIA DR3) plus a locally bundled Hipparcos bright-star
extract. All drivers return transient StellarObject instances that are NEVER
recorded to the local astrometrics database.
"""

from wayfindinglib.drivers.catalog.base_catalog_driver import CatalogDriver
from wayfindinglib.drivers.catalog.deep_star_catalog_driver import (
    DeepStarCatalogDriver,
    DeepStarSource,
    LocalDeepStarStore,
)
from wayfindinglib.drivers.catalog.local_bright_star_catalog_driver import (
    LocalBrightStarCatalogDriver,
)

__all__ = [
    "CatalogDriver",
    "DeepStarCatalogDriver",
    "DeepStarSource",
    "LocalBrightStarCatalogDriver",
    "LocalDeepStarStore",
]
