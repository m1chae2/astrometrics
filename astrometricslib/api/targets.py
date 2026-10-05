"""Main interface for managing targets in the catalog.

`TargetCatalog` allows creating, reading, updating, and deleting targets.
It also handles target-specific actions like adding new image frames,
re-indexing frames, and checking statistics. This class stores the active
targets in memory and coordinates with lower-level task modules to perform
work.
"""

import builtins
from typing import Any

from astrometricslib.drivers.job_logging import background_job
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files
from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions

__all__ = [
    "TargetCatalog",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "frame_is_spectral",
]


def _angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Give the angle between two sky positions, in degrees.

    Parameters
    ----------
    ra1, dec1, ra2, dec2 : `float`
        The two positions, in degrees.

    Returns
    -------
    separation : `float`
        The great-circle angle between them.
    """
    import math

    first, second = math.radians(dec1), math.radians(dec2)
    delta = math.radians(ra1 - ra2)
    cosine = math.sin(first) * math.sin(second) + math.cos(first) * math.cos(second) * math.cos(delta)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


class TargetCatalog:
    """Create, read, update, and delete targets, plus manage their frames.

    This class acts as the primary interface for managing observation
    targets. A target represents a physical region of the sky and
    serves as the central anchor for all related data (raw frames,
    calibration masters, stacked images). Use this catalog to query,
    create, or delete targets within your observatory's library.
    """

    def __init__(self, config: AppConfiguration, catalog_access: object):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with configuration settings and a database manager.

        This setup keeps the list of targets and tracked changes right here
        in memory, so nothing gets confused about which data is the real
        version when it comes time to save to disk later.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        catalog_access : `AbstractCatalogAccess`
            The database tool used to save and load the target catalog.
        """
        self._config = config
        self.catalog_access = catalog_access
        self._targets: list = catalog_access.get("target_catalog", {}) or []
        self._touched_target_ids: set = set()
        self._saved_fingerprints: dict[str, str] = {}
        from astrometricslib.pipelines.shared import target_records

        target_records.remember_stored_state(self, self._targets)

    # -- Create, read, update, delete -------------------------------------

    def list(self) -> builtins.list[Target]:
        """Return every Target object from the in-memory catalog.

        Returns
        -------
        result : `list` [`Target`]
            The list of all active targets.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.list_targets(self)

    def get(self, target_id: str) -> Target | None:
        """Retrieve a single target by id, supporting fuzzy matching.

        Parameters
        ----------
        target_id : `str`
            The target id to look up, exact or fuzzy-matched.

        Returns
        -------
        target : `Target` or `None`
            The matching target, or `None` if no target matches.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.get_target(self, target_id)

    def read_saved(self, target_id: str) -> Target | None:
        """Read one target's saved record straight from storage.

        The in-memory catalog is not used. Use this to check what a save
        really wrote, as another program would see it.

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
        """Create a Target, scan its directories, and register it.

        This method is used to track a new astronomical
        object. It not only creates the database record but also scans
        the local filesystem directories matching the target's name to
        automatically associate any pre-existing raw image frames.

        Parameters
        ----------
        target_id : `str`
            The id of the target to create.

        Returns
        -------
        target : `Target`
            The newly created (or existing, matching) Target.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.create_target(self, target_id)

    def add(self, target: Target) -> None:
        """Append an existing Target object to the catalog.

        Parameters
        ----------
        target : `Target`
            The target to add.
        """
        if not any(t.id == target.id for t in self._targets):
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
        """Commit all touched targets back to database storage."""
        from astrometricslib.pipelines.shared import target_records

        target_records.save_targets(self)

    # -- Actions on a single target's frames -------------------------------

    def add_frame(
        self,
        target: Target,
        path: str,
        role: str = "LIGHT",
        filter_type: str | None = None,
        camera: str | None = None,
    ) -> object:
        """Add a single FrameRecord by parsing its FITS metadata.

        This is useful for manually associating a specific image with a target.
        It reads the FITS header to extract vital metadata (like filter type
        and camera used) to ensure the frame is calibrated correctly later on.

        Parameters
        ----------
        target : `Target`
            The target to add the frame to.
        path : `str`
            Path to the FITS file to parse.
        role : `str`, optional
            The frame's role (e.g. ``"LIGHT"``, ``"DARK"``, ``"BIAS"``,
            ``"FLAT"``). Defaults to ``"LIGHT"``.
        filter_type : `str`, optional
            Filter override; parsed from the header when omitted.
        camera : `str`, optional
            Camera name override; parsed from the header when omitted.

        Returns
        -------
        frame_record : `astrometricslib.models.target.FrameRecord`
            The newly added frame record.
        """
        from astrometricslib.pipelines.shared.frame_grouping import add_frame

        return add_frame(target, path, role, filter_type, camera)

    def reindex_frames(
        self,
        target: Target,
        prune_missing: bool = False,
        catalog_access: object = None,
        refresh_headers: bool = False,
    ) -> None:
        """Sync frame records from disk and recompute total exposure time.

        Parameters
        ----------
        target : `Target`
            The target whose frames should be reindexed.
        prune_missing : `bool`, optional
            If `True`, remove frame records whose files no longer
            exist on disk. Defaults to `False`.
        refresh_headers : `bool`, optional
            If `True`, also re-read header-derived acquisition
            conditions (pier side, airmass, altitude, pixel scale,
            cooling and focuser telemetry) on frames already tracked.
            Scanning alone only builds records for previously unseen
            files, so fields added to `FrameRecord` after a frame was
            indexed stay `None` until this runs. Defaults to `False`.
        catalog_access : `AbstractCatalogAccess`, optional
            Database tool to use instead of this catalog's own.
        """
        from astrometricslib.pipelines.shared.target_records import reindex_frames

        reindex_frames(
            target,
            prune_missing=prune_missing,
            catalog_access=catalog_access,
            refresh_headers=refresh_headers,
        )

    def get_header(self, path: str, target: Target | None = None) -> builtins.list[dict[str, str]]:
        """Read header information from a FITS image file.

        If a `target` is provided, this function will first double-check
        that the image file actually belongs to that target before reading
        it to ensure data safety.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to read.
        target : `Target`, optional
            If given, verify `path` belongs to this target before
            reading.

        Returns
        -------
        header_cards : `list[dict[str, str]]`
            The FITS primary header's card entries for `path`.

        Raises
        ------
        ValueError
            If `target` is given and `path` doesn't belong to it.
        """
        if target is not None:
            belongs_to_target = any(f.path == path for f in target.frames) or path in (
                target.stacking.processed_image,
                target.stacking.stacked_image,
                target.spectral_stacking.stacked_image,
            )
            if not belongs_to_target:
                raise ValueError(f"Path {path} does not belong to target {target.id}")

        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.get_fits_header(path)

    def get_frame(self, target: Target, iso: str, exposure: str, index: int = 0) -> str:
        """Retrieve a frame path for a target by ISO, exposure, and index.

        Parameters
        ----------
        target : `Target`
            The target to search.
        iso : `str`
            The ISO/gain setting to match.
        exposure : `str`
            The exposure length to match.
        index : `int`, optional
            Which matching frame to return, by order. Defaults to 0.

        Returns
        -------
        frame_path : `str`
            The path of the matching frame.
        """
        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.get_frame(target, iso, exposure, index)

    def delete_images(self, paths: builtins.list[str], target_id: str | None = None) -> dict:
        """Remove files from disk and the target's frame list if present.

        Parameters
        ----------
        paths : `list` [`str`]
            File paths to delete.
        target_id : `str`, optional
            If given, also remove matching frame records from this
            target's frame list.

        Returns
        -------
        result : `dict`
            A summary of the deletion outcome.
        """
        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.delete_images(paths, self, target_id)

    def measure_frame_input_quality(
        self,
        target: Target,
        include_fwhm: bool = False,
        remeasure: bool = False,
        camera_name: str | None = None,
        save: bool = True,
    ) -> dict[str, int]:
        """Measure the image quality of a target's frames before stacking.

        This allows us to evaluate and filter out bad frames early in the
        process. The checks are incremental, meaning if the process is
        interrupted, it can pick up where it left off without starting over.

        Parameters
        ----------
        target : `Target`
            The target whose frames are measured.
        include_fwhm : `bool`, optional
            Whether to also measure FWHM (default `False`); roughly 50x
            the cost of the other metrics.
        remeasure : `bool`, optional
            Whether to re-measure frames that already have values
            (default `False`).
        camera_name : `str`, optional
            Restrict to frames from this camera, matched
            case-insensitively as a substring.
        save : `bool`, optional
            Whether to record the target afterwards (default `True`).

        Returns
        -------
        counts : `dict` [`str`, `int`]
            ``measured``/``skipped``/``failed`` frame counts.
        """
        from astrometricslib.pipelines.shared.quality import frame_statistics

        counts = frame_statistics.measure_frame_input_quality(
            target,
            include_fwhm=include_fwhm,
            remeasure=remeasure,
            camera_name=camera_name,
        )
        if save and counts["measured"]:
            self.save()
        return counts

    def query(
        self,
        target_id: str | None = None,
        text: str | None = None,
        camera: str | None = None,
        ra: float | None = None,
        dec: float | None = None,
        radius_deg: float | None = None,
        detail: str = "summary",
        include_frames: int = 0,
        sort: str = "newest",
        include_empty: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Look up targets in short records. Nothing is changed.

        This is the safe way to read targets: a whole target object holds
        every frame, and a list of them is far larger than a reply can be.
        Here each target is one short row, or one target is described with
        its frames grouped by night, filter, exposure and camera.

        Parameters
        ----------
        target_id : `str`, optional
            Describe this one target (``detail="full"``), or find it for a
            summary row. Matched loosely, as the app does.
        text : `str`, optional
            Keep targets whose id or common name contains this text,
            ignoring case.
        camera : `str`, optional
            Keep targets with light frames from this camera (a substring,
            ignoring case).
        ra : `float`, optional
            Right ascension, in degrees, of the centre of a region search.
        dec : `float`, optional
            Declination, in degrees, of the centre of a region search.
        radius_deg : `float`, optional
            Radius of the region search, in degrees.
        detail : `str`, optional
            ``"summary"`` (default): one row per target. ``"full"``: one
            target's record (needs ``target_id``) with grouped frames, stack
            paths and flags, and condensed quality summaries.
            ``"cameras"``: the cameras used and how many frames each took.
        include_frames : `int`, optional
            With ``detail="full"``, also list this many of the newest light
            frames (at most 50), with their measurements.
        sort : `str`, optional
            ``"newest"`` (default, by the last frame taken) or ``"name"``.
        include_empty : `bool`, optional
            Also list targets with no frames, such as the placeholders the
            calibration folders create. Off by default.
        limit : `int`, optional
            How many rows to return, from 1 to 200. Defaults to 50.
        offset : `int`, optional
            How many rows to skip, for paging.

        Returns
        -------
        answer : `dict` [`str`, `Any`]
            ``targets`` (or ``target``, ``cameras``, ``field_centers``) with
            ``total_matching`` and whether the list was cut; or
            ``{"error": ...}``.
        """
        from astrometricslib.api.target_overview import describe_target, summarize_target
        from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

        if detail not in ("summary", "full", "cameras"):
            return {"error": "detail must be one of: summary, full, cameras."}
        if sort not in ("newest", "name"):
            return {"error": "sort must be 'newest' or 'name'."}
        touched_before = set(getattr(self, "_touched_target_ids", set()))
        try:
            targets = self.list()
        finally:
            self._touched_target_ids = touched_before  # looking is not editing
        if detail == "cameras":
            from astrometricslib.pipelines.shared.quality import frame_statistics

            return {"cameras": frame_statistics.list_camera_names(targets)}
        if detail == "full":
            if not target_id:
                return {"error": "detail='full' needs a target_id."}
            target = next((item for item in targets if item.id.lower() == target_id.lower()), None)
            if target is None:
                target = next((item for item in targets if target_id.lower() in item.id.lower()), None)
            if target is None:
                return {"error": f"No target matches {target_id!r}."}
            return {"target": describe_target(target, include_frames)}
        if (ra is None) != (dec is None) or (radius_deg is not None and ra is None):
            return {"error": "A region search needs ra, dec and radius_deg together."}

        rows = []
        for target in targets:
            if target_id and target_id.lower() not in target.id.lower():
                continue
            if text and text.lower() not in f"{target.id} {target.common_name}".lower():
                continue
            row = summarize_target(target)
            if not include_empty and not target.frames:
                continue
            if camera and not any(camera.lower() in name.lower() for name in row["cameras"]):
                continue
            if radius_deg is not None:
                try:
                    separation = _angular_separation_deg(
                        ra,
                        dec,
                        parse_coordinate_string(target.ra, True),
                        parse_coordinate_string(target.dec, False),
                    )
                except TypeError, ValueError:
                    continue
                if separation > radius_deg:
                    continue
                row["separation_deg"] = round(separation, 3)
            rows.append(row)
        if sort == "name":
            rows.sort(key=lambda row: row["id"].lower())
        else:
            rows.sort(key=lambda row: row["last_frame"] or "", reverse=True)
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        return {
            "total_matching": len(rows),
            "offset": offset,
            "truncated": offset + limit < len(rows),
            "targets": rows[offset : offset + limit],
        }

    @background_job("diagnostics", grace_period_seconds=20.0)
    def imaged_field_centers(self, max_frames_per_target: int = 12) -> builtins.list[dict[str, Any]]:
        """List the distinct sky positions the library has imaged.

        Reads the position from the FITS header of the first few frames of
        every target. On a large library kept on a slow drive this takes
        about half a minute, so through the MCP server it runs as a
        background job: a quick answer comes straight back, a slow one
        returns a job id to follow with ``jobs_query``.

        Parameters
        ----------
        max_frames_per_target : `int`, optional
            How many frames of each target to read. Defaults to 12.

        Returns
        -------
        centers : `list` [`dict`]
            One entry per distinct position, with its coordinates and the
            targets that share it.
        """
        from astrometricslib.pipelines.astrometry.utilities.catalog_seeding import derive_field_centers

        touched_before = set(getattr(self, "_touched_target_ids", set()))
        try:
            targets = self.list()
        finally:
            self._touched_target_ids = touched_before
        return derive_field_centers(targets, max_frames_per_target=max_frames_per_target)

    def list_camera_names(self) -> dict[str, int]:
        """Find out which cameras were used to take the images in the catalog.

        This is helpful when processing pipelines need to be run on images
        taken by a specific camera, but aren't sure which camera names exist
        in the data yet.

        Returns
        -------
        counts_by_camera : `dict` [`str`, `int`]
            Each distinct camera name found mapped to how many frames
            across the whole catalog used it, sorted by count
            descending.
        """
        from astrometricslib.pipelines.shared.quality import frame_statistics

        return frame_statistics.list_camera_names(self.list())

    def camera_index(self, camera_names: builtins.list[str]) -> dict[str, Any]:
        """Summarize each target's light frames per configured camera.

        Used by the target list to filter by camera and sort by the most
        recent image without sending every frame to the browser.

        Parameters
        ----------
        camera_names : `list` [`str`]
            The configured camera names to report on.

        Returns
        -------
        index : `dict`
            See `build_target_camera_index`.
        """
        from astrometricslib.drivers.camera_profile_store import camera_identity
        from astrometricslib.pipelines.shared.target_camera_index import build_target_camera_index

        return build_target_camera_index(self.list(), camera_names, camera_identity)

    def get_calibration_frame_statistics(
        self,
        target: Target,
        frames: builtins.list[object],
        grouped: bool = True,
        camera: str | None = None,
    ) -> object:
        """Return statistics about how frames match with calibration data.

        Parameters
        ----------
        target : `Target`
            The target whose frames are being summarized.
        frames : `list`
            The frame records to summarize.
        grouped : `bool`, optional
            If `True` (default), group statistics by filter/exposure/
            dark-match. If `False`, return flat raw frame counts.
        camera : `str`, optional
            Camera name to scope the calibration match against.

        Returns
        -------
        result : `Any`
            Grouped filter/exposure/dark-match statistics if
            `grouped` is `True`; otherwise flat raw frame counts.
        """
        from astrometricslib.pipelines.shared.quality import frame_statistics

        if not grouped:
            return frame_statistics.get_frame_stats(target)

        from astrometricslib.api.processing import CalibrationCatalog

        calibration = CalibrationCatalog(self._config)
        return frame_statistics.get_frame_stats_grouped(target, calibration, camera)
