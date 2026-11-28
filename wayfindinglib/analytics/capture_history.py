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


StoredValue = tuple[float, int | None]
"""A stored number and the id of the row it came from."""


def merge_frame_row(
    values: dict[str, Any], row: Any, pinned: Mapping[str, int], usable: Callable[[Any], bool]
) -> None:
    """Add one newest stored row to the numbers, if it can be used.

    The capture history and the star-shape review both read the newest rows
    of each frame and metric (``latest_only``), so they keep the same rule.
    Only whole-frame rows (no color channel) of the pinned metrics count.
    The newest row gives the number when it is usable and at the pinned
    definition version; otherwise the frame has no number for that metric,
    because an unusable newest row hides an older usable one.

    Parameters
    ----------
    values : `dict`
        The numbers by subject and metric: the value and row id of each.
    row : `Any`
        A newest stored row (`QualityMeasurement`).
    pinned : `~collections.abc.Mapping` [`str`, `int`]
        The metrics that count, each with its pinned definition version.
    usable : `~collections.abc.Callable`
        Says whether a row holds a number that can be used (it has a value
        and is not marked not measured, and any other rule of the caller).
    """
    version = pinned.get(row.metric)
    if version is None or row.subject.level != SubjectLevel.FRAME or row.subject.channel is not None:
        return
    if usable(row) and row.definition_version == version:
        values.setdefault(row.subject.subject_id, {})[row.metric] = (float(row.value), row.row_id)


def _has_value(row: Any) -> bool:
    """Say whether a stored frame row holds a number the capture history uses.

    Returns
    -------
    usable : `bool`
        True for a row that has a value and is not marked not measured.
    """
    return row.status != MeasurementStatus.NOT_MEASURED and row.value is not None


def read_frame_measurements(
    astrometrics: Any, metrics: Mapping[str, int] | None = None, target_id: str | None = None
) -> dict[str, dict[str, StoredValue]]:
    """Read the stored frame measurements the capture analysis uses.

    Only the newest row of each frame and metric is read, only for the
    pinned definition version, and only a row that holds a value. A frame
    measured per color plane has rows for each plane; those are left out
    here, since the star metrics used are those of the whole frame.

    Reading every frame of the library is one quality query for each
    metric. The science library keeps those reads and, on the next one,
    reads only the rows written since, so a repeat is quick. A caller that
    keeps the answer can ask whether it is still current with
    `frame_measurement_version`.

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
    pinned = PINNED_FRAME_METRICS if metrics is None else metrics
    values: dict[str, dict[str, StoredValue]] = {}
    for metric in pinned:
        for row in astrometrics.quality.records.query(
            metric=metric,
            level="frame",
            target=None if target_id is None else Target(id=target_id),
            latest_only=True,
        ):
            merge_frame_row(values, row, pinned, _has_value)
    return values


def frame_measurement_version(astrometrics: Any) -> tuple[str, ...]:
    """Give the data version of the stored frame measurements of the library.

    It is the science library's opaque data version of each pinned metric's
    frame rows (``quality.records.get_data_version``), a few short queries
    that read no rows. It changes whenever a frame row of one of those
    metrics is added or removed, so two equal versions mean
    `read_frame_measurements` gives the same numbers.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).

    Returns
    -------
    version : `tuple` [`str`, ...]
        One opaque text for each pinned metric, in name order.

    Notes
    -----
    A library that cannot be read raises the library's error.
    """
    records = astrometrics.quality.records
    return tuple(
        records.get_data_version(metric=metric, level="frame") for metric in sorted(PINNED_FRAME_METRICS)
    )


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
        them, when the caller keeps them. Read here when omitted.

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
