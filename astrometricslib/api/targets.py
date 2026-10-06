"""Main interface for the targets in the catalog and their frame records.

`TargetCatalog` creates, reads, saves and deletes targets, finds frames, and
rebuilds a target's frame list from the files on disk. It keeps the targets
in memory and works out which ones changed, so a save writes only those. The
work itself happens in `pipelines/shared/`; the methods here check their
arguments and hand off.
"""

import builtins
from collections.abc import Callable
from typing import Any, Literal

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.drivers.job_logging import background_job, registered_job
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.catalog_queries import ReindexReport, TargetQueryResult, TargetReindexChange
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.api_arguments import (
    check_choice,
    reject_unused_arguments,
    resolve_target,
)
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files
from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions

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
    """

    def __init__(self, config: AppConfiguration, storage: AbstractCatalogAccess) -> None:
        self._config = config
        self.catalog_access = storage
        self._targets: list = storage.get("target_catalog", {}) or []
        self._touched_target_ids: set = set()
        self._saved_fingerprints: dict[str, str] = {}
        from astrometricslib.pipelines.shared import target_records

        target_records.remember_stored_state(self, self._targets)

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
            Read the stored targets first, so a target that another program
            added or changed is seen. Defaults to `False`, which uses the
            targets held in memory.

        Returns
        -------
        target : `Target` or `None`
            The matching target, or `None` if no target matches.
        """
        from astrometricslib.pipelines.shared import target_records

        if refresh:
            target_records.list_targets(self)
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

    def create(self, target_id: str) -> Target:
        """Create a target, find its frames on disk, and save it.

        The frames folders are scanned for folders named after the target,
        so frames that are already on disk are listed straight away.

        Parameters
        ----------
        target_id : `str`
            The id of the target to create.

        Returns
        -------
        target : `Target`
            The new target, or the existing one if the id is taken.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.create_target(self, target_id)

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
        filter_type: str | None = None,
        camera_id: str | None = None,
        prune_missing: bool = False,
        refresh_headers: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ReindexReport:
        """Bring frame records up to date with the files on disk, and save.

        Three forms:

        - ``target`` and ``paths``: add only those files to the target, reading
          each FITS header. ``role``, ``filter_type`` and ``camera_id``
          override what the header says. A file that is already listed is
          updated.
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
        filter_type : `str`, optional
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
            Each target's frame count before and after, and the files added.

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
                "paths": ("role", "filter_type", "camera_id"),
                "target": ("prune_missing", "refresh_headers"),
                "library": ("prune_missing", "refresh_headers", "on_progress"),
            },
            {
                "role": role != "LIGHT",
                "filter_type": filter_type is not None,
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
        if paths is not None:
            for path in paths:
                record = add_frame(resolved, path, role, filter_type, camera_id)
                added.append(record.path)
        else:
            target_records.reindex_frames(
                resolved,
                prune_missing=prune_missing,
                catalog_access=self.catalog_access,
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
                catalog_access=self.catalog_access,
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

    def delete_images(self, paths: builtins.list[str], target: str | Target | None = None) -> dict:
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
        result : `dict`
            ``deleted`` (the paths removed) and ``failed`` (each path that
            could not be removed, with the reason).
        """
        from astrometricslib.pipelines.shared import image_conversions

        target_id = resolve_target(self, target).id if target is not None else None
        return image_conversions.delete_images(paths, self, target_id)

    # -- Short descriptions -----------------------------------------------

    def query(
        self,
        target_id: str | None = None,
        text: str | None = None,
        camera_id: str | None = None,
        ra_deg: float | None = None,
        dec_deg: float | None = None,
        radius_deg: float | None = None,
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
          ``dec_deg``, ``radius_deg``, ``sort``, ``include_empty``,
          ``limit``, ``offset``.
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
            from astrometricslib.drivers.camera_profile_store import camera_identity
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
        rows = target_overview.summary_rows(targets, target_id, text, camera_id, region, include_empty)
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
    ) -> builtins.list[dict[str, Any]]:
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
        centers : `list` [`dict`]
            One entry per distinct position, with its coordinates and the
            targets that share it.
        """
        from astrometricslib.pipelines.astrometry.utilities.catalog_seeding import derive_field_centers

        with registered_job(enabled=register_job, job_type="diagnostics", target_id="library"):
            return derive_field_centers(
                self._look_at_every_target(), max_frames_per_target=max_frames_per_target
            )
