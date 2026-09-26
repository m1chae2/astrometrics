"""Main interface for managing and analyzing individual stars.

This module provides the `StellarCatalog`, which is the primary tool for
working with specific stars found in the images. It can be used to track
a star's brightness over time, analyze its spectrum, and manage its records
in the database.
"""

import logging
from typing import Any

from astrometricslib.drivers import deep_star_store
from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.utilities.config_loader import AppConfiguration

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "StellarCatalog",
]

logger = logging.getLogger(__name__)

# Bounds StellarCatalog.list_object_summaries's "browse everything, no
# target filter" case: without a cap, a screen that checks in on this
# repeatedly ends up building and sending the whole catalog's summaries
# every single time -- at 270,450 rows, real network and JSON-parsing
# cost even after list_star_summaries already skipped loading full
# StellarObjects. A caller wanting the true, unbounded catalog for
# scripting should use list_objects() instead; this cap only applies to
# the summary path documented for UI catalog-browsing callers.
DEFAULT_UNFILTERED_SUMMARY_LIMIT = 5000


class StellarCatalog:
    """A catalog for tracking and analyzing individual stars.

    While the TargetCatalog deals with the whole picture, this catalog tracks
    the properties of specific stars over time—like their brightness
    (photometry) or chemical composition (spectroscopy)—enabling
    deeper scientific analysis.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        catalog_access: AbstractCatalogAccess | None = None,
    ) -> None:
        """Initialize with a configuration and a way to reach storage.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            Application configuration. Loaded from the application
            configuration when omitted.
        catalog_access : `AbstractCatalogAccess`, optional
            The database tool used to save and load the stellar catalog.
            A `CatalogAccess` over `config` is constructed when omitted.
        """
        if config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            config = get_configuration()
        self._config = config
        if catalog_access is None:
            from astrometricslib.drivers.catalog_access import CatalogAccess

            catalog_access = CatalogAccess(config)
        self.catalog_access = catalog_access

    def list_objects(self) -> list[StellarObject]:
        """List all stellar objects extracted across the library.

        Warning: this reads every star's full record from the database
        into new objects each time -- about 12 seconds and 2.8 GB on a
        274,000-star library, and none of that memory is handed back
        afterwards. It is for one-off scripts. Application code should ask
        for only what it needs: `list_object_summaries`,
        `list_object_summaries_in_region`, `list_object_ids`,
        `list_objects_for_target`, `list_objects_in_region`,
        `find_by_id_or_name`, `find_by_position` or `get_object`.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            All stellar objects currently in the library.
        """
        from astrometricslib.api import stellar_operations

        return stellar_operations.list_objects(self)

    def list_object_ids(self) -> list[str]:
        """List the id of every star in the catalog.

        Reads only the id column, so no star record is loaded.

        Returns
        -------
        star_ids : `list` [`str`]
            One id per star.
        """
        return self.catalog_access.list_star_ids()

    def existing_ids(self, ids: list[str]) -> set[str]:
        """Say which of the given ids are stars in the catalog.

        Parameters
        ----------
        ids : `list` [`str`]
            The star ids to look for.

        Returns
        -------
        found_ids : `set` [`str`]
            The subset of `ids` that has a star record.
        """
        return self.catalog_access.existing_star_ids(ids)

    def list_spectrum_object_ids(self) -> list[str]:
        """List the id of every star that has a recorded spectrum.

        Returns
        -------
        star_ids : `list` [`str`]
            The ids of the stars with spectroscopy data.
        """
        return [summary.id for summary in self.catalog_access.list_star_summaries() if summary.has_spectra]

    def list_objects_for_target(self, target_id: str) -> list[StellarObject]:
        """Load the full records of the stars that belong to one target.

        Only that target's stars are read from the database, however large
        the rest of the catalog is.

        Parameters
        ----------
        target_id : `str`
            The target whose stars to load.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The stars recorded against `target_id`.
        """
        star_ids = [summary.id for summary in self.catalog_access.list_star_summaries(target_id=target_id)]
        return self.catalog_access.get_by_ids("stellar_catalog", star_ids)

    def list_objects_in_region(self, ra: float, dec: float, radius: float) -> list[StellarObject]:
        """Load the full records of the stars inside a circle on the sky.

        Uses the database's declination index to read only the stars near
        that spot.

        Parameters
        ----------
        ra : `float`
            Right ascension of the circle's center, in degrees.
        dec : `float`
            Declination of the circle's center, in degrees.
        radius : `float`
            Radius of the circle, in degrees.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The stars whose position is inside the circle.
        """
        star_ids = [summary.id for summary in self.catalog_access.list_stars_in_region(ra, dec, radius)]
        return self.catalog_access.get_by_ids("stellar_catalog", star_ids)

    def find_all_by_id_or_name(self, name: str) -> list[StellarObject]:
        """Find every star whose id or name equals `name`, ignoring case.

        Parameters
        ----------
        name : `str`
            The star's id or its name.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The matching stars, an exact id match first. Empty if there is
            no match.
        """
        matching_ids = self.catalog_access.find_star_ids_by_name(name)
        if name in matching_ids:
            matching_ids.remove(name)
            matching_ids.insert(0, name)
        by_id = {star.id: star for star in self.catalog_access.get_by_ids("stellar_catalog", matching_ids)}
        return [by_id[star_id] for star_id in matching_ids if star_id in by_id]

    def find_by_id_or_name(self, name: str) -> StellarObject | None:
        """Find a star whose id or name equals `name`, ignoring case.

        Parameters
        ----------
        name : `str`
            The star's id or its name.

        Returns
        -------
        stellar_object : `StellarObject` or `None`
            The matching star, or `None` if there is none. When several
            stars match, an exact id match wins.
        """
        matches = self.find_all_by_id_or_name(name)
        return matches[0] if matches else None

    def find_by_position(self, ra: float, dec: float, tolerance_arcsec: float = 5.0) -> StellarObject | None:
        """Find the catalog star nearest to a spot on the sky.

        Only the stars within the tolerance of that spot are read, using the
        database's declination index, instead of checking the whole
        catalog.

        Parameters
        ----------
        ra : `float`
            Right ascension, in degrees.
        dec : `float`
            Declination, in degrees.
        tolerance_arcsec : `float`, optional
            How far from the spot a star may be and still count as a
            match, in arcseconds. Defaults to 5.

        Returns
        -------
        stellar_object : `StellarObject` or `None`
            The nearest star inside the tolerance, or `None` if none is.
        """
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        target_coordinate = SkyCoord(ra=ra, dec=dec, unit=(u.deg, u.deg))
        nearest_id: str | None = None
        nearest_separation = tolerance_arcsec
        for candidate in self.catalog_access.list_stars_in_region(ra, dec, tolerance_arcsec / 3600.0):
            # A stored 0.0 means "position never set", not the point
            # (0, 0) on the sky, so those stars are never matched.
            if not candidate.right_ascension or not candidate.declination:
                continue
            try:
                candidate_coordinate = SkyCoord(
                    ra=float(candidate.right_ascension),
                    dec=float(candidate.declination),
                    unit=(u.deg, u.deg),
                )
            except ValueError, TypeError:
                continue
            separation = target_coordinate.separation(candidate_coordinate).arcsecond
            if separation < nearest_separation:
                nearest_id, nearest_separation = candidate.id, separation
        if nearest_id is None:
            return None
        matches = self.catalog_access.get_by_ids("stellar_catalog", [nearest_id])
        return matches[0] if matches else None

    def find_or_create_by_position(
        self,
        ra: float,
        dec: float,
        name: str | None = None,
        spectral_type: str | None = None,
        magnitude: float | None = None,
        target_id: str | None = None,
        tolerance_arcsec: float = 5.0,
    ) -> StellarObject:
        """Find the star at a spot on the sky, or record a new one there.

        Looks for an existing star with the given name, then for one within
        `tolerance_arcsec` of (`ra`, `dec`), and only creates a new star if
        neither exists. Whichever star is used gets any of the spectral
        type, magnitude and target that it does not have yet.

        Parameters
        ----------
        ra : `float`
            Right ascension, in degrees.
        dec : `float`
            Declination, in degrees.
        name : `str`, optional
            The star's catalog name (for example from SIMBAD). Also becomes
            the id of a new star.
        spectral_type : `str`, optional
            Spectral type to record if the star has none.
        magnitude : `float`, optional
            Magnitude to record if the star has none.
        target_id : `str`, optional
            Target to add to the star's list of targets.
        tolerance_arcsec : `float`, optional
            How close in position counts as the same star, in arcseconds.

        Returns
        -------
        stellar_object : `StellarObject`
            The star that was found or created.
        """
        # 1. A star with this exact id is the same star, wherever it sits.
        if name:
            matches = self.catalog_access.get_by_ids("stellar_catalog", [name])
            if matches:
                existing = matches[0]
                updates: dict[str, Any] = {}
                if spectral_type and not existing.spectral_type:
                    updates["spectral_type"] = spectral_type
                    updates["stellar_spectral_type"] = spectral_type
                if magnitude is not None and existing.magnitude is None:
                    updates["magnitude"] = magnitude
                if target_id and target_id not in existing.target_ids:
                    updates["target_ids"] = [*list(existing.target_ids), target_id]
                if updates:
                    self.update(existing.id, updates)
                    existing = self.get_object(existing.id)
                return existing

        # 2. Otherwise a star at the same spot is the same star.
        nearby = self.find_by_position(ra, dec, tolerance_arcsec)
        if nearby is not None:
            updates = {}
            new_id = nearby.id
            if name and (not nearby.name or "Star_" in nearby.id) and not self.get_object(name):
                # A field detection that a catalog has now named takes the
                # catalog name, unless that name is already another star.
                updates["name"] = name
                if "Star_" in nearby.id:
                    new_id = name
                    updates["id"] = name
            if spectral_type and not nearby.spectral_type:
                updates["spectral_type"] = spectral_type
                updates["stellar_spectral_type"] = spectral_type
            if magnitude is not None and nearby.magnitude is None:
                updates["magnitude"] = magnitude
            if target_id and target_id not in nearby.target_ids:
                updates["target_ids"] = [*list(nearby.target_ids), target_id]
            if updates:
                if new_id != nearby.id:
                    self.delete(nearby.id)
                    self.create(new_id, ra=ra, dec=dec)
                self.update(new_id, updates)
                nearby = self.get_object(new_id)
            return nearby

        # 3. Nothing there yet: record a new star.
        if name:
            new_star_id = name
        else:
            base_id = f"Star_{len(self.list_object_ids()) + 1}"
            new_star_id = base_id
            counter = 1
            while self.get_object(new_star_id):
                new_star_id = f"{base_id}_{counter}"
                counter += 1

        self.create(new_star_id, ra=ra, dec=dec)
        new_star_updates: dict[str, Any] = {"name": name or new_star_id}
        if spectral_type:
            new_star_updates["spectral_type"] = spectral_type
            new_star_updates["stellar_spectral_type"] = spectral_type
        if magnitude is not None:
            new_star_updates["magnitude"] = magnitude
        if target_id:
            new_star_updates["target_ids"] = [target_id]
        self.update(new_star_id, new_star_updates)
        return self.get_object(new_star_id)

    def list_object_summaries(
        self, target_id: str | None = None, limit: int | None = None, *, apply_default_limit: bool = True
    ) -> list[dict[str, Any]]:
        """Get a quick, lightweight summary of stars in the catalog.

        If all the detailed data for a star is needed, use `list_objects`
        instead. This function is specifically designed to be very fast by
        only grabbing basic info (like ID, name, and if it has spectra),
        which is perfect for building UI lists that need to load quickly.

        Parameters
        ----------
        target_id : `str`, optional
            Restrict to stars belonging to this target.
        limit : `int`, optional
            Maximum number of stars to return. When `target_id` is not
            given, defaults to `DEFAULT_UNFILTERED_SUMMARY_LIMIT` --
            an unfiltered "browse everything" request is exactly the
            case worth bounding, since it is the one whose size scales
            with the whole catalog rather than with one target's own
            star count. Pass an explicit value to override either
            default.
        apply_default_limit : `bool`, optional
            Whether an omitted, target-less `limit` should fall back to
            `DEFAULT_UNFILTERED_SUMMARY_LIMIT`. Defaults to `True`, matching
            this function's usual "UI catalog browsing" callers. A caller
            about to search or filter the *entire* catalog itself --
            where capping here would silently hide real matches outside
            the first `DEFAULT_UNFILTERED_SUMMARY_LIMIT` rows, rather
            than bound the response actually sent back -- should pass
            `False` and apply its own limit after filtering instead.

        Returns
        -------
        summaries : `list` [`dict`]
            One dict per star with keys ``id``, ``name``, ``ra``,
            ``dec``, ``targetIds``, ``hasSpectra``, ``hasPhotometry``,
            ``magnitude`` (`None` when unknown), and ``spectralType`` (an
            empty string when unknown), optionally filtered by
            ``target_id``.
        """
        effective_limit = limit
        if effective_limit is None and not target_id and apply_default_limit:
            effective_limit = DEFAULT_UNFILTERED_SUMMARY_LIMIT

        # Keys are camelCase because this dict is handed straight to the
        # user interface; the record's own field names are the Python
        # ones.
        return [
            {
                "id": star.id,
                "name": star.name,
                "ra": star.right_ascension,
                "dec": star.declination,
                "targetIds": star.target_ids,
                "hasSpectra": star.has_spectra,
                "hasPhotometry": star.has_photometry,
                "magnitude": star.magnitude,
                "spectralType": star.spectral_type,
            }
            for star in self.catalog_access.list_star_summaries(target_id=target_id, limit=effective_limit)
        ]

    def list_object_summaries_in_region(
        self, ra: float, dec: float, radius: float, magnitude_range: tuple[float, float] | None = None
    ) -> list[dict[str, Any]]:
        """Get quick summaries of the library stars inside a circle of sky.

        Like `list_object_summaries`, this never loads a star's full
        record, so it stays fast however large the library is. The
        difference is that it only reads the stars near one spot, using
        the database's declination index, and each summary also carries
        the star's magnitude and spectral type. The sky map calls it on
        every pan and zoom.

        Parameters
        ----------
        ra : `float`
            Right ascension of the circle's center, in degrees.
        dec : `float`
            Declination of the circle's center, in degrees.
        radius : `float`
            Radius of the circle, in degrees.
        magnitude_range : `tuple` [`float`, `float`], optional
            Lowest and highest magnitude to keep, ends included. Stars
            with no saved magnitude are left out. Every star is kept when
            omitted.

        Returns
        -------
        summaries : `list` [`dict`]
            One dict per star inside the circle with keys ``id``,
            ``name``, ``ra``, ``dec``, ``targetIds``, ``hasSpectra``,
            ``hasPhotometry``, ``magnitude`` (`None` when unknown), and
            ``spectralType`` (an empty string when unknown).
        """
        # camelCase keys for the same reason as in `list_object_summaries`.
        return [
            {
                "id": star.id,
                "name": star.name,
                "ra": star.right_ascension,
                "dec": star.declination,
                "targetIds": star.target_ids,
                "hasSpectra": star.has_spectra,
                "hasPhotometry": star.has_photometry,
                "magnitude": star.magnitude,
                "spectralType": star.spectral_type,
            }
            for star in self.catalog_access.list_stars_in_region(ra, dec, radius, magnitude_range)
        ]

    def find_deep_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float, maximum_stars: int | None = None
    ) -> list[tuple[int, float, float, float]] | None:
        """Look up stars in the downloaded Gaia deep-star catalog.

        The catalog is a copy of Gaia DR3 saved on this computer by
        ``python -m astrometricslib.scripts.build_deep_star_catalog``, so
        this makes no internet request. It is meant for drawing a sky map
        quickly; it does not touch the stars in the library itself.

        Parameters
        ----------
        ra, dec : `float`
            The center of the circle, in degrees.
        radius : `float`
            The radius of the circle, in degrees.
        magnitude_limit : `float`
            Only stars as bright as this Gaia G magnitude, or brighter.
        maximum_stars : `int`, optional
            Return at most this many stars, keeping the brightest.

        Returns
        -------
        stars : `list` of `tuple` or `None`
            One ``(source_id, ra, dec, magnitude)`` tuple per star, brightest
            first, or `None` if the catalog has not been downloaded at all.
        """
        return deep_star_store.find_deep_stars(self._config, ra, dec, radius, magnitude_limit, maximum_stars)

    def get_deep_catalog_status(self) -> dict[str, Any]:
        """Say how much of the deep-star catalog has been downloaded.

        Returns
        -------
        status : `dict`
            ``installed``, ``complete``, ``star_count``, ``pixels_downloaded``,
            ``pixels_total``, ``healpix_level``, ``magnitude_limit`` and
            ``size_megabytes``. See `deep_star_store.get_deep_catalog_status`.
        """
        return deep_star_store.get_deep_catalog_status(self._config)

    def get_object(self, object_id: str) -> StellarObject | None:
        """Find a single star in the catalog using its ID.

        Parameters
        ----------
        object_id : `str`
            The id to look up, exact or fuzzy-matched.

        Returns
        -------
        stellar_object : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.
        """
        from astrometricslib.api import stellar_operations

        return stellar_operations.get_object(self, object_id)

    def analyze_periodicity(self, object_id: str) -> StellarObject | None:
        """Search a star's light curve for a repeating pattern, and save it.

        Runs the Lomb-Scargle periodogram (needs at least 5 brightness
        measurements) and the box-fitting transit search (needs at least
        8), and saves whichever produced a result on the star's
        photometry. The photometry pipeline already runs these for a
        target's own star and its brightest stars; this is how any other
        star gets its period and transit numbers.

        Parameters
        ----------
        object_id : `str`
            The id of the star to analyze.

        Returns
        -------
        stellar_object : `StellarObject` or `None`
            The star with any new analysis saved, or `None` if no such
            star exists. A star with too few measurements is returned
            unchanged.
        """
        from astrometricslib.pipelines.photometry.variability_analyzer import VariabilityAnalyzer

        star = self.get_object(object_id)
        if star is None or not star.photometry:
            return star

        analyzer = VariabilityAnalyzer()
        periodogram = analyzer.run_lomb_scargle_periodogram(star)
        transit_candidate = analyzer.run_bls_transit_search(star)
        if periodogram is None and transit_candidate is None:
            return star
        return self.update(star.id, {"photometry": star.photometry})

    def tune_spectroscopy_calibration(
        self,
        image_path: str,
        camera_name: str | None = None,
        star_x: float | None = None,
        star_y: float | None = None,
    ) -> dict[str, Any]:
        """Automatically calibrate the physical model for a spectroscopy image.

        Parameters
        ----------
        image_path : `str`
            Path to the spectroscopy FITS image to calibrate against.
        camera_name : `str`, optional
            Camera name, used to look up its quantum-efficiency curve.
        star_x : `float`, optional
            Zero-order star's x pixel coordinate, if already known.
        star_y : `float`, optional
            Zero-order star's y pixel coordinate, if already known.

        Returns
        -------
        calibration_result : `dict`
            Tuned calibration parameters and diagnostic metrics.
        """
        from astrometricslib.api import stellar_operations

        return stellar_operations.tune_spectroscopy_calibration(self, image_path, camera_name, star_x, star_y)

    def delete(self, object_id: str) -> bool:
        """Safely delete a star from the catalog by its ID.

        Parameters
        ----------
        object_id : `str`
            The id of the stellar object to delete.

        Returns
        -------
        deleted : `bool`
            `True` if a matching object was found and removed;
            `False` otherwise.
        """
        existing = self.get_object(object_id)
        if existing is None:
            return False
        self.catalog_access.delete_by_ids("stellar_catalog", [existing.id])
        return True

    def update(self, object_id: str, updates: dict[str, Any]) -> StellarObject | None:
        """Safely update a star's properties in the catalog.

        Parameters
        ----------
        object_id : `str`
            The id of the stellar object to update.
        updates : `dict`
            Attribute name/value pairs to set on the object.

        Returns
        -------
        stellar_object : `StellarObject` or `None`
            The updated object, or `None` if `object_id` is not
            found.
        """
        existing = self.get_object(object_id)
        if not existing:
            return None

        for key, value in updates.items():
            if hasattr(existing, key):
                setattr(existing, key, value)

        def _apply_updates(current: StellarObject | None, updated: StellarObject) -> StellarObject:
            target_obj = current if current is not None else updated
            for key, value in updates.items():
                if hasattr(target_obj, key):
                    setattr(target_obj, key, value)
            return target_obj

        self.catalog_access.merge_and_record("stellar_catalog", [existing], _apply_updates)
        return self.get_object(object_id)

    def create(
        self,
        object_id: str,
        ra: str | None = None,
        dec: str | None = None,
    ) -> StellarObject:
        """Safely create a new star record in the catalog.

        Parameters
        ----------
        object_id : `str`
            The id of the stellar object to create.
        ra : `str`, optional
            Right ascension, in sexagesimal or degrees.
        dec : `str`, optional
            Declination, in sexagesimal or degrees.

        Returns
        -------
        stellar_object : `StellarObject`
            The existing or newly created stellar object.

        Raises
        ------
        ValueError
            If ``object_id`` is empty or null.
        """
        if not object_id or not str(object_id).strip():
            raise ValueError("object_id cannot be empty or null")

        existing = self.get_object(object_id)
        if existing:
            return existing

        new_obj = StellarObject()
        new_obj.id = object_id
        new_obj.name = object_id

        if ra:
            new_obj.right_ascension = ra
        if dec:
            new_obj.declination = dec

        self.catalog_access.merge_and_record(
            "stellar_catalog", [new_obj], lambda current, updated: current if current is not None else updated
        )
        return new_obj

    def get_audit(self) -> dict[str, Any]:
        """Get a summary of how much data is in the stellar catalog.

        Returns
        -------
        audit : `dict`
            Counts and coverage percentages for identified, spectral,
            and photometric records.
        """
        # Counts come from the short-form summaries, which read only the
        # indexed columns, so no star record is loaded to answer them.
        summaries = self.catalog_access.list_star_summaries()
        total = len(summaries)
        with_names = len([s for s in summaries if s.name and "Star_" not in s.id])
        with_spectral = len([s for s in summaries if s.spectral_type and s.spectral_type != "Unknown"])
        with_magnitude = len([s for s in summaries if s.magnitude not in (None, 0.0)])

        return {
            "total_objects": total,
            "identified_objects": with_names,
            "spectral_coverage": round((with_spectral / total * 100), 2) if total > 0 else 0,
            "photometric_coverage": round((with_magnitude / total * 100), 2) if total > 0 else 0,
            "stats": {"names": with_names, "spectral": with_spectral, "magnitude": with_magnitude},
        }

    def save_all(self, objects: list[StellarObject], allow_empty: bool = False) -> str:
        """Save a complete list of stars, entirely replacing the old catalog.

        Warning: This deletes any star that isn't in the new list provided!
        If only a few stars need to be updated, use `update()` instead.

        Parameters
        ----------
        objects : `list` [`StellarObject`]
            The full set of stellar objects to record.
        allow_empty : `bool`, optional
            By default, saving an empty list is stopped so the entire
            catalog isn't accidentally deleted. Pass `True` if
            really intend to wipe the catalog clean.

        Returns
        -------
        result : `str`
            Status message describing the recorded write.

        Raises
        ------
        ValueError
            Raised if `objects` is empty and `allow_empty` is `False`.
        """
        if not objects and not allow_empty:
            raise ValueError(
                "save_all() received an empty list, which would delete every stellar object. "
                "Pass allow_empty=True to clear the catalog deliberately."
            )
        # `coordinate` is required by the AbstractCatalogAccess.put signature;
        # omitting it previously made every call raise TypeError.
        self.catalog_access.put(objects, "stellar_catalog", {})
        return "stellar catalog saved"

    def detect_point_sources(
        self,
        image_data: Any,
        threshold_sigma: float = 5.0,
        fwhm: float = 4.0,
    ) -> list[dict[str, Any]]:
        """Find stars (point sources) inside raw image pixel data.

        Parameters
        ----------
        image_data : `numpy.ndarray`
            2D pixel array to search for point sources.
        threshold_sigma : `float`, optional
            Detection threshold, in standard deviations above the
            background. Defaults to 5.0.
        fwhm : `float`, optional
            Expected point-spread-function FWHM, in pixels. Defaults
            to 4.0.

        Returns
        -------
        sources : `list` [`dict`]
            Detected point sources, sorted by flux.
        """
        from astrometricslib.pipelines.astrometry.source_detection import SourceDetector

        return SourceDetector(threshold_sigma=threshold_sigma, fwhm=fwhm).detect(image_data)
