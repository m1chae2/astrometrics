"""Online astronomical catalog query drivers.

Provides a driver interface and concrete implementations for querying online
star catalogs (SIMBAD, GAIA DR3) plus a locally bundled Hipparcos bright-star
extract. All drivers return transient StellarObject instances that are NEVER
recorded to the local astrometrics database.

REQ: PLN-3.1, PLN-3.2
"""

from wayfindinglib.drivers.catalog.base_catalog_driver import CatalogDriver
from wayfindinglib.drivers.catalog.gaia_catalog_driver import GaiaCatalogDriver
from wayfindinglib.drivers.catalog.local_bright_star_catalog_driver import (
    LocalBrightStarCatalogDriver,
)
from wayfindinglib.drivers.catalog.simbad_catalog_driver import SimbadCatalogDriver

__all__ = ["CatalogDriver", "GaiaCatalogDriver", "LocalBrightStarCatalogDriver", "SimbadCatalogDriver"]
