"""The quality root: every quality read starts here.

`QualityRecords` is the child of `Astrometrics` reached as
`astrometrics.quality`. It answers three kinds of question:

* Stored: `query` reads the quality measurements that earlier runs saved,
  and `metrics` lists what each metric means.
* Live: `measure_frames` measures a target's raw frames and `measure_stack`
  a finished stack, now, without changing anything. `measure_folder`
  measures any folder of FITS files with the stored `frame.*` metric set and
  returns the numbers.
* One star: `measure_star_profile` measures the radial profile of the star at a
  clicked position, with fitted Moffat and Gaussian widths, and a straight cut
  through it.
* Detections: `measure_detections` lists the stars the astrometry run's star
  finder detects in one image, so a viewer can draw them.
* Frame state (statuses, the set-aside frames and putting them back) lives on
  ``astrometrics.targets.frames``.
* Recorded: `record_frame_measurements` measures a target's light frames and
  stores the numbers that `query` reads.
* Follow a pixel: `map_position` finds where a pixel of one image is on
  another, from plate solutions and stack records; `match_frame_positions`
  does it by matching two frames' stars.
* Building blocks: `blocks` (a `QualityBlocks`, in
  `_quality_building_blocks.py`) gives a script the library's own small
  measurements: `measure_sky`, `find_stars`, `star_widths`,
  `measure_sky_gradient`, `measure_aperture`, `sky_corners`,
  `solved_stars`, `robust_sigma` and `sigma_clip`.

The argument helpers it shares with `ProcessingPipelines` live in
`pipelines/shared/api_arguments.py`.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.renames import MovedNames
from astrometricslib.api._quality_building_blocks import QualityBlocks
from astrometricslib.foundation.arguments import (
    TimeInput,
    check_choice,
    check_include,
    check_limit,
    reject_unused_arguments,
    to_epoch_seconds,
)
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.jobs.runner import background_job, registered_job
from astrometricslib.foundation.tuning import TuningReport
from astrometricslib.models.quality.aperture_record import OddEvenApertureRecord
from astrometricslib.models.quality.blink_pair import BlinkPair, FramePositionMatch
from astrometricslib.models.quality.calibration_identities import CalibrationIdentities
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.detections import DetectionsMeasurement
from astrometricslib.models.quality.folder_measurement import FolderMeasurementReport
from astrometricslib.models.quality.frame_measurement_summary import FrameMeasurementSummary
from astrometricslib.models.quality.measurement import QualityMeasurement, SubjectLevel
from astrometricslib.models.quality.metric_spec import Checkpoint, MetricSpec, MetricSummary
from astrometricslib.models.quality.position_mapping import PositionMapping
from astrometricslib.models.quality.quality_reports import (
    InputQualityReport,
    RawFrameCheckReport,
    SpectralFrameCheckReport,
    StackQualityReport,
)
from astrometricslib.models.quality.query_result import QualityQueryResult
from astrometricslib.models.quality.row_frames import QualityRowFile, QualityRowResolution
from astrometricslib.models.quality.star_profile import StarProfileMeasurement
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.api_arguments import (
    STACK_KINDS,
    StackKind,
    TargetLookup,
    make_frame_selection,
    resolve_target,
)
from astrometricslib.pipelines.shared.position_mapping import RecordCache

__all__ = ["QualityRecords"]

FRAME_QUALITY_KINDS = ("input_quality", "raw_check", "spectral")
FRAME_QUALITY_SECTIONS = ("fwhm", "spectra")
_FRAME_QUALITY_ARGUMENTS = {
    "input_quality": (
        "remeasure",
        "camera_id",
        "filter_name",
        "first_file",
        "last_file",
        "since",
        "until",
        "trend_frames",
        "trend_threshold_percent",
    ),
    "raw_check": (
        "filter_name",
        "first_file",
        "last_file",
        "since",
        "until",
        "trend_frames",
        "trend_threshold_percent",
    ),
    "spectral": ("first_file", "last_file", "since", "until", "exposure_seconds", "predict_exposure_seconds"),
}
_FRAME_QUALITY_SECTIONS_BY_KIND = {
    "input_quality": ("fwhm", "spectra"),
    "raw_check": ("spectra",),
    "spectral": (),
}


class QualityRecords(MovedNames):
    """The start of every quality read: stored measurements and live checks.

    `query` and `metrics` read what earlier runs stored. The other methods
    measure raw frames and finished stacks now, without changing anything.
    The checks show whether a night had good tracking and clear skies, which
    frames the stacker would set aside, and whether a restack made the
    stack better or worse. A target id that names no target, or a stack
    file that does not exist, raises `NotFoundError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the frames folder.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes.
    targets : `TargetLookup`, optional
        The target catalog, used to turn a target name into a `Target`.
    calibration_library : `Callable`, optional
        Gives the calibration library, when `record_frame_measurements`
        needs the bias frames. The library is built only when it is asked
        for.
    drivers : `Drivers`, optional
        The outside services the caller chose, for the checks that plate-solve
        or measure spectra. Any left out is the built-in one.
    owner : `Callable`, optional
        Gives the `Astrometrics` this belongs to, which the tuners of
        `measure_tuned_parameters` read stored spectra through.
    """

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        targets: TargetLookup | None = None,
        calibration_library: Callable[[], Any] | None = None,
        drivers: Drivers | None = None,
        owner: Callable[[], Any] | None = None,
    ) -> None:
        self._drivers = drivers
        self._config = config
        self._storage = storage
        self._targets = targets
        self._calibration_library_provider = calibration_library
        self._owner_provider = owner
        self._record_cache = RecordCache()
        self.blocks = QualityBlocks(config)
        """The small measurements a script combines (`QualityBlocks`)."""

    def measure_tuned_parameters(
        self,
        setup: str | None = None,
        only: list[str] | None = None,
        save: bool = False,
    ) -> TuningReport:
        """Work out this library's tuned values from the observatory's data.

        A thin wrapper of `astrometricslib.parameters.tune` over this
        library's tuners. Each tuner reads the stored data of one setup and
        derives one family of values. Each value is checked against its
        allowed range and smallest sample. Nothing is saved unless ``save``
        is set; then the config is backed up and each value is written with
        where it came from.

        Parameters
        ----------
        setup : `str`, optional
            The setup to tune. The active setup when left out.
        only : `list` [`str`], optional
            Run only the tuners with these names.
        save : `bool`, optional
            Write the values to the config.

        Returns
        -------
        report : `TuningReport`
            The values that would be set or moved, and those skipped,
            refused or unchanged.

        Notes
        -----
        The core raises `NotFoundError` if the setup is not in the config,
        or there is no active setup, and `InvalidArgumentError` if ``only``
        names a tuner that does not exist.
        """
        from astrometricslib.foundation.tuning import tune

        services = {"astrometrics": self._owner_provider()} if self._owner_provider else {}
        return tune(("astrometricslib",), self._config, setup=setup, only=only, save=save, services=services)

    def query(
        self,
        metric: str | None = None,
        level: str | None = None,
        target_id: str | None = None,
        observing_night_id: str | None = None,
        camera_id: str | None = None,
        telescope: str | None = None,
        since: TimeInput | None = None,
        row_ids: list[int] | None = None,
        latest_only: bool = True,
        variant: str | None = None,
        library_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> QualityQueryResult:
        """Read the quality measurements that earlier runs stored.

        Nothing is measured and nothing is written. A library with no
        stored measurements yet gives an empty answer, and no database file
        or table is created by asking. There is no limit unless one is given.

        Parameters
        ----------
        metric : `str`, optional
            Only this metric. See `metrics` for the names.
        level : `str`, optional
            Only subjects of this level: ``"frame"``, ``"target"``, and so
            on (the values of `SubjectLevel`).
        target_id : `str`, optional
            Only this target.
        observing_night_id : `str`, optional
            Only this observing night.
        camera_id : `str`, optional
            A filter: only measurements made with this camera (its name as
            stored with the capture setting, matched exactly).
        telescope : `str`, optional
            Only measurements made with this telescope.
        since : `str`, `datetime` or `Time`, optional
            Only measurements made at or after this time. Text is ISO 8601;
            no offset means UTC.
        row_ids : `list` [`int`], optional
            Only these rows, exactly as stored, even if newer ones exist.
            ``latest_only`` is then ignored.
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
            How many matching measurements to skip, for paging. Defaults
            to 0.

        Returns
        -------
        answer : `QualityQueryResult`
            The matching measurements, oldest first, how many matched in all
            and whether the limit cut the list.

        Raises
        ------
        InvalidArgumentError
            If ``metric`` is not registered (the error lists the registered
            names), ``level`` is unknown, ``since`` cannot be read as a
            time, both ``variant`` and ``library_only`` are given, ``limit``
            is below 1 or ``offset`` is below 0.

        Notes
        -----
        A stored row that fails the model's checks raises `StorageError`.
        """
        from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
        from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry

        if metric is not None:
            check_choice("metric", metric, get_metric_registry().names())
        if level is not None:
            check_choice("level", level, tuple(member.value for member in SubjectLevel))
        if variant is not None and library_only:
            raise InvalidArgumentError("Give either variant or library_only, not both.")
        if limit is not None and limit < 1:
            raise InvalidArgumentError("limit must be at least 1, or None for no limit.")
        if offset < 0:
            raise InvalidArgumentError("offset cannot be negative.")
        since_seconds = to_epoch_seconds(since, "since")
        since_time = None if since_seconds is None else datetime.fromtimestamp(since_seconds, tz=UTC)
        rows = QualityStore(quality_database_path(self._config)).query(
            metric=metric,
            level=None if level is None else SubjectLevel(level),
            target_id=target_id,
            observing_night_id=observing_night_id,
            camera=camera_id,
            telescope=telescope,
            since=since_time,
            row_ids=None if row_ids is None else [int(row_id) for row_id in row_ids],
            latest_only=latest_only,
            variant=variant,
            library_only=library_only,
        )
        registry = get_metric_registry()
        registered = set(registry.names())
        for row in rows:
            row.unit = registry.get(row.metric).unit if row.metric in registered else None
        page = rows[offset:] if limit is None else rows[offset : offset + limit]
        return QualityQueryResult(
            rows=page,
            total_matching=len(rows),
            offset=offset,
            truncated=limit is not None and offset + limit < len(rows),
        )

    def resolve_rows(self, row_ids: list[int]) -> QualityRowResolution:
        """Find the picture behind each stored quality row.

        A stored row names its frame or stack by a stable id. This turns the
        ids into file paths a viewer can open, and says which ids no row has.
        Nothing is measured or written.

        Parameters
        ----------
        row_ids : `list` [`int`]
            The store's row ids, such as the ones a statement cites.

        Returns
        -------
        resolution : `QualityRowResolution`
            One `QualityRowFile` for each row found, in the order asked, and
            the ids that no stored row has. A row whose picture cannot be
            found has `path` set to `None` and a plain `reason`.
        """
        asked = list(dict.fromkeys(int(row_id) for row_id in row_ids))
        found = {row.row_id: row for row in self.query(row_ids=asked, latest_only=False).rows}
        frame_paths_by_target: dict[str, dict[str, str]] = {}
        files = []
        for row_id in asked:
            row = found.get(row_id)
            if row is not None:
                files.append(self._row_file(row_id, row, frame_paths_by_target))
        return QualityRowResolution(
            rows=files, not_found_row_ids=[row_id for row_id in asked if row_id not in found]
        )

    def _row_file(
        self, row_id: int, row: QualityMeasurement, frame_paths_by_target: dict[str, dict[str, str]]
    ) -> QualityRowFile:
        """Find the file of one row's subject.

        Parameters
        ----------
        row_id : `int`
            The row id.
        row : `QualityMeasurement`
            The stored row.
        frame_paths_by_target : `dict` [`str`, `dict` [`str`, `str`]]
            A cache, filled here: for each target, its frames' paths by
            subject id.

        Returns
        -------
        file : `QualityRowFile`
            The row's file path, or the reason it has none.
        """
        import os

        from astrometricslib.foundation.paths import resolve_mounted_path
        from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id

        subject = row.subject
        path: str | None = None
        reason = ""
        if subject.level == SubjectLevel.RUN:
            reason = "This measurement is about a processing run, so it has no picture."
        elif subject.target_id is None or self._targets is None:
            reason = "The measurement names no target in the library."
        elif subject.level == SubjectLevel.STACK:
            file_name = subject.subject_id.removeprefix(f"{subject.target_id}/")
            candidate = resolve_mounted_path(
                os.path.join(str(self._config.get_stacks_path()), "lights", subject.target_id, file_name)
            )
            if os.path.isfile(candidate):
                path = candidate
            else:
                reason = "The stack file is no longer on disk."
        else:
            if subject.target_id not in frame_paths_by_target:
                target = self._targets.get(subject.target_id)
                frame_paths_by_target[subject.target_id] = (
                    {}
                    if target is None
                    else {frame_subject_id(frame.path, target.id): frame.path for frame in target.frames}
                )
            candidate = frame_paths_by_target[subject.target_id].get(subject.subject_id)
            candidate = None if candidate is None else resolve_mounted_path(candidate)
            if candidate is None:
                reason = "The frame is not in the target's frame list. It may have been set aside or deleted."
            elif not os.path.isfile(candidate):
                reason = "The frame file is no longer on disk."
            else:
                path = candidate
        return QualityRowFile(
            row_id=row_id,
            level=subject.level.value,
            target_id=subject.target_id,
            subject_id=subject.subject_id,
            path=path,
            reason=reason,
        )

    def capture_settings(self, ids: list[int] | None = None) -> dict[int, CaptureSetting]:
        """Read the capture settings that stored measurements point at.

        A stored measurement carries only a ``capture_setting_id``. This
        turns those ids into the camera, telescope, filter, exposure, gain
        setting, offset and binning, in one read. Nothing is measured or
        written, and a library with no quality database gives an empty
        dictionary without creating one.

        Parameters
        ----------
        ids : `list` [`int`], optional
            Only these ids. Leave it out to read every stored setting.
            An id that is not stored is left out of the result.

        Returns
        -------
        settings : `dict` [`int`, `CaptureSetting`]
            The settings, keyed by their id.
        """
        from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path

        return QualityStore(quality_database_path(self._config)).get_capture_settings(ids)

    def get_aperture_record(self, path: str) -> OddEvenApertureRecord | None:
        """Read the per-aperture noise record that a stack's job wrote.

        The odd–even difference job saves, beside a stack, the sum and the
        difference of each empty sky aperture it measured. A stacking run's
        quality row names the file. Nothing is measured or written.

        Parameters
        ----------
        path : `str`
            The record file.

        Returns
        -------
        record : `OddEvenApertureRecord` or `None`
            The file's content: ``version``, ``stack``, ``aperture_identity``,
            ``note`` and ``apertures`` (rows with ``index``, ``x``, ``y``,
            ``distance_from_center_px``, ``distance_from_target_px``, ``sum``
            and ``difference``). `None` when the file is missing, cannot be
            read, is of another version or does not have the expected fields.
        """
        from astrometricslib.pipelines.stacking.post_processing.odd_even_apertures import (
            load_odd_even_aperture_record,
        )

        data = load_odd_even_aperture_record(path)
        if data is None:
            return None
        try:
            return OddEvenApertureRecord.model_validate(data)
        except ValidationError:
            return None

    def calibration_identities(
        self, run_ids: list[str] | None = None, target_id: str | None = None
    ) -> list[CalibrationIdentities]:
        """Read which calibration each library stacking run used.

        Two stacks of a target are confounded when their calibration
        differs: another flat or bias master (and, for the uncooled Nikon,
        dark master), another optic or camera, or another Siril or stacking
        algorithm version. Each record names the master files by path, size
        and modification time. Nothing is measured or written, and a library
        with no quality database (or one not yet migrated) gives an empty
        list without creating anything.

        Parameters
        ----------
        run_ids : `list` [`str`], optional
            Only these stacking runs (the run id a stack's measurements carry).
        target_id : `str`, optional
            Only the stacks of this target. Leave both out to read every
            stored record.

        Returns
        -------
        records : `list` [`CalibrationIdentities`]
            The records, oldest first.
        """
        from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path

        return QualityStore(quality_database_path(self._config)).get_calibration_identities(
            run_ids, target_id
        )

    def metrics(
        self,
        name: str | None = None,
        checkpoint: str | None = None,
        detail: Literal["summary", "full"] = "summary",
    ) -> list[MetricSummary] | list[MetricSpec]:
        """List the declared quality metrics and what they mean.

        Parameters
        ----------
        name : `str`, optional
            One metric name. The list then holds only that metric.
        checkpoint : `str`, optional
            Only the metrics measured at this checkpoint, such as
            ``"frame"`` or ``"spectroscopy"``.
        detail : {``"summary"``, ``"full"``}, optional
            ``"summary"`` (the default) gives the name, unit, checkpoint,
            levels and purpose of each metric. Over 200 metrics are
            registered, so the full list is too long for an MCP reply.
            ``"full"`` gives the whole declaration, including the meaning,
            which way is better and how the number is estimated. Pair it with
            ``checkpoint`` or ``name``.

        Returns
        -------
        metrics : `list` [`MetricSummary`] or `list` [`MetricSpec`]
            The declarations, in the order they are registered.

        Notes
        -----
        An unknown ``name``, ``checkpoint`` or ``detail`` raises
        `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry

        check_choice("detail", detail, ("summary", "full"))
        if checkpoint is not None:
            check_choice("checkpoint", checkpoint, tuple(member.value for member in Checkpoint))
        registry = get_metric_registry()
        if name is not None:
            check_choice("name", name, registry.names())
            specs = [registry.get(name)]
        else:
            specs = list(registry.all())
        if checkpoint is not None:
            specs = [spec for spec in specs if spec.checkpoint.value == checkpoint]
        if detail == "full":
            return specs
        return [MetricSummary.from_spec(spec) for spec in specs]

    def _calibration_library(self) -> Any:
        """Give the calibration library, which holds the bias frames.

        Returns
        -------
        library : `CalibrationLibrary`
            The library.

        Raises
        ------
        InvalidArgumentError
            If this object was built without one.
        """
        if self._calibration_library_provider is None:
            raise InvalidArgumentError("This quality root was built without a calibration library.")
        return self._calibration_library_provider()

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_frames(
        self,
        target: str | Target,
        kind: Literal["input_quality", "raw_check", "spectral"] = "input_quality",
        include: list[str] | None = None,
        remeasure: bool = False,
        camera_id: str | None = None,
        limit: int = 50,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: TimeInput | None = None,
        until: TimeInput | None = None,
        trend_frames: int | None = None,
        trend_threshold_percent: float | None = None,
        exposure_seconds: float | None = None,
        predict_exposure_seconds: float | None = None,
    ) -> InputQualityReport | RawFrameCheckReport | SpectralFrameCheckReport:
        """Measure a target's raw light or spectrum frames.

        Nothing is saved. The frames are measured on a copy of the target, so
        the measurements never reach the saved catalog. This method measures
        a target's frames only: a folder of frames, in the library or not, is
        measured by `measure_folder`, and which frames a stack would set aside
        is said by `ProcessingPipelines.predict_stack`.

        Arguments used by each kind (any other argument is refused):

        - ``"input_quality"``: ``remeasure``, ``camera_id``, ``limit``,
          ``filter_name``, ``first_file``, ``last_file``, ``since``,
          ``until``, ``trend_frames``, ``trend_threshold_percent``;
          ``include`` may hold ``"fwhm"`` and ``"spectra"``.
        - ``"raw_check"``: ``limit``, ``filter_name``, ``first_file``,
          ``last_file``, ``since``, ``until``, ``trend_frames``,
          ``trend_threshold_percent``; ``include`` may hold ``"spectra"``.
        - ``"spectral"``: ``limit``, ``first_file``, ``last_file``,
          ``since``, ``until``, ``exposure_seconds`` and
          ``predict_exposure_seconds``; it takes no ``include`` section.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose frames to measure, or its id.
        kind : `str`, optional
            ``"input_quality"`` (default): sky level, saturated pixels and,
            with ``include=["fwhm"]``, star width for the newest frames of a
            target, with a summary (count, minimum, median, maximum) per
            number. ``"raw_check"``: the batch check of a target's frames,
            flagging frames with few stars, trailing, soft or elongated
            stars, or a large jump. ``"spectral"``: a target's raw
            slitless-spectrum frames. For each it finds the zero-order star
            and measures the streak's tilt and width across, the sky level,
            the peak of the zero order and of the spectrum, and every
            saturated patch with its distance along the spectrum from the
            zero order. A clipped zero order is normal; clipped pixels inside
            the spectrum lose real data. The summary groups the frames by
            exposure length (how many clip the zero order or the spectrum)
            and by pier side (the median, smallest and largest tilt), so a
            tilt that depends on the pier side shows up.
        include : `list` [`str`], optional
            Optional sections. ``"fwhm"`` also measures star width (about 50
            times slower per frame). ``"spectra"`` also measures
            spectroscopy frames, whose smeared stars are flagged as trailing.
            To list the frames the stacker has already set aside, use
            ``targets.frames.get_set_aside``.
        remeasure : `bool`, optional
            Measure again frames that already have stored values.
        camera_id : `str`, optional
            A filter: only frames from this camera (matched as part of the
            name, ignoring case).
        limit : `int`, optional
            How many frames to measure and list, from 1 to 300 (1 to 100 for
            ``"spectral"``). Defaults to 50. A limit outside the range raises
            `InvalidArgumentError`. A frame takes about a second to measure.
            With no range or time given these are the newest frames; with a
            range or a time bound they are the first ones inside it.
        filter_name : `str`, optional
            Only frames whose filter matches this text, ignoring case, such
            as ``"L"``.
        first_file : `str`, optional
            Only frames from this file onward, by file name. A bare number
            such as ``"013"`` means frame 013 of the night.
        last_file : `str`, optional
            Only frames up to and including this file, by name or number.
        since : `str`, `datetime` or `Time`, optional
            Only frames taken at or after this time. Text is ISO 8601; no
            offset means UTC.
        until : `str`, `datetime` or `Time`, optional
            Only frames taken at or before this time.
        trend_frames : `int`, optional
            How many of the newest measured frames to check for a slow
            drift. Defaults to `TREND_WINDOW_FRAMES` (10).
        trend_threshold_percent : `float`, optional
            How far, as a percentage of its starting level, a number must
            move across those frames to count as a drift. Defaults to the
            tuned `TREND_THRESHOLD_PERCENT` (15 until tuned).
        exposure_seconds : `float`, optional
            Applies to ``"spectral"`` only. Only frames with this exposure
            length.
        predict_exposure_seconds : `float`, optional
            Applies to ``"spectral"`` only. An exposure to predict the peaks
            at, in seconds. The measured peaks are scaled to it and the
            report says whether the spectrum would clip there (a clipped
            peak is a lower bound, and the report says so).

        Returns
        -------
        report : `InputQualityReport` or `RawFrameCheckReport` or ...
            The report for the chosen kind (or `SpectralFrameCheckReport`).
            Frames are in file-name order, which is time order. Each star
            width names its estimator (``fwhm_estimator``). An
            ``"input_quality"`` row's ``measured_fwhm_px`` is a frame
            Gaussian width, and its ``measured_fwhm_on_scale_px`` is the same
            width on the library's one star-width scale. A ``"raw_check"``
            width is the check's own second-moment estimate, which has no
            ratio to that scale (``fwhm_not_on_scale_reason``).

        Notes
        -----
        `InvalidArgumentError` is raised if the kind or an include section
        is unknown, an argument the kind does not use is given, the limit is
        outside its range, or a time cannot be read. `NotFoundError` is
        raised if the target is not in the library.
        """
        from astrometricslib.pipelines.shared.quality import frame_quality_report

        check_choice("kind", kind, FRAME_QUALITY_KINDS)
        sections = check_include(include, FRAME_QUALITY_SECTIONS)
        reject_unused_arguments(
            kind,
            _FRAME_QUALITY_ARGUMENTS,
            {
                "remeasure": remeasure,
                "camera_id": camera_id is not None,
                "filter_name": filter_name is not None,
                "first_file": first_file is not None,
                "last_file": last_file is not None,
                "since": since is not None,
                "until": until is not None,
                "trend_frames": trend_frames is not None,
                "trend_threshold_percent": trend_threshold_percent is not None,
                "exposure_seconds": exposure_seconds is not None,
                "predict_exposure_seconds": predict_exposure_seconds is not None,
            },
        )
        reject_unused_arguments(kind, _FRAME_QUALITY_SECTIONS_BY_KIND, dict.fromkeys(sections, True))
        check_limit(limit, 1, 100 if kind == "spectral" else 300, allow_none=False)
        resolved = resolve_target(self._targets, target)
        if trend_frames is None or trend_threshold_percent is None:
            from astrometricslib.foundation.parameters import resolve
            from astrometricslib.pipelines.shared.quality.frame_trends import (
                TREND_THRESHOLD_PERCENT,
                TREND_WINDOW_FRAMES,
            )

            if trend_frames is None:
                trend_frames = int(resolve(TREND_WINDOW_FRAMES).value)
            if trend_threshold_percent is None:
                trend_threshold_percent = float(resolve(TREND_THRESHOLD_PERCENT).value)
        selection = make_frame_selection(
            filter_name, first_file, last_file, since, until, kind == "spectral" or "spectra" in sections
        )
        with registered_job(job_type="diagnostics", target_id=resolved.id):
            if kind == "spectral":
                from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_frame_check import (
                    check_spectral_frames,
                )

                return check_spectral_frames(
                    resolved,
                    selection,
                    exposure_seconds,
                    predict_exposure_seconds,
                    limit,
                    drivers=self._drivers,
                )
            if kind == "raw_check":
                return frame_quality_report.raw_check_report(
                    resolved, selection, limit, trend_frames, trend_threshold_percent
                )
            from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data

            return frame_quality_report.input_quality_report(
                resolved,
                selection,
                camera_id,
                limit,
                measure_fwhm_from_data if "fwhm" in sections else None,
                remeasure,
                trend_frames,
                trend_threshold_percent,
                self._stored_sky_rates(resolved),
            )

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_folder(
        self,
        folder_path: str,
        limit: int = 20,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: TimeInput | None = None,
        until: TimeInput | None = None,
        camera_id: str | None = None,
        bias_level_adu: float | None = None,
    ) -> FolderMeasurementReport:
        """Measure a folder of frames with the frame metrics, storing nothing.

        Each ``*.fits`` file is measured by the code that serves
        `record_frame_measurements`, so the numbers (star width and shape,
        sky brightness and noise, background level, saturation, temperatures)
        agree with the stored ones. Nothing is written to the quality store
        or to the library, and the folder need not be in the library, so
        this is how to check tonight's staging folder, or a set of frames
        drawn by a camera simulator, before they enter the library. A frame
        takes about 12 seconds.

        The sky brightness in electrons needs the bias level of the frame's
        camera setting. By default it comes from the calibration library's
        bias frames at exactly that setting. When there are none, give
        `bias_level_adu` (for example the level of a simulator's bias frames),
        or the sky metrics come back as not measured with the reason.

        Parameters
        ----------
        folder_path : `str`
            A folder of ``*.fits`` frames.
        limit : `int`, optional
            How many frames to measure, from 1 to 300: the first in file-name
            order. Defaults to 20. A limit outside the range raises
            `InvalidArgumentError`.
        filter_name : `str`, optional
            Only frames whose filter matches this text, ignoring case.
        first_file : `str`, optional
            Only frames from this file onward, by file name or number.
        last_file : `str`, optional
            Only frames up to and including this file, by name or number.
        since : `str`, `datetime` or `Time`, optional
            Only frames taken at or after this time. Text is ISO 8601; no
            offset means UTC.
        until : `str`, `datetime` or `Time`, optional
            Only frames taken at or before this time.
        camera_id : `str`, optional
            An override, not a filter: every frame is measured as if taken
            with this camera (a name in the camera profiles), instead of the
            camera its header names. No frame is left out for it.
        bias_level_adu : `float`, optional
            The bias level in ADU to use for the sky metrics of every frame.

        Returns
        -------
        report : `FolderMeasurementReport`
            ``frames`` (the value of each metric, such as
            ``frame.star_width_moffat_px`` in pixels and
            ``frame.sky_rate_e_per_s_per_px`` in electrons per second per
            pixel, with the reason for any that could not be measured),
            ``medians`` over the frames, and ``notices`` (for example that a
            frame was drawn by a simulator, so its noise and sky values do
            not describe a real sky). Each star width names its estimator
            (``star_width_estimators``); a pixel width whose estimator has a
            ratio to the library's one star-width scale is also given on it
            (each frame's ``widths_on_scale_px`` and the report's
            ``medians_on_scale_px``), and ``star_widths_not_on_scale`` says
            why the others are not.

        Raises
        ------
        InvalidArgumentError
            If the folder does not exist, the limit is outside its range, or
            a time cannot be read.
        """
        from astrometricslib.pipelines.stacking.folder_frame_measurement import measure_folder_frames
        from astrometricslib.pipelines.stacking.frame_measurement_run import BiasLevelFinder

        if not os.path.isdir(folder_path):
            raise InvalidArgumentError(f"folder_path {folder_path!r} is not a folder.")
        check_limit(limit, 1, 300, allow_none=False)
        selection = make_frame_selection(filter_name, first_file, last_file, since, until, True)
        bias_finder = None
        if bias_level_adu is None and self._calibration_library_provider is not None:
            bias_finder = BiasLevelFinder(self._calibration_library())
        with registered_job(job_type="diagnostics", target_id=folder_path):
            return measure_folder_frames(
                folder_path,
                selection,
                limit,
                self._config,
                datetime.now(UTC),
                camera=camera_id,
                bias_level_adu=bias_level_adu,
                bias_finder=bias_finder,
            )

    def _stored_sky_rates(self, target: Target) -> dict[str, float]:
        """Read the stored sky rate of each frame of a target, by frame path.

        The sky rate (electrons per second per pixel) is the sky level with
        the exposure length divided out, so a trend in it is not caused by a
        change of exposure.

        Parameters
        ----------
        target : `Target`
            The target whose frames to look up.

        Returns
        -------
        rates : `dict` [`str`, `float`]
            The newest stored rate of each frame that has one.
        """
        from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
        from astrometricslib.pipelines.shared.quality.metrics import FRAME_SKY_RATE_E_PER_S_PER_PX
        from astrometricslib.utilities.frame_identity import frame_subject_id

        rows = QualityStore(quality_database_path(self._config)).query(
            metric=FRAME_SKY_RATE_E_PER_S_PER_PX,
            level=SubjectLevel.FRAME,
            target_id=target.id,
            latest_only=True,
        )
        rate_by_subject = {
            row.subject.subject_id: float(row.value)
            for row in rows
            if row.value is not None and row.subject.channel is None
        }
        return {
            frame.path: rate_by_subject[frame_subject_id(frame.path, target.id)]
            for frame in target.frames
            if frame_subject_id(frame.path, target.id) in rate_by_subject
        }

    def measure_star_profile(
        self,
        path: str,
        x: float,
        y: float,
        radius_px: float | None = None,
        cut_angle_degrees: float = 0.0,
        cut_half_length_px: float | None = None,
        search_radius_px: float | None = None,
    ) -> StarProfileMeasurement:
        """Measure one star's radial profile and a straight cut through it.

        Nothing is stored. The clicked position is refined to the
        brightness-weighted centroid in a small box. The sky is the
        sigma-clipped median of a ring just outside the radius. The radial
        profile lists every pixel within the radius with its distance from
        the center and its value above the sky, and fits a round Moffat curve
        and a round Gaussian curve. The cut reads values along a straight
        line through the center (bilinear interpolation), which shows a
        one-sided flare or a close pair. A color image is averaged over its
        channels, and the result says so. A position outside the image, a
        radius under 3 pixels or a number that is not finite raises
        `InvalidArgumentError`; a missing file raises `NotFoundError`.

        Parameters
        ----------
        path : `str`
            A FITS image.
        x, y : `float`
            The star's position in 1-based pixels (as in FITS and DS9):
            the center of the first pixel is (1, 1).
        radius_px : `float`, optional
            The radius of the profile, at least 3 pixels. Taken from the
            star width when left out.
        cut_angle_degrees : `float`, optional
            The cut's direction, measured from +y (increasing row) toward
            +x (increasing column), in image pixel axes. 0 runs along a
            column, 90 along a row. Defaults to 0.
        cut_half_length_px : `float`, optional
            How far the cut reaches each way from the center. Taken from
            the star width when left out.
        search_radius_px : `float`, optional
            When given, the position first moves to the brightest peak of
            the lightly smoothed image within this many pixels (capped at
            50), so a click that lands beside a star still finds it.
            Left out, only the small centroid box refines the click.

        Returns
        -------
        profile : `StarProfileMeasurement`
            Fields: ``clicked_x``, ``clicked_y``, ``center_x``, ``center_y``,
            ``sky``, ``sky_noise``, ``profile_radius_px`` and
            ``profile_values``, ``peak``, ``clipped`` with ``clipped_reason``,
            ``moffat`` and ``gaussian`` (each with ``fwhm_px``, its
            ``estimator`` and ``fwhm_on_scale_px``,
            ``fwhm_arcsec``, ``peak``, ``beta``, curve points and
            ``r_squared``), ``cut_distance_px`` and ``cut_values``, then
            ``cutout`` (with ``cutout_origin_x`` and ``cutout_origin_y``, so
            any cut can be rebuilt), ``snapped``, ``snap_distance_px``,
            ``star_found`` and ``not_a_star_reason``. The ring around the star
            ("around the star" view) is in the ``ring_*`` fields:
            ``ring_inner_px`` and ``ring_outer_px`` (sized from the measured
            pixels, not from the fit), the 5 degree direction bins
            (``ring_bin_angle_degrees``, ``ring_bin_values``,
            ``ring_bin_counts``), the Fourier terms ``ring_harmonics`` (orders
            1 to 8: ``amplitude`` as a fraction, ``amplitude_error``,
            ``amplitude_debiased``, ``significance`` and
            ``direction_degrees``), the neighbor stars left out
            (``ring_neighbors``, ``ring_masked_neighbor_count``,
            ``ring_masked_fraction``) and ``ring_note``. Every angle runs from
            +y toward +x, like the cut. ``peak_over_sky_noise``,
            ``edge_distance_px``, ``coverage_fraction``, the photometry
            circle and sky ring radii, ``circle_neighbor_count`` and
            ``sky_ring_neighbor_count`` say whether the star is measurable.
            The core shape is ``star_elongation`` (long axis divided by
            short axis, from the same second-moment estimator as the frame's
            ``frame.star_elongation``), ``star_elongation_angle_deg``
            (the long axis, from +y toward +x like the cut, 0 up to 180),
            ``star_elongation_position_angle_deg`` (the same direction on
            the sky, north toward east, when the image is plate solved),
            ``star_shape_judgeable`` with ``star_shape_note`` when it
            is not, and ``trailing_elongation_limit`` (the stacking
            limit for a trailed frame).

        """
        from astrometricslib.foundation.parameters import resolve
        from astrometricslib.pipelines.astrometry.processing.detections import find_star_centers
        from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
            APERTURE_RADIUS_PX,
            CROWDING_MARGIN_PX,
            CROWDING_NEIGHBOR_PEAK_FRACTION,
            SKY_ANNULUS_INNER_RADIUS_PX,
            SKY_ANNULUS_OUTER_RADIUS_PX,
        )
        from astrometricslib.pipelines.shared.quality.star_profile import (
            PhotometryGeometry,
            measure_star_profile_in_file,
        )
        from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
            TRAILED_ELONGATION_LIMIT,
        )

        geometry = PhotometryGeometry(
            aperture_radius_px=float(resolve(APERTURE_RADIUS_PX).value),  # type: ignore[arg-type]
            sky_inner_px=float(resolve(SKY_ANNULUS_INNER_RADIUS_PX).value),  # type: ignore[arg-type]
            sky_outer_px=float(resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value),  # type: ignore[arg-type]
            crowding_margin_px=float(resolve(CROWDING_MARGIN_PX).value),  # type: ignore[arg-type]
            crowding_neighbor_peak_fraction=float(resolve(CROWDING_NEIGHBOR_PEAK_FRACTION).value),  # type: ignore[arg-type]
        )
        measurement = measure_star_profile_in_file(
            path,
            x,
            y,
            radius_px,
            cut_angle_degrees,
            cut_half_length_px,
            search_radius_px,
            find_neighbors=find_star_centers,
            photometry_geometry=geometry,
        )
        measurement.trailing_elongation_limit = float(resolve(TRAILED_ELONGATION_LIMIT).value)
        return measurement

    def measure_detections(self, path: str) -> DetectionsMeasurement:
        """List the stars the astrometry run's star finder detects in an image.

        A saved list is reused while it still matches the file's size and
        modified time: first the ``<stack>_detections.json`` the astrometry
        run saves beside a stack, then a copy in the cache folder. Otherwise
        the image is searched now, with the same function the astrometry run
        uses (the star-width-scaled kernel, the merge of a star reported
        twice, and the averaged copy of a color image), so a marker drawn from
        the result is a star the run finds, and the result is saved to the
        cache folder for next time. Nothing is written to the database or the
        image. A missing file raises `NotFoundError`; an image with no pixels
        raises `InvalidArgumentError`.

        Parameters
        ----------
        path : `str`
            A FITS image.

        Returns
        -------
        detections : `DetectionsMeasurement`
            Fields: ``path``, ``width`` and ``height``, ``detections`` (each
            with ``x`` and ``y`` in 1-based pixels as in
            `measure_star_profile`, ``flux`` and ``merged``, `True` when two
            reports of one star were merged), ``detection_count``,
            ``merged_count``, ``kernel_fwhm_px``, ``binned_on_color``,
            ``star_width_px`` (this image's star width, which sizes the
            circle) and ``aperture_radius_px``, the radius of
            the circle photometry measures each star in.
        """
        from astrometricslib.pipelines.astrometry.processing.detections import measure_detections_in_file

        return measure_detections_in_file(path)

    def match_frame_positions(
        self, path_a: str, path_b: str, points: list[tuple[float, float]]
    ) -> FramePositionMatch:
        """Say where positions of one frame lie in another, by their stars.

        Nothing is stored. The bright stars of the two frames are matched the
        way the quarantine's pointing check matches them, trying the second
        frame as it is and turned half a revolution (a meridian flip). It
        needs no plate solution or stack record, but it reads both frames'
        pixels and needs frames of one size. `map_position` follows one pixel
        between any two images from their plate solutions and stack records
        instead. A missing file raises `NotFoundError`.

        Parameters
        ----------
        path_a, path_b : `str`
            Two FITS frames of the same size.
        points : `list` [`tuple` [`float`, `float`]]
            ``(x, y)`` positions in ``path_a``, in 1-based pixels (as in FITS
            and DS9).

        Returns
        -------
        match : `FramePositionMatch`
            Fields: ``matched``, ``turned``, ``votes``, ``reason`` and
            ``points``, each with ``x``, ``y``, ``mapped_x``, ``mapped_y``
            (1-based pixels in ``path_b``) and ``inside``.

        Raises
        ------
        NotFoundError
            If a frame file does not exist.
        """
        from astrometricslib.pipelines.stacking.pre_processing.blink_pair import (
            match_frame_positions as match_positions,
        )

        for path in (path_a, path_b):
            if not os.path.isfile(path):
                raise NotFoundError(f"No frame at {path}.")
        return match_positions(path_a, path_b, [(float(x), float(y)) for x, y in points])

    def map_position(self, from_path: str, x: float, y: float, to_path: str) -> PositionMapping:
        """Find where a pixel of one image is on another image.

        Nothing is stored. The viewer uses this to follow a measured star when
        the image on screen changes. It tries, in order: the sky position
        (both images plate solved), a stack's registration record (frame to
        stack, stack to frame, frame to frame, or those chained through the
        sky for frames of two stacks), and the same pixel as a guess. It
        works between any two images, of any size, from the files' headers
        and the records beside the stacks, so it is fast. To match two frames
        by their stars instead, with no plate solution or record (as the
        quarantine's pointing check does), use `match_frame_positions`.

        Parameters
        ----------
        from_path, to_path : `str`
            The two FITS images.
        x, y : `float`
            The pixel on the first image, in 1-based pixels (as in FITS and
            DS9).

        Returns
        -------
        mapping : `PositionMapping`
            Fields: ``x`` and ``y`` (1-based pixels on the second image),
            ``method`` (``"sky"``, ``"stack alignment"``, ``"stack alignment
            + sky"``, ``"same pixel"`` or ``"none"``) and ``note``. When the
            pixel cannot be followed (a frame Siril left out of its stack, a
            spectral stack), ``x`` and ``y`` are `None`, ``method`` is
            ``"none"`` and ``note`` says why.

        Raises
        ------
        NotFoundError
            If an image file does not exist.
        """
        from astrometricslib.pipelines.shared.position_mapping import map_position

        for path in (from_path, to_path):
            if not os.path.isfile(path):
                raise NotFoundError(f"No image at {path}.")
        return map_position(from_path, x, y, to_path, self._registration_record_roots(), self._record_cache)

    def _registration_record_roots(self) -> list[str]:
        """List the folders searched for stack registration records.

        Returns
        -------
        roots : `list` [`str`]
            The real paths of the stacks folder and of the ``stacks`` folder
            inside the library folder, without repeats.
        """
        roots = [
            os.path.join(str(self._config.get_library_path()), "stacks"),
            str(self._config.get_stacks_path()),
        ]
        return list(dict.fromkeys(os.path.realpath(root) for root in roots))

    def choose_blink_pair(self, target: str | Target, path: str) -> BlinkPair:
        """Pick a typical frame and a reference star to blink a frame against.

        Nothing is stored. The typical frame is the frame of the same session
        (the batch the quarantine step judges the frame in) whose stored
        ``frame.star_elongation`` and ``frame.sky_unevenness`` lie closest to
        the session medians. The reference star is a bright, isolated,
        unsaturated star near the frame center, chosen with the rules of the
        star-shape measurement. Its position in the typical frame is found
        with `match_frame_positions`. The frame may be one in the stack or
        one the stacker set aside.

        Parameters
        ----------
        target : `str` or `Target`
            The target, or its id or name.
        path : `str`
            The frame to examine.

        Returns
        -------
        pair : `BlinkPair`
            Fields: ``found``, ``reason``, ``path``, ``typical_path``,
            ``star_x`` and ``star_y`` (the star in ``path``),
            ``typical_star_x`` and ``typical_star_y`` (the same star in the
            typical frame), all in 1-based pixels, then ``turned``,
            ``votes``, ``session_frame_count`` and the frame size ``width`` and
            ``height``. When nothing could be
            found, ``found`` is `False` and ``reason`` says why.

        Raises
        ------
        NotFoundError
            If the target or the frame file does not exist.
        """
        from astrometricslib.pipelines.stacking.frame_status import list_set_aside_frames_with_reasons
        from astrometricslib.pipelines.stacking.pre_processing.blink_pair import (
            choose_blink_pair as choose_pair,
        )
        from astrometricslib.pipelines.stacking.pre_processing.blink_pair import frame_record_for_set_aside

        resolved = resolve_target(self._targets, target)
        if not os.path.isfile(path):
            raise NotFoundError(f"No frame at {path}.")
        lights = [frame for frame in resolved.frames if frame.role == "LIGHT"]
        clicked = None
        if not any(frame.path == path for frame in lights):
            set_aside = list_set_aside_frames_with_reasons(resolved, self._config)
            record = next((item for item in set_aside if item.path == path), None)
            if record is not None:
                clicked = frame_record_for_set_aside(
                    lights, path, record.camera, record.filter, record.exposure_seconds, record.date
                )
        return choose_pair(resolved.id, path, lights, clicked)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_stack(
        self,
        path_or_target: str | Target,
        kind: StackKind = "imaging",
        include: list[str] | None = None,
        compare_to: str | None = None,
    ) -> StackQualityReport:
        """Measure a stack, and optionally compare it with another stack.

        The comparison measures both stacks the same way: the sky level (the
        median of the usable pixels), the noise in empty sky, how flat the sky
        is across the frame, and star width. The noise is the number the
        target report stores (``stacking.empty_aperture_noise_normalized``),
        measured in the apertures the library stack chose. Each stack's
        reference frame and trimmed edges come from its registration record;
        the report says when they could not be recovered. A number that cannot
        be measured is reported as not measured, with the reason. A failed
        plate solve can take about ten minutes. A fine-grain pixel noise is
        kept and labelled. The result gives the numbers, the change in each,
        and a plain sentence for each. It does not pick a winner, because
        that depends on what the change was for. New flats should lower the
        flatness number and leave the noise alone; more frames should lower
        the noise and leave the flatness alone.

        Parameters
        ----------
        path_or_target : `str` or `Target`
            A stack's FITS file (a path ending in ``.fit``, ``.fits`` or
            ``.fts``), or a target (its id or the `Target`) whose current
            stack to measure.
        kind : `str`, optional
            For a target: ``"imaging"`` (default) or ``"spectral"`` stack.
            Refused with a path.
        include : `list` [`str`], optional
            Measurements to make: ``"fwhm"`` (median star width of the
            brightest stars), ``"rejected_fraction"`` (from the rejection
            map beside the stack) and ``"registration"`` (one row per frame
            from the ``_Registration.seq`` file beside the stack).
        compare_to : `str`, optional
            ``"previous"`` compares with the stack the last restack kept in
            the ``_previous`` folder. Any other text is the path of a stack
            to compare with. The other stack is ``before``; this one is
            ``after``.

        Returns
        -------
        report : `StackQualityReport`
            The measurements asked for and the comparison. The star width
            names its estimator (``fwhm_estimator``, ``"stack Gaussian"``)
            and gives ``fwhm_on_scale_px``, the width on the library's one
            star-width scale; each compared stack does the same. The
            registration rows carry Siril's own width
            (``registration_fwhm_estimator``), which has no ratio to that
            scale.

        Raises
        ------
        InvalidArgumentError
            If an include section is unknown, or ``kind`` is given with a
            path.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import (
            STACK_QUALITY_SECTIONS,
            stack_path_of,
            stack_quality_report,
        )

        check_choice("kind", kind, STACK_KINDS)
        sections = check_include(include, STACK_QUALITY_SECTIONS)
        if isinstance(path_or_target, str) and path_or_target.lower().endswith((".fit", ".fits", ".fts")):
            if kind != "imaging":
                raise InvalidArgumentError("kind applies to a target's stack, not to a stack path.")
            stack_path, target_id = path_or_target, None
        else:
            resolved = resolve_target(self._targets, path_or_target)
            stack_path, target_id = stack_path_of(resolved, kind), resolved.id
        with registered_job(job_type="diagnostics", target_id=target_id or stack_path):
            return stack_quality_report(
                stack_path, target_id, sections, compare_to, kind == "spectral", self._drivers
            )

    @background_job("quality", grace_period_seconds=20.0)
    def record_frame_measurements(
        self,
        target: str | Target,
        frames: list[FrameRecord] | None = None,
        limit: int | None = None,
    ) -> FrameMeasurementSummary:
        """Measure a target's light frames once and store the numbers.

        Each light frame is read once and measured: the width, elongation
        and direction of its stars, the sky brightness and noise in
        electrons, the camera's read noise, the temperatures, and the
        measurements the library already makes (background level, saturated
        fraction, Gaussian star width, count of bright regions). The numbers
        go into the quality database and can be read with
        ``Astrometrics.quality.query``. A value that cannot be measured is
        stored as ``not_measured`` with the reason. The sky brightness needs
        the bias level of the frame's exact camera setting, taken from the
        calibration library's bias frames; with none at that setting those
        two metrics are ``not_measured``.

        The frame files are never moved or changed. Each measured frame gets
        a stamp (the file size and change time at the measurement), saved to
        the stored target every 20 frames and at the end. Only the stamps are
        written, so the save never overwrites other changes to the target. A
        frame whose file still matches its stamp is skipped, so a second call
        measures nothing again. A frame that fails, for any reason, is
        counted and tried again next time; one failure does not stop the
        others. Each frame takes about 12 s of one core, and four frames are
        measured at once in four worker processes: real data (61 frames of
        3008 by 3008 pixels) took about 345 s and about 2.7 GB of memory.

        The worker processes are started with "spawn", which imports the
        calling script again in each worker. A script that calls this method
        must therefore keep its top-level code under
        ``if __name__ == "__main__":``, or the workers stop and every frame
        fails. The backend, the bundled scripts and notebooks run as modules
        or cells and are not affected.

        Only one run measures a target at a time, and only one run in the
        process reads frames at a time. A second call for a target that is
        already being measured returns at once with ``already_running`` set
        and nothing measured. A call for another target waits for its turn.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose light frames to measure.
        frames : `list` [`FrameRecord`], optional
            Only these frames. All of the target's light frames when omitted.
        limit : `int`, optional
            Measure at most this many frames, newest first. The rest wait for
            the next call.

        Returns
        -------
        summary : `FrameMeasurementSummary`
            How many frames were measured, skipped because they had not
            changed, or failed, and how many rows were written.

        Raises
        ------
        InvalidArgumentError
            If ``limit`` is below 1.
        """
        from astrometricslib._internal.drivers.quality_store import QualityStore
        from astrometricslib.pipelines.shared.target_records import save_quality_stamps
        from astrometricslib.pipelines.stacking.frame_measurement_run import (
            FrameMeasurementSession,
            record_frame_measurements_for_target,
        )

        if limit is not None and limit < 1:
            raise InvalidArgumentError("limit must be at least 1.")
        resolved = resolve_target(self._targets, target)
        with registered_job(job_type="quality", target_id=resolved.id) as job:
            # The run gets its own run id and reports progress only when it
            # registered (or joined) a job of its own. Called from inside
            # another job (stacking, ingestion), it leaves that job's
            # progress and id alone.
            session = FrameMeasurementSession(
                QualityStore.from_configuration(self._config),
                self._calibration_library(),
                progress_job=job if job.job_id is not None and job.job_type == "quality" else None,
                measured_at=datetime.now(UTC),
            )
            return record_frame_measurements_for_target(
                resolved,
                session,
                frames=frames,
                limit=limit,
                save_target=lambda: save_quality_stamps(self._targets, resolved),
            )
