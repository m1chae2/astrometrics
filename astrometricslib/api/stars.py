"""Main interface for managing and analyzing individual stars.

This module provides the `StellarCatalog`, which is the primary tool for
working with specific stars found in the images. It can be used to track
a star's brightness over time, analyze its spectrum, and manage its records
in the database.
"""

import logging
from typing import Any

from astrometricslib.api.star_analysis import SPECTRAL_CLASS_LABELS, spectral_class_letter, summarize_star
from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.stellar_source import StellarObject

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

QUERY_LIMITS = {"ids": 2000, "summary": 500, "full": 10, "analysis": 10}
"""Most stars one `StellarCatalog.query` answer holds, by detail level. An
analysis record is about two kilobytes, so ten fit well inside a reply."""

MAXIMUM_MATCH_RANKING = 200
"""Most stars with spectra read when ranking a class by spectrum match."""

QUERY_MAXIMUM_RADIUS_DEGREES = 5.0
"""Widest region `StellarCatalog.query` searches. A wider circle on a
274,000-star library returns more than a client can use."""

QUERY_DETAILS = ("exists", "ids", "summary", "analysis", "full", "class_counts", "stats")
"""The detail levels `StellarCatalog.query` accepts."""


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
            from astrometricslib.foundation.config import get_configuration

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

    def list_objects_by_ids(self, object_ids: list[str]) -> list[StellarObject]:
        """Load the full records for a specific set of star ids.

        Used when a caller already knows which stars it wants (for
        example, from a lightweight summary scan) and needs their full
        records -- their extracted spectra, not just the summary flags.

        Parameters
        ----------
        object_ids : `list` [`str`]
            The ids to load.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The matching stars, in no particular order.
        """
        return self.catalog_access.get_by_ids("stellar_catalog", object_ids)

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

    # find_deep_stars/get_deep_catalog_status used to live here, reading the
    # downloaded Gaia deep-star catalog. That catalog exists only to feed the
    # Planetarium's faint-star layer, a wayfindinglib-owned display -- both
    # provisioning it (wayfindinglib.drivers.catalog.deep_star_catalog_builder)
    # and querying it (wayfindinglib.drivers.catalog.deep_star_store, via
    # LocalDeepStarStore) now live there instead of being reached through this
    # facade.

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
        from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

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

    def query(
        self,
        ids: list[str] | None = None,
        name: str | None = None,
        target_id: str | None = None,
        ra: float | None = None,
        dec: float | None = None,
        radius_deg: float | None = None,
        tolerance_arcsec: float | None = None,
        magnitude_min: float | None = None,
        magnitude_max: float | None = None,
        has_spectra: bool | None = None,
        spectral_class: str | None = None,
        detail: str = "summary",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Look up stars in the library, with a hard cap on the answer.

        One front door for reading stars. Give at most one selector:
        ``ids``, ``name``, ``target_id``, a region (``ra``, ``dec`` and
        ``radius_deg``) or a position (``ra``, ``dec`` and
        ``tolerance_arcsec``). With none, it browses the whole library in id
        order. It reads only; nothing is changed.

        Parameters
        ----------
        ids : `list` [`str`], optional
            Star ids to look up.
        name : `str`, optional
            A star id or name, matched loosely.
        target_id : `str`, optional
            Stars that belong to this target.
        ra : `float`, optional
            Right ascension of the centre, in degrees.
        dec : `float`, optional
            Declination of the centre, in degrees.
        radius_deg : `float`, optional
            Radius of a region search, in degrees, up to 5.
        tolerance_arcsec : `float`, optional
            Match radius of a position search, in arcseconds. The nearest
            star within it is returned.
        magnitude_min : `float`, optional
            Keep stars at least this magnitude (numerically). Stars with no
            magnitude are dropped when a magnitude bound is given.
        magnitude_max : `float`, optional
            Keep stars no fainter than this magnitude.
        has_spectra : `bool`, optional
            Keep only stars that do (or do not) have a recorded spectrum.
        spectral_class : `str`, optional
            Keep only stars whose catalog spectral type is this class
            (O, B, A, F, G, K, M, C or W; a full type such as ``"G2V"``
            uses its first letter).
        detail : `str`, optional
            ``"summary"`` (default): id, name, position, magnitude, spectral
            type, targets and data flags. ``"ids"``: only ids. ``"exists"``:
            which of ``ids`` are in the library. ``"analysis"`` (or its old
            name ``"full"``): what the analysis found for each star, at most
            10 -- the star's own spectral type and how well it matched, the
            absorption features and emission lines, and whether the
            brightness repeats, with no raw arrays. With ``spectral_class``
            the best-matched stars come first. ``"class_counts"``: how many
            stars each spectral class has (no selector). ``"stats"``:
            counts and coverage for the whole library (no selector).
        limit : `int`, optional
            How many stars to return. At most 2000 for ids, 500 for
            summaries and 10 for full records. Defaults to 50.
        offset : `int`, optional
            How many stars to skip, for paging. Stars are in id order.

        Returns
        -------
        answer : `dict` [`str`, `Any`]
            ``stars`` (or ``ids``, ``found``, ``stats``), ``total_matching``,
            and whether the answer was cut by the limit; or ``{"error": ...}``.
        """
        if detail not in QUERY_DETAILS:
            return {"error": f"detail must be one of: {', '.join(QUERY_DETAILS)}."}
        region_given = radius_deg is not None
        position_given = tolerance_arcsec is not None
        if region_given and position_given:
            return {"error": "Give radius_deg (a region) or tolerance_arcsec (one position), not both."}
        if (region_given or position_given) and (ra is None or dec is None):
            return {"error": "A region or position search needs ra and dec."}
        selectors = [
            label
            for label, given in (
                ("ids", ids is not None),
                ("name", name is not None),
                ("target_id", target_id is not None),
                ("region", region_given),
                ("position", position_given),
            )
            if given
        ]
        if len(selectors) > 1:
            return {"error": f"Give one selector, not several: {', '.join(selectors)}."}
        if detail == "stats":
            return {"stats": self.get_audit()}
        if detail == "class_counts":
            return {"classes": self.spectral_class_counts()}
        if spectral_class is not None and not spectral_class_letter(spectral_class):
            return {"error": f"spectral_class must start with one of: {', '.join(SPECTRAL_CLASS_LABELS)}."}
        if detail == "exists":
            if ids is None:
                return {"error": "detail='exists' needs ids."}
            if len(ids) > QUERY_LIMITS["ids"]:
                return {"error": f"At most {QUERY_LIMITS['ids']} ids per call."}
            return {"found": sorted(self.existing_ids(ids))}
        if region_given and not 0 < radius_deg <= QUERY_MAXIMUM_RADIUS_DEGREES:
            return {"error": f"radius_deg must be above 0 and at most {QUERY_MAXIMUM_RADIUS_DEGREES}."}

        if detail == "full":
            detail = "analysis"
        limit = max(1, min(int(limit), QUERY_LIMITS["ids" if detail == "ids" else detail]))
        offset = max(0, int(offset))
        magnitude_range = None
        if magnitude_min is not None or magnitude_max is not None:
            magnitude_range = (
                magnitude_min if magnitude_min is not None else -30.0,
                magnitude_max if magnitude_max is not None else 60.0,
            )

        summaries = self._query_summaries(
            selectors[0] if selectors else None,
            ids,
            name,
            target_id,
            ra,
            dec,
            radius_deg,
            tolerance_arcsec,
            magnitude_range,
        )
        if summaries is None:
            return {"stars": [], "total_matching": 0, "truncated": False}
        if has_spectra is not None:
            summaries = [item for item in summaries if bool(item["hasSpectra"]) is has_spectra]
        if spectral_class is not None:
            wanted = spectral_class_letter(spectral_class)
            summaries = [
                item for item in summaries if spectral_class_letter(item["spectralType"] or "") == wanted
            ]
        summaries.sort(key=lambda item: item["id"])
        total = len(summaries)
        if detail == "analysis" and spectral_class is not None:
            summaries = self._best_matched_first(summaries)
        page = summaries[offset : offset + limit]
        answer: dict[str, Any] = {
            "total_matching": total,
            "offset": offset,
            "truncated": offset + limit < total,
        }
        if detail == "ids":
            answer["ids"] = [item["id"] for item in page]
        elif detail == "summary":
            answer["stars"] = page
        else:
            answer["stars"] = [
                summarize_star(star) for star in self.list_objects_by_ids([item["id"] for item in page])
            ]
        return answer

    def spectral_class_counts(self) -> list[dict[str, Any]]:
        """Count the library's stars by catalog spectral class.

        Returns
        -------
        classes : `list` [`dict`]
            One entry per class present, in class order, with the letter, a
            short label and the count. Stars with no catalog type are not
            counted.
        """
        counts: dict[str, int] = {}
        for row in self.list_object_summaries(apply_default_limit=False):
            letter = spectral_class_letter(row["spectralType"] or "")
            if letter:
                counts[letter] = counts.get(letter, 0) + 1
        return [
            {"spectralClass": letter, "label": SPECTRAL_CLASS_LABELS[letter], "count": counts[letter]}
            for letter in SPECTRAL_CLASS_LABELS
            if letter in counts
        ]

    def _best_matched_first(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Put stars whose own spectrum matched best first.

        Only stars that have a spectrum are measured; the rest keep their
        order at the end. Reading a spectrum record is cheap next to
        building a reply, but it is skipped for a long list.

        Parameters
        ----------
        rows : `list` [`dict`]
            Summary rows of one class.

        Returns
        -------
        rows : `list` [`dict`]
            The same rows, matched stars first, by how well their own
            spectrum matched a reference (lowest difference first).
        """
        with_spectra = [row for row in rows if row["hasSpectra"]][:MAXIMUM_MATCH_RANKING]
        scored = {}
        for star in self.list_objects_by_ids([row["id"] for row in with_spectra]):
            rms = star.spectroscopy.self_determined_spectral_type_rms if star.spectroscopy else None
            if rms is not None:
                scored[star.id] = rms
        return sorted(rows, key=lambda row: (row["id"] not in scored, scored.get(row["id"], 0.0)))

    def _query_summaries(
        self,
        selector: str | None,
        ids: list[str] | None,
        name: str | None,
        target_id: str | None,
        ra: float | None,
        dec: float | None,
        radius_deg: float | None,
        tolerance_arcsec: float | None,
        magnitude_range: tuple[float, float] | None,
    ) -> list[dict[str, Any]] | None:
        """Find the stars a selector names, as summary rows.

        Parameters
        ----------
        selector : `str` or `None`
            Which selector was given, or `None` to browse.
        ids, name, target_id, ra, dec, radius_deg, tolerance_arcsec : optional
            The selector values, as for `query`.
        magnitude_range : `tuple` [`float`, `float`] or `None`
            Magnitude bounds, applied to every selector.

        Returns
        -------
        summaries : `list` [`dict`] or `None`
            One row per star, or `None` when a position search finds nothing.
        """
        if selector == "region":
            rows = self.list_object_summaries_in_region(ra, dec, radius_deg, magnitude_range)
            return rows
        if selector == "ids":
            stars = self.list_objects_by_ids(list(ids)[: QUERY_LIMITS["summary"]])
        elif selector == "name":
            stars = self.find_all_by_id_or_name(name)
        elif selector == "position":
            star = self.find_by_position(ra, dec, tolerance_arcsec)
            stars = [star] if star is not None else []
        elif selector == "target_id":
            rows = self.list_object_summaries(target_id=target_id)
            return self._within_magnitudes(rows, magnitude_range)
        else:
            rows = self.list_object_summaries(apply_default_limit=False)
            return self._within_magnitudes(rows, magnitude_range)
        rows = [self._summary_row(star) for star in stars]
        return self._within_magnitudes(rows, magnitude_range)

    @staticmethod
    def _within_magnitudes(
        rows: list[dict[str, Any]], magnitude_range: tuple[float, float] | None
    ) -> list[dict[str, Any]]:
        """Keep the rows inside a magnitude range.

        Parameters
        ----------
        rows : `list` [`dict`]
            Summary rows.
        magnitude_range : `tuple` [`float`, `float`] or `None`
            Lowest and highest magnitude, or `None` to keep every row.

        Returns
        -------
        rows : `list` [`dict`]
            The rows in range. A row with no magnitude is dropped when a
            range is given.
        """
        if magnitude_range is None:
            return rows
        low, high = magnitude_range
        return [row for row in rows if row["magnitude"] is not None and low <= row["magnitude"] <= high]

    @staticmethod
    def _summary_row(star: StellarObject) -> dict[str, Any]:
        """Describe one star as a summary row.

        Parameters
        ----------
        star : `StellarObject`
            The star.

        Returns
        -------
        row : `dict` [`str`, `Any`]
            The same keys `list_object_summaries` gives.
        """
        return {
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
        # None means "not yet known"; 0.0 is a legitimate measured
        # magnitude (see StellarObject.magnitude), so only None is excluded.
        with_magnitude = len([s for s in summaries if s.magnitude is not None])

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
        from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector

        return SourceDetector(threshold_sigma=threshold_sigma, fwhm=fwhm).detect(image_data)
