"""Thin service adapter managing target metadata and catalog sync.

Delegates all core astronomical and storage operations directly to the
Astrometrics library. # REQ: BKD-5: Data Persistence
"""

import logging
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from astrometricslib import (
    AstrometricsError,
    FilterType,
    InvalidArgumentError,
    NotFoundError,
    ReindexReport,
    Target,
    preview_path_for,
)
from backend.services.data.deletion_archive import archive_record_before_delete
from backend.services.data.image_service import ImageService
from backend.services.infrastructure.phone_share import send_file_to_phone

logger = logging.getLogger(__name__)

PICTURE_SUFFIXES = (".jpg", ".jpeg")
"""File endings of a picture that can be sent as it is."""


class TargetService:
    """Thin service layer orchestrating Target CRUD and catalog queries.

    Strictly delegates all business logic and FITS directory scanning to
    astrolib high-level interfaces. # REQ: BKD-5
    """

    def __init__(self, config: Any, astrometrics: Any = None) -> None:
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
        self.image_service = ImageService(target_service=self)

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
                "processed_image": target.stacking.processed_image,
                "stacked_image": target.stacking.stacked_image,
            }
            for target in targets
        ]

    def reindex_library(
        self, prune_missing: bool = False, on_progress: Callable[[int, int, str], None] | None = None
    ) -> ReindexReport:
        """Bring every target's frame list up to date with the frames folder.

        The library scans each target folder under the frames folder's
        ``lights`` folder, creates a target for a folder that has none, and
        saves each target as it finishes.

        Parameters
        ----------
        prune_missing : `bool`, optional
            Also drop records of files that no longer exist.
        on_progress : `Callable`, optional
            Called as ``(index, total, target_id)`` before each target.

        Returns
        -------
        report : `ReindexReport`
            Each target's frame count before and after.
        """
        return self.astrometrics.targets.reindex_frames(prune_missing=prune_missing, on_progress=on_progress)

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

    def get_camera_index(self) -> dict[str, Any]:
        """Summarize which camera took each target's frames, and when.

        Returns
        -------
        index : `dict`
            The configured cameras with how many targets each imaged, and
            per target the newest light-frame time and per-camera frame
            counts. See `build_target_camera_index`.
        """
        return self.astrometrics.targets.query(detail="camera_index").camera_index

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
        InvalidArgumentError
            If neither ``target_or_id`` nor ``target_id`` is provided.
        """
        val = target_or_id if target_or_id is not None else target_id
        if val is None:
            raise InvalidArgumentError("Required parameter 'target_or_id' or 'target_id' is missing")
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

    def add_target_data(self, target_id: str, image_file: Any, camera: str | None = None) -> dict[str, Any]:
        """Link a captured frame file or a finished picture to a target.

        The library decides what the file is. A FITS file is added as a
        light frame. A finished picture (such as a ``.jpg`` or ``.png``)
        becomes the target's processed image. A target that does not exist
        yet is created first.

        Returns
        -------
        result : `dict`
            ``{"status": "success"}``, plus ``frame`` when a FITS file
            was added.

        Raises
        ------
        InvalidArgumentError
            If ``image_file`` is neither a path nor an object with a
            ``path`` attribute.
        """
        path = image_file if isinstance(image_file, str) else getattr(image_file, "path", None)
        if not isinstance(path, str):
            raise InvalidArgumentError(
                "The image file must be a file path.", details={"target_id": target_id}
            )

        target = self.astrometrics.targets.get(target_id)
        if not target:
            target = self.astrometrics.targets.create(target_id)

        report = self.astrometrics.targets.reindex_frames(target, paths=[path], camera_id=camera or "Unknown")
        if report.processed_image is not None:
            return {"status": "success"}
        frame = next((frame for frame in target.frames if frame.path == path), None)
        return {"status": "success", "frame": frame}

    def send_to_phone(self, target_id: str) -> dict[str, str]:
        """Send a target's JPEG picture to the user's phone.

        The picture is the stack's JPEG preview, the same picture the Image
        Viewer shows as a stretched FITS file. See
        `send_file_to_phone` for how it reaches the phone and what it raises
        when no phone link works.

        Parameters
        ----------
        target_id : `str`
            The target whose picture to send, such as ``"M 57"``.

        Returns
        -------
        result : `dict`
            ``path`` (the file sent), ``method`` (``"gsconnect"`` or
            ``"localsend"``) and, for GSConnect, the phone's ``device`` name.

        Raises
        ------
        NotFoundError
            If the target does not exist, has no stack, or its JPEG picture
            is missing from the disk.
        """
        target = self.astrometrics.targets.get(target_id)
        if not target:
            raise NotFoundError(f"No target named {target_id!r}.")
        picture = target.stacking.processed_image
        if picture and picture.lower().endswith(PICTURE_SUFFIXES):
            path = picture
        elif target.stacking.stacked_image:
            # The viewer shows the stretched FITS. The stack's JPEG sits
            # beside the stack under a name the library decides.
            path = preview_path_for(target.stacking.stacked_image)
        else:
            raise NotFoundError(f"{target_id} has no stack to send a picture of.")
        if not os.path.isfile(path):
            raise NotFoundError(f"The JPEG picture for {target_id} is missing.", details={"path": path})
        return {"path": path, **send_file_to_phone(path)}

    def refresh_target_images(self, target: Target, prune_missing: bool = False) -> None:
        """Force a library rescan on the FITS directory catalog.

        Refreshes the target's frame entries.
        """
        try:
            self.astrometrics.targets.reindex_frames(target, prune_missing=prune_missing)
        except (AstrometricsError, sqlite3.Error, OSError) as e:
            logger.warning("Failed to refresh images for %s: %s", target.id, e)

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
        target = self.astrometrics.targets.get(target_id)
        if target is not None:
            archive_record_before_delete(
                self.config.get_library_path(), "target", target.id, target.serialize()
            )
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
        lights = self.astrometrics.processing.calibration.query(detail="target_frames", target=target).lights
        return {"lights": lights or []}

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
            "stackedImage": target.stacking.stacked_image or None,
            "stackedSpectralTarget": target.spectral_stacking.stacked_image or None,
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
        answer = self.astrometrics.processing.calibration.query(
            detail="target_match", target=target, camera_id=camera
        )
        return answer.groups or []

    def save_target(self, target: Target) -> None:
        """Save target database states.

        Uses the library's storage controllers.

        Raises
        ------
        AstrometricsError
            Re-raised after logging if the library refuses the save.
        sqlite3.Error
            Re-raised after logging if the database write fails.
        OSError
            Re-raised after logging if a file cannot be written.
        """
        try:
            self.astrometrics.targets.save()
        except AstrometricsError, sqlite3.Error, OSError:
            logger.exception("Failed to save target %s", target.id)
            raise

    def read_saved_target(self, target_id: str) -> Target | None:
        """Read one target's saved record straight from storage.

        Memory is not used, so this shows what another program would see
        after a save.

        Returns
        -------
        target : `Target` or `None`
            The stored target, or `None` if nothing is stored under that id.
        """
        return self.astrometrics.targets.read_saved(target_id)

    def save_targets(self) -> None:
        """Commit all active targets catalog arrays.

        Writes them to library storage files.

        Raises
        ------
        AstrometricsError
            Re-raised after logging if the library refuses the save.
        sqlite3.Error
            Re-raised after logging if the database write fails.
        OSError
            Re-raised after logging if a file cannot be written.
        """
        try:
            self.astrometrics.targets.save()
        except AstrometricsError, sqlite3.Error, OSError:
            logger.exception("Failed to save targets")
            raise

    def get_frame_header(self, target_id: str, frame_path: str) -> list[dict[str, str]]:
        """Read full FITS headers for a specified frame record.

        Scoped to the target index.

        Returns
        -------
        result : `list` of `dict` of `str`
            FITS header key/value pairs for the specified frame. Empty when
            the file is no longer on disk: a stack that was moved or deleted
            is a normal state for a target record, not a server error (a
            server error would be logged and shown as a failed request every
            time the target is opened).

        Raises
        ------
        NotFoundError
            If no target matches ``target_id``.
        """
        target = self.astrometrics.targets.get(target_id)
        if not target:
            raise NotFoundError(f"Target not found: {target_id}")
        try:
            return self.astrometrics.targets.get_header(frame_path, target=target)
        except NotFoundError:
            logger.debug("Frame file for %s is missing, returning an empty header: %s", target_id, frame_path)
            return []

    def refresh_target_images_by_id(self, target_id: str, prune_missing: bool = False) -> None:
        """RPC wrapper to trigger frame scans on a target.

        The target is identified by its ID string.
        """
        logger.info(
            "Starting single-target reindex for target '%s' (prune_missing=%s)", target_id, prune_missing
        )
        target = self.astrometrics.targets.get(target_id)
        if target:
            old_count = len(target.frames)
            self.astrometrics.targets.reindex_frames(target, prune_missing=prune_missing)
            new_count = len(target.frames)
            logger.info(
                "Reindex complete for target '%s'. Frames before: %s, after: %s",
                target_id,
                old_count,
                new_count,
            )
