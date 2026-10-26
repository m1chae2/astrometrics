"""The quality root: every quality read starts here.

`QualityRecords` is the child of `Astrometrics` reached as
`astrometrics.quality`. It answers two kinds of question:

* Stored: `query` reads the quality measurements that earlier runs saved,
  and `metrics` lists what each metric means.
* Live: `measure_frames`, `check_spectral_frames` and `measure_stack` measure
  raw frames and finished stacks now, without changing anything.

The argument helpers it shares with `ProcessingPipelines` live in
`pipelines/shared/api_arguments.py`.
"""

from datetime import UTC, datetime
from typing import Literal

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, registered_job
from astrometricslib.models.quality.calibration_identities import CalibrationIdentities
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.excluded_frames import QuarantinePreview, SetAsideFrameRecord
from astrometricslib.models.quality.measurement import QualityMeasurement, SubjectLevel
from astrometricslib.models.quality.metric_spec import Checkpoint, MetricSpec, MetricSummary
from astrometricslib.models.quality.quality_reports import (
    InputQualityReport,
    RawFrameCheckReport,
    SpectralFrameCheckReport,
    StackQualityReport,
)
from astrometricslib.models.quality.row_frames import QualityRowFile, QualityRowResolution
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.api_arguments import (
    STACK_KINDS,
    StackKind,
    TargetLookup,
    TimeInput,
    check_choice,
    check_include,
    make_frame_selection,
    reject_unused_arguments,
    resolve_target,
    to_epoch_seconds,
)

__all__ = ["QualityRecords"]

FRAME_QUALITY_KINDS = ("input_quality", "raw_check", "quarantine_preview")
FRAME_QUALITY_SECTIONS = ("fwhm", "spectra", "excluded")
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
        "folder_path",
        "filter_name",
        "first_file",
        "last_file",
        "since",
        "until",
        "trend_frames",
        "trend_threshold_percent",
    ),
    "quarantine_preview": (),
}
_FRAME_QUALITY_SECTIONS_BY_KIND = {
    "input_quality": ("fwhm", "spectra", "excluded"),
    "raw_check": ("spectra", "excluded"),
    "quarantine_preview": ("excluded",),
}


def _clamp(value: int, lowest: int, highest: int) -> int:
    """Keep a whole number inside a range.

    Returns
    -------
    clamped : `int`
        ``value``, raised to ``lowest`` or lowered to ``highest``.
    """
    return max(lowest, min(int(value), highest))


class QualityRecords:
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
    """

    def __init__(
        self, config: AppConfiguration, storage: AbstractCatalogAccess, *, targets: TargetLookup | None = None
    ) -> None:
        self._config = config
        self._storage = storage
        self._targets = targets

    def query(
        self,
        metric: str | None = None,
        level: str | None = None,
        target_id: str | None = None,
        observing_night_id: str | None = None,
        camera: str | None = None,
        telescope: str | None = None,
        since: TimeInput = None,
        row_ids: list[int] | None = None,
        latest_only: bool = True,
        variant: str | None = None,
        library_only: bool = False,
    ) -> list[QualityMeasurement]:
        """Read the quality measurements that earlier runs stored.

        Nothing is measured and nothing is written. A library with no
        stored measurements yet gives an empty list, and no database file
        or table is created by asking.

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
        camera : `str`, optional
            Only measurements made with this camera.
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

        Returns
        -------
        measurements : `list` [`QualityMeasurement`]
            The matching measurements, oldest first.

        Raises
        ------
        InvalidArgumentError
            If ``metric`` is not registered (the error lists the registered
            names), ``level`` is unknown, ``since`` cannot be read as a
            time, or both ``variant`` and ``library_only`` are given.

        Notes
        -----
        A stored row that fails the model's checks raises `StorageError`.
        """
        from astrometricslib.drivers.quality_store import QualityStore, quality_database_path
        from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry

        if metric is not None:
            check_choice("metric", metric, get_metric_registry().names())
        if level is not None:
            check_choice("level", level, tuple(member.value for member in SubjectLevel))
        if variant is not None and library_only:
            raise InvalidArgumentError("Give either variant or library_only, not both.")
        since_seconds = to_epoch_seconds(since, "since")
        since_time = None if since_seconds is None else datetime.fromtimestamp(since_seconds, tz=UTC)
        rows = QualityStore(quality_database_path(self._config)).query(
            metric=metric,
            level=None if level is None else SubjectLevel(level),
            target_id=target_id,
            observing_night_id=observing_night_id,
            camera=camera,
            telescope=telescope,
            since=since_time,
            row_ids=None if row_ids is None else [int(row_id) for row_id in row_ids],
            latest_only=latest_only,
            variant=variant,
            library_only=library_only,
        )
        registry = get_metric_registry()
        for row in rows:
            row.unit = registry.get(row.metric).unit if row.metric in registry.names() else None
        return rows

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
        found = {row.row_id: row for row in self.query(row_ids=asked, latest_only=False)}
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
        from astrometricslib.drivers.quality_store import QualityStore, quality_database_path

        return QualityStore(quality_database_path(self._config)).get_capture_settings(ids)

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
        from astrometricslib.drivers.quality_store import QualityStore, quality_database_path

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

    def _label(self, target: Target | None, folder_path: str | None = None) -> str:
        """Name the subject of a job.

        Returns
        -------
        label : `str`
            The target's id, the folder, or ``"library"``.
        """
        return target.id if target is not None else (folder_path or "library")

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_frames(
        self,
        target: str | Target | None = None,
        folder_path: str | None = None,
        kind: Literal["input_quality", "raw_check", "quarantine_preview"] = "input_quality",
        include: list[str] | None = None,
        remeasure: bool = False,
        camera_id: str | None = None,
        limit: int = 50,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: TimeInput = None,
        until: TimeInput = None,
        trend_frames: int = 10,
        trend_threshold_percent: float = 15.0,
        register_job: bool = True,
    ) -> InputQualityReport | RawFrameCheckReport | QuarantinePreview:
        """Measure raw light frames, or preview what the stacker sets aside.

        Nothing is saved. For a target the frames are measured on a copy, so
        the measurements never reach the saved catalog.

        Arguments used by each kind (any other argument is refused):

        - ``"input_quality"``: ``target`` (needed), ``remeasure``,
          ``camera_id``, ``limit``, ``filter_name``, ``first_file``,
          ``last_file``, ``since``, ``until``, ``trend_frames``,
          ``trend_threshold_percent``; ``include`` may hold ``"fwhm"``,
          ``"spectra"`` and ``"excluded"``.
        - ``"raw_check"``: ``folder_path`` or ``target``, ``limit``,
          ``filter_name``, ``first_file``, ``last_file``, ``since``,
          ``until``, ``trend_frames``, ``trend_threshold_percent``;
          ``include`` may hold ``"spectra"`` and ``"excluded"``.
        - ``"quarantine_preview"``: ``target`` (needed) and ``limit``;
          ``include`` may hold ``"excluded"``.

        Parameters
        ----------
        target : `str` or `Target`, optional
            The target whose light frames to measure.
        folder_path : `str`, optional
            A folder of ``*.fits`` frames, for ``raw_check``. It need not be
            in the library, such as a staging folder from tonight.
        kind : `str`, optional
            ``"input_quality"`` (default): sky level, saturated pixels and,
            with ``include=["fwhm"]``, star width for the newest frames of a
            target, with a summary (count, minimum, median, maximum) per
            number. ``"raw_check"``: the batch check of a folder (or of a
            target's frames), flagging frames with few stars, trailing,
            soft or elongated stars, or a large jump. ``"quarantine_preview"``:
            which frames the stacker would set aside, without moving any.
        include : `list` [`str`], optional
            Optional sections. ``"fwhm"`` also measures star width (about 50
            times slower per frame). ``"spectra"`` also measures
            spectroscopy frames, whose smeared stars are flagged as trailing.
            ``"excluded"`` lists the frames the stacker has already set aside
            for the target.
        remeasure : `bool`, optional
            Measure again frames that already have stored values.
        camera_id : `str`, optional
            Only frames from this camera (matched as part of the name,
            ignoring case).
        limit : `int`, optional
            How many frames to measure and list, from 1 to 300. Defaults to
            50. A frame takes about a second to measure. With no range or
            time given these are the newest frames; with a range or a time
            bound they are the first ones inside it.
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
            drift. Defaults to 10.
        trend_threshold_percent : `float`, optional
            How far, as a percentage of its starting level, a number must
            move across those frames to count as a drift. Defaults to 15.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        report : `InputQualityReport`, `RawFrameCheckReport`, ...
            The report for the chosen kind: `InputQualityReport`,
            `RawFrameCheckReport` or `QuarantinePreview`. Frames are in
            file-name order, which is time order.

        Raises
        ------
        InvalidArgumentError
            If the kind or an include section is unknown, an argument the
            kind does not use is given, a needed target or folder is
            missing, or a time cannot be read.
        """
        from astrometricslib.pipelines.shared.quality import frame_quality_report

        check_choice("kind", kind, FRAME_QUALITY_KINDS)
        sections = check_include(include, FRAME_QUALITY_SECTIONS)
        reject_unused_arguments(
            kind,
            _FRAME_QUALITY_ARGUMENTS,
            {
                "folder_path": folder_path is not None,
                "remeasure": remeasure,
                "camera_id": camera_id is not None,
                "filter_name": filter_name is not None,
                "first_file": first_file is not None,
                "last_file": last_file is not None,
                "since": since is not None,
                "until": until is not None,
                "trend_frames": trend_frames != 10,
                "trend_threshold_percent": abs(trend_threshold_percent - 15.0) > 1e-9,
            },
        )
        reject_unused_arguments(kind, _FRAME_QUALITY_SECTIONS_BY_KIND, dict.fromkeys(sections, True))
        resolved = resolve_target(self._targets, target) if target is not None else None
        if resolved is None and (kind != "raw_check" or not folder_path):
            raise InvalidArgumentError(
                f"kind={kind!r} needs a target" + (" or a folder_path." if kind == "raw_check" else ".")
            )
        if "excluded" in sections and resolved is None:
            raise InvalidArgumentError('include=["excluded"] needs a target.')
        limit = _clamp(limit, 1, 300)
        selection = make_frame_selection(
            filter_name, first_file, last_file, since, until, "spectra" in sections
        )
        with registered_job(
            enabled=register_job, job_type="diagnostics", target_id=self._label(resolved, folder_path)
        ):
            if kind == "raw_check":
                report = frame_quality_report.raw_check_report(
                    resolved, folder_path, selection, limit, trend_frames, trend_threshold_percent
                )
            elif kind == "quarantine_preview":
                from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
                    quarantine_preview_report,
                )

                report = quarantine_preview_report(resolved, limit)
            else:
                from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data

                report = frame_quality_report.input_quality_report(
                    resolved,
                    selection,
                    camera_id,
                    limit,
                    measure_fwhm_from_data if "fwhm" in sections else None,
                    remeasure,
                    trend_frames,
                    trend_threshold_percent,
                )
            if "excluded" in sections:
                from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
                    list_set_aside_frames,
                )

                report.excluded = list_set_aside_frames(str(self._config.get_frames_path()), resolved.id)
            return report

    def set_aside_frames(self, target: str | Target) -> list[SetAsideFrameRecord]:
        """List the frames in a target's `_excluded` folders, with reasons.

        The stacker moves frames with clouds or trailed stars into an
        `_excluded` folder and stores the move with the frame's quality rows.
        This lists every frame in those folders. For each one the reason comes
        from the stored rows if they carry the action taken, else from the
        folder's `excluded_frames.json` file (frames set aside before the rows
        carried the action), else it is empty. ``reason_source`` says which.

        Nothing is moved, measured or written, and a library with no quality
        database gives the folder-based answer without creating one.

        Parameters
        ----------
        target : `str` or `Target`
            The target, or its id or name.

        Returns
        -------
        frames : `list` [`SetAsideFrameRecord`]
            One entry per frame file in an `_excluded` folder. Empty when none.

        """
        from astrometricslib.pipelines.stacking.metrics import (
            FRAME_QUARANTINE_ROUNDNESS,
            FRAME_QUARANTINE_SKY_MEDIAN,
        )
        from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import target_folder_names
        from astrometricslib.pipelines.stacking.pre_processing.set_aside_listing import (
            build_set_aside_records,
            find_excluded_folders,
        )

        resolved = resolve_target(self._targets, target)
        folders = find_excluded_folders(str(self._config.get_frames_path()), target_folder_names(resolved.id))
        if not folders:
            return []
        rows = [
            row
            for metric in (FRAME_QUARANTINE_ROUNDNESS, FRAME_QUARANTINE_SKY_MEDIAN)
            for row in self.query(metric=metric, target_id=resolved.id, latest_only=True)
        ]
        setting_ids = sorted({
            row.subject.capture_setting_id for row in rows if row.subject.capture_setting_id is not None
        })
        settings = self.capture_settings(setting_ids) if setting_ids else {}
        return build_set_aside_records(
            resolved.id, folders, rows, {key: value.exposure_seconds for key, value in settings.items()}
        )

    @background_job("diagnostics", grace_period_seconds=20.0)
    def check_spectral_frames(
        self,
        target: str | Target,
        first_file: str | None = None,
        last_file: str | None = None,
        since: TimeInput = None,
        until: TimeInput = None,
        exposure_seconds: float | None = None,
        predict_exposure_seconds: float | None = None,
        limit: int = 30,
        register_job: bool = True,
    ) -> SpectralFrameCheckReport:
        """Measure a target's raw spectrum frames and where they clip.

        For each slitless-spectrum frame it finds the zero-order star and
        measures the streak's tilt and width across, the sky level, the
        peak of the zero order and of the spectrum, and every saturated
        patch with its distance along the spectrum from the zero order. A
        clipped zero order is normal; clipped pixels inside the spectrum
        lose real data. Given ``predict_exposure_seconds`` it scales the
        measured peaks to that exposure and says whether the spectrum would
        clip there (a clipped peak is a lower bound, and the reply says so).

        The summary groups the frames by exposure length (how many clip the
        zero order or the spectrum) and by pier side (the median, smallest
        and largest tilt), so a tilt that depends on the side of the pier
        shows up. Nothing is saved. A frame takes about a second.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose spectrum frames to measure.
        first_file : `str`, optional
            Only frames from this file onward. A bare number such as
            ``"013"`` means frame 013.
        last_file : `str`, optional
            Only frames up to this file or number.
        since : `str`, `datetime` or `Time`, optional
            Only frames taken at or after this time (UTC if no offset).
        until : `str`, `datetime` or `Time`, optional
            Only frames taken at or before this time.
        exposure_seconds : `float`, optional
            Only frames with this exposure length.
        predict_exposure_seconds : `float`, optional
            An exposure to predict the peaks at, in seconds.
        limit : `int`, optional
            How many frames to measure, from 1 to 100. Defaults to 30. With
            a range or a time bound these are the first frames inside it,
            otherwise the newest.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        report : `SpectralFrameCheckReport`
            One row per frame and the summary.
        """
        from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_frame_check import (
            check_spectral_frames,
        )

        resolved = resolve_target(self._targets, target)
        selection = make_frame_selection(None, first_file, last_file, since, until, True)
        with registered_job(enabled=register_job, job_type="diagnostics", target_id=resolved.id):
            return check_spectral_frames(
                resolved, selection, exposure_seconds, predict_exposure_seconds, _clamp(limit, 1, 100)
            )

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_stack(
        self,
        path_or_target: str | Target,
        kind: StackKind = "imaging",
        include: list[str] | None = None,
        compare_to: str | None = None,
        register_job: bool = True,
    ) -> StackQualityReport:
        """Measure a stack, and optionally compare it with another stack.

        The comparison measures both stacks the same way: sky level, pixel
        noise, how flat the sky is across the frame, and star width. It
        gives the numbers, the change in each, and a plain sentence for
        each. It does not pick a winner, because that depends on what the
        change was for. New flats should lower the flatness number and leave
        the noise alone; more frames should lower the noise and leave the
        flatness alone.

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
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        report : `StackQualityReport`
            The measurements asked for and the comparison.

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
        with registered_job(enabled=register_job, job_type="diagnostics", target_id=target_id or stack_path):
            return stack_quality_report(stack_path, target_id, sections, compare_to)

    def flag_value_outliers(
        self, values: list[float | None], sigma_threshold: float, low_is_bad: bool = False
    ) -> list[bool]:
        """Flag entries more than sigma_threshold standard deviations away.

        Parameters
        ----------
        values : `list` [`float` or `None`]
            Values to check; `None` entries are never flagged.
        sigma_threshold : `float`
            Number of standard deviations from the mean beyond which a
            value is flagged.
        low_is_bad : `bool`, optional
            If `True`, only flag values below the mean; otherwise flag
            deviations on either side. Defaults to `False`.

        Returns
        -------
        flags : `list[bool]`
            A list the same length as values, `True` where the
            corresponding entry is an outlier.
        """
        from astrometricslib.pipelines.spectroscopy.utilities.registration_quality import flag_outliers

        return flag_outliers(values, sigma_threshold, low_is_bad)

    @property
    def spectral_registration_thresholds(self) -> dict[str, float]:
        """The default outlier thresholds for spectral registration checks.

        Returns
        -------
        thresholds : `dict` [`str`, `float`]
            Threshold values keyed by threshold name.
        """
        from astrometricslib.pipelines.spectroscopy.utilities import registration_quality as srq

        return {
            "min_matched_star_pairs": srq.MIN_MATCHED_STAR_PAIRS,
            "zero_order_amplitude_outlier_sigma": srq.ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA,
            "zero_order_position_jump_outlier_sigma": srq.ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA,
        }
