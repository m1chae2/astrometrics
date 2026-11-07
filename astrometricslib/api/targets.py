"""Main interface for the targets in the catalog and their frame records.

`TargetCatalog` creates, reads, saves and deletes targets, finds frames, and
rebuilds a target's frame list from the files on disk. It keeps the targets
in memory and works out which ones changed, so a save writes only those. The
work itself happens in `pipelines/shared/`; the methods here check their
arguments and hand off.
"""

import builtins
from collections.abc import Callable, Sequence
from datetime import date
from typing import Literal

from astropy.io import fits

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, registered_job
from astrometricslib.models.catalog_queries import ReindexReport, TargetQueryResult, TargetReindexChange
from astrometricslib.models.field_centers import FieldCenter, ImagedFieldCenters
from astrometricslib.models.image_deletion import FailedDeletion, ImageDeletionResult
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, Target, TargetObjectType
from astrometricslib.pipelines.shared.api_arguments import (
    check_choice,
    reject_unused_arguments,
    resolve_target,
)
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files
from astrometricslib.pipelines.shared.target_sessions import TargetSession, derive_target_sessions

__all__ = [
    "TargetCatalog",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "frame_is_spectral",
]

QUERY_DETAILS = ("summary", "full", "cameras", "nights", "camera_index")
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
    "full": ("target_id", "include_frames"),
    "cameras": (),
    "nights": (),
    "camera_index": (),
}


class TargetCatalog:
    """Create, read, save and delete targets, and keep their frames current.

    A target is a place on the sky that the observatory images. It anchors
    all the data about that place: the raw frames, the stacks, and the
    analysis results. Use `get` for a `Target` to work with, and `query`
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
    """

    def __init__(
        self, config: AppConfiguration, storage: AbstractCatalogAccess, *, drivers: Drivers | None = None
    ) -> None:
        self._config = config
        self._drivers = drivers or Drivers()
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

    # -- Create, read, save, delete ------------------------------------------

    def list(self) -> builtins.list[Target]:
        """Return every target, reading what other programs have saved.

        A target this catalog already holds keeps its object, so code that
        is still editing it does not lose its changes.

        Returns
        -------
        targets : `list` [`Target`]
            Every target in the catalog.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.list_targets(self)

    def get(self, target_id: str, refresh: bool = False) -> Target | None:
        """Find one target by its id, allowing small differences in spelling.

        Parameters
        ----------
        target_id : `str`
            The target id. Underscores, spaces and capital letters may
            differ from the stored id.
        refresh : `bool`, optional
            Bring the target up to date with storage first, so a target
            that another program added or changed is seen. Only that
            target's row is read, and nothing is read when nothing has been
            written since the last read. A target this catalog does not hold
            yet makes it read all the targets. Unsaved edits to the held
            target are kept. Defaults to `False`, which uses the targets held
            in memory.

        Returns
        -------
        target : `Target` or `None`
            The matching target, or `None` if no target matches.
        """
        from astrometricslib.pipelines.shared import target_records

        if refresh:
            target_records.refresh_target(self, target_id)
        return target_records.get_target(self, target_id)

    def read_saved(self, target_id: str) -> Target | None:
        """Read one target's saved record straight from storage.

        The targets held in memory are not used. Use this to check what a
        save really wrote, as another program would see it.

        Parameters
        ----------
        target_id : `str`
            The exact id of the target.

        Returns
        -------
        target : `Target` or `None`
            The stored target, or `None` if nothing is stored under that id.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.read_saved_target(self, target_id)

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

    def get_header(self, path: str, target: str | Target | None = None) -> builtins.list[dict[str, str]]:
        """Read the header of a FITS image file.

        When ``target`` is given, the file must belong to that target: one
        of its frames, or one of its stacks.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to read.
        target : `str` or `Target`, optional
            The target the file must belong to.

        Returns
        -------
        header_cards : `list` [`dict` [`str`, `str`]]
            The header cards, each with ``key``, ``value`` and ``comment``.

        Raises
        ------
        InvalidArgumentError
            If ``target`` is given and the file does not belong to it.
        """
        if target is not None:
            resolved = resolve_target(self, target)
            belongs_to_target = any(frame.path == path for frame in resolved.frames) or path in (
                resolved.stacking.processed_image,
                resolved.stacking.stacked_image,
                resolved.spectral_stacking.stacked_image,
            )
            if not belongs_to_target:
                raise InvalidArgumentError(f"Path {path} does not belong to target {resolved.id}")

        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.get_fits_header(path)

    def read_frame_header(self, path: str) -> fits.Header:
        """Read the header of a FITS file with its values typed.

        This reads the first block (HDU 0) of the file, where cameras and
        capture software write their notes. It does not read the pixels.
        Unlike `get_header`, it does not check the file against a target,
        and it keeps each value as a number, text or flag, not as text.

        Parameters
        ----------
        path : `str`
            Path to the FITS file.

        Returns
        -------
        header : `astropy.io.fits.Header`
            A copy of the file's first header. It can be used like a
            dictionary, for example ``header.get("FILTER")``.
        """
        # A missing, cut-off or damaged file raises one of FITS_READ_ERRORS.
        from astrometricslib._internal.drivers.fits_access import read_header

        return read_header(path, first_hdu_only=True)

    def select_frames(
        self,
        target: str | Target,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> builtins.list[FrameRecord]:
        """Pick a target's light frames by filter, file name range and time.

        Only light frames that have a start time are considered. Spectroscopy
        frames are left out unless ``filter_name`` asks for one.

        Parameters
        ----------
        target : `str` or `Target`
            The target to search.
        filter_name : `str`, optional
            Keep frames whose filter matches this text, ignoring case.
        first_file, last_file : `str`, optional
            Keep frames whose file name sorts from ``first_file`` to
            ``last_file``. A short bound such as ``"013"`` is matched
            against the frame number at the end of the name.
        since, until : `str`, optional
            Keep frames that began from ``since`` to ``until``. Each is an
            ISO 8601 time such as ``"2026-10-03T21:30:00-06:00"``; no offset
            means UTC.
        limit : `int`, optional
            How many frames to keep. With a file or time bound these are the
            first frames in the range; without one they are the newest.
            Omit it to keep every match.

        Returns
        -------
        frames : `list` [`FrameRecord`]
            The kept frames, oldest first.

        Notes
        -----
        Raises `NotFoundError` if the target does not exist, and
        `InvalidArgumentError` if ``since`` or ``until`` is not an ISO 8601
        time or ``limit`` is less than 1.
        """
        from astrometricslib.pipelines.shared.quality import frame_selection

        resolved = resolve_target(self, target)
        return frame_selection.select_target_light_frames(
            resolved.frames,
            filter_name=filter_name,
            first_file=first_file,
            last_file=last_file,
            since=since,
            until=until,
            limit=limit,
        )

    def resolve(self, target: str | Target) -> Target:
        """Turn a target id into the `Target` it names.

        A `Target` is returned as it is. An id is matched loosely, as `get`
        does, after reading the stored targets again so that a target
        another program added is seen.

        Parameters
        ----------
        target : `str` or `Target`
            A target id or a `Target`.

        Returns
        -------
        resolved : `Target`
            The target.

        Notes
        -----
        Raises `NotFoundError` if no target has that id, and
        `InvalidArgumentError` if ``target`` is neither text nor a `Target`.
        """
        return resolve_target(self, target)

    def get_stars(self, target: str | Target) -> builtins.list[StellarObject]:
        """Read every star the library holds for a target.

        This is the short form of ``StellarCatalog.query`` with the target
        selected, the full records asked for and no cap on the count. Use
        ``StellarCatalog.query`` when you need a filter, an order or a
        smaller answer. Single-frame detections are left out, as they are
        in that query.

        Parameters
        ----------
        target : `str` or `Target`
            A target id or a `Target`.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The target's stars, in id order. Empty if the target has none.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import StarQuery, run_star_query

        resolved = self.resolve(target)
        answer = run_star_query(
            self._catalog_access, StarQuery(target_id=resolved.id), "objects", None, 0, None
        )
        return answer.objects or []

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
            Also remove the matching frame records from this target, and
            save it.

        Returns
        -------
        result : `ImageDeletionResult`
            ``deleted`` (the paths removed) and ``failed`` (each path that
            could not be removed, with the reason).
        """
        from astrometricslib.pipelines.shared import image_conversions

        target_id = resolve_target(self, target).id if target is not None else None
        outcome = image_conversions.delete_images(paths, self, target_id)
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
        detail: Literal["summary", "full", "cameras", "nights", "camera_index"] = "summary",
        include_frames: int = 0,
        sort: Literal["newest", "name", "separation"] = "newest",
        include_empty: bool = False,
        limit: int = 50,
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
            ``"summary"`` (default): one row per target. ``"full"``: one
            target's record with grouped frames, stack paths and flags, and
            condensed quality summaries. ``"cameras"``: the cameras used and
            how many frames each took. ``"nights"``: for each observing
            night, how many targets have frames from it.
            ``"camera_index"``: for each target, the light frames from each
            configured camera and the newest frame time.
        include_frames : `int`, optional
            With ``"full"``, also list this many of the newest light frames
            (at most 50), with their measurements.
        sort : `str`, optional
            ``"newest"`` (default, by the last frame taken), ``"name"``, or
            ``"separation"`` (nearest the centre first; needs a region
            search).
        include_empty : `bool`, optional
            Also list targets with no frames, such as the placeholders the
            calibration folders create. Off by default.
        limit : `int`, optional
            How many rows to return, from 1 to 200. Defaults to 50.
        offset : `int`, optional
            How many rows to skip, for paging.

        Returns
        -------
        answer : `TargetQueryResult`
            The rows, record, cameras, nights or camera index, with
            ``total_matching`` and whether a list was cut.

        Raises
        ------
        InvalidArgumentError
            If the detail or sort is unknown, an argument the detail does
            not use is given, or a region search is incomplete.
        NotFoundError
            If ``detail="full"`` names no target.
        """
        from astrometricslib.foundation.errors import NotFoundError
        from astrometricslib.pipelines.shared import target_overview

        check_choice("detail", detail, QUERY_DETAILS)
        check_choice("sort", sort, ("newest", "name", "separation"))
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
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        return TargetQueryResult(
            detail=detail,
            total_matching=len(rows),
            offset=offset,
            truncated=offset + limit < len(rows),
            targets=rows[offset : offset + limit],
        )

    def _look_at_every_target(self) -> builtins.list[Target]:
        """Read every target without marking any as changed.

        Returns
        -------
        targets : `list` [`Target`]
            Every target. Looking is not editing, so none is saved later
            because of this read.
        """
        touched_before = set(self._touched_target_ids)
        try:
            return self.list()
        finally:
            self._touched_target_ids = touched_before

    @background_job("diagnostics", grace_period_seconds=20.0)
    def imaged_field_centers(
        self, max_frames_per_target: int = 12, register_job: bool = True
    ) -> ImagedFieldCenters:
        """List the distinct sky positions the library has imaged.

        Reads the position from the FITS header of the first few frames of
        every target. On a large library kept on a slow drive this takes
        about half a minute.

        Parameters
        ----------
        max_frames_per_target : `int`, optional
            How many frames of each target to read. Defaults to 12.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        centers : `ImagedFieldCenters`
            One entry per distinct position, with its coordinates and the
            targets that share it.
        """
        from astrometricslib.pipelines.astrometry.utilities.catalog_seeding import derive_field_centers

        with registered_job(enabled=register_job, job_type="diagnostics", target_id="library"):
            found = derive_field_centers(
                self._look_at_every_target(), max_frames_per_target=max_frames_per_target
            )
            return ImagedFieldCenters(
                centers=[
                    FieldCenter(
                        right_ascension_deg=float(center["right_ascension_deg"]),
                        declination_deg=float(center["declination_deg"]),
                        target_ids=[str(target_id) for target_id in center["target_ids"]],
                        frames_examined=int(center["frames_examined"]),
                    )
                    for center in found
                ]
            )
