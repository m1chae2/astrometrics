"""Sky Engine High-Level Interface.

Composes coordinate transforms, target resolution, catalog queries, and
visibility calculations into a single astrometrics backed by descriptive skylib
operations modules.
"""

from datetime import datetime
from typing import Any

import astropy.units as u
from astropy.coordinates import EarthLocation
from astropy.time import Time

from astrometricslib import StellarObject, Target
from wayfindinglib.skylib.catalog_operations import build_catalog_driver_registry
from wayfindinglib.skylib.constellation_operations import ConstellationLineLibrary


class Sky:
    """Astronomical coordinate projection and visibility engine.

    Computes sidereal times, coordinate transformations, tracking rates,
    target resolution, catalog queries, and rise/set/transit/meridian
    visibility using Astropy.

    Parameters
    ----------
    config : Optional[Any]
        Application configuration object.
    latitude : Optional[float]
        Observatory latitude in degrees. If None, defaults to Denver
        (39.7392).
    longitude : Optional[float]
        Observatory longitude in degrees. If None, defaults to Denver
        (-104.9903).
    elevation : Optional[float]
        Observatory elevation in meters. If None, defaults to Denver
        (1600.0).
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        config: Any | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        elevation: float | None = None,
    ):
        self._config = config

        # Read from config if available and args are None
        if config is not None and hasattr(config, "app_config"):
            self.latitude = latitude or float(
                config.app_config.get("Observatory.Location", "latitude", fallback="39.7392")
            )
            self.longitude = longitude or float(
                config.app_config.get("Observatory.Location", "longitude", fallback="-104.9903")
            )
            self.elevation = elevation or float(
                config.app_config.get("Observatory.Location", "elevation", fallback="1600.0")
            )
            self.meridian_flip_delay_min = float(
                config.app_config.get("Observatory.Telescope", "meridian_flip_delay_min", fallback="5.0")
            )
        else:
            self.latitude = latitude or 39.7392
            self.longitude = longitude or -104.9903
            self.elevation = elevation or 1600.0
            self.meridian_flip_delay_min = 5.0

        self.location = EarthLocation(
            lat=self.latitude * u.deg, lon=self.longitude * u.deg, height=self.elevation * u.m
        )

        from astrometricslib import Astrometrics

        self._astrometrics = Astrometrics()
        self._catalog_driver_registry = build_catalog_driver_registry()
        # Bundled constellation stick-figure line data — not a CatalogDriver
        # since it's static cultural/artistic topology, not a live query.
        self._constellation_lines = ConstellationLineLibrary()

    def get_local_sidereal_time(self, time_input: datetime | Time) -> float:
        """Delegate get_local_sidereal_time to skylib coordinate_operations.

        Returns
        -------
        lst_hours : `float`
            The local sidereal time, in hours.
        """
        from wayfindinglib.skylib import coordinate_operations

        return coordinate_operations.get_local_sidereal_time(self, time_input)

    def radec_to_altaz(
        self, ra_deg: float, dec_deg: float, time_input: datetime | Time
    ) -> tuple[float, float]:
        """Delegate radec_to_altaz to skylib coordinate_operations.

        Returns
        -------
        altaz : `tuple` [`float`, `float`]
            The (altitude, azimuth) in degrees.
        """
        from wayfindinglib.skylib import coordinate_operations

        return coordinate_operations.radec_to_altaz(self, ra_deg, dec_deg, time_input)

    def altaz_to_radec(
        self, alt_deg: float, az_deg: float, time_input: datetime | Time
    ) -> tuple[float, float]:
        """Delegate altaz_to_radec to skylib coordinate_operations.

        Returns
        -------
        radec : `tuple` [`float`, `float`]
            The (right ascension, declination) in degrees.
        """
        from wayfindinglib.skylib import coordinate_operations

        return coordinate_operations.altaz_to_radec(self, alt_deg, az_deg, time_input)

    def get_tracking_rates(
        self, ra_deg: float, dec_deg: float, time_input: datetime | Time
    ) -> dict[str, float]:
        """Delegate get_tracking_rates to skylib coordinate_operations.

        Returns
        -------
        rates : `dict` [`str`, `float`]
            Tracking rate values keyed by axis name.
        """
        from wayfindinglib.skylib import coordinate_operations

        return coordinate_operations.get_tracking_rates(self, ra_deg, dec_deg, time_input)

    def resolve_target_coordinates(self, target_name: str) -> Target | StellarObject:
        """Delegate resolve_target_coordinates to resolution_operations.

        Returns
        -------
        target : `Target` or `StellarObject`
            The resolved target or stellar object.
        """
        from wayfindinglib.skylib import resolution_operations

        return resolution_operations.resolve_target_coordinates(self, target_name)

    def get_sources(
        self, ra_deg: float, dec_deg: float, radius_deg: float, include_catalog: bool = False
    ) -> list[Target | StellarObject]:
        """Delegate get_sources to skylib resolution_operations.

        Returns
        -------
        sources : `list` [`Target` or `StellarObject`]
            The matching targets and/or stellar objects.
        """
        from wayfindinglib.skylib import resolution_operations

        return resolution_operations.get_sources(self, ra_deg, dec_deg, radius_deg, include_catalog)

    def get_online_catalog_sources(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        enabled_driver_names: list[str],
    ) -> list[tuple[str, StellarObject]]:
        """Delegate get_online_catalog_sources to resolution_operations.

        Returns
        -------
        sources : `list` [`tuple` [`str`, `StellarObject`]]
            Each entry pairs the catalog driver name with a matching
            stellar object.
        """
        from wayfindinglib.skylib import resolution_operations

        return resolution_operations.get_online_catalog_sources(
            self, ra_deg, dec_deg, radius_deg, enabled_driver_names
        )

    def list_catalog_driver_metadata(self) -> list[dict[str, Any]]:
        """Delegate list_catalog_driver_metadata to catalog_operations.

        Returns
        -------
        driver_metadata : `list` [`dict`]
            Metadata describing each registered catalog driver.
        """
        from wayfindinglib.skylib import catalog_operations

        return catalog_operations.list_catalog_driver_metadata(self)

    def get_constellation_lines(self) -> list[dict[str, Any]]:
        """Delegate get_constellation_lines to skylib constellation_operations.

        REQ: PLN-3.3

        Returns
        -------
        line_segments : `list` [`dict`]
            Constellation stick-figure line segment definitions.
        """
        from wayfindinglib.skylib import constellation_operations

        return constellation_operations.get_constellation_line_segments(self)

    def get_meridian_status(
        self, ra_deg: float, dec_deg: float, time_input: datetime | Time
    ) -> dict[str, Any]:
        """Delegate get_meridian_status to skylib visibility_operations.

        Returns
        -------
        meridian_status : `dict`
            Meridian proximity/flip status fields for the object.
        """
        from wayfindinglib.skylib import visibility_operations

        return visibility_operations.get_meridian_status(self, ra_deg, dec_deg, time_input)

    def get_object_visibility(
        self, ra_deg: float, dec_deg: float, time_input: datetime | Time
    ) -> dict[str, Any]:
        """Delegate get_object_visibility to skylib visibility_operations.

        Returns
        -------
        visibility : `dict`
            Visibility fields (e.g. altitude, rise/set times) for the
            object at the given time.
        """
        from wayfindinglib.skylib import visibility_operations

        return visibility_operations.get_object_visibility(self, ra_deg, dec_deg, time_input)

    def get_visibility(
        self, objects: list[Target | StellarObject], time_input: datetime | Time | None = None
    ) -> list[dict[str, Any]]:
        """Delegate get_visibility to skylib visibility_operations.

        Returns
        -------
        visibility : `list` [`dict`]
            Visibility fields for each object.
        """
        from wayfindinglib.skylib import visibility_operations

        return visibility_operations.get_visibility(self, objects, time_input)
