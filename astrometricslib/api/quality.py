"""The quality sub-API: stored measurements, and checks of frames and stacks.

`QualityRecords` is ``astrometrics.quality``. It holds five objects, each
reached by name:

* ``records`` (`QualityMeasurements`, here): the measurements earlier runs
  stored (`query`, `get`), a text that changes when they do
  (`get_data_version`), the picture behind each row (`get_paths`), the
  declared metrics (`get_metrics`, `get_metric`), and the capture settings
  and calibration identities the rows point at.
* ``frames`` (`QualityFrames`, in `_quality_frames.py`): measure raw frames,
  record a target's frame measurements, pick a frame to blink against, and
  follow positions from one image to another.
* ``stacks`` (`QualityStacks`, in `_quality_frames.py`): measure a stack,
  read its aperture record, and record the noise of subset stacks.
* ``detections`` (`QualityDetections`, in `_quality_building_blocks.py`):
  one star's profile, the stars a finder detects, and star widths by color.
* ``blocks`` (`QualityBlocks`, in `_quality_building_blocks.py`): the small
  measurements a script combines.

Frame state (statuses, the set-aside frames and putting them back) lives on
``astrometrics.targets.frames``. Each method is a thin wrapper of one owner
function, which its docstring names.
"""

from collections.abc import Callable, Sequence
from typing import Any

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.renames import MovedNames
from astrometricslib.api._quality_building_blocks import QualityBlocks, QualityDetections
from astrometricslib.api._quality_frames import QualityFrames, QualityStacks
from astrometricslib.foundation.arguments import TimeInput
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import NotFoundError
from astrometricslib.models.page import Page
from astrometricslib.models.quality.calibration_identities import CalibrationIdentities
from astrometricslib.models.quality.capture_setting import StoredCaptureSetting
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.models.quality.row_frames import QualityRowResolution
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.api_arguments import TargetLookup

__all__ = ["QualityMeasurements", "QualityRecords"]


class QualityMeasurements(MovedNames):
    """The quality measurements that earlier runs stored, and what they mean.

    Reached as ``astrometrics.quality.records``. Nothing here measures or
    writes. A library with no stored measurements yet gives empty answers,
    and no database file or table is created by asking.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder.
    targets : `TargetLookup`, optional
        The target catalog, which `get_paths` reads a target's frames from.
    """

    def __init__(self, config: AppConfiguration, targets: TargetLookup | None = None) -> None:
        self._config = config
        self._targets = targets

    def query(
        self,
        *,
        metric: str | None = None,
        level: str | None = None,
        target: Target | None = None,
        night: str | None = None,
        camera: str | None = None,
        telescope: str | None = None,
        since: TimeInput | None = None,
        until: TimeInput | None = None,
        latest_only: bool = True,
        variant: str | None = None,
        library_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[QualityMeasurement]:
        """Read the stored quality measurements that match some filters.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.query_measurements

        Parameters
        ----------
        metric : `str`, optional
            Only this metric. `get_metrics` lists the names.
        level : `str`, optional
            Only subjects of this level: ``"frame"``, ``"stack"``,
            ``"target"`` and so on (the values of `SubjectLevel`).
        target : `Target`, optional
            Only this target.
        night : `str`, optional
            Only this observing night, ``YYYY-MM-DD``.
        camera : `str`, optional
            Only measurements made with this camera (its name as stored with
            the capture setting, matched exactly).
        telescope : `str`, optional
            Only measurements made with this telescope.
        since, until : `str`, `datetime` or `Time`, optional
            Only measurements made from ``since`` to ``until``. Text is ISO
            8601; no offset means UTC.
        latest_only : `bool`, optional
            Give the newest row for each subject, metric and channel
            (default) instead of every row.
        variant : `str`, optional
            Only rows about the subset stack with this label.
        library_only : `bool`, optional
            Only rows that are not about a subset stack. Cannot be combined
            with ``variant``.
        limit : `int`, optional
            The most measurements to return. `None` (the default) returns
            every match.
        offset : `int`, optional
            How many matching measurements to skip. Defaults to 0.

        Returns
        -------
        rows : `Page` [`QualityMeasurement`]
            The matching measurements, oldest first, and how many matched in
            all.

        Notes
        -----
        Raises `InvalidArgumentError` if ``metric`` is not registered,
        ``level`` is unknown, a time cannot be read, both ``variant`` and
        ``library_only`` are given, ``limit`` is below 1 or ``offset`` is
        below 0. A stored row that fails the model's checks raises
        `StorageError`.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import query_measurements

        return query_measurements(
            self._config,
            metric=metric,
            level=level,
            target=target,
            night=night,
            camera=camera,
            telescope=telescope,
            since=since,
            until=until,
            latest_only=latest_only,
            variant=variant,
            library_only=library_only,
            limit=limit,
            offset=offset,
        )

    def get(self, row_id: int, *, required: bool = False) -> QualityMeasurement | None:
        """Read one stored measurement by its row id.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.get_measurement

        A row is found even when a newer row about the same subject replaced
        it, so a citation always finds what it cited.

        Parameters
        ----------
        row_id : `int`
            The store's row id, such as one a statement cites.
        required : `bool`, optional
            Raise `NotFoundError` instead of returning `None` when no row has
            this id.

        Returns
        -------
        row : `QualityMeasurement` or `None`
            The row, or `None` when no row has this id.

        Raises
        ------
        NotFoundError
            If ``required`` is set and no row has this id.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import get_measurement

        row = get_measurement(self._config, row_id)
        if row is None and required:
            raise NotFoundError(f"No stored quality row has the id {row_id}.")
        return row

    def get_data_version(
        self, *, metric: str | None = None, level: str | None = None, target: Target | None = None
    ) -> str:
        """Give a short text that changes whenever the matching rows change.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.get_data_version

        A script or a cache that works something out from the rows `query`
        gives can keep its answer while this text stays the same. Asking
        reads no rows. The text is opaque: compare it with an earlier one.

        Parameters
        ----------
        metric : `str`, optional
            Only this metric. `get_metrics` lists the names.
        level : `str`, optional
            Only subjects of this level, as in `query`.
        target : `Target`, optional
            Only this target.

        Returns
        -------
        version : `str`
            The text. It changes when a row of the metric, level and target
            is added, removed or replaced (or a frame of the target moves to
            another target), and stays the same otherwise.

        Notes
        -----
        Raises `InvalidArgumentError` if ``metric`` is not registered or
        ``level`` is unknown.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import get_data_version

        return get_data_version(self._config, metric=metric, level=level, target=target)

    def get_paths(self, row_ids: Sequence[int]) -> QualityRowResolution:
        """Find the picture behind each stored quality row.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.find_row_paths

        A stored row names its frame or stack by a stable id. This turns the
        ids into file paths a viewer can open, and says which ids no row has.

        Parameters
        ----------
        row_ids : `Sequence` [`int`]
            The store's row ids, such as the ones a statement cites.

        Returns
        -------
        resolution : `QualityRowResolution`
            One `QualityRowFile` for each row found, in the order asked, and
            the ids that no stored row has. A row whose picture cannot be
            found has ``path`` set to `None` and a plain ``reason``.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import find_row_paths

        targets = self._targets

        def get_target(target_id: str) -> Target | None:
            """Give the target with an id, or `None`.

            Returns
            -------
            target : `Target` or `None`
                The target.
            """
            return None if targets is None else targets.get(target_id)

        return find_row_paths(self._config, row_ids, get_target)

    def get_metrics(self, checkpoint: str | None = None) -> list[MetricSpec]:
        """List the declared quality metrics and what they mean.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.list_metrics

        Parameters
        ----------
        checkpoint : `str`, optional
            Only the metrics measured at this checkpoint, such as
            ``"frame"`` or ``"spectroscopy"``.

        Returns
        -------
        metrics : `list` [`MetricSpec`]
            The whole declarations (name, unit, levels, purpose, meaning,
            which way is better, how the number is estimated), in the order
            they are registered.

        Notes
        -----
        An unknown checkpoint raises `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import list_metrics

        return list_metrics(checkpoint)

    def get_metric(self, name: str) -> MetricSpec:
        """Read what one quality metric means.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.get_metric_spec

        Parameters
        ----------
        name : `str`
            The metric's name, such as ``"frame.star_width_moffat_px"``.

        Returns
        -------
        spec : `MetricSpec`
            The whole declaration.

        Notes
        -----
        A name that no metric has raises `NotFoundError`.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import get_metric_spec

        return get_metric_spec(name)

    def get_capture_settings(self, ids: Sequence[int] | None = None) -> list[StoredCaptureSetting]:
        """Read the capture settings that stored measurements point at.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.read_capture_settings

        A stored measurement carries only a ``capture_setting_id``. This
        turns those ids into the camera, telescope, filter, exposure, gain,
        offset and binning, in one read.

        Parameters
        ----------
        ids : `Sequence` [`int`], optional
            Only these ids. Leave it out to read every stored setting. An id
            that is not stored is left out of the result.

        Returns
        -------
        settings : `list` [`StoredCaptureSetting`]
            Each setting (``setting``) with its id (``id``), in id order.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import read_capture_settings

        return read_capture_settings(self._config, ids)

    def get_calibration_identities(
        self, target: Target | None = None, *, run_ids: Sequence[str] | None = None
    ) -> list[CalibrationIdentities]:
        """Read which calibration each library stacking run used.

        Owner:
            astrometricslib.pipelines.shared.quality.stored_measurements.read_calibration_identities

        Two stacks of a target are confounded when their calibration
        differs: another flat or bias master (and, for the uncooled Nikon,
        dark master), another optic or camera, or another Siril or stacking
        algorithm version. Each record names the master files by path, size
        and modification time.

        Parameters
        ----------
        target : `Target`, optional
            Only the stacks of this target.
        run_ids : `Sequence` [`str`], optional
            Only these stacking runs (the run id a stack's measurements
            carry). Leave both out to read every stored record.

        Returns
        -------
        records : `list` [`CalibrationIdentities`]
            The records, oldest first.
        """
        from astrometricslib.pipelines.shared.quality.stored_measurements import read_calibration_identities

        return read_calibration_identities(self._config, target, run_ids)


class QualityRecords(MovedNames):
    """The quality sub-API, ``astrometrics.quality``, and its five objects.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes.
    targets : `TargetLookup`, optional
        The target catalog, which saves the frame measurement stamps.
    calibration_library : `Callable`, optional
        Gives the calibration library, for the bias frames. The library is
        built only when it is asked for.
    drivers : `Drivers`, optional
        The outside services the caller chose, for the checks that
        plate-solve or measure spectra. Any left out is the built-in one.
    """

    records: QualityMeasurements
    frames: QualityFrames
    stacks: QualityStacks
    detections: QualityDetections
    blocks: QualityBlocks

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        targets: TargetLookup | None = None,
        calibration_library: Callable[[], Any] | None = None,
        drivers: Drivers | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self.records = QualityMeasurements(config, targets)
        """The stored measurements and the metric declarations."""
        self.frames = QualityFrames(config, targets, calibration_library, drivers)
        """Measure and record raw frames; follow positions across images."""
        self.stacks = QualityStacks(config, drivers)
        """Measure stacks and record the noise of subset stacks."""
        self.detections = QualityDetections()
        """One star's profile, the detected stars and star widths by color."""
        self.blocks = QualityBlocks(config)
        """The small measurements a script combines."""
