"""Locally cached Hipparcos bright-star catalog driver.

Serves the full-sky "Bright Stars" overlay from a small, pre-downloaded SQLite
extract instead of a live query. Hipparcos is used instead of GAIA DR3 here
because GAIA's detectors saturate on very bright stars, so GAIA is missing
nearly every naked-eye-famous star (Sirius, Vega, Polaris, Arcturus, Capella,
Rigel, etc. — verified absent from a bundled GAIA extract in practice).
Hipparcos was purpose-built for precise astrometry of naked-eye-range stars
and is complete and reliable down to about V = 9, including the brightest.

See wayfindinglib/scripts/catalog_management/build_local_bright_star_catalog.py
for the one-time download that populates the catalog file this driver reads.

REQ: PLN-3.2
"""

import logging
import sqlite3
from pathlib import Path

import numpy as np

from astrometricslib import StellarObject
from wayfindinglib.drivers.catalog.base_catalog_driver import CatalogDriver

logger = logging.getLogger(__name__)

CATALOG_FILENAME = "bright_star_catalog.sqlite"
CATALOG_TABLE_NAME = "bright_stars"

# Caps the response to the brightest N matches regardless of how many fall
# within the query radius. Without this, a wide-FOV query can match tens of
# thousands of bundled stars, which serializes to a multi-tens-of-MB JSON
# payload — far more than the star overlay can usefully render per frame and
# slow enough to parse that it looks like nothing loaded.
_MAX_RESULTS = 5000

_ROW_DTYPE = [("hip_id", "i8"), ("ra", "f8"), ("dec", "f8"), ("magnitude", "f8")]


def default_catalog_path() -> Path:
    """Return the default on-disk location for the bundled catalog.

    The catalog is the Hipparcos bright-star SQLite extract.

    Returns
    -------
    path : `pathlib.Path`
        The default catalog file path, alongside this module.
    """
    return Path(__file__).parent / CATALOG_FILENAME


class LocalBrightStarCatalogDriver(CatalogDriver):
    """Serves Hipparcos bright stars from a locally cached SQLite extract.

    Registered under the 'hipparcos' registry key (see
    skylib.catalog_operations), distinct from the live TAP-backed
    GaiaCatalogDriver ('gaia', intended for small viewport-scoped deep
    queries).

    REQ: PLN-3.2
    """

    def __init__(self, catalog_path: Path | None = None):  # ruff: ignore[missing-return-type-special-method]
        self._catalog_path = catalog_path or default_catalog_path()
        self._cached_rows: np.ndarray | None = None

    @property
    def driver_name(self) -> str:
        """The registry key for this driver ("hipparcos")."""
        return "hipparcos"

    @property
    def display_name(self) -> str:
        """The human-readable catalog name."""
        return "Bright Stars (Hipparcos)"

    @property
    def maximum_query_radius_degrees(self) -> float:
        """The largest radius, in degrees, this driver accepts."""
        return 180.0

    def _load_rows(self) -> np.ndarray:
        """Lazily loads and caches the full bundled catalog.

        Returns
        -------
        rows : `numpy.ndarray`
            The catalog rows as a structured array with fields
            ``hip_id``, ``ra``, ``dec``, and ``magnitude``.
        """
        if self._cached_rows is not None:
            return self._cached_rows

        if not self._catalog_path.exists():
            logger.warning(
                "Local bright-star catalog not found at %s — run "
                "wayfindinglib/scripts/catalog_management/build_local_bright_star_catalog.py "
                "to download it.",
                self._catalog_path,
            )
            self._cached_rows = np.empty(0, dtype=_ROW_DTYPE)
            return self._cached_rows

        conn = sqlite3.connect(str(self._catalog_path))
        try:
            cursor = conn.execute(f"SELECT hip_id, ra, dec, magnitude FROM {CATALOG_TABLE_NAME}")
            rows = cursor.fetchall()
        finally:
            conn.close()

        self._cached_rows = np.array(rows, dtype=_ROW_DTYPE)
        return self._cached_rows

    def query_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
    ) -> list[StellarObject]:
        """Return bundled bright Hipparcos stars within a circular sky region.

        Filters the in-memory cached catalog by great-circle separation
        (vectorized haversine) rather than issuing any network request.

        Parameters
        ----------
        ra_degrees : float
            Center Right Ascension in degrees (ICRS).
        dec_degrees : float
            Center Declination in degrees (ICRS).
        radius_degrees : float
            Search radius in degrees.

        Returns
        -------
        List[StellarObject]
            Transient StellarObject instances, capped to the _MAX_RESULTS
            brightest matches. Never recorded to the database.
        """
        rows = self._load_rows()
        if rows.size == 0:
            return []

        ra_center_rad = np.radians(ra_degrees)
        dec_center_rad = np.radians(dec_degrees)
        ra_rad = np.radians(rows["ra"])
        dec_rad = np.radians(rows["dec"])

        delta_dec = dec_rad - dec_center_rad
        delta_ra = ra_rad - ra_center_rad
        haversine_term = (
            np.sin(delta_dec / 2.0) ** 2
            + np.cos(dec_center_rad) * np.cos(dec_rad) * np.sin(delta_ra / 2.0) ** 2
        )
        separation_deg = np.degrees(2.0 * np.arcsin(np.sqrt(np.clip(haversine_term, 0.0, 1.0))))

        matched_rows = rows[separation_deg <= radius_degrees]
        if matched_rows.size > _MAX_RESULTS:
            brightest_indices = np.argsort(matched_rows["magnitude"])[:_MAX_RESULTS]
            matched_rows = matched_rows[brightest_indices]

        return [
            StellarObject(
                id=f"HIP_{int(row['hip_id'])}",
                name=f"HIP {int(row['hip_id'])}",
                ra=float(row["ra"]),
                dec=float(row["dec"]),
                magnitude=float(row["magnitude"]),
                spectralType="",
            )
            for row in matched_rows
        ]
