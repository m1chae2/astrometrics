"""Main interface for the targets in the catalog and their frame records.

`TargetCatalog` creates, reads, saves and deletes targets, finds frames, and
rebuilds a target's frame list from the files on disk. It keeps the targets
in memory and works out which ones changed, so a save writes only those. The
work itself happens in `pipelines/shared/`; the methods here check their
arguments and hand off.
"""

import builtins
import logging
from collections.abc import Callable, Sequence
from datetime import date
from typing import TYPE_CHECKING, Literal

from astropy.io import fits

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.arguments import TimeInput, check_choice, check_limit, reject_unused_arguments
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job
from astrometricslib.models.catalog_queries import ReindexReport, TargetQueryResult, TargetReindexChange
from astrometricslib.models.field_centers import FieldCenter, ImagedFieldCenters
from astrometricslib.models.image_deletion import FailedDeletion, ImageDeletionResult
from astrometricslib.models.observatory_configuration import ObservatoryConfiguration
from astrometricslib.models.target import FrameRecord, Target, TargetObjectType
from astrometricslib.pipelines.shared.api_arguments import resolve_target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files
from astrometricslib.pipelines.shared.target_sessions import TargetSession, derive_target_sessions

if TYPE_CHECKING:
    from astrometricslib.api.quality import QualityRecords

logger = logging.getLogger(__name__)

__all__ = [
    "TargetCatalog",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "frame_is_spectral",
]

QUERY_DETAILS = ("summary", "objects", "full", "cameras", "nights", "camera_index")
"""The kinds of answer `TargetCatalog.query` gives."""

_QUERY_ARGUMENTS = {
    "summary": (
        "target_id",
        "text",
        "camera_id",
        "ra_deg",
        "dec_deg",
        "radius_deg",
        "object_type",
        "sort",
        "include_empty",
        "limit",
        "offset",
    ),
    "objects": ("limit", "offset"),
    "full": ("target_id", "include_frames"),
    "cameras": (),
    "nights": (),
    "camera_index": (),
}


class TargetCatalog(MovedNames):
    """Create, read, save and delete targets, and keep their frames current.

    A target is a place on the sky that the observatory images. It anchors
    all the data about that place: the raw frames, the stacks, and the
    analysis results. Use `get` for a `Target` to work with,
    ``query(detail="objects", limit=None)`` for every `Target`, and `query`
    for short descriptions that a person reads. A method
    that takes a target accepts its id or the `Target`; an id that names no
    target raises `NotFoundError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the frames folder and the
        configured cameras.
    storage : `AbstractCatalogAccess`
        The database the target catalog is read from and saved to.
    drivers : `Drivers`, optional
        The outside services to use. `create` with ``from_catalog`` asks the
        SIMBAD driver for a position. The built-in driver is used when
        omitted.
    quality : `Callable`, optional
        Gives the `QualityRecords` whose `forget` `delete_images` calls. One
        is built over the same settings and storage when omitted.
    """

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        drivers: Drivers | None = None,
        quality: Callable[[], QualityRecords] | None = None,
    ) -> None:
        self._config = config
        self._drivers = drivers or Drivers()
        self._quality_provider = quality
        self._catalog_access = storage
        from astrometricslib.pipelines.shared import target_records

        # The write counter is read before the targets, so a write made while
        # they are read is noticed by the next refresh.
        version = target_records._stored_targets_version(self)
        self._targets: list = storage.get("target_catalog", {}) or []
        self._touched_target_ids: set = set()
        self._saved_fingerprints: dict[str, str] = {}
        target_records.remember_stored_state(self, self._targets)
        target_records.record_listed_state(self, version)

    def _quality(self) -> QualityRecords:
        """Give the quality records that learn of the deleted frames.

        Returns
        -------
        quality : `QualityRecords`
            The shared one, or one built over this catalog's settings.
        """
        if self._quality_provider is not None:
            return self._quality_provider()
        from astrometricslib.api.quality import QualityRecords

        return QualityRecords(self._config, self._catalog_access, targets=self)

    # -- Create, read, save, delete ------------------------------------------

    def get(
        self,
        target: str | Target,
        refresh: bool | Literal["storage"] = False,
        *,
        required: bool = False,
    ) -> Target | None:
        """Find one target by its id, allowing small differences in spelling.

        Parameters
        ----------
        target : `str` or `Target`
            The target id, or a `Target`, which is returned as it is.
            Underscores, spaces and capital letters may differ from the
            stored id.
        refresh : `bool` or ``"storage"``, optional
            `True` brings the target up to date with storage first, so a
            target that another program added or changed is seen. Only that
            target's row is read, and nothing is read when nothing has been
            written since the last read. A target this catalog does not hold
            yet makes it read all the targets. Unsaved edits to the held
            target are kept. ``"storage"`` reads the saved record straight
            from storage and leaves the targets held in memory alone: use it
            to check what a save really wrote, as another program would see
            it. It needs the exact id. Defaults to `False`, which uses the
            targets held in memory.
        required : `bool`, optional
            Raise `NotFoundError` instead of returning `None` when no target
            matches. Defaults to `False`.

        Returns
        -------
        target : `Target` or `None`
            The matching target, or `None` if no target matches and
            ``required`` is `False`.

        Raises
        ------
        NotFoundError
            If ``required`` is `True` and no target matches.
        InvalidArgumentError
            If ``target`` is neither a non-empty id nor a `Target`, or
            ``refresh`` is not `True`, `False` or ``"storage"``.
        """
        from astrometricslib.foundation.errors import NotFoundError
        from astrometricslib.pipelines.shared import target_records

        if isinstance(target, Target):
            return target
        if not isinstance(target, str) or not target.strip():
            raise InvalidArgumentError("target must be a target id or a Target.")
        if refresh == "storage":
            found = target_records.read_saved_target(self, target)
        elif isinstance(refresh, bool):
            if refresh:
                target_records.refresh_target(self, target)
            found = target_records.get_target(self, target)
        else:
            raise InvalidArgumentError(
                "refresh must be True, False or 'storage'.", details={"refresh": refresh}
            )
        if found is None and required:
            raise NotFoundError(f"No target with id {target!r} in the library.", details={"target": target})
        return found

    def create(self, target_id: str, from_catalog: bool = False) -> Target:
        """Create a target, find its frames on disk, and save it.

        The frames folders are scanned for folders named after the target,
        so frames that are already on disk are listed straight away.

        Parameters
        ----------
        target_id : `str`
            The id of the target to create. With ``from_catalog``, a
            catalog name such as ``"M 57"`` or ``"Vega"``.
        from_catalog : `bool`, optional
            Look the name up in the SIMBAD catalog and store the position
            it gives (right ascension and declination, ICRS, J2000) on the
            new target. A target that already has the id is returned as it
            is, without a lookup. Defaults to `False`, which makes a bare
            target with no position. Planets, the Moon, the Sun, comets and
            asteroids have no fixed position, so they are refused. A name
            the catalog does not know (or cannot reach, which logs a
            warning) raises `NotFoundError`.

        Returns
        -------
        target : `Target`
            The new target, or the existing one if the id is taken.

        """
        from astrometricslib.pipelines.shared import target_records

        if not from_catalog:
            return target_records.create_target(self, target_id)
        return target_records.create_target_from_catalog(self, target_id, self._drivers.simbad_or_default())

    def add(self, target: Target) -> None:
        """Add a `Target` object to the catalog and save it.

        Parameters
        ----------
        target : `Target`
            The target to add.
        """
        if not any(existing.id == target.id for existing in self._targets):
            self._targets.append(target)
        self._touched_target_ids.add(target.id)
        self.save()

    def delete(self, target_id: str) -> bool:
        """Remove a target from the catalog.

        Parameters
        ----------
        target_id : `str`
            The id of the target to remove.

        Returns
        -------
        removed : `bool`
            `True` if a matching target was found and removed.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.delete_target(self, target_id)

    def save(self) -> None:
        """Write every target that changed back to storage."""
        from astrometricslib.pipelines.shared import target_records

        target_records.save_targets(self)

    # -- Frame records ----------------------------------------------------

    def reindex_frames(
        self,
        target: str | Target | None = None,
        paths: builtins.list[str] | None = None,
        role: str = "LIGHT",
        filter_name: str | None = None,
        camera_id: str | None = None,
        prune_missing: bool = False,
        refresh_headers: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ReindexReport:
        """Bring frame records up to date with the files on disk, and save.

        Three forms:

        - ``target`` and ``paths``: add only those files to the target, reading
          each FITS header. ``role``, ``filter_name`` and ``camera_id``
          override what the header says. A file that is already listed is
          updated. A finished picture (``.jpg``, ``.jpeg``, ``.png``,
          ``.tiff`` or ``.tif``) is not a frame: it becomes the target's
          processed image instead.
        - ``target`` alone: scan the frames folder for the target's files,
          add the new ones and recompute the total exposure.
          ``prune_missing`` and ``refresh_headers`` apply.
        - neither: do the same for every target folder under the frames
          folder's ``lights`` folder, creating a target for each folder that
          has none. ``prune_missing``, ``refresh_headers`` and
          ``on_progress`` apply.

        An argument that does not apply to the form is refused. Each target
        that changed is saved.

        Parameters
        ----------
        target : `str` or `Target`, optional
            The target to update. `None` means every target.
        paths : `list` [`str`], optional
            Only these FITS files are added.
        role : `str`, optional
            With ``paths``: the frames' role, such as ``"LIGHT"`` (default),
            ``"DARK"``, ``"BIAS"`` or ``"FLAT"``.
        filter_name : `str`, optional
            With ``paths``: the filter, instead of the one in the header.
        camera_id : `str`, optional
            With ``paths``: the camera name, instead of the one in the header.
        prune_missing : `bool`, optional
            Without ``paths``: also remove records of files that no longer
            exist. Defaults to `False`.
        refresh_headers : `bool`, optional
            Without ``paths``: also read the header values again (pier side,
            airmass, altitude, pixel scale, cooling and focuser readings) of
            frames already listed. Defaults to `False`.
        on_progress : `Callable`, optional
            For every target: called as ``(index, total, target_id)`` before
            each target is scanned.

        Returns
        -------
        report : `ReindexReport`
            Each target's frame count before and after, the files added,
            and the processed image set from ``paths``, if any.

        Raises
        ------
        InvalidArgumentError
            If an argument that does not apply to the form is given, or
            ``paths`` is given without a target.
        """
        from astrometricslib.pipelines.shared import target_records
        from astrometricslib.pipelines.shared.frame_grouping import add_frame

        form = "paths" if paths is not None else ("target" if target is not None else "library")
        reject_unused_arguments(
            form,
            {
                "paths": ("role", "filter_name", "camera_id"),
                "target": ("prune_missing", "refresh_headers"),
                "library": ("prune_missing", "refresh_headers", "on_progress"),
            },
            {
                "role": role != "LIGHT",
                "filter_name": filter_name is not None,
                "camera_id": camera_id is not None,
                "prune_missing": prune_missing,
                "refresh_headers": refresh_headers,
                "on_progress": on_progress is not None,
            },
        )
        if paths is not None and target is None:
            raise InvalidArgumentError("paths needs a target to add the files to.")

        if target is None:
            return self._reindex_library(prune_missing, refresh_headers, on_progress)

        resolved = resolve_target(self, target)
        frames_before = len(resolved.frames)
        added: builtins.list[str] = []
        processed_image: str | None = None
        if paths is not None:
            for path in paths:
                if target_records.is_processed_image(path):
                    resolved.stacking.processed_image = path
                    processed_image = path
                    continue
                record = add_frame(resolved, path, role, filter_name, camera_id)
                added.append(record.path)
        else:
            target_records.reindex_frames(
                resolved,
                prune_missing=prune_missing,
                catalog_access=self._catalog_access,
                refresh_headers=refresh_headers,
            )
        self._assign_configurations(resolved)
        self._touched_target_ids.add(resolved.id)
        self.save()
        return ReindexReport(
            targets=[
                TargetReindexChange(
                    target_id=resolved.id, frames_before=frames_before, frames_after=len(resolved.frames)
                )
            ],
            added_paths=added,
            processed_image=processed_image,
        )

    def _reindex_library(
        self,
        prune_missing: bool,
        refresh_headers: bool,
        on_progress: Callable[[int, int, str], None] | None,
    ) -> ReindexReport:
        """Reindex every target folder under the frames folder.

        Returns
        -------
        report : `ReindexReport`
            Each target's frame count before and after.
        """
        import os

        from astrometricslib.pipelines.shared import target_records

        lights_path = os.path.join(str(self._config.get_frames_path()), "lights")
        folders = (
            sorted(
                name
                for name in os.listdir(lights_path)
                if os.path.isdir(os.path.join(lights_path, name)) and name != "test_write"
            )
            if os.path.isdir(lights_path)
            else []
        )
        changes = []
        for index, folder in enumerate(folders):
            if on_progress is not None:
                on_progress(index, len(folders), folder)
            existing = self.get(folder)
            resolved = existing or self.create(folder)
            frames_before = len(resolved.frames) if existing is not None else 0
            target_records.reindex_frames(
                resolved,
                prune_missing=prune_missing,
                catalog_access=self._catalog_access,
                refresh_headers=refresh_headers,
            )
            self._assign_configurations(resolved)
            self._touched_target_ids.add(resolved.id)
            # Saved one target at a time, so an interrupted run keeps the
            # targets it already finished.
            self.save()
            changes.append(
                TargetReindexChange(
                    target_id=resolved.id,
                    frames_before=frames_before,
                    frames_after=len(resolved.frames),
                    created=existing is None,
                )
            )
        return ReindexReport(targets=changes)

    def _assign_configurations(self, target: Target) -> None:
        """Point each of a target's frames at its observatory configuration.

        Creates the configurations the frames imply when none covers them
        (see `astrometricslib.pipelines.shared.equipment`).
        A store that cannot be reached leaves the frames as they are, with
        one warning: the frames are still indexed.

        Parameters
        ----------
        target : `Target`
            The target just reindexed. Its frames are changed in place.
        """
        import sqlite3

        from astrometricslib._internal.drivers.observatory_configuration_store import (
            ObservatoryConfigurationStore,
        )
        from astrometricslib.foundation.errors import AstrometricsError
        from astrometricslib.pipelines.shared.equipment import seed_configurations

        try:
            seed_configurations(ObservatoryConfigurationStore(self._config), self._config, [target])
        except (sqlite3.Error, OSError, AstrometricsError) as problem:
            logger.warning("Could not match the frames of %r to their configurations: %s", target.id, problem)

    @staticmethod
    def _check_belongs(path: str, target: Target) -> None:
        """Refuse a file that is not one of a target's frames or stacks.

        Parameters
        ----------
        path : `str`
            The file.
        target : `Target`
            The target it must belong to.

        Raises
        ------
        InvalidArgumentError
            If the file is not one of the target's frames or stacks.
        """
        belongs_to_target = any(frame.path == path for frame in target.frames) or path in (
            target.stacking.processed_image,
            target.stacking.stacked_image,
            target.spectral_stacking.stacked_image,
        )
        if not belongs_to_target:
            raise InvalidArgumentError(f"Path {path} does not belong to target {target.id}")

    def display_turn(self, path: str, target: str | Target) -> int:
        """Say how far to turn a frame on screen to match the target's stack.

        Frames taken on the two sides of a meridian flip are upside down from
        each other. A viewer shows each raw frame turned by a quarter turn
        (clockwise) so it looks the same way round as the target's imaging
        stack: the stack's rotation from its plate solution minus the frame's
        capture rotation (``CROTA2``), rounded to a quarter turn, or half a
        turn for a frame from the other side of the pier (``PIERSIDE``) when
        it has no ``CROTA2``. The turn is for display only. A stack with no
        plate solution, a frame with its own plate solution, a frame already
        turned to north and a file that cannot be read all give 0.

        Parameters
        ----------
        path : `str`
            The frame, one of the target's frames or stacks.
        target : `str` or `Target`
            The target.

        Returns
        -------
        turn : `int`
            0, 90, 180 or 270 degrees, clockwise. A file that does not belong
            to the target raises `InvalidArgumentError`.
        """
        from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
        from astrometricslib.pipelines.stacking.post_processing.display_turn import display_turn_degrees

        resolved = resolve_target(self, target)
        self._check_belongs(path, resolved)
        stack_path = resolved.stacking.stacked_image
        if not stack_path:
            return 0
        try:
            return display_turn_degrees(read_header(path), read_header(stack_path))
        except FITS_READ_ERRORS:
            return 0

    def read_frame_header(
        self, path: str, target: str | Target | None = None, as_cards: bool = False
    ) -> fits.Header | builtins.list[dict[str, str]]:
        """Read the first header of a FITS file.

        This reads the first block (HDU 0) of the file, where cameras and
        capture software write their notes. It does not read the pixels.

        Parameters
        ----------
        path : `str`
            Path to the FITS file.
        target : `str` or `Target`, optional
            The target the file must belong to: one of its frames, or one of
            its stacks. Left out, any file is read.
        as_cards : `bool`, optional
            Give the header as text cards for a person or a reply, instead of
            with typed values. Defaults to `False`.

        Returns
        -------
        header : `astropy.io.fits.Header` or `list` [`dict` [`str`, `str`]]
            A copy of the file's first header, with each value a number,
            text or flag. It can be used like a dictionary, for example
            ``header.get("FILTER")``. With ``as_cards``, a list of cards,
            each with ``key``, ``value`` and ``comment`` as text.

        Raises
        ------
        NotFoundError
            If the file does not exist.

        Notes
        -----
        Also raises `NotFoundError` if ``target`` names no target,
        `InvalidArgumentError` if the file does not belong to ``target``, and
        one of `astrometricslib.exceptions.FITS_READ_ERRORS` if the file is
        cut off or damaged.
        """
        import os

        from astrometricslib._internal.drivers.fits_access import read_header
        from astrometricslib.foundation.errors import NotFoundError
        from astrometricslib.pipelines.shared import image_conversions

        if target is not None:
            self._check_belongs(path, resolve_target(self, target))
        if as_cards:
            return image_conversions.get_fits_header(path)
        if not os.path.exists(path):
            raise NotFoundError(f"File not found: {path}")
        return read_header(path, first_hdu_only=True)

    def select_frames(
        self,
        target: str | Target,
        configuration: ObservatoryConfiguration | None = None,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: TimeInput | None = None,
        until: TimeInput | None = None,
        limit: int | None = None,
    ) -> builtins.list[FrameRecord]:
        """Pick a target's light frames by filter, file name range and time.

        Only light frames that have a start time are considered. Spectroscopy
        frames are left out unless ``filter_name`` asks for one.

        Parameters
        ----------
        target : `str` or `Target`
            The target to search.
        configuration : `ObservatoryConfiguration`, optional
            Keep frames taken with this set of equipment (from
            ``astrometrics.observatories.configurations``).
        filter_name : `str`, optional
            Keep frames whose filter matches this text, ignoring case.
        first_file, last_file : `str`, optional
            Keep frames whose file name sorts from ``first_file`` to
            ``last_file``. A short bound such as ``"013"`` is matched
            against the frame number at the end of the name.
        since, until : `str`, `datetime` or `Time`, optional
            Keep frames that began from ``since`` to ``until``. Each is an
            ISO 8601 time such as ``"2026-10-03T21:30:00-06:00"``, a
            `datetime` or an astropy `Time`; no offset means UTC.
        limit : `int` or `None`, optional
            How many frames to keep, at least 1. With a file or time bound
            these are the first frames in the range; without one they are
            the newest. `None` (the default) keeps every match.

        Returns
        -------
        frames : `list` [`FrameRecord`]
            The kept frames, oldest first.

        Notes
        -----
        Raises `NotFoundError` if the target does not exist, and
        `InvalidArgumentError` if ``since`` or ``until`` cannot be read as a
        time or ``limit`` is less than 1.
        """
        from astrometricslib.pipelines.shared.equipment import frame_matches
        from astrometricslib.pipelines.shared.quality import frame_selection

        resolved = resolve_target(self, target)
        frames = resolved.frames
        if configuration is not None:
            frames = [frame for frame in frames if frame_matches(frame, configuration, self._config)]
        return frame_selection.select_target_light_frames(
            frames,
            filter_name=filter_name,
            first_file=first_file,
            last_file=last_file,
            since=since,
            until=until,
            limit=limit,
        )

    def add_frames(self, paths: Sequence[str], target: str | Target, *, telescope: str) -> builtins.list[str]:
        """Move new image files into the library, under a target.

        Each path is a folder to search or a single FITS file. A file is
        sorted by its header: a light frame goes under the target, and a dark,
        bias or flat frame goes to the calibration folders. Frames set aside
        by the stacking pipeline are skipped. Files are moved, not copied, and
        a file that cannot be read is left where it is. The call does not
        update the frame records: call `reindex_frames` with the returned
        paths for that.

        Parameters
        ----------
        paths : `Sequence` [`str`]
            Folders to search, or FITS files.
        target : `str` or `Target`
            The target the light frames belong to.
        telescope : `str`
            The name of the telescope the frames were taken with. It is part
            of the library folder of a light or flat frame.

        Returns
        -------
        added_paths : `list` [`str`]
            The new path of each file that was moved to a place where no file
            of that name yet existed.

        Notes
        -----
        Raises `NotFoundError` if the target does not exist.
        """
        from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files

        resolved = resolve_target(self, target)
        added_paths: builtins.list[str] = []
        classify_and_sort_fits_files(list(paths), resolved.id, self._config, telescope, added_paths)
        return added_paths

    def sessions(self, target: str | Target, night_date: date | None = None) -> builtins.list[TargetSession]:
        """Group a target's frames into observing sessions.

        Frames go in the same session when they were taken on the same night
        (noon to noon, local time) with the same gain and offset. A change of
        filter or exposure length does not start a new session. Frames with
        no start time are ignored.

        Parameters
        ----------
        target : `str` or `Target`
            The target.
        night_date : `datetime.date`, optional
            Only the sessions of this night.

        Returns
        -------
        sessions : `list` [`TargetSession`]
            The sessions, ordered by night.

        Notes
        -----
        Raises `NotFoundError` if the target does not exist.
        """
        resolved = resolve_target(self, target)
        found = derive_target_sessions(resolved.id, resolved.frames)
        return [session for session in found if night_date is None or session.night_date == night_date]

    def get_frame(self, target: str | Target, iso: str, exposure: str, index: int = 0) -> str:
        """Find the path of a target's frame by gain, exposure and order.

        Parameters
        ----------
        target : `str` or `Target`
            The target to search.
        iso : `str`
            The ISO or gain setting to match.
        exposure : `str`
            The exposure length to match.
        index : `int`, optional
            Which matching frame to return, by order. Defaults to 0.

        Returns
        -------
        frame_path : `str`
            The path of the matching frame.

        Notes
        -----
        Raises `NotFoundError` if the target does not exist or no frame
        matches.
        """
        from astrometricslib.pipelines.shared import image_conversions

        resolved = resolve_target(self, target)
        return image_conversions.get_frame(resolved, iso, exposure, index)

    def delete_images(
        self, paths: builtins.list[str], target: str | Target | None = None
    ) -> ImageDeletionResult:
        """Delete files from disk, and from a target's frame list if given.

        Parameters
        ----------
        paths : `list` [`str`]
            File paths to delete.
        target : `str` or `Target`, optional
            Also remove the matching frame records from this target, save
            it, and forget the stored quality measurements of the deleted
            frames (through `QualityRecords.forget`). Without a target the
            measurements stay, because a frame is named by its target.

        Returns
        -------
        result : `ImageDeletionResult`
            ``deleted`` (the paths removed) and ``failed`` (each path that
            could not be removed, with the reason).
        """
        from astrometricslib.pipelines.shared import image_conversions

        resolved = resolve_target(self, target) if target is not None else None
        outcome = image_conversions.delete_images(paths, self, resolved.id if resolved is not None else None)
        if resolved is not None and outcome["deleted"]:
            self._quality().forget([resolved.frame_subject_id(str(path)) for path in outcome["deleted"]])
        return ImageDeletionResult(
            deleted=[str(path) for path in outcome["deleted"]],
            failed=[
                FailedDeletion(path=str(item["path"]), reason=str(item["reason"]))
                for item in outcome["failed"]
            ],
        )

    # -- Short descriptions -----------------------------------------------

    def query(
        self,
        target_id: str | None = None,
        text: str | None = None,
        camera_id: str | None = None,
        ra_deg: float | None = None,
        dec_deg: float | None = None,
        radius_deg: float | None = None,
        object_type: Literal["solar_system", "messier", "ngc", "ic", "comet", "calibration", "star"]
        | None = None,
        detail: Literal["summary", "objects", "full", "cameras", "nights", "camera_index"] = "summary",
        include_frames: int = 0,
        sort: Literal["newest", "name", "separation"] = "newest",
        include_empty: bool = False,
        limit: int | None = 50,
        offset: int = 0,
    ) -> TargetQueryResult:
        """Describe targets in short records. Nothing is changed.

        This is the safe way to read targets: a whole target object holds
        every frame, and a list of them is far larger than a reply can be.
        Here each target is one short row, or one target is described with
        its frames grouped by night, filter, exposure and camera.

        Arguments used by each detail (any other argument is refused):

        - ``"summary"``: ``target_id``, ``text``, ``camera_id``, ``ra_deg``,
          ``dec_deg``, ``radius_deg``, ``object_type``, ``sort``,
          ``include_empty``, ``limit``, ``offset``.
        - ``"objects"``: ``limit`` and ``offset``.
        - ``"full"``: ``target_id`` (needed) and ``include_frames``.
        - ``"cameras"``, ``"nights"`` and ``"camera_index"``: none.

        Parameters
        ----------
        target_id : `str`, optional
            For ``"summary"``, keep targets whose id contains this text. For
            ``"full"``, the target to describe (an exact id first, then part
            of one), matched ignoring case.
        text : `str`, optional
            Keep targets whose id or common name contains this text,
            ignoring case.
        camera_id : `str`, optional
            Keep targets with light frames from this camera (part of the
            name, ignoring case).
        ra_deg : `float`, optional
            Right ascension of the centre of a region search, in degrees.
        dec_deg : `float`, optional
            Declination of the centre of a region search, in degrees.
        radius_deg : `float`, optional
            Radius of the region search, in degrees.
        object_type : `str`, optional
            Keep targets of this kind, read from their names (see
            `Target.object_type`): ``"solar_system"``, ``"messier"``,
            ``"ngc"``, ``"ic"``, ``"comet"``, ``"calibration"`` or
            ``"star"`` (any other name). Each row carries its
            ``object_type``.
        detail : `str`, optional
            ``"summary"`` (default): one row per target. ``"objects"``: every
            target as a full `Target` record, in ``objects``, in the order
            they are stored, after reading what other programs have saved (a
            target this catalog already holds keeps its object, so code that
            is still editing it does not lose its changes). ``"full"``: one
            target's record with grouped frames, stack paths and flags, and
            condensed quality summaries. ``"cameras"``: the cameras used and
            how many frames each took. ``"nights"``: for each observing
            night, how many targets have frames from it.
            ``"camera_index"``: for each target, the light frames from each
            configured camera and the newest frame time.
        include_frames : `int`, optional
            With ``"full"``, also list this many of the newest light frames
            (0 to 50), with their measurements.
        sort : `str`, optional
            ``"newest"`` (default, by the last frame taken), ``"name"``, or
            ``"separation"`` (nearest the centre first; needs a region
            search).
        include_empty : `bool`, optional
            Also list targets with no frames, such as the placeholders the
            calibration folders create. Off by default.
        limit : `int` or `None`, optional
            How many rows or targets to return, from 1 to 200. Defaults to
            50. `None` returns every match.
        offset : `int`, optional
            How many rows or targets to skip, for paging, at least 0.

        Returns
        -------
        answer : `TargetQueryResult`
            The rows, targets, record, cameras, nights or camera index, with
            ``total_matching`` and whether a list was cut.

        Raises
        ------
        InvalidArgumentError
            If the detail or sort is unknown, an argument the detail does
            not use is given, a region search is incomplete, or ``limit``,
            ``offset`` or ``include_frames`` is out of range.
        NotFoundError
            If ``detail="full"`` names no target.
        """
        from astrometricslib.foundation.errors import NotFoundError
        from astrometricslib.pipelines.shared import target_overview

        check_choice("detail", detail, QUERY_DETAILS)
        check_choice("sort", sort, ("newest", "name", "separation"))
        check_limit(limit, 1, 200)
        check_limit(offset, 0, None, name="offset", allow_none=False)
        check_limit(include_frames, 0, 50, name="include_frames", allow_none=False)
        if object_type is not None:
            check_choice("object_type", object_type, tuple(kind.value for kind in TargetObjectType))
        reject_unused_arguments(
            detail,
            _QUERY_ARGUMENTS,
            {
                "target_id": target_id is not None,
                "text": text is not None,
                "camera_id": camera_id is not None,
                "ra_deg": ra_deg is not None,
                "dec_deg": dec_deg is not None,
                "radius_deg": radius_deg is not None,
                "object_type": object_type is not None,
                "include_frames": include_frames != 0,
                "sort": sort != "newest",
                "include_empty": include_empty,
                "limit": limit != 50,
                "offset": offset != 0,
            },
        )
        region_values = (ra_deg, dec_deg, radius_deg)
        if any(value is not None for value in region_values) and any(
            value is None for value in region_values
        ):
            raise InvalidArgumentError("A region search needs ra_deg, dec_deg and radius_deg together.")
        if sort == "separation" and radius_deg is None:
            raise InvalidArgumentError(
                "sort='separation' needs a region search (ra_deg, dec_deg and radius_deg)."
            )
        if detail == "objects":
            from astrometricslib.pipelines.shared import target_records

            everything = target_records.list_targets(self)
            end = None if limit is None else offset + limit
            return TargetQueryResult(
                detail=detail,
                total_matching=len(everything),
                offset=offset,
                truncated=end is not None and end < len(everything),
                objects=everything[offset:end],
            )
        targets = self._look_at_every_target()

        if detail == "cameras":
            from astrometricslib.pipelines.shared.quality import frame_statistics

            return TargetQueryResult(detail=detail, cameras=frame_statistics.list_camera_names(targets))
        if detail == "nights":
            return TargetQueryResult(detail=detail, nights=target_overview.count_targets_per_night(targets))
        if detail == "camera_index":
            from astrometricslib._internal.drivers.camera_profile_store import camera_identity
            from astrometricslib.pipelines.shared.target_camera_index import build_target_camera_index

            camera_names = list(self._config.get_available_cameras())
            return TargetQueryResult(
                detail=detail, camera_index=build_target_camera_index(targets, camera_names, camera_identity)
            )
        if detail == "full":
            if not target_id:
                raise InvalidArgumentError("detail='full' needs a target_id.")
            target = target_overview.find_target_loosely(targets, target_id)
            if target is None:
                raise NotFoundError(f"No target matches {target_id!r}.", details={"target_id": target_id})
            return TargetQueryResult(
                detail=detail, target=target_overview.describe_target(target, include_frames)
            )

        region = (ra_deg, dec_deg, radius_deg) if radius_deg is not None else None
        rows = target_overview.summary_rows(
            targets, target_id, text, camera_id, region, include_empty, object_type
        )
        if sort == "name":
            rows.sort(key=lambda row: row["id"].lower())
        elif sort == "separation":
            rows.sort(key=lambda row: row["separation_deg"])
        else:
            rows.sort(key=lambda row: row["last_frame"] or "", reverse=True)
        end = None if limit is None else offset + limit
        return TargetQueryResult(
            detail=detail,
            total_matching=len(rows),
            offset=offset,
            truncated=end is not None and end < len(rows),
            targets=rows[offset:end],
        )

    def _look_at_every_target(self) -> builtins.list[Target]:
        """Read every target without marking any as changed.

        Returns
        -------
        targets : `list` [`Target`]
            Every target. Looking is not editing, so none is saved later
            because of this read.
        """
        from astrometricslib.pipelines.shared import target_records

        touched_before = set(self._touched_target_ids)
        try:
            return target_records.list_targets(self)
        finally:
            self._touched_target_ids = touched_before

    @background_job("diagnostics", grace_period_seconds=20.0)
    def imaged_field_centers(self, max_frames_per_target: int = 12) -> ImagedFieldCenters:
        """List the distinct sky positions the library has imaged.

        Reads the position from the FITS header of the first few frames of
        every target. On a large library kept on a slow drive this takes
        about half a minute.

        Parameters
        ----------
        max_frames_per_target : `int`, optional
            How many frames of each target to read. Defaults to 12.

        Returns
        -------
        centers : `ImagedFieldCenters`
            One entry per distinct position, with its coordinates and the
            targets that share it.
        """
        from astrometricslib.pipelines.astrometry.utilities.catalog_seeding import derive_field_centers

        # A read, not a pipeline run: it records no job of its own.
        found = derive_field_centers(
            self._look_at_every_target(), max_frames_per_target=max_frames_per_target
        )
        return ImagedFieldCenters(
            centers=[
                FieldCenter(
                    ra_deg=float(center["right_ascension_deg"]),
                    dec_deg=float(center["declination_deg"]),
                    target_ids=[str(target_id) for target_id in center["target_ids"]],
                    frames_examined=int(center["frames_examined"]),
                )
                for center in found
            ]
        )
