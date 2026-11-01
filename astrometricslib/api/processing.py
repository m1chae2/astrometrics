"""Main interfaces for processing images and checking their quality.

`ProcessingPipelines` stacks a target's frames, makes the preview picture of a
stack again, and runs the analysis stages (astrometry, photometry,
spectroscopy and the asteroid search). It holds one child that it builds
once and shares: `CalibrationCatalog` keeps track of the dark, bias and
flat frames that remove a camera's noise from the raw frames.

Quality reads are not here. They start at `astrometrics.quality`
(`QualityRecords`, in `quality.py`).

The methods here check their arguments and hand the work to `pipelines/`.
They raise an error from `astrometricslib.foundation.errors` when a request
cannot be met, and return a typed result otherwise.
"""

import logging
import math
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, registered_job
from astrometricslib.models.calibration_ingest import (
    CalibrationIngestReport,
    CalibrationMeasurementReport,
    CalibrationSetHealth,
)
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.catalog_queries import CalibrationQueryResult
from astrometricslib.models.processing_results import (
    PreviewRemakeResult,
    ProcessTargetResult,
    StackResult,
    StackWeightPrediction,
)
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.quality_reports import (
    StackSummary,
)
from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
from astrometricslib.models.quality.subset_stacks import (
    NightContributionResult,
    NoiseGrowthResult,
    OddEvenDifferenceNoiseResult,
)
from astrometricslib.models.sensor_noise_measurement import SensorNoiseMeasurement
from astrometricslib.models.target import FrameRecord, Target
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
)
from astrometricslib.pipelines.shared.camera_passes import resolve_processing_frames
from astrometricslib.pipelines.shared.quality.metrics import CALIBRATION_FRAME_METRICS, FRAME_METRICS
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.calibration_ingest import (
    assess_flat_group,
    build_ingest_report,
    flatten_frame_index,
)
from astrometricslib.pipelines.stacking.calibration_measurement_run import measure_calibration_frames
from astrometricslib.pipelines.stacking.pre_processing.calibration_assessment import (
    combine_status,
    flat_set_checks,
    summarize_checks,
)
from astrometricslib.pipelines.stacking.sensor_noise_run import measure_sensor_noise_from_library
from astrometricslib.utilities.parallel_batch import BatchRunSummary

if TYPE_CHECKING:
    from astrometricslib.api.targets import TargetCatalog

logger = logging.getLogger(__name__)

__all__ = ["CalibrationCatalog", "ProcessingPipelines"]

NOISE_KINDS = ("growth", "night_contributions", "odd_even_difference")
NOISE_SECTIONS = ("night_contributions", "odd_even_difference")
_NOISE_ARGUMENTS = {
    "growth": ("maximum_steps", "include"),
    "night_contributions": (),
    "odd_even_difference": ("keep_halves",),
}
_CalibrationKind = Literal["dark", "bias", "flat"]
_CALIBRATION_KINDS: tuple[str, ...] = ("dark", "bias", "flat")

CALIBRATION_DETAILS = ("counts", "target_match", "target_frames", "match")
_CALIBRATION_ARGUMENTS = {
    "counts": ("kind",),
    "target_match": ("target", "camera_id"),
    "target_frames": ("target",),
    "match": ("kind", "camera_id", "gain", "offset", "binning", "temperature_c"),
}


_ASSESS_ARGUMENTS = {
    "dark": ("camera_id", "gain", "offset", "binning", "temperature_c", "exposure_seconds"),
    "bias": ("camera_id", "gain", "offset", "binning"),
    "flat": ("camera_id", "gain", "offset", "binning", "telescope", "filter_name"),
}

# The definition version of each metric a calibration judgement reads. A stored
# row of another version is not used, so a redefined metric is never mixed
# with the old numbers.
_CURRENT_VERSIONS = {
    spec.name: spec.definition_version for spec in (*FRAME_METRICS, *CALIBRATION_FRAME_METRICS)
}

# Two dark exposures are the same set when they differ by less than this many
# seconds. It is the tolerance the library uses to pick a dark for a light
# (`select_dark_frames`).
_EXPOSURE_MATCH_TOLERANCE_SECONDS = 0.05


def _calibration_set_matches(
    calibration_set: Any,
    gain: Any,
    offset: float | None,
    binning: Any,
    temperature_c: float | None,
    exposure_seconds: float | None,
) -> bool:
    """Tell whether a dark or bias set has the asked settings.

    Parameters
    ----------
    calibration_set : `CalibrationSet`
        The set.
    gain, offset, binning, temperature_c, exposure_seconds : optional
        The settings to match. A setting left as `None` is not compared.
        Gains match as numbers when both are numbers. Binning is written in
        the library's standard form first. The temperature is turned into
        the library's temperature slot.

    Returns
    -------
    matches : `bool`
        `True` when every given setting matches.
    """
    from astrometricslib._internal.drivers.calibration_library import (
        DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
        temperature_slot_c,
    )

    row = {"camera": calibration_set.camera, "iso": calibration_set.gain, "offset": calibration_set.offset}
    row["binning"] = calibration_set.binning
    row["temperature_c"] = calibration_set.temperature_c
    if not _calibration_row_matches(
        calibration_set.kind, row, calibration_set.camera, gain, offset, binning, temperature_c
    ):
        return False
    if temperature_c is not None and calibration_set.temperature_c != temperature_slot_c(
        temperature_c, DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
    ):
        return False
    return exposure_seconds is None or (
        calibration_set.exposure_seconds is not None
        and abs(calibration_set.exposure_seconds - exposure_seconds) < _EXPOSURE_MATCH_TOLERANCE_SECONDS
    )


def _calibration_row_key(kind: str, row: dict[str, Any]) -> str:
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
        row.get("iso"),
        row.get("offset"),
        row.get("binning"),
        row.get("temperature_c"),
        row.get("exposure"),
    ]
    return "|".join("" if part is None else str(part) for part in parts)


def _calibration_row_matches(
    kind: str,
    row: dict[str, Any],
    camera_id: str,
    gain: Any,
    offset: float | None,
    binning: Any,
    temperature_c: float | None,
) -> bool:
    """Tell whether a counts row has the asked settings.

    Parameters
    ----------
    kind : `str`
        The row's kind.
    row : `dict` [`str`, `Any`]
        The counts row.
    camera_id : `str`
        The camera, matched ignoring case.
    gain, offset, binning, temperature_c : optional
        The settings to match. A setting left as `None` is not compared.
        Gains match as numbers when both are numbers. Binning is written in
        the library's standard form first, so ``"2"`` equals ``"2x2"``. The
        temperature is turned into the library's temperature slot, and only
        darks are compared on it, since only darks are filed by temperature.

    Returns
    -------
    matches : `bool`
        `True` when every given setting matches.
    """
    from astrometricslib._internal.drivers.calibration_library import (
        DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
        normalize_binning,
        temperature_slot_c,
    )

    if str(row.get("camera", "")).strip().casefold() != camera_id.strip().casefold():
        return False
    if gain is not None:
        try:
            gain_matches = math.isclose(float(row.get("iso")), float(gain))
        except TypeError, ValueError:
            gain_matches = str(row.get("iso")).strip() == str(gain).strip()
        if not gain_matches:
            return False
    if offset is not None and not math.isclose(float(row.get("offset") or 0.0), float(offset)):
        return False
    if binning is not None and normalize_binning(row.get("binning")) != normalize_binning(binning):
        return False
    return not (
        kind == "dark"
        and temperature_c is not None
        and row.get("temperature_c")
        != temperature_slot_c(temperature_c, DEFAULT_DARK_TEMPERATURE_TOLERANCE_C)
    )


class CalibrationCatalog:
    """The library of calibration frames: darks, biases and flats.

    Cameras add noise to every picture. Calibration frames are pictures
    taken with the lens cap on (darks and biases) or of an evenly lit
    surface (flats). They map that noise so the stacker can remove it. This
    class keeps track of those files so they can be applied to real frames.
    A target id that names no target raises `NotFoundError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the calibration index file.
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
        self._library: Any = None

    @property
    def _calibration_library(self) -> Any:
        """The calibration-data model, built the first time it is used.

        Returns
        -------
        library : `CalibrationLibrary`
            The underlying calibration library model.
        """
        if self._library is None:
            from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary

            self._library = CalibrationLibrary(app_config=self._config)
        return self._library

    def query(
        self,
        kind: _CalibrationKind | None = None,
        detail: Literal["counts", "target_match", "target_frames"] = "counts",
        target: str | Target | None = None,
        camera_id: str | None = None,
        refresh: bool = False,
        gain: str | int | float | None = None,
        offset: float | None = None,
        binning: str | int | None = None,
        temperature_c: float | None = None,
    ) -> CalibrationQueryResult:
        """Report on the calibration library. Nothing is written.

        Arguments used by each detail (any other argument is refused):

        - ``"counts"``: ``kind``.
        - ``"match"``: ``camera_id`` (needed), ``kind``, ``gain``, ``offset``,
          ``binning`` and ``temperature_c``.
        - ``"target_match"``: ``target`` (needed) and ``camera_id``.
        - ``"target_frames"``: ``target`` (needed).

        ``refresh`` works with every detail.

        Parameters
        ----------
        kind : `str`, optional
            ``"dark"``, ``"bias"`` or ``"flat"``. Leave it out for all three.
        detail : `str`, optional
            ``"counts"`` (default): how many frames the library holds, per
            camera, setting and exposure (or filter, for flats).
            ``"target_match"``: a target's light frames grouped by filter,
            gain and exposure, each with how many darks match it.
            ``"target_frames"``: a target's light frames counted by
            telescope, camera, gain, exposure and filter.
            ``"match"``: the ``counts`` rows that have the asked camera,
            kind and settings, as `matched_row_keys`.
        target : `str` or `Target`, optional
            The target, for the target details.
        camera_id : `str`, optional
            For ``"target_match"``, only frames from this camera. For
            ``"match"``, the camera to match (ignoring case).
        gain, offset, binning, temperature_c : optional
            For ``"match"``, the settings a row must have. Left out, a
            setting is not compared. Binning ``"2"`` and ``"2x2"`` are the
            same. The temperature is compared by the library's temperature
            slot, and only for darks.
        refresh : `bool`, optional
            Read the calibration index from disk again first, so files
            another program added are counted. This replaces the copy in
            memory; it writes nothing.

        Returns
        -------
        answer : `CalibrationQueryResult`
            The rows for the chosen detail.

        Raises
        ------
        InvalidArgumentError
            If the detail or kind is unknown, an argument the detail does
            not use is given, or a needed target is missing.
        """
        from astrometricslib._internal.drivers.calibration_library import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
        from astrometricslib.pipelines.shared.quality import frame_statistics

        check_choice("detail", detail, CALIBRATION_DETAILS)
        if kind is not None:
            check_choice("kind", kind, _CALIBRATION_KINDS)
        reject_unused_arguments(
            detail,
            _CALIBRATION_ARGUMENTS,
            {
                "kind": kind is not None,
                "target": target is not None,
                "camera_id": camera_id is not None,
                "gain": gain is not None,
                "offset": offset is not None,
                "binning": binning is not None,
                "temperature_c": temperature_c is not None,
            },
        )
        if refresh:
            self._calibration_library.load_library()
        if detail in ("counts", "match"):
            stats = self._calibration_library.get_stats()
            wanted = {"dark": "darks", "bias": "biases", "flat": "flats"}
            keyed = {
                key: [{**row, "row_key": _calibration_row_key(label, row)} for row in stats.get(key, [])]
                for label, key in wanted.items()
            }
            totals: dict[str, dict[str, Any]] = {}
            for label, key in wanted.items():
                for row in keyed[key]:
                    entry = totals.setdefault(
                        row["camera"],
                        {"camera": row["camera"], "dark_count": 0, "bias_count": 0, "flat_count": 0},
                    )
                    entry[f"{label}_count"] += int(row.get("count") or 0)
            camera_totals = sorted(totals.values(), key=lambda entry: str(entry["camera"]).casefold())
            if detail == "match":
                if camera_id is None:
                    raise InvalidArgumentError("detail='match' needs a camera_id.")
                matched = [
                    row["row_key"]
                    for label, key in wanted.items()
                    if kind in (None, label)
                    for row in keyed[key]
                    if _calibration_row_matches(label, row, camera_id, gain, offset, binning, temperature_c)
                ]
                return CalibrationQueryResult(
                    detail=detail,
                    kind=kind,
                    camera_totals=camera_totals,
                    matched_row_keys=matched,
                    dark_temperature_tolerance_c=DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
                )
            rows = keyed
            if kind is not None:
                rows = {wanted[kind]: rows[wanted[kind]]}
            return CalibrationQueryResult(
                detail=detail,
                kind=kind,
                camera_totals=camera_totals,
                dark_temperature_tolerance_c=DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
                **rows,
            )
        if target is None:
            raise InvalidArgumentError(f"detail={detail!r} needs a target.")
        resolved = resolve_target(self._targets, target)
        if detail == "target_frames":
            lights = frame_statistics.get_frame_stats(resolved)["lights"]
            return CalibrationQueryResult(
                detail=detail,
                target_id=resolved.id,
                lights=lights,
                dark_temperature_tolerance_c=DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
            )
        groups = frame_statistics.get_frame_stats_grouped(resolved, self._calibration_library, camera_id)
        return CalibrationQueryResult(
            detail=detail,
            target_id=resolved.id,
            groups=groups,
            dark_temperature_tolerance_c=DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
        )

    def add_frames(self, paths: Sequence[str], *, telescope: str) -> list[str]:
        """Move new dark, bias and flat files into the calibration library.

        Each path is a folder to search or a single FITS file. A file is
        sorted by its header into the calibration folders. A light frame is
        left where it is: `TargetCatalog.add_frames` moves those. Files are
        moved, not copied, and a file that cannot be read stays where it is.
        The call does not update the index: call `reindex_frames` and `save`
        for that.

        Parameters
        ----------
        paths : `Sequence` [`str`]
            Folders to search, or FITS files.
        telescope : `str`
            The name of the telescope the frames were taken with. It is part
            of the library folder of a flat frame.

        Returns
        -------
        added_paths : `list` [`str`]
            The new path of each file that was moved to a place where no file
            of that name yet existed.
        """
        from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files

        added_paths: list[str] = []
        classify_and_sort_fits_files(list(paths), "", self._config, telescope, added_paths)
        return added_paths

    def camera_profile(self, camera_name: str | None) -> CameraProfile:
        """Give the stored facts about a camera model.

        The profile holds the camera's other spellings, its saturation
        limits with where each came from, and its gain and read noise for
        each sensor setting. The match ignores case, spaces and punctuation.

        Parameters
        ----------
        camera_name : `str` or `None`
            The camera name, written in any spelling that the profile lists.

        Returns
        -------
        profile : `CameraProfile`
            The matching profile. When nothing matches, or no name is given,
            this is the generic fallback profile, whose
            ``is_generic_fallback`` is `True`. A name that matches nothing
            also logs a warning, once per distinct name.
        """
        return resolve_camera_profile(camera_name, self._config)

    def save(self) -> None:
        """Write the calibration library to its JSON file on disk."""
        self._calibration_library.save_library()

    def reindex_frames(self, kind: _CalibrationKind, prune_missing: bool = False) -> CalibrationIngestReport:
        """Rescan one kind of calibration frame and report what was found.

        Use this after new calibration frames have been downloaded. The
        report says how many frames were found, which groups they were
        filed under and whether each set that gained frames is good enough.
        Darks and biases are judged from the numbers `measure_frames`
        stored, and nothing is measured during the rescan, so a new frame
        with no numbers yet gives checks that are ``not_checked`` with the
        reason. New flats are measured, as before.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        prune_missing : `bool`, optional
            If `True`, remove index entries whose files no longer
            exist on disk. Defaults to `False`.

        Returns
        -------
        report : `CalibrationIngestReport`
            The frames added and removed, per group, and the health of each
            set that gained frames.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        before = flatten_frame_index(getattr(self._calibration_library, f"{kind}_frames"))
        method = getattr(self._calibration_library, f"refresh_{kind}_frames")
        method(prune_missing=prune_missing)
        after = flatten_frame_index(getattr(self._calibration_library, f"{kind}_frames"))
        added_paths = set().union(*after.values()) - set().union(*before.values())
        assessments: list[CalibrationSetHealth] = []
        if added_paths:
            if kind == "flat":
                minimum_frames = self._config.get_minimum_calibration_frames()
                assessments = [
                    self._flat_set_health(group, minimum_frames)
                    for group in self._calibration_library.list_flat_groups()
                    if added_paths.intersection(group.paths)
                ]
            else:
                gained = [
                    calibration_set
                    for calibration_set in self._calibration_library.list_calibration_sets(
                        kind, validate_paths=False
                    )
                    if added_paths.intersection(calibration_set.paths)
                ]
                assessments = self._judge_dark_or_bias_sets(kind, gained, None)
        return build_ingest_report(kind, before, after, assessments)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def assess(
        self,
        kind: _CalibrationKind,
        camera_id: str | None = None,
        gain: str | int | float | None = None,
        offset: float | None = None,
        binning: str | int | None = None,
        temperature_c: float | None = None,
        exposure_seconds: float | None = None,
        telescope: str | None = None,
        filter_name: str | None = None,
        register_job: bool = True,
    ) -> list[CalibrationSetHealth]:
        """Check whether the calibration sets in the library are good enough.

        Each set (the frames of one camera at one setting) is judged on its
        own, and the answer is one common record for darks, biases and flats.
        A dark or bias set is judged from the numbers stored for each of its
        frames by `measure_frames`, against limits taken from the equipment:
        the camera profile's read noise and bias level, and the bias frames
        of the same setting. Nothing is stored for the judgement; it is worked
        out when asked, so a changed limit shows at once. A check that has no
        reference, or whose numbers are not stored yet, is ``not_checked``
        with the reason. It never uses a default. A flat set is measured when
        asked, with the check the stacker runs. Nothing is written.

        Arguments used by each kind (any other argument is refused):

        - ``"dark"``: ``camera_id``, ``gain``, ``offset``, ``binning``,
          ``temperature_c`` and ``exposure_seconds``.
        - ``"bias"``: ``camera_id``, ``gain``, ``offset`` and ``binning``.
        - ``"flat"``: ``camera_id``, ``gain``, ``offset``, ``binning``,
          ``telescope`` and ``filter_name``.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The kind of calibration frame.
        camera_id : `str`, optional
            Only this camera's sets.
        gain, offset, binning : optional
            Only sets with this gain (or ISO), camera offset and binning.
            Binning ``"2"`` and ``"2x2"`` are the same.
        temperature_c : `float`, optional
            Darks only: the set whose temperature slot holds this sensor
            temperature, in degrees Celsius.
        exposure_seconds : `float`, optional
            Darks only: the set with this exposure, within 0.05 s.
        telescope : `str`, optional
            Flats only: the telescope the flats were taken through.
        filter_name : `str`, optional
            Flats only: the filter, such as ``"L"`` or ``"SPEC"``.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        health : `list` [`CalibrationSetHealth`]
            One record per matching set. Empty if no set matches. An unknown
            kind, or an argument the kind does not use, is refused with an
            `InvalidArgumentError`.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        reject_unused_arguments(
            kind,
            _ASSESS_ARGUMENTS,
            {
                "camera_id": camera_id is not None,
                "gain": gain is not None,
                "offset": offset is not None,
                "binning": binning is not None,
                "temperature_c": temperature_c is not None,
                "exposure_seconds": exposure_seconds is not None,
                "telescope": telescope is not None,
                "filter_name": filter_name is not None,
            },
        )
        with registered_job(enabled=register_job, job_type="diagnostics", target_id="calibration"):
            if kind == "flat":
                return self._assess_flat_sets(telescope, camera_id, filter_name, gain, offset, binning)
            return self._assess_dark_or_bias_sets(
                kind, camera_id, gain, offset, binning, temperature_c, exposure_seconds
            )

    def _assess_flat_sets(
        self,
        telescope: str | None,
        camera_id: str | None,
        filter_name: str | None,
        gain: Any,
        offset: float | None,
        binning: Any,
    ) -> list[CalibrationSetHealth]:
        """Judge the flat sets that match, with the stacker's own flat check.

        Returns
        -------
        health : `list` [`CalibrationSetHealth`]
            One record per matching flat set.
        """
        from astrometricslib._internal.drivers.calibration_library import normalize_binning

        minimum_frames = self._config.get_minimum_calibration_frames()
        groups = self._calibration_library.list_flat_groups(
            telescope=telescope, camera=camera_id, filter_type=filter_name, gain=gain, offset=offset
        )
        return [
            self._flat_set_health(group, minimum_frames)
            for group in groups
            if binning is None or normalize_binning(group.binning) == normalize_binning(binning)
        ]

    @staticmethod
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
            "iso": group.gain,
            "offset": group.offset,
            "binning": group.binning,
        }
        return CalibrationSetHealth(
            kind="flat",
            rowKey=_calibration_row_key("flat", row),
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

    def _assess_dark_or_bias_sets(
        self,
        kind: str,
        camera_id: str | None,
        gain: Any,
        offset: float | None,
        binning: Any,
        temperature_c: float | None,
        exposure_seconds: float | None,
    ) -> list[CalibrationSetHealth]:
        """Judge the dark or bias sets that match, from the stored numbers.

        Returns
        -------
        health : `list` [`CalibrationSetHealth`]
            One record per matching set.
        """
        wanted = [
            calibration_set
            for calibration_set in self._calibration_library.list_calibration_sets(kind, camera=camera_id)
            if _calibration_set_matches(
                calibration_set, gain, offset, binning, temperature_c, exposure_seconds
            )
        ]
        return self._judge_dark_or_bias_sets(kind, wanted, camera_id)

    def _judge_dark_or_bias_sets(
        self, kind: str, wanted: list[Any], camera_id: str | None
    ) -> list[CalibrationSetHealth]:
        """Judge the given dark or bias sets from the stored numbers.

        Nothing is measured here: a frame with no stored numbers makes its
        checks ``not_checked`` with the reason.

        Parameters
        ----------
        kind : {"dark", "bias"}
            The kind of the sets.
        wanted : `list` [`CalibrationSet`]
            The sets to judge.
        camera_id : `str` or `None`
            Limits the bias sets used as references to this camera.

        Returns
        -------
        health : `list` [`CalibrationSetHealth`]
            One record per set.
        """
        from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
        from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import (
            calibration_subject_id,
        )
        from astrometricslib.pipelines.stacking.pre_processing.calibration_assessment import (
            assess_dark_or_bias_frames,
            build_bias_reference,
            frame_facts_from_rows,
            health_of_judgement,
        )

        library = self._calibration_library
        if not wanted:
            return []
        store = QualityStore(quality_database_path(self._config))
        bias_sets = library.list_calibration_sets("bias", camera=camera_id) if kind == "dark" else []
        subject_paths = {
            calibration_subject_id(path, "bias"): path for bias_set in bias_sets for path in bias_set.paths
        }
        subject_paths.update({
            calibration_subject_id(path, kind): path
            for calibration_set in wanted
            for path in calibration_set.paths
        })
        prefixes = {"calibration/bias/", f"calibration/{kind}/"}
        rows = [
            row
            for prefix in sorted(prefixes)
            for row in store.query(level=SubjectLevel.FRAME, subject_id_prefix=prefix)
            if row.definition_version == _CURRENT_VERSIONS.get(row.metric)
        ]
        facts = frame_facts_from_rows(rows, subject_paths)
        minimum_frames = self._config.get_minimum_calibration_frames()
        health = []
        for calibration_set in wanted:
            profile = resolve_camera_profile(calibration_set.camera, self._config)
            setting = (calibration_set.gain, calibration_set.offset, calibration_set.binning)
            reference = None
            if kind == "dark":
                same_setting = [
                    bias_set for bias_set in bias_sets if bias_set.has_setting_of(calibration_set)
                ]
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
                exposure_seconds=calibration_set.exposure_seconds,
                reference=reference,
            )
            row = {
                "camera": calibration_set.camera,
                "iso": calibration_set.gain,
                "offset": calibration_set.offset,
                "binning": calibration_set.binning,
                "temperature_c": calibration_set.temperature_c,
                "exposure": calibration_set.exposure_seconds,
            }
            health.append(
                health_of_judgement(
                    judgement,
                    kind=kind,
                    row_key=_calibration_row_key(kind, row),
                    camera=calibration_set.camera,
                    gain=calibration_set.gain,
                    offset=calibration_set.offset,
                    binning=calibration_set.binning,
                    temperature_slot_c=calibration_set.temperature_c,
                    exposure_seconds=calibration_set.exposure_seconds,
                )
            )
        return health

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_frames(
        self,
        kind: Literal["dark", "bias"] | None = None,
        camera_id: str | None = None,
        force: bool = False,
        register_job: bool = True,
    ) -> CalibrationMeasurementReport:
        """Measure the dark and bias frames that are new or changed.

        Each frame is read once and its numbers (level, zero share, noise,
        large-scale pattern and so on) are stored, so `assess` can judge the
        sets. A frame whose file has not changed since it was measured is
        skipped, so a second run costs almost nothing. The first run over a
        large library takes a long time (about half a second per frame).
        This writes rows to the quality store and the frame stamps to the
        calibration index. It never changes, moves or deletes a frame.

        Parameters
        ----------
        kind : {"dark", "bias"}, optional
            Only this kind. Leave it out for both.
        camera_id : `str`, optional
            Only this camera's frames.
        force : `bool`, optional
            Measure every frame again, even if its file has not changed.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        report : `CalibrationMeasurementReport`
            How many frames were measured, skipped and unreadable. An unknown
            kind is refused with an `InvalidArgumentError`.
        """
        from uuid import uuid4

        from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path

        if kind is not None:
            check_choice("kind", kind, ("dark", "bias"))
        with registered_job(enabled=register_job, job_type="diagnostics", target_id="calibration") as job:

            def report_progress(done: int, total: int) -> None:
                """Tell the job how far the run is."""
                job.stage(int(100 * done / max(total, 1)), f"Measured {done} of {total} calibration frames")

            summary = measure_calibration_frames(
                self._calibration_library,
                QualityStore(quality_database_path(self._config)),
                self._config,
                run_id=job.job_id or f"calibration-measure-{uuid4().hex}",
                job_id=job.job_id,
                measured_at=datetime.now(UTC),
                kinds=("dark", "bias") if kind is None else (kind,),
                camera=camera_id,
                force=force,
                progress=report_progress,
            )
        return CalibrationMeasurementReport(
            frameCount=summary.frame_count,
            measuredCount=summary.measured_count,
            unchangedCount=summary.unchanged_count,
            unreadableCount=summary.unreadable_count,
            failedCount=summary.failed_count,
            rowsStored=summary.rows_stored,
            notes=summary.notes,
        )

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_sensor_noise(
        self,
        camera_id: str,
        gain: str | float,
        offset: str | float,
        binning: str = "1x1",
        temperature_c: float | None = None,
        exposure_seconds: float | None = None,
        telescope: str | None = None,
        filter_name: str | None = None,
        save: bool = False,
        register_job: bool = True,
    ) -> SensorNoiseMeasurement:
        """Measure a camera setting's gain and read noise.

        The gain (electrons per ADU, where ADU is one step of the stored
        pixel value) comes from pairs of flat frames, and the read noise
        from pairs of bias frames, all taken at exactly this setting. The
        frames are paired in file-name order. Reading never writes: the
        camera profile changes only when ``save`` is `True` and the
        measurement succeeded. If it cannot be made (too few pairs, or the
        frames do not share one setting, exposure or temperature), the result
        has status ``not_measured`` and a reason, and nothing is written.
        Saving raises `ConfigurationError` when the camera has no profile or
        its profile still holds an unmigrated gain.

        Parameters
        ----------
        camera_id : `str`
            The camera whose frames to use.
        gain : `str` or `float`
            The camera gain setting (or ISO).
        offset : `str` or `float`
            The camera offset.
        binning : `str`, optional
            The binning, such as ``"1x1"``. Defaults to ``"1x1"``.
        temperature_c : `float`, optional
            Use only frames within 1 degree C of this sensor temperature,
            in degrees Celsius.
        exposure_seconds : `float`, optional
            Use only flats of this exposure. Needed when the setting's flats
            were taken at several exposures.
        telescope : `str`, optional
            Use only flats taken through this telescope.
        filter_name : `str`, optional
            Use only flats taken through this filter.
        save : `bool`, optional
            Write a measured result into the camera profile, with a backup
            of the config first. Defaults to `False`.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        measurement : `SensorNoiseMeasurement`
            The gain, read noise, spreads, counts and a source sentence for
            the profile, or the reason nothing was measured.
        """
        with registered_job(enabled=register_job, job_type="diagnostics", target_id="calibration"):
            return measure_sensor_noise_from_library(
                self._calibration_library,
                self._config,
                camera_id,
                gain,
                offset,
                binning,
                temperature_c=temperature_c,
                exposure_seconds=exposure_seconds,
                telescope=telescope,
                filter_type=filter_name,
                save=save,
                measured_on=datetime.now(UTC).date(),
            )


class _TargetsOnDemand:
    """Find targets through a `ProcessingPipelines`, building its catalog late.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The pipelines whose target catalog to use.
    """

    def __init__(self, pipelines: ProcessingPipelines) -> None:
        self._pipelines = pipelines

    def get(self, target_id: str, refresh: bool = False) -> Target | None:
        """Find one target by its id.

        Parameters
        ----------
        target_id : `str`
            The target's id, matched loosely.
        refresh : `bool`, optional
            Read the stored targets first.

        Returns
        -------
        target : `Target` or `None`
            The target, or `None` when no target has that id.
        """
        return self._pipelines._targets.get(target_id, refresh=refresh)


class ProcessingPipelines:
    """Stack targets, remake their previews, and run the analysis stages.

    Every method that takes a target accepts its id or the `Target`. An id
    that names no target, or a stack that does not exist, raises
    `NotFoundError`. An unknown ``kind`` raises `InvalidArgumentError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes. The analysis stages
        record stars in it.
    targets : `TargetCatalog`, optional
        The target catalog. It turns target names into targets and saves a
        target after a stack. One is built from ``storage`` when omitted.
    calibration : `CalibrationCatalog`, optional
        The calibration library to share. One is built when omitted.
    drivers : `Drivers`, optional
        The plate solver, stacking program and SIMBAD driver to use. Any
        left out is the built-in one (Astrometry.net, Siril, astroquery).
        They reach `stack`, the astrometry and spectroscopy stages of
        `process_target` for one target, and `run_spectroscopy_by_session`.
        `process_target` for several targets runs in worker processes, which
        cannot receive them, so it refuses to start when any were given.

    Attributes
    ----------
    calibration : `CalibrationCatalog`
        Tracking, querying, and refreshing dark, bias, and flat
        calibration frames.
    """

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        targets: TargetCatalog | None = None,
        calibration: CalibrationCatalog | None = None,
        drivers: Drivers | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._drivers = drivers or Drivers()
        self._target_catalog = targets
        lookup = targets if targets is not None else _TargetsOnDemand(self)
        self.calibration = calibration or CalibrationCatalog(config, storage, targets=lookup)

    @property
    def _targets(self) -> TargetCatalog:
        """The target catalog, built from the storage when first needed.

        Returns
        -------
        targets : `TargetCatalog`
            The shared target catalog.
        """
        if self._target_catalog is None:
            from astrometricslib.api.targets import TargetCatalog

            self._target_catalog = TargetCatalog(self._config, self._storage)
        return self._target_catalog

    def _resolve(self, target: str | Target) -> Target:
        """Turn a target name into the `Target` it names.

        Returns
        -------
        target : `Target`
            The target.
        """
        if isinstance(target, Target):
            return target
        return resolve_target(self._targets, target)

    # -- Stacking ----------------------------------------------------------

    @background_job("stacking", grace_period_seconds=8.0)
    def stack(
        self,
        target: str | Target,
        frames: list[FrameRecord] | None = None,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: TimeInput = None,
        until: TimeInput = None,
        plan_only: bool = False,
        kind: StackKind = "imaging",
        camera_id: str | None = None,
        force: bool = False,
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
        rejection_sigma: tuple[float, float] | None = None,
        filter_wfwhm: str | None = None,
        filter_round: str | None = None,
        stack_weight: str | None = None,
        generate_rejmap: bool | None = None,
        output_file: str | None = None,
        log_file: str | None = None,
        register_job: bool = True,
    ) -> StackResult:
        """Stack a target's light frames, choosing which frames go in.

        This runs the same stacking stage as the Stack button in the app:
        it sets aside bad frames, stacks, trims the noisy edges, records the
        quality summary and the preview, and saves the target. Imaging and
        spectroscopy frames are never mixed, so ``kind`` picks one, and
        frames from two cameras are never stacked together. An unchanged
        stack is kept unless ``force`` is set. A target, camera or frame
        choice that matches nothing raises `NotFoundError`; a stacking stage
        that finishes without making a stack raises `ProcessingError`.

        Parameters
        ----------
        target : `str` or `Target`
            The target to stack.
        frames : `list` [`FrameRecord`], optional
            The exact frames to stack. Cannot be combined with the frame
            choices below.
        filter_name : `str`, optional
            Only frames of this filter, such as ``"L"`` or ``"Luminance"``.
        first_file : `str`, optional
            Only frames from this file onward. A bare number such as
            ``"013"`` means frame 013.
        last_file : `str`, optional
            Only frames up to this file or number.
        since : `str`, `datetime` or `Time`, optional
            Only frames taken at or after this time (UTC if no offset).
        until : `str`, `datetime` or `Time`, optional
            Only frames taken at or before this time.
        plan_only : `bool`, optional
            Only report which frames would be stacked. Nothing is stacked
            or saved.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` frames.
        camera_id : `str`, optional
            Only frames from this camera, such as ``"ASI 533MM"``. Chosen
            before any other frame check. A target with frames from more
            than one camera needs this; without it the answer is an error
            that names the cameras and how many frames each took.
        force : `bool`, optional
            Rebuild even if nothing changed since the stack on disk.
        denoise : `bool`, optional
            For this run's preview picture: `False` skips Cosmic Clarity,
            `True` runs it even if the settings have it switched off. Left
            out, the settings apply. Never written to the settings.
        denoise_strength : `float`, optional
            Cosmic Clarity's denoise strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the preview's star toning on or off for this run.
        rejection_sigma : `tuple` [`float`, `float`], optional
            Low and high sigma-clipping bounds for pixel rejection.
        filter_wfwhm : `str`, optional
            Siril's weighted-FWHM frame filter.
        filter_round : `str`, optional
            Siril's star-roundness frame filter.
        stack_weight : `str`, optional
            How Siril weights each frame.
        generate_rejmap : `bool`, optional
            Also write the rejection maps.
        output_file : `str`, optional
            Where to write the stack instead of the usual place.
        log_file : `str`, optional
            Where to write Siril's log.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`. When the
            caller already runs a job, the stack's provenance is recorded
            against that job.

        Returns
        -------
        result : `StackResult`
            The frames chosen and, unless ``plan_only``, the stack's path,
            flags and quality summary (``quality_summary``). To compare
            it with the previous stack, use
            `Astrometrics.quality.measure_stack`.

        Raises
        ------
        InvalidArgumentError
            If ``kind`` or ``denoise_strength`` is out of range, a time
            cannot be read, ``frames`` is combined with a frame choice, or
            the frames come from more than one camera.
        """
        from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings
        from astrometricslib.pipelines.stacking.pre_processing.camera_selection import ensure_single_camera
        from astrometricslib.pipelines.stacking.stack_request import (
            SirilOptions,
            choose_frames_to_stack,
            run_stack,
        )

        check_choice("kind", kind, STACK_KINDS)
        if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
            raise InvalidArgumentError("denoise_strength must be between 0 and 1.")
        resolved = self._resolve(target)
        if frames is not None:
            choices = (filter_name, first_file, last_file, since, until, camera_id)
            if any(value is not None for value in choices):
                raise InvalidArgumentError(
                    "Pass frames, or choose them with filter_name, first_file, last_file, since, until "
                    "and camera_id, not both."
                )
            if not frames:
                raise InvalidArgumentError("frames is empty: there is nothing to stack.")
            ensure_single_camera(frames, target_id=resolved.id)
            chosen = list(frames)
        else:
            selection = make_frame_selection(
                filter_name, first_file, last_file, since, until, kind == "spectral"
            )
            chosen = choose_frames_to_stack(resolved, kind, selection, camera_id)
        return run_stack(
            resolved,
            chosen,
            kind=kind,
            plan_only=plan_only,
            force=force,
            preview_settings=PreviewSettings(
                denoise=denoise, denoise_strength=denoise_strength, star_toning=star_toning
            ),
            siril=SirilOptions(
                rejection_sigma=rejection_sigma,
                filter_wfwhm=filter_wfwhm,
                filter_round=filter_round,
                stack_weight=stack_weight,
                generate_rejmap=generate_rejmap,
                output_file=output_file,
                log_file=log_file,
            ),
            register_job=register_job,
            stacking_slot=self.acquire_job_slot,
            save_targets=self._targets.save,
            drivers=self._drivers,
            measured_at=datetime.now(UTC),
            quality_recording=FrameQualityRecording(self._config, self.calibration._calibration_library),
        )

    @background_job("stacking", grace_period_seconds=8.0)
    def measure_noise(
        self,
        target: str | Target,
        kind: Literal["growth", "night_contributions", "odd_even_difference"] = "growth",
        *,
        maximum_steps: int = 4,
        include: list[str] | None = None,
        keep_halves: bool = False,
        register_job: bool = True,
    ) -> NoiseGrowthResult | NightContributionResult | OddEvenDifferenceNoiseResult:
        """Stack parts of a target's library stack and measure the noise.

        Each kind stacks some of the library stack's frames (read from the
        stack's ``_inputs.json``) into the target's ``_subsets`` folder with
        the normal settings (without the frame quarantine step), and measures
        the noise in the same sky apertures as the library stack, so the
        numbers compare. Rows are stored at level ``stack``. The library
        stack, its previews and its record are never changed. None of the
        kinds starts by itself after a stack, and none runs inside the stack's
        own process; a caller starts it. The target must already have the
        noise apertures that its library stack stored, so stack it once first
        if it was stacked before they were recorded. A target with no stack,
        or a stack with no ``_inputs.json``, raises `NotFoundError`.

        Arguments used by each kind (any other argument is refused):

        - ``"growth"``: ``maximum_steps`` and ``include``; ``include`` may
          hold ``"night_contributions"`` and ``"odd_even_difference"``.
        - ``"night_contributions"``: none.
        - ``"odd_even_difference"``: ``keep_halves``.

        Parameters
        ----------
        target : `str` or `Target`
            The target.
        kind : `str`, optional
            ``"growth"`` (default): the noise against the number of frames.
            The frames are split by observing night. The subsets are the first
            night, the first two nights, and so on, up to one night fewer than
            the stack. A stack of one night uses the first quarter, half and
            three quarters of its frames instead. At most ``maximum_steps``
            subsets are made, spaced evenly through the possible ones, and a
            subset with fewer than four frames is left out. The library stack
            is the last point of the curve and is not stacked again. The
            noise, the reference flux, the effective frame count and the
            effective time are stored with the subset's label in the subject.
            The cost is one stack for each subset, four by default, each with
            fewer frames than the library stack, plus the odd–even difference
            noise (about one library stack's time and Siril's temporary disk).
            Expect the time of about two to four library stacks (a design
            estimate, not a measurement).

            ``"night_contributions"``: what each observing night gave to the
            library stack. For every night, the stack's frames without that
            night are stacked, and four numbers are stored on that leave-out
            stack, with the night in the subject's observing night: the
            night's share of the library stack's total frame weight
            (``stacking.night_weight_fraction``), the noise of the stack
            without the night (``stacking.noise_without_night``), the paired
            difference from the library stack's noise
            (``stacking.noise_difference_without_night``) and the ratio the
            two stacks' effective frame counts predict
            (``stacking.subset_expected_noise_ratio``). The cost is one stack
            for every night, each close to the library stack's own cost. A
            stack of one night has nothing to leave out. A share is stored as
            not measured when the stack's weights are unknown (Siril weighted
            the frames itself).

            ``"odd_even_difference"``: the library stack's random noise from
            two halves. The frames are put in order of capture time and split
            into the 1st, 3rd, ... and the 2nd, 4th, ... frame. The library
            stack's registration reference frame goes into both, and it is
            handed to the stacker as the frame to line up with, so a half
            cannot choose its own and the two half stacks have the same pixel
            grid. Each half is stacked (without the edge trim), the
            difference is taken, and its noise is measured in the library
            stack's stored empty apertures. The difference cancels what the
            frames share (sky structure, faint stars, nebula) and keeps their
            random noise, so it is the more honest noise of a deep or crowded
            field. Nine rows are stored on the library stack, beside its
            ``stacking.empty_aperture_noise_*`` rows and never replacing
            them: the noise (``..._stack_unit`` and ``..._normalized``), the
            reference flux the normalized value divides by, the two halves'
            effective frame counts, the variance factor, the variance ratio
            with each frame's modeled noise, and the shared frame's two
            weight shares (all ``stacking.odd_even_difference_*``). Five more
            rows say what the two halves share at the scale of a few pixels
            (an upper limit of a fixed pattern on the sensor) and how that
            differs along and across the pointing drift (the
            ``stacking.half_shared_fine_scale_*`` and
            ``stacking.walking_noise_*`` metrics). The halves are deleted
            afterwards. The cost is two stacks of half the frames each, about
            one library stack's time in all, plus the temporary disk space
            Siril needs for each. While it runs, no library stack and no
            other subset job of the target can run. When the measurement
            cannot be made (a half with under 8 frames, no recorded
            registration reference, other apertures than the library stack's,
            a Siril failure), the rows are stored as not measured with the
            reason, and the result says why.
        maximum_steps : `int`, optional
            Applies to ``"growth"`` only. The most subsets to stack. Defaults
            to 4.
        include : `list` [`str`], optional
            Applies to ``"growth"`` only. Sections to run in the same job,
            under the same lock, after the series: ``"night_contributions"``
            (one more stack for every night) and ``"odd_even_difference"``
            (about one library stack's time, and the noise measure that
            ignores structure the frames share; its result is in
            ``odd_even_difference`` and its rows are counted there). `None`
            means ``["odd_even_difference"]``; pass an empty list for neither.
        keep_halves : `bool`, optional
            Applies to ``"odd_even_difference"`` only. Keep the two half
            stacks in the ``_subsets`` folder. Defaults to `False`.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        result : `NoiseGrowthResult`, `NightContributionResult`, ...
            For ``"growth"`` a `NoiseGrowthResult` (the subsets, smallest
            first, with notes about steps left out); for
            ``"night_contributions"`` a `NightContributionResult` (one entry
            for each night); for ``"odd_even_difference"`` an
            `OddEvenDifferenceNoiseResult` (the difference noise, the halves'
            effective frame counts and the library stack's own noise that the
            difference implies, or the reason there are none).

        Raises
        ------
        InvalidArgumentError
            If the kind or an include section is unknown, an argument the
            kind does not use is given, or ``maximum_steps`` is below 1.
        """
        from astrometricslib.pipelines.stacking.odd_even_difference_run import run_odd_even_difference_noise
        from astrometricslib.pipelines.stacking.subset_noise_run import (
            run_night_contributions,
            run_noise_growth,
        )
        from astrometricslib.pipelines.stacking.target_stack_guard import subset_job_guard

        check_choice("kind", kind, NOISE_KINDS)
        sections = check_include(include, NOISE_SECTIONS)
        reject_unused_arguments(
            kind,
            _NOISE_ARGUMENTS,
            {
                "maximum_steps": maximum_steps != 4,
                "include": bool(sections),
                "keep_halves": keep_halves,
            },
        )
        if maximum_steps < 1:
            raise InvalidArgumentError("maximum_steps must be at least 1.")
        if kind == "growth" and include is None:
            sections = frozenset(("odd_even_difference",))
        resolved = self._resolve(target)
        library_stack_path = self._library_stack_path(resolved, "imaging")
        with registered_job(enabled=register_job, job_type="stacking", target_id=resolved.id) as job:
            context = self._subset_context(resolved, job.job_id)
            shared = {
                "context": context,
                "configuration": self._config,
                "drivers": self._drivers,
                "stacking_slot": self.acquire_job_slot,
            }
            if kind == "night_contributions":
                return run_night_contributions(resolved, library_stack_path, **shared)
            if kind == "odd_even_difference":
                return run_odd_even_difference_noise(
                    resolved, library_stack_path, keep_halves=keep_halves, **shared
                )
            with subset_job_guard(resolved.id):
                result = run_noise_growth(resolved, library_stack_path, maximum_steps=maximum_steps, **shared)
                if "night_contributions" in sections:
                    result.night_contributions = run_night_contributions(
                        resolved, library_stack_path, **shared
                    )
                if "odd_even_difference" in sections:
                    try:
                        result.odd_even_difference = run_odd_even_difference_noise(
                            resolved, library_stack_path, **shared
                        )
                    except Exception as error:  # the series is already stored; report, do not lose it
                        logger.exception("The odd–even difference noise of %s failed", resolved.id)
                        reason = f"{type(error).__name__}: {error}"
                        result.notes.append(f"The odd–even difference noise was not measured ({reason}).")
                return result

    def _library_stack_path(self, target: Target, kind: StackKind, *, must_exist: bool = True) -> str:
        """Find the path of a target's library stack for the subset jobs.

        Parameters
        ----------
        target : `Target`
            The target.
        kind : {"imaging", "spectral"}
            Which stack.
        must_exist : `bool`, optional
            Raise when the target has no stack. When `False`, a target
            without a stack gets the path its stack would have, so a
            subset can still be stacked beside it.

        Returns
        -------
        path : `str`
            The library stack's path.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

        if must_exist:
            return stack_path_of(target, kind)
        stacking = target.spectral_stacking if kind == "spectral" else target.stacking
        if stacking.stacked_image:
            return str(stacking.stacked_image)
        folder = os.path.join(str(self._config.get_stacks_path()), "lights", target.id)
        tag = "_SPEC" if kind == "spectral" else ""
        return os.path.join(folder, f"{target.id.replace(' ', '_')}{tag}_Stacked.fits")

    def _subset_context(self, target: Target, job_id: str | None) -> PipelineRunContext | None:
        """Make the shared facts of one subset-stack run.

        Parameters
        ----------
        target : `Target`
            The target.
        job_id : `str` or `None`
            The run's job id, if it recorded a job.

        Returns
        -------
        context : `PipelineRunContext`
            The context, stamped with the time now (the API layer reads the
            clock, the pipelines do not).
        """
        return PipelineRunContext.from_options(
            target.id,
            "stacking",
            STACKING_PIPELINE_VERSION,
            {"job_id": job_id, "measured_at": datetime.now(UTC)},
        )

    @background_job("preview", grace_period_seconds=20.0)
    def remake_preview(
        self,
        target: str | Target,
        kind: StackKind = "imaging",
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
        starnet: bool = False,
        star_strength: float | None = None,
        star_stretch: float | None = None,
        keep_previous: bool = True,
        register_job: bool = True,
    ) -> PreviewRemakeResult:
        """Make a target's preview picture again from its existing stack.

        Runs only the preview step (GraXpert, Siril stretch, Cosmic Clarity,
        star toning), so a changed post-processing setting shows up without a
        restack. The stack file is not changed. The overrides apply to this
        run only and are never written to the configuration. By default the
        old pictures are copied into a ``_previous_preview`` folder beside
        the stack first, so the two can be compared; a failed run puts them
        back. The target is saved when the viewer shows the new picture. A
        missing target or stack raises `NotFoundError`, and a run that makes
        no preview raises `ProcessingError`.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose stack to make a picture of.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.
        denoise : `bool`, optional
            `False` skips Cosmic Clarity for this run. `True` runs it even if
            the settings have it switched off. Left out, the settings apply.
        denoise_strength : `float`, optional
            Cosmic Clarity's denoise strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the star toning on or off for this run.
        starnet : `bool`, optional
            Stretch the stars and the rest of the picture on their own,
            using StarNet through Siril. It adds about 35 to 55 seconds to
            the remake and is off by default. The star toning is not used
            with it, and a failed StarNet run gives the normal picture.
        star_strength : `float`, optional
            With ``starnet``, how much of the stars layer is blended, from 0
            to 1. Left out, 0.8.
        star_stretch : `float`, optional
            With ``starnet``, the arcsinh strength of the stars' own stretch,
            from 1 to 100000. Larger values lift fainter stars. Left out,
            1000.
        keep_previous : `bool`, optional
            Copy the current pictures aside first. On by default.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        result : `PreviewRemakeResult`
            Where the picture went, the steps that ran, and where the old
            pictures were kept.

        Raises
        ------
        InvalidArgumentError
            If ``kind``, ``denoise_strength``, ``star_strength`` or
            ``star_stretch`` is out of range.
        """
        from astrometricslib.pipelines.stacking.post_processing.preview_remake import remake_stack_preview
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings

        check_choice("kind", kind, STACK_KINDS)
        if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
            raise InvalidArgumentError("denoise_strength must be between 0 and 1.")
        if star_strength is not None and not 0.0 <= star_strength <= 1.0:
            raise InvalidArgumentError("star_strength must be between 0 and 1.")
        if star_stretch is not None and not 1.0 <= star_stretch <= 100000.0:
            raise InvalidArgumentError("star_stretch must be between 1 and 100000.")
        resolved = self._resolve(target)
        settings = PreviewSettings(
            denoise=denoise,
            denoise_strength=denoise_strength,
            star_toning=star_toning,
            starnet=starnet,
            star_strength=star_strength,
            star_stretch=star_stretch,
        )
        with (
            self.acquire_job_slot(),
            registered_job(enabled=register_job, job_type="preview", target_id=resolved.id),
        ):
            result = remake_stack_preview(resolved, kind == "spectral", settings, keep_previous)
        if result.shown_in_viewer:
            self._targets.save()
        return result

    # -- Analysis stages ----------------------------------------------------

    @background_job("process_target", grace_period_seconds=8.0)
    def process_target(
        self,
        target: str | Target | list[str | Target] | None,
        stages: list[str] | None = None,
        astrometry: dict[str, Any] | None = None,
        photometry: dict[str, Any] | None = None,
        spectroscopy: dict[str, Any] | None = None,
        asteroids: dict[str, Any] | None = None,
        camera_id: str | None = None,
        focal_length_mm: float | None = None,
        on_item_complete: Callable[[str, dict, int, int], None] | None = None,
        register_job: bool = True,
    ) -> ProcessTargetResult | BatchRunSummary:
        """Run the analysis stages for one target, or the pipeline for many.

        For one target, the chosen ``stages`` run in this process, always
        in the order astrometry, photometry, spectroscopy, asteroids.
        Photometry measures only the frames of one camera and one optic,
        chosen the way the batch run chooses them: the camera the batch
        would give the target to (the first camera in the config's setups
        that has frames of it, the primary camera first) and the primary
        optic (``[Observatory.Telescope] focal_length_mm``). Pass
        ``camera_id`` or ``focal_length_mm`` to choose differently.
        Spectroscopy is skipped (with a ``{"status": "skipped", ...}``
        entry, not an error) when the target has no spectral data, and
        otherwise receives photometry's result. Each stage's own options
        are a separate dictionary, so an option meant for one stage never
        reaches another. Nothing is saved; save the target afterwards.

        For a list of targets, or `None` for every target, each target runs
        the full pipeline in its own worker process: stack the frames of
        ``camera_id``, plate solve, measure star brightness, and extract
        spectra when there are spectroscopy frames. Each target is saved
        after each stage.

        Arguments used by each form (any other argument is refused):

        - one target: ``stages``, ``astrometry``, ``photometry``,
          ``spectroscopy``, ``asteroids``, and optionally ``camera_id`` and
          ``focal_length_mm``.
        - several targets: ``camera_id`` (needed), ``focal_length_mm``,
          ``on_item_complete``.

        Parameters
        ----------
        target : `str`, `Target`, `list` or `None`
            One target (its id or the `Target`), a list of them, or `None`
            for every target in the library.
        stages : `list` [`str`], optional
            For one target, which of ``"astrometry"``, ``"photometry"``,
            ``"spectroscopy"`` and ``"asteroids"`` to run. Defaults to the
            first three. The asteroid search needs a stack.
        astrometry : `dict`, optional
            Options for the astrometry stage: ``path`` (the image to plate
            solve; the stack by default).
        photometry : `dict`, optional
            Options for the photometry stage: ``frames``, ``filter_name``,
            ``use_astrometry_seed`` and ``max_workers``.
        spectroscopy : `dict`, optional
            Options for the spectroscopy stage: ``path`` and ``limit`` (a
            cap on how many stars to process).
        asteroids : `dict`, optional
            Options for the asteroid search: ``moving_object_config`` (a
            dictionary of settings, such as
            ``{"detection_threshold_sigma": 4.0}``; a key left out keeps its
            default value, and a key that names no setting raises
            `InvalidArgumentError`).
        camera_id : `str`, optional
            Only frames from this camera are processed (the full name, or
            part of it). Needed for several targets. For one target, it
            defaults to the camera the batch would use.
        focal_length_mm : `float`, optional
            Only frames at this focal length are processed. For one target,
            it defaults to the config's primary focal length.
        on_item_complete : `Callable`, optional
            For several targets, called as ``(target_id, result,
            completed_count, total_count)`` after each target finishes.
        register_job : `bool`, optional
            For one target, whether each stage shows up in the job list.
            Defaults to `True`.

        Returns
        -------
        result : `ProcessTargetResult` or `BatchRunSummary`
            For one target, each stage's result keyed by stage name, and
            the target's quality summaries afterwards (``quality``). For
            several, which targets succeeded, failed or were skipped.

        Raises
        ------
        InvalidArgumentError
            If a stage or stage option is unknown, or an argument the form
            does not use is given. Also if several targets are asked for
            while `Astrometrics` was given drivers, because the worker
            processes cannot receive them.
        """
        from astrometricslib.pipelines import tasks

        if target is None or isinstance(target, list):
            reject_unused_arguments(
                "several targets",
                {"several targets": ("camera_id", "focal_length_mm", "on_item_complete")},
                {
                    "stages": stages is not None,
                    "astrometry": astrometry is not None,
                    "photometry": photometry is not None,
                    "spectroscopy": spectroscopy is not None,
                    "asteroids": asteroids is not None,
                },
            )
            if not camera_id:
                raise InvalidArgumentError("Processing several targets needs a camera_id.")
            if self._drivers.any_chosen:
                raise InvalidArgumentError(
                    "Processing several targets runs each target in its own worker process, which cannot "
                    "receive the drivers given to Astrometrics(...). Process the targets one at a time, or "
                    "build Astrometrics without drivers."
                )
            from astrometricslib.pipelines.target_batch import process_targets_in_parallel

            if target is None:
                target_ids = [item.id for item in self._targets.list()]
            else:
                target_ids = [self._resolve(item).id for item in target]
            return process_targets_in_parallel(
                self._config,
                target_ids,
                camera_id=camera_id,
                focal_length_mm=focal_length_mm,
                on_item_complete=on_item_complete,
                measured_at=datetime.now(UTC),
            )

        reject_unused_arguments(
            "one target",
            {"one target": ("camera_id", "focal_length_mm")},
            {
                "on_item_complete": on_item_complete is not None,
            },
        )
        chosen_stages = tuple(stages) if stages is not None else tasks.DEFAULT_ANALYSIS_STAGES
        for stage in chosen_stages:
            check_choice("stages", stage, tasks.ANALYSIS_STAGES)
        stage_options = {
            "astrometry": astrometry,
            "photometry": photometry,
            "spectroscopy": spectroscopy,
            "asteroids": asteroids,
        }
        for stage, options in stage_options.items():
            unknown = sorted(set(options or {}) - tasks.STAGE_OPTIONS[stage])
            if unknown:
                raise InvalidArgumentError(
                    f"Unknown {stage} option(s): {', '.join(unknown)}. "
                    f"Choose from: {', '.join(sorted(tasks.STAGE_OPTIONS[stage]))}."
                )
        from astrometricslib.pipelines.asteroid_detection.runner import moving_object_config_from_options

        moving_object_config_from_options((asteroids or {}).get("moving_object_config"))
        resolved = self._resolve(target)
        frame_selection = None
        if "photometry" in chosen_stages or "spectroscopy" in chosen_stages:
            frame_selection = resolve_processing_frames(resolved, self._config, camera_id, focal_length_mm)
        return tasks.run_target_stages(
            resolved,
            chosen_stages,
            {stage: options or {} for stage, options in stage_options.items()},
            self._storage,
            register_job,
            drivers=self._drivers,
            frame_selection=frame_selection,
            measured_at=datetime.now(UTC),
        )

    def run_spectroscopy_by_session(
        self,
        target: str | Target,
        frame_records: list[Any],
        max_workers: int | None = None,
        on_item_complete: Any | None = None,
    ) -> Any:
        """Analyze a target's spectroscopy frames, grouped by session.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose spectroscopy frames should be processed.
        frame_records : `list`
            The frame records to process, grouped internally by session.
        max_workers : `int`, optional
            Maximum concurrent worker count; defaults to the
            configured spectroscopy concurrency.
        on_item_complete : `Callable`, optional
            A function to run after each session finishes.

        Returns
        -------
        summary : `BatchRunSummary`
            Aggregated success/failure/result state across every
            session's frames.
        session_results : `list` [`tuple`]
            One `(session, identify_result)` pair per session.
        """
        from astrometricslib.pipelines.spectroscopy import batch as spectroscopy_batch_operations

        resolved = self._resolve(target)
        with registered_job(
            enabled=True,
            job_type="spectroscopy_session",
            target_id=resolved.id,
            completed_message=f"[{resolved.id}] Session-based spectroscopy completed successfully.",
            failed_message=f"[{resolved.id}] Session-based spectroscopy failed.",
        ) as job:
            return spectroscopy_batch_operations.process_spectroscopy_frames_by_session(
                self._config,
                resolved,
                frame_records,
                max_workers=max_workers,
                on_item_complete=on_item_complete,
                job_id=job.job_id,
                drivers=self._drivers,
                measured_at=datetime.now(UTC),
            )

    # -- Reading and changing a target's stacks and set-aside frames --------

    def stack_summary(self, target: str | Target, kind: StackKind = "imaging") -> StackSummary:
        """Summarize a target's current stack in one short answer.

        Reads the numbers the stacking stage saved with the stack, so nothing
        is measured again. The answer covers how many frames went in and how
        many were set aside, the share of pixels rejected, the star width
        against what the input frames predict, the flags, and what happened
        to each exposure group (its frames, whether it clipped, how far it
        was moved to line up, and why it was left out if it was).

        Parameters
        ----------
        target : `str` or `Target`
            The target to summarize.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.

        Returns
        -------
        summary : `StackSummary`
            The summary.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import summarize_stack

        check_choice("kind", kind, STACK_KINDS)
        return summarize_stack(self._resolve(target), kind)

    @staticmethod
    def predict_stack_weights(
        exposure_seconds: Sequence[float | None],
        configured_weight: str | None = None,
        *,
        is_spectral: bool = False,
        star_width_px: Sequence[float | None] | None = None,
        rate_variance: Sequence[float | None] | None = None,
        group_frame_noise_adu: Mapping[float, float | None] | None = None,
    ) -> StackWeightPrediction:
        """Predict how the stacker would weight a set of frames.

        This runs the stacker's own rules on the frame facts you give, so a
        forecast made before stacking agrees with the stack made later.
        Nothing is read from disk or saved.

        Parameters
        ----------
        exposure_seconds : `Sequence` [`float` or `None`]
            The exposure length of each frame, in seconds. `None` marks a
            frame whose length cannot be read.
        configured_weight : `str`, optional
            The ``Processing.Siril.stack_weight`` setting. A configured mode
            wins over the stacker's own choice.
        is_spectral : `bool`, optional
            Whether the stack is spectral. Those keep their configured weight.
        star_width_px : `Sequence` [`float` or `None`], optional
            The weighted star width of each frame, in pixels. Needed for the
            ``wfwhm`` weights and the weights inside each exposure group.
        rate_variance : `Sequence` [`float` or `None`], optional
            The variance of each frame in rate units. Needed for the
            ``noise`` weights.
        group_frame_noise_adu : `dict` [`float`, `float` or `None`], optional
            The noise of one raw frame of each exposure group, in counts,
            keyed by the group's length (``folded_exposure_lengths`` of the
            result). Needed for the weights of an exposure-group stack.

        Returns
        -------
        prediction : `StackWeightPrediction`
            The weighting mode, the exposure group of each frame, and the
            weight of each frame (or the reasons they are unknown).

        Notes
        -----
        The call reads no stored data, so it is a static method. Code that
        holds no `Astrometrics` handle can call it on the class,
        ``ProcessingPipelines.predict_stack_weights(...)``.
        """
        from astrometricslib.pipelines.stacking.stack_weight_prediction import predict_stack_weights

        return predict_stack_weights(
            exposure_seconds,
            configured_weight,
            is_spectral=is_spectral,
            star_width_px=star_width_px,
            rate_variance=rate_variance,
            group_frame_noise_adu=group_frame_noise_adu,
        )

    def open_in_siril(self, file_path: str) -> None:
        """Open a stack in the Siril program's window, for a person to inspect.

        This uses the stacking program chosen for this API (Siril unless a
        caller chose another one that has a window; one without a window raises
        `ProcessingError`). Siril starts as its
        own process and keeps running after the call returns. If the program
        cannot start, the failure is logged and nothing is raised. Nothing is
        stacked, read or saved.

        Parameters
        ----------
        file_path : `str`
            The FITS file to open. An empty string opens Siril alone.
        """
        self._drivers.stacking_or_default().open_in_viewer(file_path)

    def plate_solve(self, image_path: str) -> Any | None:
        """Work out where on the sky an image points, from its stars.

        Plate solving matches the pattern of stars in the image against a
        star catalog. The result is a World Coordinate System (WCS): the
        mapping from each pixel to a sky position.

        Parameters
        ----------
        image_path : `str`
            The FITS file to solve.

        Returns
        -------
        wcs : `astropy.wcs.WCS` or `None`
            The pixel-to-sky mapping, or `None` when the image cannot be
            read or too few stars are found to try a solve. When a solve is
            tried and the star pattern cannot be matched, the star
            identifier raises `PlateSolveFailedError` instead.
        """
        from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

        _, wcs = StarIdentifier(config=self._config, drivers=self._drivers).process_image(
            image_path, attempt_plate_solving=True
        )
        return wcs

    # -- Limits on how much heavy work runs at once -------------------------

    def acquire_job_slot(self) -> AbstractContextManager:
        """Limit how many heavy jobs can run at the same time.

        Analyzing and stacking images use a lot of CPU power and memory.
        This makes sure the computer is not overwhelmed, by limiting how
        many jobs run at once. Analysis and stacking share one slot pool;
        both draw on ``max_concurrent_jobs``.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            job's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())
