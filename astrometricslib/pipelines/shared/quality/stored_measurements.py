"""Purpose: Read the quality measurements that earlier runs stored.

Description: These are the owner functions behind
``astrometrics.quality.records``. Each reads the quality database and writes
nothing. A library with no quality database yet gives an empty answer, and no
database file or table is created by asking.

* `query_measurements` reads the stored rows that match some filters, as a
  `Page`.
* `get_measurement` reads one stored row by its id.
* `find_row_paths` finds the frame or stack file behind each stored row.
* `list_metrics` and `get_metric_spec` read the declared metrics.
* `read_capture_settings` and `read_calibration_identities` read the capture
  settings and the calibration identities that stored rows point at.
"""

import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
from astrometricslib.foundation.arguments import TimeInput, check_choice, to_epoch_seconds
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.page import Page
from astrometricslib.models.quality.calibration_identities import CalibrationIdentities
from astrometricslib.models.quality.capture_setting import StoredCaptureSetting
from astrometricslib.models.quality.measurement import QualityMeasurement, SubjectLevel
from astrometricslib.models.quality.metric_spec import Checkpoint, MetricSpec
from astrometricslib.models.quality.row_frames import QualityRowFile, QualityRowResolution
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry

__all__ = [
    "find_row_paths",
    "get_measurement",
    "get_metric_spec",
    "list_metrics",
    "query_measurements",
    "read_calibration_identities",
    "read_capture_settings",
]


def _store(config: AppConfiguration) -> QualityStore:
    """Open the quality store of a library.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.

    Returns
    -------
    store : `QualityStore`
        The store. Opening it creates nothing.
    """
    return QualityStore(quality_database_path(config))


def _with_units(rows: list[QualityMeasurement]) -> list[QualityMeasurement]:
    """Fill in each row's unit from the metric declarations.

    Parameters
    ----------
    rows : `list` [`QualityMeasurement`]
        Stored rows.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The same rows, each with ``unit`` set (`None` for a metric that is no
        longer declared).
    """
    registry = get_metric_registry()
    registered = set(registry.names())
    for row in rows:
        row.unit = registry.get(row.metric).unit if row.metric in registered else None
    return rows


def _to_time(value: TimeInput | None, name: str) -> datetime | None:
    """Read a time argument as an aware UTC time.

    Parameters
    ----------
    value : `str`, `datetime`, `Time` or `None`
        The time; ISO 8601 text with no offset means UTC.
    name : `str`
        The argument's name, for the error message.

    Returns
    -------
    time : `datetime` or `None`
        The time in UTC, or `None` when no time was given.
    """
    seconds = to_epoch_seconds(value, name)
    return None if seconds is None else datetime.fromtimestamp(seconds, tz=UTC)


def query_measurements(
    config: AppConfiguration,
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

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    metric : `str`, optional
        Only this metric.
    level : `str`, optional
        Only subjects of this level (a `SubjectLevel` value).
    target : `Target`, optional
        Only this target.
    night : `str`, optional
        Only this observing night, ``YYYY-MM-DD``.
    camera, telescope : `str`, optional
        Only measurements made with this camera or telescope, as stored
        with the capture setting (matched exactly).
    since, until : `str`, `datetime` or `Time`, optional
        Only measurements made from ``since`` to ``until``.
    latest_only : `bool`, optional
        The newest row for each subject, metric and channel only.
    variant : `str`, optional
        Only rows about the subset stack with this label.
    library_only : `bool`, optional
        Only rows that are not about a subset stack.
    limit : `int`, optional
        The most rows to give. `None` gives every match.
    offset : `int`, optional
        How many matching rows to skip.

    Returns
    -------
    rows : `Page` [`QualityMeasurement`]
        The matching rows, oldest first.

    Raises
    ------
    InvalidArgumentError
        If the metric or the level is unknown, a time cannot be read, both
        ``variant`` and ``library_only`` are given, ``limit`` is below 1 or
        ``offset`` is below 0.
    """
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
    if target is not None and not isinstance(target, Target):
        raise InvalidArgumentError("Pass the Target itself; turn an id into one with targets.get(id).")
    until_time = _to_time(until, "until")
    rows = _store(config).query(
        metric=metric,
        level=None if level is None else SubjectLevel(level),
        target_id=None if target is None else target.id,
        observing_night_id=night,
        camera=camera,
        telescope=telescope,
        since=_to_time(since, "since"),
        latest_only=latest_only,
        variant=variant,
        library_only=library_only,
    )
    if until_time is not None:
        rows = [row for row in rows if row.measured_at <= until_time]
    return Page.of(_with_units(rows), offset=offset, limit=limit)


def get_measurement(config: AppConfiguration, row_id: int) -> QualityMeasurement | None:
    """Read one stored row by its id, even when a newer row replaced it.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    row_id : `int`
        The store's row id.

    Returns
    -------
    row : `QualityMeasurement` or `None`
        The row, or `None` when no row has this id.
    """
    rows = _store(config).query(row_ids=[int(row_id)], latest_only=False)
    return _with_units(rows)[0] if rows else None


def find_row_paths(
    config: AppConfiguration,
    row_ids: Sequence[int],
    get_target: Callable[[str], Target | None],
) -> QualityRowResolution:
    """Find the picture behind each stored quality row.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library and stacks folders.
    row_ids : `Sequence` [`int`]
        The store's row ids, such as the ones a statement cites.
    get_target : `Callable` [[`str`], `Target` or `None`]
        Gives the target with an id, or `None`.

    Returns
    -------
    resolution : `QualityRowResolution`
        One `QualityRowFile` for each row found, in the order asked, and the
        ids that no stored row has.
    """
    asked = list(dict.fromkeys(int(row_id) for row_id in row_ids))
    found = {row.row_id: row for row in _store(config).query(row_ids=asked, latest_only=False)}
    frame_paths_by_target: dict[str, dict[str, str]] = {}
    files = [
        _row_file(config, row_id, found[row_id], get_target, frame_paths_by_target)
        for row_id in asked
        if row_id in found
    ]
    return QualityRowResolution(
        rows=files, not_found_row_ids=[row_id for row_id in asked if row_id not in found]
    )


def _row_file(
    config: AppConfiguration,
    row_id: int,
    row: QualityMeasurement,
    get_target: Callable[[str], Target | None],
    frame_paths_by_target: dict[str, dict[str, str]],
) -> QualityRowFile:
    """Find the file of one row's subject.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    row_id : `int`
        The row id.
    row : `QualityMeasurement`
        The stored row.
    get_target : `Callable`
        Gives the target with an id.
    frame_paths_by_target : `dict` [`str`, `dict` [`str`, `str`]]
        A cache, filled here: for each target, its frames' paths by subject
        id.

    Returns
    -------
    file : `QualityRowFile`
        The row's file path, or the reason it has none.
    """
    from astrometricslib.foundation.paths import resolve_mounted_path
    from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id

    subject = row.subject
    path: str | None = None
    reason = ""
    if subject.level == SubjectLevel.RUN:
        reason = "This measurement is about a processing run, so it has no picture."
    elif subject.target_id is None:
        reason = "The measurement names no target in the library."
    elif subject.level == SubjectLevel.STACK:
        file_name = subject.subject_id.removeprefix(f"{subject.target_id}/")
        candidate = resolve_mounted_path(
            os.path.join(str(config.get_stacks_path()), "lights", subject.target_id, file_name)
        )
        if os.path.isfile(candidate):
            path = candidate
        else:
            reason = "The stack file is no longer on disk."
    else:
        if subject.target_id not in frame_paths_by_target:
            target = get_target(subject.target_id)
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


def list_metrics(checkpoint: str | None = None) -> list[MetricSpec]:
    """List the declared quality metrics, in the order they are registered.

    Parameters
    ----------
    checkpoint : `str`, optional
        Only the metrics measured at this checkpoint (a `Checkpoint` value).

    Returns
    -------
    metrics : `list` [`MetricSpec`]
        The declarations.

    Notes
    -----
    An unknown checkpoint raises `InvalidArgumentError`.
    """
    if checkpoint is not None:
        check_choice("checkpoint", checkpoint, tuple(member.value for member in Checkpoint))
    specs = list(get_metric_registry().all())
    if checkpoint is not None:
        specs = [spec for spec in specs if spec.checkpoint.value == checkpoint]
    return specs


def get_metric_spec(name: str) -> MetricSpec:
    """Read the declaration of one quality metric.

    Parameters
    ----------
    name : `str`
        The metric's name, such as ``"frame.star_width_moffat_px"``.

    Returns
    -------
    spec : `MetricSpec`
        The whole declaration.

    Raises
    ------
    NotFoundError
        If no metric has this name.
    """
    registry = get_metric_registry()
    if name not in set(registry.names()):
        raise NotFoundError(f"No quality metric is named {name!r}. quality.records.get_metrics() lists them.")
    return registry.get(name)


def read_capture_settings(
    config: AppConfiguration, ids: Sequence[int] | None = None
) -> list[StoredCaptureSetting]:
    """Read the capture settings that stored measurements point at.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    ids : `Sequence` [`int`], optional
        Only these ids. Every stored setting when left out.

    Returns
    -------
    settings : `list` [`StoredCaptureSetting`]
        The settings with their ids, in id order. An id that is not stored
        is left out.
    """
    found = _store(config).get_capture_settings(None if ids is None else [int(value) for value in ids])
    return [StoredCaptureSetting(id=key, setting=found[key]) for key in sorted(found)]


def read_calibration_identities(
    config: AppConfiguration, target: Target | None = None, run_ids: Sequence[str] | None = None
) -> list[CalibrationIdentities]:
    """Read which calibration each library stacking run used.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings, which give the library folder.
    target : `Target`, optional
        Only the stacks of this target.
    run_ids : `Sequence` [`str`], optional
        Only these stacking runs.

    Returns
    -------
    records : `list` [`CalibrationIdentities`]
        The records, oldest first.
    """
    return _store(config).get_calibration_identities(
        None if run_ids is None else list(run_ids), None if target is None else target.id
    )
