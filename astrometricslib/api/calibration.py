"""The calibration sub-API: dark, bias and flat frames.

`CalibrationCatalog` (`astrometrics.processing.calibration`) keeps track of
the dark, bias and flat frames that remove a camera's noise from the raw
frames. It finds the sets that match a target's frames, reindexes the
library's calibration folders, judges each set (`assess`) and measures the
frames and the camera's sensor noise.

Its methods check their arguments and hand the work to `pipelines/`. They
raise an error from `astrometricslib.foundation.errors` when a request
cannot be met, and return a typed result otherwise.
"""

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, current_job_id, registered_job
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.storage.data_versions import QUALITY_KIND, get_library_data_version
from astrometricslib.foundation.versioned_cache import VersionedCache
from astrometricslib.models.calibration_ingest import (
    CalibrationIngestReport,
    CalibrationMeasurementReport,
    CalibrationSetHealth,
)
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.catalog_queries import CalibrationQueryResult
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.sensor_noise_measurement import SensorNoiseMeasurement
from astrometricslib.models.target import Target
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.api_arguments import (
    TargetLookup,
    check_choice,
    reject_unused_arguments,
    resolve_target,
)
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
from astrometricslib.pipelines.stacking.sensor_noise_run import measure_sensor_noise_from_library

__all__ = ["CalibrationCatalog"]

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
_EXPOSURE_MATCH_TOLERANCE_SECONDS = TunedParameter(
    name="api.exposure_match_tolerance_seconds",
    default=0.05,
    unit="s",
    description="Largest exposure time difference at which a calibration set still matches.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.EQUIPMENT_PROFILE,
    equipment=frozenset({EquipmentKind.CAMERA}),
    register_entry="STK-D07",
)


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
        temperature_c, resolve(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value
    ):
        return False
    return exposure_seconds is None or (
        calibration_set.exposure_seconds is not None
        and abs(calibration_set.exposure_seconds - exposure_seconds)
        < resolve(_EXPOSURE_MATCH_TOLERANCE_SECONDS).value
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
        != temperature_slot_c(temperature_c, resolve(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value)
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
        self._assess_cache = VersionedCache()

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
                    dark_temperature_tolerance_c=resolve(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value,
                )
            rows = keyed
            if kind is not None:
                rows = {wanted[kind]: rows[wanted[kind]]}
            return CalibrationQueryResult(
                detail=detail,
                kind=kind,
                camera_totals=camera_totals,
                dark_temperature_tolerance_c=resolve(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value,
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
                dark_temperature_tolerance_c=resolve(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value,
            )
        groups = frame_statistics.get_frame_stats_grouped(resolved, self._calibration_library, camera_id)
        return CalibrationQueryResult(
            detail=detail,
            target_id=resolved.id,
            groups=groups,
            dark_temperature_tolerance_c=resolve(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value,
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
        key = (
            "assess",
            str(self._config.get_library_path()),
            kind,
            camera_id,
            gain,
            offset,
            binning,
            temperature_c,
            exposure_seconds,
        )
        return self._assess_cache.get_or_build(
            key,
            lambda: self._assess_version(kind, wanted, camera_id),
            lambda: self._judge_dark_or_bias_sets(kind, wanted, camera_id),
        )

    def _assess_version(self, kind: str, wanted: list[Any], camera_id: str | None) -> Any:
        """Work out a sign for the judgement of dark or bias sets.

        The sign changes whenever the judgement would.

        The judgement reads the sets (their frames and settings), the stored
        numbers of those frames, the bias sets that serve as reference, the
        camera profiles and the configured fewest frames. The sign holds a
        description of each, so a new, moved or removed calibration file, a
        new measurement, or a changed profile gives a new sign.

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
        version : `Any`
            A hashable sign, or `None` when the stored numbers' counter
            cannot be read (nothing is then kept).
        """
        counter = get_library_data_version(self._config, (QUALITY_KIND,))
        if counter is None:
            return None
        bias_sets = (
            self._calibration_library.list_calibration_sets("bias", camera=camera_id)
            if kind == "dark"
            else []
        )

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
                calibration_set.exposure_seconds,
                tuple(calibration_set.paths),
            )

        profiles = sorted({calibration_set.camera for calibration_set in wanted})
        return (
            counter,
            self._config.get_minimum_calibration_frames(),
            tuple(describe(calibration_set) for calibration_set in wanted),
            tuple(describe(calibration_set) for calibration_set in bias_sets),
            tuple(repr(resolve_camera_profile(camera, self._config)) for camera in profiles),
        )

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
        # Only the frames of these sets are read, not every dark and bias
        # frame of every camera (about 20 000 rows on a large library).
        rows = [
            row
            for row in store.query(level=SubjectLevel.FRAME, subject_ids=sorted(subject_paths))
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
                job_id=job.job_id or current_job_id(),
                measured_at=datetime.now(UTC),
                kinds=("dark", "bias") if kind is None else (kind,),
                camera=camera_id,
                force=force,
                progress=report_progress,
            )
            defect_notes: list[str] = []
            if kind in (None, "dark"):
                # The defects of a dark show against the frames of its own
                # set, so they are measured after the frame numbers.
                defects = measure_dark_defects_in_library(
                    self._calibration_library,
                    QualityStore(quality_database_path(self._config)),
                    self._config,
                    run_id=job.job_id or f"calibration-measure-{uuid4().hex}",
                    job_id=job.job_id or current_job_id(),
                    measured_at=datetime.now(UTC),
                    camera=camera_id,
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
