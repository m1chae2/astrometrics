"""Purpose: Read a camera's light frames and their stored measurements.

Description: Reads what the capture analysis and the performance envelope
need from the science library and from the recorded Ekos sessions, and
shapes it into plain data. This is Foundation code, so the Control analyses
and Planning can both call it.

A frame belongs to the equipment in use when its telescope and camera names
match the active ones. The camera may be spelled several ways (the FITS
header's name, the library's record name), so every spelling its profile
knows is accepted. Frames of other equipment are left out, which is how a
change of equipment starts the capture history afresh.

The frame record supplies when and how a frame was taken. Everything that was
measured on the pixels (star width, star shape, saturated pixels, background)
is read from the stored quality measurements with
`astrometrics.quality.records.query`, never from the frame record's
`measurements` object. Each metric is pinned to
the definition version this code was written for (`PINNED_FRAME_METRICS`). A
stored row of another version is not used, so the frame shows that value as
missing; remeasuring the frames brings it back (issue #95).
"""

import re
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from astrometricslib import Target
from astrometricslib.arguments import observing_night_id
from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from astrometricslib.reports import MeasurementStatus, SubjectLevel
from wayfindinglib.analytics.camera_profiles import profile_for_name
from wayfindinglib.analytics.performance_envelope import (
    MINIMUM_ATTEMPTS_PER_NIGHT,
    MINIMUM_FRAMES_PER_NIGHT,
)
from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.ekos_session import EkosCapture, EkosSessionContext

STAR_WIDTH_METRIC = "frame.star_width_moffat_arcsec"
"""Star width (FWHM of a Moffat fit), in arcseconds, one row per frame."""

STAR_ELONGATION_METRIC = "frame.star_elongation"
"""Long axis over short axis of the stars (1 is round), one row per frame."""

SATURATED_FRACTION_METRIC = "frame.saturated_pixel_fraction"
"""Share of the frame's pixels at or above the camera's saturation level."""

BACKGROUND_METRIC = "frame.background_level_adu"
"""Median pixel value of the frame, in ADU."""

PINNED_FRAME_METRICS = {
    STAR_WIDTH_METRIC: 1,
    STAR_ELONGATION_METRIC: 1,
    SATURATED_FRACTION_METRIC: 1,
    BACKGROUND_METRIC: 1,
}
"""The metrics read for each frame, and the version each is pinned to.

The versions are those registered in `astrometricslib` when this code was
written. A test fails when the registry moves past one, so a change of
definition is reviewed here rather than mixed in silently.
"""

_CALIBRATION_FOLDER_NAMES = frozenset({"dark", "bias", "flat"})
"""Folder names Ekos gives calibration exposures."""

_LIGHT_FOLDER_NAME = "light"
"""The folder name Ekos gives light exposures."""

_PROCESSED_FILE_MARKER = "processed"
"""Text in a file name that marks the output of post-processing.

A stacked image saved beside the raw frames (for example
``NGC_2403_Processed.fits``, 13,740 seconds long) is not a capture. The
science library's frame scan now skips such files. This check stays as a
second line of defence, because one recorded as a light frame would add hours
of exposure to a night that never had them.
"""


def normalize_name(name: str | None) -> str:
    """Reduce a device name to lowercase letters and digits.

    Returns
    -------
    normalized : `str`
        The normalized name, or an empty string for no name.
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def equipment_spellings(astrometrics: Any, camera_name: str) -> set[str]:
    """List every normalized spelling of a camera's name.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``); it
        holds the camera profiles.
    camera_name : `str`
        The camera's name, written in any spelling.

    Returns
    -------
    spellings : `set` [`str`]
        The name itself, the profile's name, its record name and its
        aliases, each normalized.
    """
    profile = profile_for_name(astrometrics, camera_name)
    return {
        normalize_name(name)
        for name in (camera_name, profile.camera_name, profile.record_name, *profile.name_aliases)
        if name
    }


def _positive(value: float | None) -> float | None:
    """Return `value` if it is a positive number, otherwise `None`.

    Returns
    -------
    value : `float` or `None`
        A zero or negative measurement is a failed fit, not a measurement.
    """
    return value if value is not None and value > 0 else None


RecencyKey = tuple[int, int]
"""When a stored row was measured and its row id, in the store's order.

The time is in whole microseconds since 1970, so it compares exactly. The
store's latest view picks, for each subject, metric and channel, the row
with the latest measurement time, and of those the largest row id. Comparing
these keys gives the same choice.
"""

RowMark = tuple[int, str, str, str, datetime]
"""What identifies one stored row: its id, metric, subject, run and time."""

StoredValue = tuple[float, int | None]
"""A stored number and the id of the row it came from."""

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""The start of the Unix clock, for `RecencyKey` times."""

ROW_READ_MARGIN = timedelta(seconds=1)
"""How far before the kept newest time a read of new rows starts.

The quality query turns its ``since`` time into seconds and back, which can
move it by a microsecond. Starting a second early cannot miss a new row;
rows that were already read are then left out by their row id.
"""


def row_recency(row: Any) -> RecencyKey:
    """Give the order key of a stored row (see `RecencyKey`).

    Returns
    -------
    key : `RecencyKey`
        The measurement time in whole microseconds since 1970, and the row
        id (0 when it has none).
    """
    return ((row.measured_at - _EPOCH) // timedelta(microseconds=1), int(row.row_id or 0))


def row_mark(row: Any) -> RowMark:
    """Describe a stored row well enough to see whether it was replaced.

    Returns
    -------
    mark : `RowMark`
        The row's id, metric, subject, run and measurement time.
    """
    return (int(row.row_id), row.metric, row.subject.subject_id, row.run_id, row.measured_at)


def read_row_mark(astrometrics: Any, row_id: int) -> RowMark | None:
    """Read the `RowMark` of one stored row by its id.

    Returns
    -------
    mark : `RowMark` or `None`
        The row's mark, or `None` when no row has this id.
    """
    row = astrometrics.quality.records.get(int(row_id))
    return None if row is None else row_mark(row)


def merge_newest_frame_row(
    values: dict[str, Any],
    recency: dict[str, Any],
    row: Any,
    pinned: Mapping[str, int],
    usable: Callable[[Any], bool],
) -> None:
    """Add one stored row to kept numbers, as the store's latest view would.

    A full read (of the newest rows) and an update that reads only added rows
    both use this, so they keep the same rule. Only whole-frame rows (no
    color channel) of the pinned metrics count. A row replaces the held one
    only when it is newer (`RecencyKey`). The newest row then gives the
    number when it is usable and at the pinned definition version; otherwise
    the subject has no number for that metric, as in a full read.

    Parameters
    ----------
    values : `dict`
        The numbers by subject and metric: the value and row id of each. A
        subject left with no number is removed.
    recency : `dict`
        The `RecencyKey` of the newest row of each subject and metric,
        usable or not.
    row : `Any`
        A stored row (`QualityMeasurement`).
    pinned : `~collections.abc.Mapping` [`str`, `int`]
        The metrics that count, each with its pinned definition version.
    usable : `~collections.abc.Callable`
        Says whether a row holds a number that can be used (it has a value
        and is not marked not measured, and any other rule of the caller).
    """
    version = pinned.get(row.metric)
    if version is None or row.subject.level != SubjectLevel.FRAME or row.subject.channel is not None:
        return
    subject, key = row.subject.subject_id, row_recency(row)
    held = recency.get(subject, {}).get(row.metric)
    if held is not None and held >= key:
        return
    recency.setdefault(subject, {})[row.metric] = key
    if usable(row) and row.definition_version == version:
        values.setdefault(subject, {})[row.metric] = (float(row.value), row.row_id)
        return
    held_values = values.get(subject)
    if held_values is not None:
        held_values.pop(row.metric, None)
        if not held_values:
            del values[subject]


def _has_value(row: Any) -> bool:
    """Say whether a stored frame row holds a number the capture history uses.

    Returns
    -------
    usable : `bool`
        True for a row that has a value and is not marked not measured.
    """
    return row.status != MeasurementStatus.NOT_MEASURED and row.value is not None


def _read_newest_frame_rows(
    astrometrics: Any, metrics: Mapping[str, int], target_id: str | None
) -> tuple[dict[str, dict[str, StoredValue]], dict[str, dict[str, RecencyKey]]]:
    """Read the newest stored frame row of each frame and metric.

    Returns
    -------
    values, recency : `tuple` [`dict`, `dict`]
        The numbers and the `RecencyKey` of the newest whole-frame rows,
        each by frame subject id and then by metric (see
        `merge_newest_frame_row`).
    """
    values: dict[str, dict[str, StoredValue]] = {}
    recency: dict[str, dict[str, RecencyKey]] = {}
    for metric in metrics:
        for row in astrometrics.quality.records.query(
            metric=metric,
            level="frame",
            target=None if target_id is None else Target(id=target_id),
            latest_only=True,
        ):
            merge_newest_frame_row(values, recency, row, metrics, _has_value)
    return values, recency


def read_frame_measurements(
    astrometrics: Any, metrics: Mapping[str, int] | None = None, target_id: str | None = None
) -> dict[str, dict[str, StoredValue]]:
    """Read the stored frame measurements the capture analysis uses.

    Only the newest row of each frame and metric is read, only for the
    pinned definition version, and only a row that holds a value. A frame
    measured per color plane has rows for each plane; those are left out
    here, since the star metrics used are those of the whole frame.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    metrics : `~collections.abc.Mapping` [`str`, `int`], optional
        The metrics to read, each with its pinned definition version.
        `PINNED_FRAME_METRICS` when omitted.
    target_id : `str`, optional
        Read only this target's frames. Every target's when omitted.

    Returns
    -------
    measurements : `dict`
        Keyed by frame key (see `Target.frame_subject_id`), then by metric: the
        `tuple` of each metric's value (`float`) and row id (`int` or `None`)
        of the stored measurement it came from.
    """
    values, _ = _read_newest_frame_rows(
        astrometrics, PINNED_FRAME_METRICS if metrics is None else metrics, target_id
    )
    return values


@dataclass(frozen=True)
class FrameMeasurements:
    """The stored measurements of every frame in the library, read once.

    The capture history reads four metrics of every frame
    (`PINNED_FRAME_METRICS`). Reading them takes about half a second for a
    library of a few thousand frames, and a new frame adds only a few rows.
    So a cache keeps this and brings it up to date with
    `update_frame_measurements`, which reads only the rows added since.
    Treat the mappings as read-only: a cache may hand the same object to
    every caller.

    Attributes
    ----------
    values : `~collections.abc.Mapping` [`str`, `~collections.abc.Mapping`]
        By frame subject id, then by metric: the value and row id of the
        newest usable row, as `read_frame_measurements` gives them.
    recency : `~collections.abc.Mapping` [`str`, `~collections.abc.Mapping`]
        By frame subject id, then by metric: the `RecencyKey` of the newest
        whole-frame row, usable or not. A newer row that is not usable hides
        an older usable one, so this is what an added row is compared with.
    version : `Mapping` or `None`
        The data version of each metric
        (``astrometricslib.drivers.get_frame_metric_data_versions``) that
        these numbers match exactly, or `None` when that is not known (the
        rows changed while they were read). Only numbers with a version can
        be brought up to date.
    marks : `~collections.abc.Mapping` [`str`, `RowMark`]
        For each metric that has rows at `version`, the mark of its newest
        row. If that row is gone or changed later, a row was removed and its
        row id may have been used again, so everything is read again.
    """

    values: Mapping[str, Mapping[str, StoredValue]]
    recency: Mapping[str, Mapping[str, RecencyKey]] = field(default_factory=dict)
    version: Mapping[str, Any] | None = None
    marks: Mapping[str, RowMark] = field(default_factory=dict)


def read_frame_measurement_table(
    astrometrics: Any, read_version: Callable[[], Mapping[str, Any] | None] | None = None
) -> FrameMeasurements:
    """Read the pinned measurements of every frame, ready to be kept.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    read_version : `~collections.abc.Callable`, optional
        Gives the data version of each pinned metric
        (``get_frame_metric_data_versions``). When given, it is read before
        and after the rows. If it did not change, the answer records it, so
        `update_frame_measurements` can later read only newer rows.

    Returns
    -------
    measurements : `FrameMeasurements`
        The numbers, the same as `read_frame_measurements` gives.
    """
    before = read_version() if read_version is not None else None
    values, recency = _read_newest_frame_rows(astrometrics, PINNED_FRAME_METRICS, None)
    if before is None or read_version is None:
        return FrameMeasurements(values=values, recency=recency)
    marks = {}
    for metric, part in before.items():
        if part.measurement_count > 0:
            mark = read_row_mark(astrometrics, part.newest_row_id)
            if mark is None:
                return FrameMeasurements(values=values, recency=recency)
            marks[metric] = mark
    if read_version() != before:
        return FrameMeasurements(values=values, recency=recency)
    return FrameMeasurements(values=values, recency=recency, version=before, marks=marks)


def update_frame_measurements(
    astrometrics: Any, kept: FrameMeasurements, version: Mapping[str, Any]
) -> FrameMeasurements | None:
    """Bring kept frame measurements up to date by reading only new rows.

    The quality store only adds rows; the one exception is a deleted frame,
    whose rows are removed. So when the rows of the pinned metrics were only
    added since the kept numbers were read, reading the added rows and
    merging them in (`merge_newest_frame_row`) gives the same answer as
    reading everything again. This checks that for each metric whose
    version changed, in order:

    1. The kept newest row of the metric (`FrameMeasurements.marks`) is
       still there and unchanged. Then no row id at or below it was used
       again, and every added row of the metric has a larger row id.
    2. The frame rows of the metric with a larger row id, up to the newest
       one in `version`, are read (by measurement time from just before the
       kept newest time, then by row id). Their count and the sum of their
       row ids must equal the growth of the count and the sum in `version`.
       A removed row lowers both, and an added row measured earlier than the
       kept newest time is not found, so either shows as a mismatch.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    kept : `FrameMeasurements`
        The numbers kept from an earlier read. They are not changed.
    version : `~collections.abc.Mapping`
        The data version of each pinned metric now
        (``get_frame_metric_data_versions``).

    Returns
    -------
    measurements : `FrameMeasurements` or `None`
        The numbers for `version`, or `None` when they cannot be brought up
        to date this way and must be read again.
    """
    base = kept.version
    if base is None or version is None or set(base) != set(version):
        return None
    grown = {}
    for metric, now in version.items():
        before = base[metric]
        if now == before:
            continue
        if before.measurement_count == 0 or not before.newest_measured_at:
            return None
        if now.measurement_count < before.measurement_count or now.newest_row_id < before.newest_row_id:
            return None
        if read_row_mark(astrometrics, before.newest_row_id) != kept.marks.get(metric):
            return None
        grown[metric] = (before, now)
    if not grown:
        return replace(kept, version=version)
    since = min(datetime.fromisoformat(before.newest_measured_at) for before, _ in grown.values())
    added = [
        row
        for row in astrometrics.quality.records.query(
            level="frame", since=since - ROW_READ_MARGIN, latest_only=False
        )
        if row.metric in grown
        and row.row_id is not None
        and grown[row.metric][0].newest_row_id < row.row_id <= grown[row.metric][1].newest_row_id
    ]
    marks = dict(kept.marks)
    for metric, (before, now) in grown.items():
        rows = [row for row in added if row.metric == metric]
        if len(rows) != now.measurement_count - before.measurement_count:
            return None
        if sum(row.row_id for row in rows) != now.measurement_row_id_sum - before.measurement_row_id_sum:
            return None
        newest = max(rows, key=lambda row: row.row_id, default=None)
        if newest is None or newest.row_id != now.newest_row_id:
            return None
        marks[metric] = row_mark(newest)
    values = dict(kept.values)
    recency = dict(kept.recency)
    for subject in {row.subject.subject_id for row in added}:
        # Copy only the subjects that may change, so the kept numbers, which
        # other callers may still hold, stay as they were.
        if subject in values:
            values[subject] = dict(values[subject])
        if subject in recency:
            recency[subject] = dict(recency[subject])
    for row in sorted(added, key=row_recency):
        merge_newest_frame_row(values, recency, row, PINNED_FRAME_METRICS, _has_value)
    return FrameMeasurements(values=values, recency=recency, version=version, marks=marks)


def equipment_light_frames(
    astrometrics: Any, telescope_name: str, camera_name: str, targets: Sequence[Any] | None = None
) -> Iterator[tuple[Any, Any]]:
    """Walk the light frames taken with this telescope and camera.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    telescope_name : `str`
        Name of the imaging telescope.
    camera_name : `str`
        Name of the imaging camera, in any spelling its profile knows.
    targets : `Sequence` [`Target`], optional
        The library's targets, when the caller has just listed them.

    Yields
    ------
    target : `Target`
        The target the frame belongs to.
    frame : `FrameRecord`
        A light frame of the equipment that has a capture time. Output of
        post-processing recorded as a light frame is left out.
    """
    camera_spellings = equipment_spellings(astrometrics, camera_name)
    telescope_spelling = normalize_name(telescope_name)
    for target in astrometrics.targets.query(include_empty=True).items if targets is None else targets:
        for frame in target.frames:
            if not str(frame.role).upper().endswith("LIGHT") or frame.timestamp is None:
                continue
            if _PROCESSED_FILE_MARKER in frame.path.rsplit("/", 1)[-1].lower():
                continue
            if normalize_name(frame.camera) not in camera_spellings:
                continue
            if normalize_name(frame.telescope) != telescope_spelling:
                continue
            yield target, frame


def collect_capture_frames(
    astrometrics: Any,
    telescope_name: str,
    camera_name: str,
    targets: Sequence[Any] | None = None,
    stored: Mapping[str, Mapping[str, StoredValue]] | None = None,
) -> list[CaptureFrame]:
    """Read every light frame taken with this telescope and camera.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    telescope_name : `str`
        Name of the imaging telescope.
    camera_name : `str`
        Name of the imaging camera, in any spelling its profile knows.
    targets : `Sequence` [`Target`], optional
        The library's targets, when the caller has just listed them.
    stored : `~collections.abc.Mapping`, optional
        The frames' stored measurements, as `read_frame_measurements` gives
        them, when the caller keeps them (`FrameMeasurements.values`). Read
        here when omitted.

    Returns
    -------
    frames : `list` [`CaptureFrame`]
        The equipment's light frames that have a capture time, oldest first.
        A frame with no capture time cannot be placed in a night and is left
        out, as is the output of post-processing recorded as a light frame.
        A measurement the frame lacks (or has only at another definition
        version) stays `None`.
    """
    if stored is None:
        stored = read_frame_measurements(astrometrics)
    frames = []
    for target, frame in equipment_light_frames(astrometrics, telescope_name, camera_name, targets):
        values = stored.get(target.frame_subject_id(frame), {})
        pixel_scale = _positive(frame.pixel_scale_arcsec)
        elongation = values.get(STAR_ELONGATION_METRIC)
        frames.append(
            CaptureFrame(
                path=frame.path,
                target_id=target.id,
                timestamp=float(frame.timestamp),
                exposure_s=float(frame.exposure_s or 0.0),
                filter_name=str(getattr(frame.filter, "value", frame.filter)),
                is_spectral=frame.is_spectral,
                sensor_temperature_c=frame.sensor_temperature_c,
                saturated_pixel_fraction=values.get(SATURATED_FRACTION_METRIC, (None, None))[0],
                background_adu=values.get(BACKGROUND_METRIC, (None, None))[0],
                star_width_arcsec=_positive(values.get(STAR_WIDTH_METRIC, (None, None))[0]),
                # The elongation is long axis over short axis (at least 1),
                # and roundness is its inverse (at most 1).
                roundness=1.0 / elongation[0] if elongation is not None and elongation[0] >= 1.0 else None,
                altitude_deg=frame.altitude_deg,
                azimuth_deg=frame.azimuth_deg,
                pier_side=frame.pier_side,
                pixel_scale_arcsec=pixel_scale,
                binning=frame.binning or 1,
                measurement_rows={
                    metric: row_id for metric, (_, row_id) in values.items() if row_id is not None
                },
            )
        )
    frames.sort(key=lambda frame: frame.timestamp)
    return frames


def night_of(frame: CaptureFrame) -> str:
    """Name the observing night a frame was taken on.

    Returns
    -------
    night : `str`
        The local date on which the night began, for example ``"2026-09-24"``.
    """
    return observing_night_id(frame.timestamp)


def frames_by_night(frames: Iterable[CaptureFrame]) -> dict[str, list[CaptureFrame]]:
    """Group frames by the observing night they were taken on.

    Returns
    -------
    grouped : `dict` [`str`, `list` [`CaptureFrame`]]
        Each night's frames, in time order.
    """
    grouped: dict[str, list[CaptureFrame]] = defaultdict(list)
    for frame in frames:
        grouped[night_of(frame)].append(frame)
    return dict(grouped)


def usable_star_frames(frames: Iterable[CaptureFrame], minimum_exposure_s: float) -> list[CaptureFrame]:
    """Pick the frames whose star measurements say something about the sky.

    Spectroscopy frames are left out, since a dispersed star has no
    meaningful width. So are frames shorter than `minimum_exposure_s`:
    in a short exposure guiding error has no time to widen the star, and the
    bright single stars usually shot that briefly are clipped, which
    inflates their measured width.

    Returns
    -------
    usable : `list` [`CaptureFrame`]
        Imaging frames with a measured star width that are long enough.
    """
    return [
        frame
        for frame in frames
        if not frame.is_spectral
        and frame.star_width_arcsec is not None
        and frame.exposure_s >= minimum_exposure_s
    ]


MINIMUM_FRAMES_PER_EXPOSURE_LENGTH = TunedParameter(
    name="wayfinding.minimum_frames_per_exposure_length",
    default=10,
    unit="frames",
    description="Fewest frames an exposure length needs to count as one in use.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D125",
)
"""Fewest frames an exposure length needs to count as one in use.

An exposure length tried a few times while testing is not a length the
equipment is used with.
"""

MAXIMUM_EXPOSURE_LENGTHS = TunedParameter(
    name="wayfinding.maximum_exposure_lengths",
    default=6,
    unit="exposure lengths",
    description="Most exposure lengths the exposure-length view reports, most used first.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D125",
)
"""Most exposure lengths the exposure-length view reports, most used first."""


def exposure_lengths_in_use(frames: Iterable[CaptureFrame], minimum_exposure_s: float) -> list[float]:
    """List the exposure lengths the equipment is actually used with.

    Only lengths long enough for guiding error to matter count, since guiding
    cannot spoil a shorter exposure.

    Parameters
    ----------
    frames : `Iterable` [`CaptureFrame`]
        The equipment's light frames.
    minimum_exposure_s : `float`
        Shortest exposure length that counts.

    Returns
    -------
    lengths : `list` [`float`]
        Lengths with at least `MINIMUM_FRAMES_PER_EXPOSURE_LENGTH` frames, the
        `MAXIMUM_EXPOSURE_LENGTHS` most used if there are more, shortest first.
    """
    counts: dict[float, int] = defaultdict(int)
    for frame in frames:
        if frame.exposure_s >= minimum_exposure_s:
            counts[round(frame.exposure_s, 3)] += 1
    used = [
        (length, count)
        for length, count in counts.items()
        if count >= resolve_parameter(MINIMUM_FRAMES_PER_EXPOSURE_LENGTH).value
    ]
    used.sort(key=lambda item: -item[1])
    return sorted(length for length, _ in used[: resolve_parameter(MAXIMUM_EXPOSURE_LENGTHS).value])


def capture_kind(capture: EkosCapture) -> str:
    """Tell what kind of exposure an Ekos capture was.

    Ekos saves each kind in its own folder (``.../Light/...``, ``.../Dark/``),
    so the saved path names the kind. A capture with no saved path (older
    Ekos versions did not log it) has an unknown kind.

    Returns
    -------
    kind : `str`
        ``"calibration"`` for a dark, bias or flat, ``"light"`` for a light,
        or ``"unknown"`` if the path says neither.
    """
    folders = {part.lower() for part in capture.file_path.split("/") if part}
    if folders & _CALIBRATION_FOLDER_NAMES:
        return "calibration"
    if _LIGHT_FOLDER_NAME in folders:
        return "light"
    return "unknown"


def night_captures(contexts: Iterable[EkosSessionContext], night: str) -> tuple[list[EkosCapture], int, bool]:
    """Collect one night's captures and aborts from the Ekos records.

    Parameters
    ----------
    contexts : `Iterable` [`EkosSessionContext`]
        Every recorded Ekos session.
    night : `str`
        The observing night.

    Returns
    -------
    captures : `list` [`EkosCapture`]
        Every exposure Ekos finished that night.
    aborted : `int`
        How many exposures it cancelled.
    has_record : `bool`
        Whether any session record exists for the night.
    """
    captures: list[EkosCapture] = []
    aborted = 0
    has_record = False
    for context in contexts:
        if context.session_id != night:
            continue
        has_record = True
        captures.extend(context.captures)
        aborted += len(context.aborted_captures)
    return captures, aborted, has_record


def collect_capture_baseline_values(
    frames: Sequence[CaptureFrame],
    contexts: Iterable[EkosSessionContext],
    minimum_exposure_s: float | None,
    before_night: str | None = None,
) -> dict[str, list[float]]:
    """Summarise each earlier night of this equipment's captures.

    Only nights with frames from this equipment count, so a change of
    equipment starts with an empty history. For each such night this gives
    the median star width and roundness of the imaging frames, and the share
    of exposures Ekos cancelled.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        Every light frame of the equipment in use.
    contexts : `Iterable` [`EkosSessionContext`]
        Every recorded Ekos session.
    minimum_exposure_s : `float` or `None`
        Shortest exposure whose star measurements count. `None` when it is
        not yet known, in which case no star-quality history is built.
    before_night : `str` or `None`, optional
        If given, only nights earlier than this one are read. Judging a night
        against a history that includes that night, or later ones, would let
        a bad night make itself look normal.

    Returns
    -------
    baseline_values : `dict` [`str`, `list` [`float`]]
        ``"night_star_width"`` (arcseconds), ``"night_star_roundness"`` and
        ``"capture_abort_fraction"``, one value per qualifying night.
    """
    contexts = list(contexts)
    widths: list[float] = []
    roundnesses: list[float] = []
    abort_fractions: list[float] = []
    for night, night_frames in sorted(frames_by_night(frames).items()):
        if before_night is not None and night >= before_night:
            continue
        if len(night_frames) < resolve_parameter(MINIMUM_FRAMES_PER_NIGHT).value:
            continue
        if minimum_exposure_s is not None:
            measured = usable_star_frames(night_frames, minimum_exposure_s)
            if len(measured) >= resolve_parameter(MINIMUM_FRAMES_PER_NIGHT).value:
                widths.append(statistics.median(frame.star_width_arcsec for frame in measured))
                night_roundness = [frame.roundness for frame in measured if frame.roundness is not None]
                if len(night_roundness) >= resolve_parameter(MINIMUM_FRAMES_PER_NIGHT).value:
                    roundnesses.append(statistics.median(night_roundness))
        captures, aborted, has_record = night_captures(contexts, night)
        attempts = len(captures) + aborted
        if has_record and attempts >= resolve_parameter(MINIMUM_ATTEMPTS_PER_NIGHT).value:
            abort_fractions.append(aborted / attempts)
    return {
        "night_star_width": widths,
        "night_star_roundness": roundnesses,
        "capture_abort_fraction": abort_fractions,
    }


def roundness_baseline_signature(
    frames: Iterable[Any],
    stored: Mapping[str, Mapping[str, StoredValue]],
    before_night: str | None = None,
) -> tuple[Any, ...]:
    """Describe every frame that can change a night's median roundness.

    `collect_capture_baseline_values` adds a night's median roundness only
    when the night has at least ``MINIMUM_FRAMES_PER_NIGHT`` frames that are
    not spectral, have a positive star width, have a roundness (an
    elongation of at least 1) and are long enough. A frame that lacks any of
    the first three cannot be one of them, whatever its exposure time, and a
    night with fewer such frames than the minimum adds nothing. So this
    lists only the frames that have all three, of the nights with enough of
    them: each frame's night, subject, exposure time and elongation. When
    this signature is the same, the roundness series is the same for every
    shortest exposure time (which comes from the guiding records).

    A frame that was just indexed and not yet measured, a frame whose width
    or elongation is missing or unusable, and a spectral frame leave the
    signature as it is. A frame that gains both rows, changes its exposure
    time, spectral flag or night, or is removed or set aside (it leaves the
    target's frame list) changes it.

    Parameters
    ----------
    frames : `~collections.abc.Iterable`
        The equipment's light frames, each with ``night``, ``subject_id``,
        ``exposure_s`` and ``is_spectral`` (a ``NightFrame``). They must be
        the frames `collect_capture_frames` reads.
    stored : `~collections.abc.Mapping`
        The frames' stored measurements (`read_frame_measurements`).
    before_night : `str` or `None`, optional
        Leave out this night and later ones, as
        `collect_capture_baseline_values` does.

    Returns
    -------
    signature : `tuple`
        The minimum frame count, then, for each night with enough frames,
        the night and its sorted frames.
    """
    minimum = resolve_parameter(MINIMUM_FRAMES_PER_NIGHT).value
    by_night: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for frame in frames:
        if frame.is_spectral or (before_night is not None and frame.night >= before_night):
            continue
        values = stored.get(frame.subject_id, {})
        width = _positive(values.get(STAR_WIDTH_METRIC, (None, None))[0])
        elongation = values.get(STAR_ELONGATION_METRIC, (None, None))[0]
        if width is None or elongation is None or elongation < 1.0:
            continue
        by_night[frame.night].append((frame.subject_id, float(frame.exposure_s), elongation))
    return (
        minimum,
        tuple(
            (night, tuple(sorted(night_frames)))
            for night, night_frames in sorted(by_night.items())
            if len(night_frames) >= minimum
        ),
    )
