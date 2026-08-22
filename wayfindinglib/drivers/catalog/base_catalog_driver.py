"""Abstract base class for online astronomical catalog query drivers.

Each concrete driver encapsulates the query logic for one online catalog
service and returns transient StellarObject instances that are NEVER
persisted to the local astrometrics database.

REQ: PLN-3.1
"""

import abc
import logging

from astrometricslib import StellarObject

logger = logging.getLogger(__name__)


class CatalogDriver(abc.ABC):
    """Abstract base for online astronomical catalog query drivers.

    Each subclass implements query_region() for a specific online service
    (SIMBAD, GAIA, VizieR, etc.). Drivers are stateless singletons — they
    hold no per-query mutable state and are safe to call concurrently.

    REQ: PLN-3.1
    """

    @property
    @abc.abstractmethod
    def driver_name(self) -> str:
        """Short unique identifier used as a registry key.

        For example, 'simbad' or 'gaia'.
        """

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable label shown in the UI (e.g. 'SIMBAD', 'GAIA DR3')."""

    @property
    def maximum_query_radius_degrees(self) -> float:
        """Maximum query radius accepted by this driver.

        Subclasses should override to enforce service-specific limits and
        prevent runaway queries against large catalogs such as GAIA.

        Returns
        -------
        float
            Maximum search radius in degrees. Defaults to 5.0.
        """
        return 5.0

    @abc.abstractmethod
    def query_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
    ) -> list[StellarObject]:
        """Query the catalog for objects in a circular sky region.

        The caller is responsible for clamping radius_degrees to
        maximum_query_radius_degrees before calling this method.
        Implementations should still handle oversized radii gracefully.

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
            Transient StellarObject instances. Must not be saved to the
            database.
        """
