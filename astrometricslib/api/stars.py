"""Main interface for managing and analyzing individual stars.

This module provides the `StellarCatalog`, which is the primary tool for
working with specific stars found in your images. You can use it to track
a star's brightness over time, analyze its spectrum, and manage its records
in the database.
"""

import logging
from typing import Any

from astrometricslib.data_access.catalog_access import AbstractCatalogAccess
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
# target filter" case: without a cap, a UI listing polling that RPC
# hydrates and transmits the whole catalog's summaries every request --
# at 270,450 rows, real network and JSON-parse cost even after
# list_projected already skipped hydrating full StellarObjects. A
# caller wanting the true, unbounded catalog for scripting should use
# list_objects() instead; this cap only applies to the summary path
# documented for UI catalog-browsing callers.
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
            Storage backend for the stellar catalog. A `CatalogAccess`
            over `config` is constructed when omitted.
        """
        if config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            config = get_configuration()
        self._config = config
        if catalog_access is None:
            from astrometricslib.data_access.catalog_access import CatalogAccess

            catalog_access = CatalogAccess(config)
        self.catalog_access = catalog_access

    def list_objects(self) -> list[StellarObject]:
        """List all stellar objects extracted across the library.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            All stellar objects currently in the library.
        """
        from astrometricslib.api import stellar_operations

        return stellar_operations.list_objects(self)

    def list_object_summaries(
        self, target_id: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Get a quick, lightweight summary of stars in the catalog.

        If you need all the detailed data for a star, use `list_objects`
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
            star count. Pass an explicit value (or `0` for
            `CatalogAccess.list_projected`'s own no-limit-argument behavior
            -- i.e. omit `limit` from the call) to override either
            default.

        Returns
        -------
        summaries : `list` [`dict`]
            One dict per star with keys ``id``, ``name``, ``ra``,
            ``dec``, ``targetIds``, ``hasSpectra``, and
            ``hasPhotometry``, optionally filtered by ``target_id``.
        """
        effective_limit = limit
        if effective_limit is None and not target_id:
            effective_limit = DEFAULT_UNFILTERED_SUMMARY_LIMIT

        rows = self.catalog_access.list_projected(
            "stellar_catalog",
            ["id", "name", "ra", "dec", "target_id", "has_spectra", "has_photometry"],
            like={"target_id": target_id} if target_id else None,
            limit=effective_limit,
        )
        summaries = []
        for row in rows:
            target_ids = row["target_id"].split(",") if row["target_id"] else []
            if target_id and target_id not in target_ids:
                continue
            summaries.append({
                "id": row["id"],
                "name": row["name"],
                "ra": row["ra"],
                "dec": row["dec"],
                "targetIds": target_ids,
                "hasSpectra": bool(row["has_spectra"]),
                "hasPhotometry": bool(row["has_photometry"]),
            })
        return summaries

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
        """Get a summary of how much data we have in the stellar catalog.

        Returns
        -------
        audit : `dict`
            Counts and coverage percentages for identified, spectral,
            and photometric records.
        """
        stellar_objects = self.list_objects()
        total = len(stellar_objects)
        with_names = len([o for o in stellar_objects if o.name and "Star_" not in o.id])
        with_spectral = len([o for o in stellar_objects if o.spectral_type and o.spectral_type != "Unknown"])
        with_magnitude = len([o for o in stellar_objects if o.magnitude not in (None, "", 0.0)])

        return {
            "total_objects": total,
            "identified_objects": with_names,
            "spectral_coverage": round((with_spectral / total * 100), 2) if total > 0 else 0,
            "photometric_coverage": round((with_magnitude / total * 100), 2) if total > 0 else 0,
            "stats": {"names": with_names, "spectral": with_spectral, "magnitude": with_magnitude},
        }

    def save_all(self, objects: list[StellarObject], allow_empty: bool = False) -> str:
        """Save a complete list of stars, entirely replacing the old catalog.

        Warning: This deletes any star that isn't in the new list you provide!
        If you only want to update a few stars, use `update()` instead.

        Parameters
        ----------
        objects : `list` [`StellarObject`]
            The full set of stellar objects to record.
        allow_empty : `bool`, optional
            By default, we stop you from saving an empty list so you don't
            accidentally delete the entire catalog. Pass `True` if you
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
        from astrometricslib.image_processing.source_detection import SourceDetector

        return SourceDetector(threshold_sigma=threshold_sigma, fwhm=fwhm).detect(image_data)
