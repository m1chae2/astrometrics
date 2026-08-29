"""Main interface for managing targets in the catalog.

`TargetCatalog` allows you to create, read, update, and delete targets.
It also handles target-specific actions like adding new image frames,
re-indexing frames, and checking statistics. This class stores the active
targets in memory and coordinates with lower-level task modules to perform
work.
"""

import builtins

from astrometricslib.catalog_services.frame_scanning import classify_and_sort_fits_files
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions
from astrometricslib.utilities.config_loader import AppConfiguration

__all__ = [
    "TargetCatalog",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
]


class TargetCatalog:
    """CRUD operations for the target catalog, plus object management.

    This class acts as the primary interface for managing observation
    targets. A target represents a physical region of the sky and
    serves as the central anchor for all related data (raw frames,
    calibration masters, stacked images). Use this catalog to query,
    create, or delete targets within your observatory's library.
    """

    def __init__(self, config: AppConfiguration, catalog_access: object):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with configuration settings and a database manager.

        This setup keeps the list of targets and tracked changes right here
        in memory, which prevents confusing circular dependencies when
        saving data to disk later.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        catalog_access : `AbstractCatalogAccess`
            Storage backend for the target catalog.
        """
        self._config = config
        self.catalog_access = catalog_access
        self._targets: list = catalog_access.get("target_catalog", {}) or []
        self._touched_target_ids: set = set()

    # -- CRUD ------------------------------------------------------------

    def list(self) -> builtins.list[Target]:
        """Return every Target object from the in-memory catalog.

        Returns
        -------
        result : `list` [`Target`]
            The list of all active targets.
        """
        from astrometricslib.catalog_services import target_records

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
        from astrometricslib.catalog_services import target_records

        return target_records.get_target(self, target_id)

    def create(self, target_id: str) -> Target:
        """Create a Target, scan its directories, and register it.

        This method is used when you want to track a new astronomical
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
        from astrometricslib.catalog_services import target_records

        return target_records.create_target(self, target_id)

    def add(self, target: Target) -> None:
        """Append an existing Target domain object to the catalog.

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
        from astrometricslib.catalog_services import target_records

        return target_records.delete_target(self, target_id)

    def save(self) -> None:
        """Commit all touched targets back to database storage."""
        from astrometricslib.catalog_services import target_records

        target_records.save_targets(self)

    # -- Object management (target-scoped, not CRUD) ----------------------

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
            Storage backend override; defaults to this catalog's own.
        """
        from astrometricslib.catalog_services.target_records import reindex_frames

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
        """
        if target is not None:
            from astrometricslib.pipelines.dispatch import get_header_information

            return get_header_information(target, path)

        from astrometricslib.catalog_services import image_conversions

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
        from astrometricslib.catalog_services import image_conversions

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
        from astrometricslib.catalog_services import image_conversions

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
        from astrometricslib.data_access import frame_statistics

        counts = frame_statistics.measure_frame_input_quality(
            target,
            include_fwhm=include_fwhm,
            remeasure=remeasure,
            camera_name=camera_name,
        )
        if save and counts["measured"]:
            self.save()
        return counts

    def list_camera_names(self) -> dict[str, int]:
        """Find out which cameras were used to take the images in the catalog.

        This is helpful when you need to run processing pipelines on images
        taken by a specific camera, but aren't sure which camera names exist
        in the data yet.

        Returns
        -------
        counts_by_camera : `dict` [`str`, `int`]
            Each distinct camera name found mapped to how many frames
            across the whole catalog used it, sorted by count
            descending.
        """
        from astrometricslib.data_access import frame_statistics

        return frame_statistics.list_camera_names(self.list())

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
        from astrometricslib.data_access import frame_statistics

        if not grouped:
            return frame_statistics.get_frame_stats(target)

        from astrometricslib.api.processing import CalibrationCatalog

        calibration = CalibrationCatalog(self._config)
        return frame_statistics.get_frame_stats_grouped(target, calibration, camera)
