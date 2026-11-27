"""Purpose: The work behind the calibration sub-API.

Description: The public methods of `CalibrationCatalog` and of its `frames`
child are thin wrappers. Each one calls one function here, which does the
work on the library's index of calibration frames (`CalibrationLibrary`):

* `query_calibration_sets`: how many darks, biases and flats the library
  holds per setting, narrowed to the fields a `CaptureSetting` gives.
* `match_calibration`: groups light frames by setting and runs the
  stacker's own dark, bias and flat selection for each group.
* `assess_calibration_sets`: judges dark, bias or flat sets.
* `add_calibration_frames` and `scan_calibration_frames`: move new files into
  the calibration folders, rescan the index and save it.
* `record_calibration_frame_measurements`: measures the dark and bias frames
  that are new or changed and stores their numbers.
* `get_camera_profile`: the stored noise profile of a camera.

A `CaptureSetting` says which sets a call means. A field left empty is not
compared. The camera is compared ignoring case, gains as numbers, binning in
its standard form (``2`` is ``"2x2"``), a dark's temperature by its
temperature slot, a dark's exposure within
`EXPOSURE_MATCH_TOLERANCE_SECONDS`, and a flat's filter in its standard
spelling. A camera also matches in any spelling its noise profile lists.
A field a kind is not filed by (a bias has no exposure) is not
compared for that kind.
"""

import logging
import math
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from astrometricslib._internal.drivers.calibration_library import (
    DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
    CalibrationLibrary,
    normalize_binning,
    temperature_slot_c,
)
from astrometricslib._internal.drivers.camera_profile_store import camera_identity, resolve_camera_profile
from astrometricslib.foundation.arguments import check_choice, check_limit
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.foundation.jobs.runner import current_job_id, registered_job
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.storage.data_versions import QUALITY_KIND, get_library_data_version
from astrometricslib.foundation.versioned_cache import VersionedCache
from astrometricslib.models.calibration_ingest import (
    CalibrationIngestReport,
    CalibrationMeasurementReport,
    CalibrationSetHealth,
)
from astrometricslib.models.calibration_inventory import CalibrationMatch, CalibrationSetCount
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.observatory_configuration import Camera
from astrometricslib.models.page import Page
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.stellar_source import CalibrationGroupMatch
from astrometricslib.models.target import FrameRecord
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.frame_choice import FrameChoice, frames_from_choice
from astrometricslib.pipelines.shared.quality.metrics import CALIBRATION_FRAME_METRICS, FRAME_METRICS
from astrometricslib.pipelines.stacking.calibration_ingest import (
    assess_flat_group,
    build_ingest_report,
    flatten_frame_index,
)
from astrometricslib.pipelines.stacking.calibration_measurement_run import measure_calibration_frames
from astrometricslib.pipelines.stacking.dark_defect_run import measure_dark_defects_in_library
from astrometricslib.pipelines.stacking.pre_processing.calibration_assessment import (
    combine_status,
    flat_set_checks,
    summarize_checks,
)

__all__ = [
    "CALIBRATION_KINDS",
    "EXPOSURE_MATCH_TOLERANCE_SECONDS",
    "add_calibration_frames",
    "assess_calibration_sets",
    "get_camera_profile",
    "match_calibration",
    "query_calibration_sets",
    "record_calibration_frame_measurements",
    "scan_calibration_frames",
]

CALIBRATION_KINDS: tuple[str, ...] = ("dark", "bias", "flat")
"""The kinds of calibration frame, in the order every answer lists them."""

_STATS_KEYS = {"dark": "darks", "bias": "biases", "flat": "flats"}
"""The key of each kind's rows in `CalibrationLibrary.get_stats`."""

# The definition version of each metric a calibration judgement reads. A stored
# row of another version is not used, so a redefined metric is never mixed
# with the old numbers.
_CURRENT_VERSIONS = {
    spec.name: spec.definition_version for spec in (*FRAME_METRICS, *CALIBRATION_FRAME_METRICS)
}

# Two dark exposures are the same set when they differ by less than this many
# seconds. It is the tolerance the library uses to pick a dark for a light
# (`select_dark_frames`).
EXPOSURE_MATCH_TOLERANCE_SECONDS = TunedParameter(
    name="api.exposure_match_tolerance_seconds",
    default=0.05,
    unit="s",
    description="Largest exposure time difference at which a calibration set still matches.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.EQUIPMENT_PROFILE,
    equipment=frozenset({EquipmentKind.CAMERA}),
    register_entry="STK-D07",
)

logger = logging.getLogger(__name__)


def _filter_text(value: Any) -> str:
    """Write a filter in its one standard spelling.

    Parameters
    ----------
    value : `Any`
        A `FilterType`, or text such as ``"L"`` or ``"Luminance"``.

    Returns
    -------
    text : `str`
        The `FilterType` value when the text is a filter's value or member
        name, else the text.
    """
    text = str(value).strip()
    try:
        return FilterType(text).value
    except ValueError:
        member = FilterType.__members__.get(text)
        return text if member is None else member.value


def _gains_match(stored: Any, wanted: Any) -> bool:
    """Tell whether a stored gain is the wanted one.

    Parameters
    ----------
    stored, wanted : `Any`
        The two gains. They are compared as numbers when both are numbers,
        else as text.

    Returns
    -------
    matches : `bool`
        `True` when they are the same gain.
    """
    try:
        return math.isclose(float(stored), float(wanted))
    except TypeError, ValueError:
        return str(stored).strip() == str(wanted).strip()


def _row_key(kind: str, row: dict[str, Any]) -> str:
    """Make the stable key of one calibration counts row.

    Parameters
    ----------
    kind : `str`
        ``"dark"``, ``"bias"`` or ``"flat"``.
    row : `dict` [`str`, `Any`]
        The counts row from `CalibrationLibrary.get_stats`.

    Returns
    -------
    key : `str`
        The kind, camera, filter, gain, offset, binning, temperature and
        exposure joined with ``|``. Two rows never share a key.
    """
    parts = [
        kind,
        row.get("camera"),
        row.get("filter"),
        row.get("gain"),
        row.get("offset"),
        row.get("binning"),
        row.get("temperature_c"),
        row.get("exposure_s"),
    ]
    return "|".join("" if part is None else str(part) for part in parts)


def _same_camera(stored: Any, wanted: str, config: AppConfiguration | None) -> bool:
    """Tell whether a stored camera name is the wanted camera.

    Parameters
    ----------
    stored : `Any`
        The camera name a set is filed under.
    wanted : `str`
        The camera asked for.
    config : `AppConfiguration` or `None`
        The settings that hold the camera profiles, whose aliases count.

    Returns
    -------
    same : `bool`
        `True` when the names are equal ignoring case, or name the same
        camera through its profile.
    """
    if str(stored or "").strip().casefold() == wanted.strip().casefold():
        return True
    try:
        return camera_identity(str(stored or ""), config) == camera_identity(wanted, config)
    except ConfigurationError:
        return False


def _matches_setting(
    kind: str, row: dict[str, Any], setting: CaptureSetting | None, config: AppConfiguration | None = None
) -> bool:
    """Tell whether a set, written as a counts row, has the asked settings.

    Parameters
    ----------
    kind : `str`
        The set's kind.
    row : `dict` [`str`, `Any`]
        The set as a counts row: ``camera``, ``gain``, ``offset``,
        ``binning`` and, by kind, ``temperature_c``, ``exposure_s`` or
        ``filter``.
    setting : `CaptureSetting` or `None`
        The settings to match (see the module description). `None` matches
        every set.
    config : `AppConfiguration`, optional
        The settings that hold the camera profiles, for the camera match.

    Returns
    -------
    matches : `bool`
        `True` when every given field matches.
    """
    if setting is None:
        return True
    if not _same_camera(row.get("camera"), setting.camera, config):
        return False
    if setting.gain is not None and not _gains_match(row.get("gain"), setting.gain):
        return False
    if setting.offset is not None and not math.isclose(
        float(row.get("offset") or 0.0), float(setting.offset)
    ):
        return False
    if setting.binning is not None and normalize_binning(row.get("binning")) != normalize_binning(
        setting.binning
    ):
        return False
    if kind == "dark":
        tolerance_c = resolve(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value
        if setting.temperature_c is not None and row.get("temperature_c") != temperature_slot_c(
            setting.temperature_c, tolerance_c
        ):
            return False
        if setting.exposure_s is not None and (
            row.get("exposure_s") is None
            or abs(float(row["exposure_s"]) - setting.exposure_s)
            >= resolve(EXPOSURE_MATCH_TOLERANCE_SECONDS).value
        ):
            return False
    if kind == "flat" and setting.filter_name is not None and row.get("filter") is not None:
        return _filter_text(row["filter"]) == _filter_text(setting.filter_name)
    return True


def query_calibration_sets(
    library: CalibrationLibrary,
    config: AppConfiguration | None,
    kind: str | None = None,
    setting: CaptureSetting | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> Page[CalibrationSetCount]:
    """List how many frames the library holds per kind and setting.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration` or `None`
        The settings that hold the camera profiles, so a camera matches in
        any spelling its profile lists.
    kind : {"dark", "bias", "flat"}, optional
        Only this kind. `None` lists all three, darks first.
    setting : `CaptureSetting`, optional
        Only the sets with these settings (see the module description). The
        telescope is not compared, because the counts do not keep it.
    limit : `int`, optional
        Most rows to return. `None` returns every match.
    offset : `int`, optional
        How many matches to skip first.

    Returns
    -------
    page : `Page` [`CalibrationSetCount`]
        One row per kind, camera and setting (and exposure, for darks, or
        filter, for flats).

    Notes
    -----
    Raises `InvalidArgumentError` if the kind is unknown or the paging is
    not valid.
    """
    if kind is not None:
        check_choice("kind", kind, CALIBRATION_KINDS)
    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)
    stats = library.get_stats()
    rows = [
        CalibrationSetCount(
            kind=label,
            camera=str(row.get("camera") or ""),
            gain=row.get("gain"),
            offset=row.get("offset"),
            binning=row.get("binning"),
            temperature_c=row.get("temperature_c"),
            exposure_s=row.get("exposure_s"),
            filter_name=row.get("filter"),
            count=int(row.get("count") or 0),
            row_key=_row_key(label, row),
        )
        for label in CALIBRATION_KINDS
        if kind in (None, label)
        for row in stats.get(_STATS_KEYS[label], [])
        if _matches_setting(label, row, setting, config)
    ]
    return Page.of(rows, offset=offset, limit=limit)


def _selection_to_match(selection: Any, kind: str, setting_text: str) -> CalibrationGroupMatch:
    """Turn one calibration selection into the match record a screen shows.

    Parameters
    ----------
    selection : `CalibrationSelection`
        What the library chose for the lights (frames and flags).
    kind : `str`
        ``"dark"``, ``"bias"`` or ``"flat"``.
    setting_text : `str`
        The light settings in words, such as ``"gain 100, offset 10"``.

    Returns
    -------
    match : `CalibrationGroupMatch`
        The count, the fallbacks and the problem. A blocking flag or an
        empty choice gives a ``problem``.
    """
    count = len(selection.frames)
    blocking = list(selection.blocking_flags)
    problem = ""
    if blocking:
        problem = "; ".join(blocking)
    elif count == 0:
        problem = f"no {kind} frames at {setting_text}"
    return CalibrationGroupMatch(
        count=count, fallbacks=[] if problem else list(selection.flags), problem=problem
    )


def match_calibration(library: CalibrationLibrary, frames: FrameChoice) -> list[CalibrationMatch]:
    """Group light frames and say which calibration the stacker would use.

    The light frames are grouped by camera, optic, filter, gain, offset and
    exposure. For each group the library runs the same dark, flat and bias
    selection that the stacking stage runs (``choose_batch_calibration``),
    with the same inputs, so no matching rule is repeated here. A group says
    why when nothing matches (for example "no bias frames at gain 100,
    offset 10, binning 1x1"). Frames that are not light frames are left out.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    frames : `FrameChoice`
        The frames: the page `targets.frames.query` returns or a list of
        frame records.

    Returns
    -------
    groups : `list` [`CalibrationMatch`]
        One entry per group, sorted by filter, gain, offset, exposure and
        optic.

    Notes
    -----
    Raises `InvalidArgumentError` if ``frames`` is a page cut short or
    holds paths (a path does not say which target it belongs to; give frame
    records).
    """
    from astrometricslib._internal.drivers.batch_calibration import choose_batch_calibration

    records = frames_from_choice(frames)
    groups: dict[tuple[str, str, str, str, float, int | None], list[FrameRecord]] = {}
    for frame in records:
        if frame.role != "LIGHT":
            continue
        filter_text = frame.filter.name if hasattr(frame.filter, "name") else str(frame.filter)
        if filter_text == "NONE":
            filter_text = "None"
        try:
            exposure_s = float(frame.exposure_s)
        except ValueError, TypeError:
            exposure_s = 1.0
        focal_length = getattr(frame, "focal_length_mm", None)
        optic = None if focal_length is None else round(float(focal_length))
        key = (frame.camera, filter_text, str(frame.gain), str(frame.offset), exposure_s, optic)
        groups.setdefault(key, []).append(frame)

    matches = []
    for key in sorted(groups, key=lambda item: (item[1], item[2], item[3], item[4], item[5] or 0)):
        camera, filter_text, gain, offset, exposure_s, optic = key
        # This only shows the choice, and returns its fallbacks to the caller,
        # so the flag sentences are not logged as warnings on every call.
        chosen = choose_batch_calibration(library, groups[key], [], flag_log_level=logging.DEBUG)
        setting_text = f"gain {chosen.gain}, offset {chosen.offset}, binning {chosen.binning}"
        matches.append(
            CalibrationMatch(
                camera=camera or "Unknown",
                focal_length_mm=optic,
                filter_name=filter_text,
                gain=gain,
                offset=offset,
                binning=chosen.binning,
                exposure_s=exposure_s,
                frame_count=len(groups[key]),
                dark=_selection_to_match(chosen.dark, "dark", f"{exposure_s:g} s, {setting_text}"),
                bias=_selection_to_match(chosen.bias, "bias", setting_text),
                flat=_selection_to_match(chosen.flat, "flat", f"filter {chosen.filter_name}, {setting_text}"),
            )
        )
    return matches


def _flat_set_health(group: Any, minimum_frames: int) -> CalibrationSetHealth:
    """Measure one flat set with the stacker's flat check and judge it.

    Parameters
    ----------
    group : `FlatGroup`
        The set.
    minimum_frames : `int`
        The configured fewest frames a master may be built from.

    Returns
    -------
    health : `CalibrationSetHealth`
        The set's health.
    """
    assessment = assess_flat_group(group)
    checks = flat_set_checks(assessment, minimum_frames)
    row = {
        "camera": group.camera,
        "filter": group.filter,
        "gain": group.gain,
        "offset": group.offset,
        "binning": group.binning,
    }
    return CalibrationSetHealth(
        kind="flat",
        rowKey=_row_key("flat", row),
        camera=group.camera,
        gain=group.gain,
        offset=group.offset,
        binning=group.binning,
        telescope=group.telescope,
        filter=group.filter,
        frameCount=len(group.paths),
        uniqueFrameCount=assessment.frame_count,
        status=combine_status(checks),
        summary=summarize_checks(checks),
        checks=checks,
        smoothingSigmaPixels=assessment.smoothing_sigma_pixels,
    )


def _set_as_row(calibration_set: Any) -> dict[str, Any]:
    """Write a dark or bias set as its counts row.

    Parameters
    ----------
    calibration_set : `CalibrationSet`
        The set.

    Returns
    -------
    row : `dict` [`str`, `Any`]
        The fields `_row_key` and `_matches_setting` read.
    """
    row = {
        "camera": calibration_set.camera,
        "gain": calibration_set.gain,
        "offset": calibration_set.offset,
        "binning": calibration_set.binning,
    }
    if calibration_set.kind == "dark":
        row["temperature_c"] = calibration_set.temperature_c
        row["exposure_s"] = calibration_set.exposure_s
    return row


def _assess_flat_sets(
    library: CalibrationLibrary, config: AppConfiguration, setting: CaptureSetting | None
) -> list[CalibrationSetHealth]:
    """Judge the flat sets that match, with the stacker's own flat check.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings, for the fewest frames of a master.
    setting : `CaptureSetting` or `None`
        The sets to judge: camera, telescope, filter, gain, offset and
        binning are compared.

    Returns
    -------
    health : `list` [`CalibrationSetHealth`]
        One record per matching flat set.
    """
    minimum_frames = config.get_minimum_calibration_frames()
    groups = library.list_flat_groups(
        telescope=None if setting is None else setting.telescope,
        camera=None if setting is None else setting.camera,
        filter_type=None if setting is None else setting.filter_name,
        gain=None if setting is None else setting.gain,
        offset=None if setting is None else setting.offset,
    )
    return [
        _flat_set_health(group, minimum_frames)
        for group in groups
        if setting is None
        or setting.binning is None
        or normalize_binning(group.binning) == normalize_binning(setting.binning)
    ]


def _assess_version(
    library: CalibrationLibrary, config: AppConfiguration, kind: str, wanted: list[Any], camera: str | None
) -> Any:
    """Work out a sign for the judgement of dark or bias sets.

    The sign changes whenever the judgement would. The judgement reads the
    sets (their frames and settings), the stored numbers of those frames,
    the bias sets that serve as reference, the camera profiles and the
    configured fewest frames. The sign holds a description of each, so a
    new, moved or removed calibration file, a new measurement, or a changed
    profile gives a new sign.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings.
    kind : {"dark", "bias"}
        The kind of the sets.
    wanted : `list` [`CalibrationSet`]
        The sets to judge.
    camera : `str` or `None`
        Limits the bias sets used as references to this camera.

    Returns
    -------
    version : `Any`
        A hashable sign, or `None` when the stored numbers' counter cannot
        be read (nothing is then kept).
    """
    counter = get_library_data_version(config, (QUALITY_KIND,))
    if counter is None:
        return None
    bias_sets = library.list_calibration_sets("bias", camera=camera) if kind == "dark" else []

    def describe(calibration_set: Any) -> tuple:
        """Describe one set by its settings and its frame paths.

        Returns
        -------
        description : `tuple`
            The settings and the paths.
        """
        return (
            calibration_set.kind,
            calibration_set.camera,
            calibration_set.gain,
            calibration_set.offset,
            calibration_set.binning,
            calibration_set.temperature_c,
            calibration_set.exposure_s,
            tuple(calibration_set.paths),
        )

    profiles = sorted({calibration_set.camera for calibration_set in wanted})
    return (
        counter,
        config.get_minimum_calibration_frames(),
        tuple(describe(calibration_set) for calibration_set in wanted),
        tuple(describe(calibration_set) for calibration_set in bias_sets),
        tuple(repr(resolve_camera_profile(name, config)) for name in profiles),
    )


def _judge_dark_or_bias_sets(
    library: CalibrationLibrary, config: AppConfiguration, kind: str, wanted: list[Any], camera: str | None
) -> list[CalibrationSetHealth]:
    """Judge the given dark or bias sets from the stored numbers.

    Nothing is measured here: a frame with no stored numbers makes its
    checks ``not_checked`` with the reason.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings.
    kind : {"dark", "bias"}
        The kind of the sets.
    wanted : `list` [`CalibrationSet`]
        The sets to judge.
    camera : `str` or `None`
        Limits the bias sets used as references to this camera.

    Returns
    -------
    health : `list` [`CalibrationSetHealth`]
        One record per set.
    """
    from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
    from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import calibration_subject_id
    from astrometricslib.pipelines.stacking.pre_processing.calibration_assessment import (
        assess_dark_or_bias_frames,
        build_bias_reference,
        frame_facts_from_rows,
        health_of_judgement,
    )

    if not wanted:
        return []
    store = QualityStore(quality_database_path(config))
    bias_sets = library.list_calibration_sets("bias", camera=camera) if kind == "dark" else []
    subject_paths = {
        calibration_subject_id(path, "bias"): path for bias_set in bias_sets for path in bias_set.paths
    }
    subject_paths.update({
        calibration_subject_id(path, kind): path
        for calibration_set in wanted
        for path in calibration_set.paths
    })
    # Only the frames of these sets are read, not every dark and bias frame
    # of every camera (about 20 000 rows on a large library).
    rows = [
        row
        for row in store.query(level=SubjectLevel.FRAME, subject_ids=sorted(subject_paths))
        if row.definition_version == _CURRENT_VERSIONS.get(row.metric)
    ]
    facts = frame_facts_from_rows(rows, subject_paths)
    minimum_frames = config.get_minimum_calibration_frames()
    health = []
    for calibration_set in wanted:
        profile = resolve_camera_profile(calibration_set.camera, config)
        setting = (calibration_set.gain, calibration_set.offset, calibration_set.binning)
        reference = None
        if kind == "dark":
            same_setting = [bias_set for bias_set in bias_sets if bias_set.has_setting_of(calibration_set)]
            reference = build_bias_reference([
                facts[path] for bias_set in same_setting for path in bias_set.paths if path in facts
            ])
        judgement = assess_dark_or_bias_frames(
            kind,
            calibration_set.paths,
            facts,
            minimum_frames=minimum_frames,
            profile=profile,
            setting=setting,
            exposure_s=calibration_set.exposure_s,
            reference=reference,
        )
        row = {**_set_as_row(calibration_set), "temperature_c": calibration_set.temperature_c}
        row["exposure_s"] = calibration_set.exposure_s
        health.append(
            health_of_judgement(
                judgement,
                kind=kind,
                row_key=_row_key(kind, row),
                camera=calibration_set.camera,
                gain=calibration_set.gain,
                offset=calibration_set.offset,
                binning=calibration_set.binning,
                temperature_slot_c=calibration_set.temperature_c,
                exposure_s=calibration_set.exposure_s,
            )
        )
    return health


def assess_calibration_sets(
    library: CalibrationLibrary,
    config: AppConfiguration,
    kind: str,
    setting: CaptureSetting | None = None,
    *,
    cache: VersionedCache | None = None,
) -> list[CalibrationSetHealth]:
    """Check whether the calibration sets in the library are good enough.

    Each set (the frames of one camera at one setting) is judged on its own.
    A dark or bias set is judged from the numbers stored for each of its
    frames by `record_calibration_frame_measurements`, against limits taken
    from the equipment: the camera profile's read noise and bias level, and
    the bias frames of the same setting. Nothing is stored; a check that has
    no reference, or whose numbers are not stored yet, is ``not_checked``
    with the reason. A flat set is measured when asked, with the check the
    stacker runs. The call is recorded as a ``diagnostics`` job.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings.
    kind : {"dark", "bias", "flat"}
        The kind of calibration frame.
    setting : `CaptureSetting`, optional
        Only the sets with these settings. Darks compare the camera, gain,
        offset, binning, temperature and exposure; biases the camera, gain,
        offset and binning; flats the camera, telescope, filter, gain,
        offset and binning. `None` judges every set of the kind.
    cache : `VersionedCache`, optional
        Keeps dark and bias judgements until their inputs change.

    Returns
    -------
    health : `list` [`CalibrationSetHealth`]
        One record per matching set. Empty if no set matches.

    Notes
    -----
    Raises `InvalidArgumentError` if the kind is unknown.
    """
    check_choice("kind", kind, CALIBRATION_KINDS)
    with registered_job(job_type="diagnostics", target_id="calibration"):
        if kind == "flat":
            return _assess_flat_sets(library, config, setting)
        camera = None if setting is None else setting.camera
        wanted = [
            calibration_set
            for calibration_set in library.list_calibration_sets(kind, camera=camera)
            if _matches_setting(kind, _set_as_row(calibration_set), setting, config)
        ]
        if cache is None:
            return _judge_dark_or_bias_sets(library, config, kind, wanted, camera)
        # The cache belongs to one catalog, so one library: the key needs
        # only the question.
        key = ("assess", kind, setting)
        return cache.get_or_build(
            key,
            lambda: _assess_version(library, config, kind, wanted, camera),
            lambda: _judge_dark_or_bias_sets(library, config, kind, wanted, camera),
        )


def _rescan(
    library: CalibrationLibrary, config: AppConfiguration, kind: str, prune_missing: bool
) -> CalibrationIngestReport:
    """Rescan one kind of calibration frame and report what was found.

    Darks and biases are judged from the numbers already stored, and nothing
    is measured during the rescan, so a new frame with no numbers yet gives
    checks that are ``not_checked`` with the reason. New flats are measured.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings.
    kind : {"dark", "bias", "flat"}
        The kind to rescan.
    prune_missing : `bool`
        Remove index entries whose files no longer exist.

    Returns
    -------
    report : `CalibrationIngestReport`
        The frames added and removed, per group, and the health of each set
        that gained frames.
    """
    before = flatten_frame_index(getattr(library, f"{kind}_frames"))
    getattr(library, f"refresh_{kind}_frames")(prune_missing=prune_missing)
    after = flatten_frame_index(getattr(library, f"{kind}_frames"))
    added_paths = set().union(*after.values()) - set().union(*before.values())
    assessments: list[CalibrationSetHealth] = []
    if added_paths:
        if kind == "flat":
            minimum_frames = config.get_minimum_calibration_frames()
            assessments = [
                _flat_set_health(group, minimum_frames)
                for group in library.list_flat_groups()
                if added_paths.intersection(group.paths)
            ]
        else:
            gained = [
                calibration_set
                for calibration_set in library.list_calibration_sets(kind, validate_paths=False)
                if added_paths.intersection(calibration_set.paths)
            ]
            assessments = _judge_dark_or_bias_sets(library, config, kind, gained, None)
    return build_ingest_report(kind, before, after, assessments)


def scan_calibration_frames(
    library: CalibrationLibrary,
    config: AppConfiguration,
    kind: str | None = None,
    *,
    prune_missing: bool = False,
) -> list[CalibrationIngestReport]:
    """Read the calibration folders again, index what changed and save it.

    Use this after calibration frames were copied into the library folders
    by another program.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings.
    kind : {"dark", "bias", "flat"}, optional
        Only this kind. `None` rescans all three.
    prune_missing : `bool`, optional
        Remove index entries whose files no longer exist.

    Returns
    -------
    reports : `list` [`CalibrationIngestReport`]
        One report per kind rescanned, in the order dark, bias, flat.

    Notes
    -----
    Raises `InvalidArgumentError` if the kind is unknown.
    """
    if kind is not None:
        check_choice("kind", kind, CALIBRATION_KINDS)
    reports = [
        _rescan(library, config, label, prune_missing) for label in CALIBRATION_KINDS if kind in (None, label)
    ]
    library.save_library()
    return reports


def add_calibration_frames(
    library: CalibrationLibrary,
    config: AppConfiguration,
    paths: Sequence[str | os.PathLike[str]],
    *,
    telescope: str | None = None,
) -> list[CalibrationIngestReport]:
    """Move new dark, bias and flat files into the library, index and save.

    Each path is a folder to search or a single FITS file. A file is sorted
    by its header into the calibration folders. A light frame is left where
    it is: `targets.frames.add` moves those. Files are moved, not copied,
    and a file that cannot be read stays where it is. Then every kind is
    rescanned and the index is saved.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings.
    paths : `Sequence` [`str`]
        Folders to search, or FITS files.
    telescope : `str`, optional
        The telescope the frames were taken with. It is part of the library
        folder of a flat frame. The caller passes the active telescope when
        none is named; `None` files flats with no telescope folder name.

    Returns
    -------
    reports : `list` [`CalibrationIngestReport`]
        One report per kind, in the order dark, bias, flat. Each lists the
        paths it added.
    """
    from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files

    moved: list[str] = []
    classify_and_sort_fits_files([os.fspath(path) for path in paths], "", config, telescope or "", moved)
    return scan_calibration_frames(library, config)


def record_calibration_frame_measurements(
    library: CalibrationLibrary,
    config: AppConfiguration,
    kind: str | None = None,
    setting: CaptureSetting | None = None,
    *,
    force: bool = False,
) -> CalibrationMeasurementReport:
    """Measure the new or changed dark and bias frames, and store the numbers.

    Each frame is read once and its numbers (level, zero share, noise,
    large-scale pattern and so on) are stored in the quality store, so
    `assess_calibration_sets` can judge the sets. A frame whose file has not
    changed since it was measured is skipped. The defects of each dark are
    then measured against its own set. The frame stamps go to the
    calibration index. No frame is changed, moved or deleted. The run is
    recorded as a ``diagnostics`` job.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    config : `AppConfiguration`
        The settings.
    kind : {"dark", "bias"}, optional
        Only this kind. `None` measures both.
    setting : `CaptureSetting`, optional
        Only frames of this setting's camera. The other fields are not used.
    force : `bool`, optional
        Measure every frame again, even if its file has not changed.

    Returns
    -------
    report : `CalibrationMeasurementReport`
        How many frames were measured, skipped and unreadable.

    Notes
    -----
    Raises `InvalidArgumentError` if the kind is not ``"dark"`` or
    ``"bias"``.
    """
    from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path

    if kind is not None:
        check_choice("kind", kind, ("dark", "bias"))
    camera = None if setting is None else setting.camera
    with registered_job(job_type="diagnostics", target_id="calibration") as job:

        def report_progress(done: int, total: int) -> None:
            """Tell the job how far the run is."""
            job.stage(int(100 * done / max(total, 1)), f"Measured {done} of {total} calibration frames")

        run_id = job.job_id or f"calibration-measure-{uuid4().hex}"
        summary = measure_calibration_frames(
            library,
            QualityStore(quality_database_path(config)),
            config,
            run_id=run_id,
            job_id=job.job_id or current_job_id(),
            measured_at=datetime.now(UTC),
            kinds=("dark", "bias") if kind is None else (kind,),
            camera=camera,
            force=force,
            progress=report_progress,
        )
        defect_notes: list[str] = []
        if kind in (None, "dark"):
            # The defects of a dark show against the frames of its own set,
            # so they are measured after the frame numbers.
            defects = measure_dark_defects_in_library(
                library,
                QualityStore(quality_database_path(config)),
                config,
                run_id=run_id,
                job_id=job.job_id or current_job_id(),
                measured_at=datetime.now(UTC),
                camera=camera,
                force=force,
            )
            if defects.measured_count or defects.warned_count or defects.omitted_count:
                defect_notes.append(
                    f"Dark defects: {defects.measured_count} dark frame(s) measured against their set, "
                    f"{defects.warned_count} kept with a warning and {defects.omitted_count} left out "
                    "of the master."
                )
            defect_notes.extend(defects.notes)
    return CalibrationMeasurementReport(
        frameCount=summary.frame_count,
        measuredCount=summary.measured_count,
        unchangedCount=summary.unchanged_count,
        unreadableCount=summary.unreadable_count,
        failedCount=summary.failed_count,
        rowsStored=summary.rows_stored,
        notes=[*summary.notes, *defect_notes],
    )


def get_camera_profile(camera: Camera | None, config: AppConfiguration) -> CameraProfile:
    """Give the stored noise profile of a camera.

    The camera's name is matched first, then each of its other spellings
    (`Camera.aliases`). The match ignores case, spaces and punctuation, and
    a profile's own aliases count.

    Parameters
    ----------
    camera : `Camera` or `None`
        The camera. `None` gives the generic fallback profile.
    config : `AppConfiguration`
        The settings that hold the profiles.

    Returns
    -------
    profile : `CameraProfile`
        The matching profile, or the generic fallback profile
        (``is_generic_fallback`` is `True`) when none matches.
    """
    if camera is None:
        return resolve_camera_profile(None, config)
    profile = resolve_camera_profile(camera.name, config)
    for alias in camera.aliases:
        if not profile.is_generic_fallback:
            break
        profile = resolve_camera_profile(alias, config)
    return profile
