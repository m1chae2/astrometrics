"""Thin service adapter managing target metadata and catalog sync.

Delegates all core astronomical and storage operations directly to the
Astrometrics library. # REQ: BKD-5: Data Persistence
"""

import logging
import os
from typing import Any, ClassVar

from astrometricslib import FilterType, Target
from backend.services.data.image_service import ImageService

logger = logging.getLogger(__name__)


class TargetService:
    """Thin service layer orchestrating Target CRUD and catalog queries.

    Strictly delegates all business logic and FITS directory scanning to
    astrolib high-level interfaces. # REQ: BKD-5
    """

    def __init__(self, config: Any, astrometrics: Any = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize TargetService.

        Wires it to the high-level interface library high-level interface.

        Parameters
        ----------
        config : `Any`
            Application configuration instance providing datastore paths.
        astrometrics : `Any`, optional
            The Astrometrics facade. Injected for testability or loaded
            on-demand if omitted.
        """
        self.config = config
        if astrometrics is None:
            from astrometricslib import Astrometrics

            self.astrometrics = Astrometrics(config)
        else:
            self.astrometrics = astrometrics
        self.image_service = ImageService()

    def get_all_targets_list(self) -> list[dict[str, Any]]:
        """Return a summarized catalog list of all registered targets.

        Each entry contains the target ID and its image paths.

        Returns
        -------
        result : `list` of `dict`
            Summarized entries with ``id``, ``processed_image``, and
            ``stacked_image`` keys.
        """
        targets = self.get_targets()
        return [
            {
                "id": target.id,
                "processed_image": target.processed_image,
                "stacked_image": target.stacked_image,
            }
            for target in targets
        ]

    def discover_all_targets(self) -> list[str]:
        """Walk the filesystem lights directory.

        Lists potential physical target folder matches.

        Returns
        -------
        result : `list` of `str`
            Sorted candidate target folder names.
        """
        lights_path = os.path.join(self.config.get_frames_path(), "lights")
        if not os.path.exists(lights_path):
            return []

        folders = [d for d in os.listdir(lights_path) if os.path.isdir(os.path.join(lights_path, d))]
        return sorted([f for f in folders if f != "test_write"])

    def get_targets(self, target_id: str | None = None) -> Any:
        """Unified targets getter.

        Delegates directly to the high-level interface.

        Returns
        -------
        result : `Any`
            Target(s) matching ``target_id``, or all targets if `None`.
        """
        if target_id:
            return self.astrometrics.targets.get(target_id)
        return self.astrometrics.targets.list()

    def get_target(self, target_id: str) -> Target | None:
        """Query a specific target by identifier.

        Deprecated: use get_targets instead.

        Returns
        -------
        result : `Target` or `None`
            The matching target, or `None` if not found.
        """
        return self.get_targets(target_id)

    def load_targets(self, refresh_images: bool = False) -> None:
        """No-op retained for backward compatibility.

        Preloading is not required; this service is stateless.
        """
        pass

    def create_target(
        self,
        target_or_id: Any = None,
        ra: str | None = None,
        dec: str | None = None,
        target_id: str | None = None,
    ) -> Target:
        """Generate a new target, scan physical frames, and record it.

        Accepts either a target_id (str) or a pre-instantiated Target
        object. # REQ: BKD-5.2

        Returns
        -------
        result : `Target`
            The created (or added) target.

        Raises
        ------
        ValueError
            If neither ``target_or_id`` nor ``target_id`` is provided.
        """
        val = target_or_id if target_or_id is not None else target_id
        if val is None:
            raise ValueError("Required parameter 'target_or_id' or 'target_id' is missing")
        if isinstance(val, str):
            target = self.astrometrics.targets.create(val)
            if ra is not None:
                target.ra = ra
            if dec is not None:
                target.dec = dec
            self.astrometrics.targets.save()
            return target
        else:
            self.astrometrics.targets.add(target_or_id)
            return target_or_id

    PROCESSED_IMAGE_EXTENSIONS: ClassVar[set[str]] = {".jpg", ".jpeg", ".png", ".tiff", ".tif"}

    def add_target_data(self, target_id: str, image_file: Any, camera: str | None = None) -> dict[str, Any]:
        """Link captured frame files or scaling outputs to a target.

        FITS files (``.fits``/``.fit``) are indexed as light frames,
        subject to the spectral/standard homogeneity check. Image files
        (``.jpg``/``.jpeg``/``.png``/``.tiff``/``.tif``) are treated as
        a finished processed image and set directly on the target,
        bypassing frame indexing entirely.

        Returns
        -------
        result : `dict`
            Status dictionary with ``status`` (and ``frame`` on success,
            or ``message`` on failure).
        """
        target = self.astrometrics.targets.get(target_id)
        if not target:
            target = self.astrometrics.targets.create(target_id)

        path = image_file if isinstance(image_file, str) else getattr(image_file, "path", None)
        if not isinstance(path, str):
            return {"status": "error", "message": "Invalid target data input."}

        if os.path.splitext(path)[1].lower() in self.PROCESSED_IMAGE_EXTENSIONS:
            target.processed_image = path
            self.astrometrics.targets.save()
            return {"status": "success"}

        frame = self.astrometrics.targets.add_frame(target, path, camera=camera or "Unknown")
        self.astrometrics.targets.save()
        return {"status": "success", "frame": frame}

    def refresh_target_images(self, target: Target, prune_missing: bool = False) -> None:
        """Force a library rescan on the FITS directory catalog.

        Refreshes the target's frame entries.
        """
        try:
            self.astrometrics.targets.reindex_frames(target, prune_missing=prune_missing)
            self.astrometrics.targets.save()
        except Exception as e:
            logger.warning(f"Failed to refresh images for {target.id}: {e}")

    def update_target(self, target_id: str, updates: dict) -> Target | None:
        """Update specific attributes on a target by ID.

        Returns
        -------
        result : `Target` or `None`
            The updated target, or `None` if not found.
        """
        target = self.astrometrics.targets.get(target_id)
        if not target:
            return None
        for k, v in updates.items():
            if hasattr(target, k):
                setattr(target, k, v)
        self.astrometrics.targets.save()
        return target

    def delete_target(self, target_id: str) -> bool:
        """Purges a target record completely.

        Removes it from database indices and catalog arrays.

        Returns
        -------
        result : `bool`
            `True` if the target was deleted, `False` otherwise.
        """
        return self.astrometrics.targets.delete(target_id)

    def get_frame_stats(self, target_id: str) -> dict[str, list[dict[str, Any]]]:
        """Provide aggregated exposure statistics.

        Totals and counts are grouped by lens/filter specs.

        Returns
        -------
        result : `dict`
            Mapping of grouping keys to lists of statistics dicts;
            ``{"lights": []}`` if the target is not found.
        """
        target = self.astrometrics.targets.get(target_id)
        if not target:
            return {"lights": []}
        return self.astrometrics.targets.get_calibration_frame_statistics(
            target, target.frames, grouped=False
        )

    def get_file_list(self, target_id: str) -> dict:
        """Return a formatted catalog file list with all frame details.

        Returns
        -------
        result : `dict`
            Dictionary with ``files``, ``stackedImage``,
            ``stackedSpectralTarget``, and ``totalExposure`` keys.
        """
        target = self.get_targets(target_id)
        if not target:
            return {"files": [], "stackedImage": None, "stackedSpectralTarget": None, "totalExposure": 0}

        files = []
        for frame in target.frames:
            filter_str = frame.filter.name if frame.filter != FilterType.NONE else "None"
            files.append({
                "path": frame.path,
                "name": os.path.basename(frame.path),
                "camera": frame.camera,
                "iso": frame.iso,
                "exposure": frame.exposure,
                "filter": filter_str,
                "date": frame.date,
            })

        return {
            "files": files,
            "stackedImage": target.stacked_image or None,
            "stackedSpectralTarget": target.stacked_spectral_target or None,
            "totalExposure": target.exposure_sec,
        }

    def get_frame_stats_grouped(self, target_id: str, camera: str | None = None) -> list:
        """Calculate exposure specs grouped by filter, with calibration status.

        Returns
        -------
        result : `list`
            Grouped exposure statistics, empty if the target is not
            found.
        """
        target = self.astrometrics.targets.get(target_id)
        if not target:
            return []
        return self.astrometrics.targets.get_calibration_frame_statistics(
            target, target.frames, grouped=True, camera=camera
        )

    def save_target(self, target: Target) -> None:
        """Save target database states.

        Uses the library's storage controllers.
        """
        try:
            self.astrometrics.targets.save()
        except Exception as e:
            logger.error(f"Failed to save target {target.id}: {e}")
            raise e

    def save_targets(self) -> None:
        """Commit all active targets catalog arrays.

        Writes them to library storage files.
        """
        try:
            self.astrometrics.targets.save()
        except Exception as e:
            logger.error(f"Failed to save targets: {e}")
            raise e

    def get_frame_header(self, target_id: str, frame_path: str) -> list[dict[str, str]]:
        """Read full FITS headers for a specified frame record.

        Scoped to the target index.

        Returns
        -------
        result : `list` of `dict` of `str`
            FITS header key/value pairs for the specified frame.

        Raises
        ------
        ValueError
            If no target matches ``target_id``.
        """
        target = self.astrometrics.targets.get(target_id)
        if not target:
            raise ValueError(f"Target not found: {target_id}")
        return self.astrometrics.targets.get_header(frame_path, target=target)

    def refresh_target_images_by_id(self, target_id: str, prune_missing: bool = False) -> None:
        """RPC wrapper to trigger frame scans on a target.

        The target is identified by its ID string.
        """
        logger.info(
            f"Starting single-target reindex for target '{target_id}' (prune_missing={prune_missing})"
        )
        target = self.astrometrics.targets.get(target_id)
        if target:
            old_count = len(target.frames)
            self.astrometrics.targets.reindex_frames(target, prune_missing=prune_missing)
            self.astrometrics.targets.save()
            new_count = len(target.frames)
            logger.info(
                f"Reindex complete for target '{target_id}'. Frames before: {old_count}, after: {new_count}"
            )

    def get_planetarium_targets(self) -> list[dict]:
        """Return all targets with coordinates and image paths for the sky.

        Delegates serialization to the shared
        _serialize_target_for_planetarium helper, which applies the
        canonical coordinate parser and longest-exposure frame fallback.

        Returns
        -------
        result : `list` of `dict`
            Serialized planetarium payloads for targets with valid
            coordinates.

        REQ: PLN-2.2
        """
        from backend.services.data.stellar_service import _serialize_target_for_planetarium

        targets = []
        for target in self.get_targets():
            if not target.ra or not target.dec:
                continue
            result = _serialize_target_for_planetarium(target)
            if result:
                targets.append(result)
        return targets
