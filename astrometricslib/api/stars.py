"""Layer-1 domain high-level interface for the stellar domain.

`StellarCatalog` is the entry point external callers -- scripts,
backend services -- should use for stellar-object analysis and
spectroscopy calibration; callers should never import
`astrometricslib.tasks.stellar_tasks` or `self.butler`'s underlying
`data_access` module directly. Most methods delegate to
`tasks.stellar_tasks.analysis_operations`; `delete`/`update`/`create`/
`save_all` instead call `self.butler` directly for their lock-guarded
persistence -- both are Layer 1 reaching into a lower layer, which is
fine (see `astrometricslib.api`'s module docstring); the split is a
historical artifact of these four methods predating the delegation
convention, not a designed distinction.
"""

import logging
from typing import Any

from astrometricslib.data_access.butler import AbstractButler
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.utilities.config_loader import AppConfiguration

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "StellarCatalog",
]

logger = logging.getLogger(__name__)


class StellarCatalog:
    """Synchronous analysis operations for spectral/variability pipelines.

    This class manages 'Stellar Objects', which are the individual
    stars and point sources extracted from your images. While the
    TargetCatalog deals with the whole picture, this catalog tracks
    the properties of specific stars over time—like their brightness
    (photometry) or chemical composition (spectroscopy)—enabling
    deeper scientific analysis.
    """

    def __init__(self, config: AppConfiguration | None = None, butler: AbstractButler | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with application configuration and a butler.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            Application configuration. Loaded from the application
            configuration when omitted.
        butler : `AbstractButler`, optional
            Storage backend for the stellar catalog. A `DiskButler`
            over `config` is constructed when omitted.
        """
        if config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            config = get_configuration()
        self._config = config
        if butler is None:
            from astrometricslib.data_access.butler import DiskButler

            butler = DiskButler(config)
        self.butler = butler

    def list_objects(self) -> list[StellarObject]:
        """List all stellar objects extracted across the library.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            All stellar objects currently in the library.
        """
        from astrometricslib.tasks.stellar_tasks import analysis_operations

        return analysis_operations.list_objects(self)

    def list_object_summaries(self, target_id: str | None = None) -> list[dict[str, Any]]:
        """Lightweight per-star summaries for catalog-browsing callers.

        Prefer `list_objects` for anything that needs a real, complete
        `StellarObject` -- this exists for callers like a UI catalog
        listing that only read id/name/targetIds/hasSpectra/
        hasPhotometry, want the fetch itself to be cheap, and are
        likely to be polled repeatedly. See
        `disk_interface.load_stellar_object_summaries` for why that
        matters at scale.

        Returns
        -------
        summaries : `list` [`dict`]
            One dict per star with keys ``id``, ``name``, ``targetIds``,
            ``hasSpectra``, and ``hasPhotometry``, optionally filtered
            by ``target_id``.
        """
        from astrometricslib.drivers import disk_interface

        return disk_interface.load_stellar_object_summaries(self._config, target_id)

    def get_object(self, object_id: str) -> StellarObject | None:
        """Get a single stellar object by ID using exact/fuzzy matching.

        Parameters
        ----------
        object_id : `str`
            The id to look up, exact or fuzzy-matched.

        Returns
        -------
        stellar_object : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.
        """
        from astrometricslib.tasks.stellar_tasks import analysis_operations

        return analysis_operations.get_object(self, object_id)

    def tune_spectroscopy_calibration(
        self,
        image_path: str,
        camera_name: str | None = None,
        star_x: float | None = None,
        star_y: float | None = None,
    ) -> dict[str, Any]:
        """Run the autonomous physical-model spectroscopy calibration tuner.

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
        from astrometricslib.tasks.stellar_tasks import analysis_operations

        return analysis_operations.tune_spectroscopy_calibration(
            self, image_path, camera_name, star_x, star_y
        )

    def delete(self, object_id: str) -> bool:
        """Delete a stellar object by ID.

        Uses a lock-guarded, targeted delete so a concurrent
        create/update on a different object never races against this
        removal.

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
        self.butler.delete_by_ids("stellar_catalog", [existing.id])
        return True

    def update(self, object_id: str, updates: dict[str, Any]) -> StellarObject | None:
        """Update a stellar object by ID.

        Uses a lock-guarded, targeted merge so a concurrent
        create/update/delete on a different object never races
        against this write.

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

        self.butler.merge_and_persist_records("stellar_catalog", [existing], _apply_updates)
        return self.get_object(object_id)

    def create(
        self,
        object_id: str,
        ra: str | None = None,
        dec: str | None = None,
    ) -> StellarObject:
        """Create a stellar object.

        Uses a lock-guarded, targeted merge so a concurrent
        create/update/delete on a different object never races
        against this write.

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

        self.butler.merge_and_persist_records(
            "stellar_catalog", [new_obj], lambda current, updated: current if current is not None else updated
        )
        return new_obj

    def get_audit(self) -> dict[str, Any]:
        """Summarize database metrics for spectral and variability records.

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
        """Persist stellar library records, replacing the whole table.

        This is a *replace-all* write: any stellar object not present in
        `objects` is deleted. Only call it from a caller that genuinely
        holds the entire catalog; for partial updates use
        `butler.merge_and_persist_records` or `delete` instead.

        Parameters
        ----------
        objects : `list` [`StellarObject`]
            The full set of stellar objects to persist.
        allow_empty : `bool`, optional
            Whether an empty `objects` list may wipe the entire catalog
            (default `False`). Guarded because the underlying
            `put_all` runs an unconditional ``DELETE FROM
            stellar_objects`` when handed an empty list -- so a caller
            that simply hadn't loaded the catalog yet (the backend's
            `stellar_service.save_stellar_objects` passes whatever
            `get_stellar_objects()` returns) would silently destroy
            every row. Pass `True` only to deliberately clear it.

        Returns
        -------
        result : `str`
            Status message describing the persisted write.

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
        # `coordinate` is required by the AbstractButler.put signature;
        # omitting it previously made every call raise TypeError.
        self.butler.put(objects, "stellar_catalog", {})
        return "stellar catalog saved"

    def detect_point_sources(
        self,
        image_data: Any,
        threshold_sigma: float = 5.0,
        fwhm: float = 4.0,
    ) -> list[dict[str, Any]]:
        """Detect point sources in already-loaded image data.

        A thin pass-through to `SourceDetector`, exposed here so
        scripts detect sources through the Layer-1 astrometrics rather than
        importing `astrometricslib.tasks.shared.source_detection_shared`
        directly.

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
        from astrometricslib.tasks.shared.source_detection_shared import SourceDetector

        return SourceDetector(threshold_sigma=threshold_sigma, fwhm=fwhm).detect(image_data)
