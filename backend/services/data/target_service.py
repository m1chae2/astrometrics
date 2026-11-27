"""Thin service adapter managing target metadata and catalog sync.

Delegates all core astronomical and storage operations directly to the
Astrometrics library.
"""

import logging
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from astrometricslib import FilterType, Target
from astrometricslib.drivers import existing_stack_path
from astrometricslib.exceptions import AstrometricsError, InvalidArgumentError, NotFoundError
from backend.services.data.deletion_archive import archive_record_before_delete
from backend.services.data.image_paths import resolve_allowed_image
from backend.services.data.image_service import ImageService
from backend.services.error_logging import log_failure
from backend.services.infrastructure.phone_share import send_file_to_phone
from backend.services.subject_resolver import SubjectResolver

logger = logging.getLogger(__name__)

PICTURE_SUFFIXES = (".jpg", ".jpeg")
"""File endings of a picture that can be sent as it is."""


def _without_frames(target: Target) -> Target:
    """Copy a target with an empty frame list, leaving the original alone.

    The copy is shallow: every other field is shared with the original, so
    the copy is for reading and sending only, never for saving.

    Parameters
    ----------
    target : `Target`
        The target to copy.

    Returns
    -------
    summary : `Target`
        The copy, with ``frames`` empty.
    """
    return target.model_copy(update={"frames": []})


class TargetService:
    """Thin service layer orchestrating Target CRUD and catalog queries.

    Strictly delegates all business logic and FITS directory scanning to
    astrolib high-level interfaces.
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
        self.subjects = SubjectResolver(self.astrometrics)
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
    ) -> Any:
        """Bring every target's frame list up to date with the frames folder.

        The library scans each target folder under the frames folder's
        ``lights`` folder, creates a target for a folder that has none, and
        saves each target as it finishes. It also reads again the header of
        each known frame whose file changed, or whose record was made before
        a header field (such as the ISO) was added. An unchanged, up-to-date
        frame is not opened.

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
        return self.astrometrics.targets.reindex_frames(
            prune_missing=prune_missing, refresh_headers=True, on_progress=on_progress
        )

    def get_targets(self, target_id: str | None = None, detail: str = "full") -> Any:
        """Unified targets getter.

        Delegates directly to the high-level interface.

        Parameters
        ----------
        target_id : `str`, optional
            The target to return. All targets when omitted.
        detail : `str`, optional
            ``"full"`` (default) returns each target with all its frame
            records. ``"summary"`` returns each target with an empty frame
            list: everything else (id, name, object type, positions, stack
            image paths, exposure totals) is the same. A list that shows
            targets by name and picture needs only the summary, which is
            about a fifth of the size on a library of thousands of frames.

        Returns
        -------
        result : `Any`
            Target(s) matching ``target_id``, or all targets if `None`.

        Raises
        ------
        InvalidArgumentError
            If ``detail`` is neither ``"full"`` nor ``"summary"``.
        """
        if detail not in ("full", "summary"):
            raise InvalidArgumentError('detail must be "full" or "summary".', details={"detail": detail})
        if target_id:
            found = self.astrometrics.targets.get(target_id)
            return _without_frames(found) if detail == "summary" and found is not None else found
        everything = self.astrometrics.targets.query(detail="objects", limit=None).objects
        return [_without_frames(target) for target in everything] if detail == "summary" else everything

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
        object.

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
            ``path`` (the file sent), ``method`` (always ``"gsconnect"``)
            and the phone's ``device`` name.

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
            path = target.stacking.preview_path
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
        # A position typed in by a person is trusted: it is no longer a
        # guess taken from a frame center.
        if ("ra" in updates or "dec" in updates) and "position_unverified" not in updates:
            target.position_unverified = False
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
            ``stackedSpectralTarget``, ``totalExposure``,
            ``stackConfiguration`` (the camera and optic of the target's
            current stack, or `None`) and ``stacks`` (every stack by camera
            and optic whose file is on disk, the main one first) keys.
        """
        target = self.get_targets(target_id)
        if not target:
            return {
                "files": [],
                "stackedImage": None,
                "stackedSpectralTarget": None,
                "totalExposure": 0,
                "stacks": [],
            }

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
                "focalLengthMm": None if frame.focal_length_mm is None else float(frame.focal_length_mm),
            })

        return {
            "files": files,
            "stackedImage": self._existing_stack_path(target.stacking.stacked_image),
            "stackedSpectralTarget": self._existing_stack_path(target.spectral_stacking.stacked_image),
            "totalExposure": target.exposure_sec,
            "stackConfiguration": self._current_stack_configuration(target),
            "stacks": self._stack_choices(target),
        }

    def _stack_choices(self, target: Any) -> list[dict]:
        """List every stack of a target by camera and optic.

        Only stacks whose file is on disk are listed, so opening one never
        asks for a missing file. The main stack is the one the target record
        names as its stack; it comes first, then the others by name.

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        choices : `list` [`dict`]
            One entry per stack with ``configurationKey``, ``camera``,
            ``focalLengthMm`` (whole millimeters or `None`),
            ``framesStacked``, ``stackedImage`` (a path that exists) and
            ``isMain``.
        """
        main_path = self._existing_stack_path(target.stacking.stacked_image)
        choices = []
        for key, configuration in target.stacking.stacks_by_configuration.items():
            path = self._existing_stack_path(configuration.stacked_image)
            if not path:
                continue
            focal_length = configuration.focal_length_mm
            choices.append({
                "configurationKey": configuration.configuration_key or key,
                "camera": configuration.camera,
                "focalLengthMm": None if focal_length is None else round(float(focal_length)),
                "framesStacked": int(configuration.frames_stacked),
                "stackedImage": path,
                "isMain": main_path is not None and os.path.abspath(path) == os.path.abspath(main_path),
            })
        choices.sort(key=lambda choice: (not choice["isMain"], choice["configurationKey"]))
        return choices

    @staticmethod
    def _current_stack_configuration(target: Any) -> dict | None:
        """Name the camera and optic of the target's current stack.

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        configuration : `dict` or `None`
            ``camera``, ``focalLengthMm`` (whole millimeters or `None`) and
            ``label`` (the library's configuration key, such as
            ``ZWO ASI 533MM Pro@405mm``) of the preferred stack, or of the
            only stack. `None` when the target
            has no stack by configuration, or several and none preferred.
        """
        configurations = list(target.stacking.stacks_by_configuration.values())
        chosen = [item for item in configurations if item.is_preferred]
        if not chosen and len(configurations) == 1:
            chosen = configurations
        if not chosen:
            return None
        focal_length = chosen[0].focal_length_mm
        return {
            "camera": chosen[0].camera,
            "focalLengthMm": None if focal_length is None else round(float(focal_length)),
            "label": chosen[0].configuration_key,
        }

    def get_frame_statuses(self, target_id: str) -> dict:
        """Say what state each frame of a target is in, for the File Browser.

        The library decides each state and finds the newest stack file
        (`quality.frame_statuses`): ``included``, ``flagged`` (in the stack,
        but its stars are stretched: at least the warning level and kept by
        the quarantine, with a ``reason`` giving the numbers),
        ``not_stacked`` (taken after the latest stack file was written, or
        the target has no stack) or ``set_aside`` (moved into an `_excluded`
        folder).

        Parameters
        ----------
        target_id : `str`
            The target's id.

        Returns
        -------
        report : `dict`
            ``frames`` (each with ``path``, ``name``, ``state``, ``kind``,
            ``reason``, ``date``, ``exposure``, ``elongation``,
            ``originalPath``, ``restorable`` and ``restoreNote``) and
            ``stackedAt``, the time of the latest stack file in seconds since
            1970, or `None`.

        Notes
        -----
        A target that is not in the library raises the library's
        `NotFoundError`.
        """
        return self.astrometrics.quality.frame_statuses(self.subjects.target(target_id)).model_dump(
            by_alias=True
        )

    def put_back_frames(self, target_id: str, paths: list[str]) -> dict:
        """Move set-aside frames back into a target.

        Parameters
        ----------
        target_id : `str`
            The target's id.
        paths : `list` [`str`]
            The set-aside frames to move back, as ``get_frame_statuses``
            lists them.

        Returns
        -------
        result : `dict`
            ``restored``, the original paths of the frames that went back.
            A frame that could not be moved is not in the list.

        """
        restored = self.astrometrics.quality.put_back_set_aside_frames(self.subjects.target(target_id), paths)
        return {"restored": restored}

    def _existing_stack_path(self, recorded_path: str | None) -> str | None:
        """Return a recorded stack path only if the file is on disk.

        The library's rule (`astrometricslib.drivers.existing_stack_path`)
        also finds a stack that an old record names under ``lights/``.

        Parameters
        ----------
        recorded_path : `str` or `None`
            The stack path saved in the target record.

        Returns
        -------
        path : `str` or `None`
            A path that exists, or `None` if there is no such stack.
        """
        get_stacks_path = getattr(getattr(self, "config", None), "get_stacks_path", None)
        stacks_path = str(get_stacks_path()) if callable(get_stacks_path) else None
        return existing_stack_path(recorded_path, stacks_path)

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
        except (AstrometricsError, sqlite3.Error, OSError) as error:
            log_failure(logger, error, "Failed to save target %s", target.id)
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
        return self.astrometrics.targets.get(target_id, refresh="storage")

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
        except (AstrometricsError, sqlite3.Error, OSError) as error:
            log_failure(logger, error, "Failed to save targets")
            raise

    def get_frame_header(self, frame_path: str, target_id: str | None = None) -> dict[str, Any]:
        """Read a frame's FITS header and the turn that shows it as the stack.

        The header is the library's (`targets.read_frame_header` with
        ``as_cards``).

        Parameters
        ----------
        frame_path : `str`
            The FITS file.
        target_id : `str`, optional
            The target the file must belong to (one of its frames or stacks).
            Left out, the file must be inside the library folders or the
            scratch folder, and no turn is worked out.

        Returns
        -------
        result : `dict`
            ``entries``, the FITS header cards of the frame (each with
            ``key``, ``value`` and ``comment``), and ``displayTurn``, the
            quarter turn (0, 90, 180 or 270 degrees, clockwise) the viewer
            shows the frame with so it looks the same way round as the
            target's stack (the library decides it, `targets.display_turn`;
            0 without a target). Empty entries and no turn when the file is
            no longer on disk: a stack that was moved or deleted is a normal
            state for a target record, not a server error (a server error
            would be logged and shown as a failed request every time the
            target is opened).

        Notes
        -----
        An unknown ``target_id`` raises the library's `NotFoundError`. A file
        that does not belong to the target, or, with no target, a file outside
        the allowed folders raises `InvalidArgumentError`.
        """
        target = None
        if target_id is None:
            frame_path = resolve_allowed_image(frame_path, self.config)
        else:
            target = self.astrometrics.targets.get(target_id, required=True)
        try:
            entries = self.astrometrics.targets.read_frame_header(frame_path, target=target, as_cards=True)
        except NotFoundError:
            logger.debug("Frame file %s is missing, returning an empty header", frame_path)
            return {"entries": [], "displayTurn": 0}
        turn = 0 if target is None else self.astrometrics.targets.display_turn(frame_path, target)
        return {"entries": entries, "displayTurn": turn}

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
