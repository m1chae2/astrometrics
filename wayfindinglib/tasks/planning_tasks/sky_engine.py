"""Purpose: The planning sky engine.

Description: `SkyEngine` holds what the planning sums need about the sky:
the observatory site (as an astropy `EarthLocation`), the meridian-flip
delay, the science library handle, the catalog drivers and the
constellation line data. `ObservationPlanning` builds one, the first time
it is needed, and passes it to the functions in this package
(`coordinate_operations`, `resolution_operations`, `catalog_operations`,
`constellation_operations`, `visibility_operations`, `visibility_report`
and `sky_sources`). Its few methods hand their work to those functions.
"""

import logging
from datetime import datetime
from typing import Any

import astropy.units as u
from astropy.coordinates import EarthLocation
from astropy.time import Time

from astrometricslib import StellarObject, Target
from wayfindinglib.data_access.equipment_catalog_reader import (
    DEFAULT_MERIDIAN_FLIP_DELAY_MIN,
    get_meridian_flip_delay_min,
)
from wayfindinglib.data_access.site_profile_reader import FALLBACK_SITE, resolve_site_location
from wayfindinglib.tasks.planning_tasks.catalog_operations import build_catalog_driver_registry
from wayfindinglib.tasks.planning_tasks.constellation_operations import ConstellationLineLibrary

logger = logging.getLogger(__name__)


class SkyEngine:
    """The site, catalogs and library handle the planning sums share.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        Application configuration. The site and the meridian-flip delay
        are read from it. Without one, the Denver fallback site and the
        default delay are used.
    latitude, longitude : `float`, optional
        Site latitude and longitude in degrees, used instead of the
        configured ones.
    elevation : `float`, optional
        Site elevation in metres, used instead of the configured one.
    astrometrics : `astrometricslib.Astrometrics`, optional
        The science library handle. If `None`, one is built from
        `config`.
    """

    def __init__(
        self,
        config: Any | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        elevation: float | None = None,
        astrometrics: Any | None = None,
    ) -> None:
        self._config = config
        has_config = config is not None and hasattr(config, "app_config")
        site = resolve_site_location(config) if has_config else FALLBACK_SITE
        self.latitude = latitude or site["latitude"]
        self.longitude = longitude or site["longitude"]
        self.elevation = elevation or site["elevation"]
        self.meridian_flip_delay_min = (
            get_meridian_flip_delay_min(config) if has_config else DEFAULT_MERIDIAN_FLIP_DELAY_MIN
        )

        self.location = EarthLocation(
            lat=self.latitude * u.deg, lon=self.longitude * u.deg, height=self.elevation * u.m
        )

        from astrometricslib import Astrometrics
        from wayfindinglib.drivers.catalog import LocalDeepStarStore

        self._astrometrics = astrometrics or Astrometrics(self._config)
        self._catalog_driver_registry = build_catalog_driver_registry(
            star_source=LocalDeepStarStore(self._config)
        )
        # Bundled constellation stick-figure line data -- not a CatalogDriver
        # since it's static cultural/artistic topology, not a live query.
        self._constellation_lines = ConstellationLineLibrary()

    def get_local_sidereal_time(self, time_input: datetime | Time) -> float:
        """Delegate get_local_sidereal_time to coordinate_operations.

        Returns
        -------
        lst_hours : `float`
            The local sidereal time, in hours.
        """
        from wayfindinglib.tasks.planning_tasks import coordinate_operations

        return coordinate_operations.get_local_sidereal_time(self, time_input)

    def resolve_target_coordinates(self, target_name: str) -> Target | StellarObject:
        """Delegate resolve_target_coordinates to resolution_operations.

        Returns
        -------
        target : `Target` or `StellarObject`
            The resolved target or stellar object.
        """
        from wayfindinglib.tasks.planning_tasks import resolution_operations

        return resolution_operations.resolve_target_coordinates(self, target_name)

    def get_sources(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        include_catalog: bool = False,
        include_stars: bool = True,
    ) -> list[Target | StellarObject]:
        """Delegate get_sources to resolution_operations.

        Returns
        -------
        sources : `list` [`Target` or `StellarObject`]
            The matching targets and/or stellar objects.
        """
        from wayfindinglib.tasks.planning_tasks import resolution_operations

        return resolution_operations.get_sources(
            self, ra_deg, dec_deg, radius_deg, include_catalog, include_stars
        )

    def get_library_star_summaries(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        magnitude_range: tuple[float, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate get_library_star_summaries to resolution_operations.

        Returns
        -------
        summaries : `list` [`dict`]
            Quick summaries of the user's own stars inside the region.
        """
        from wayfindinglib.tasks.planning_tasks import resolution_operations

        return resolution_operations.get_library_star_summaries(
            self, ra_deg, dec_deg, radius_deg, magnitude_range
        )

    def list_catalog_driver_metadata(self) -> list[dict[str, Any]]:
        """Delegate list_catalog_driver_metadata to catalog_operations.

        Returns
        -------
        driver_metadata : `list` [`dict`]
            Metadata describing each registered catalog driver.
        """
        from wayfindinglib.tasks.planning_tasks import catalog_operations

        return catalog_operations.list_catalog_driver_metadata(self)

    def get_constellation_lines(self) -> list[dict[str, Any]]:
        """Delegate get_constellation_lines to constellation_operations.

        REQ: PLN-3.3

        Returns
        -------
        line_segments : `list` [`dict`]
            Constellation stick-figure line segment definitions.
        """
        from wayfindinglib.tasks.planning_tasks import constellation_operations

        return constellation_operations.get_constellation_line_segments(self)
