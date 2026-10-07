"""Main interfaces for processing images and checking their quality.

`ProcessingPipelines` stacks a target's frames, makes the preview picture of a
stack again, and runs the analysis stages (astrometry, photometry,
spectroscopy and the asteroid search). It holds two children that it builds
once and shares:

* `QualityDiagnostics` measures raw frames and finished stacks without
  changing anything.
* `CalibrationCatalog` keeps track of the dark, bias and flat frames that
  remove a camera's noise from the raw frames.

The methods here check their arguments and hand the work to `pipelines/`.
They raise an error from `astrometricslib.foundation.errors` when a request
cannot be met, and return a typed result otherwise.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from astropy.time import Time

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.drivers.job_logging import background_job, registered_job
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.calibration_ingest import CalibrationIngestReport, FlatSetAssessment
from astrometricslib.models.catalog_queries import CalibrationQueryResult
from astrometricslib.models.excluded_frames import QuarantinePreview, RestoreReport
from astrometricslib.models.processing_results import PreviewRemakeResult, ProcessTargetResult, StackResult
from astrometricslib.models.quality_reports import (
    InputQualityReport,
    RawFrameCheckReport,
    SpectralFrameCheckReport,
    StackQualityReport,
    StackSummary,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.api_arguments import (
    TargetLookup,
    check_choice,
    check_include,
    reject_unused_arguments,
    resolve_target,
    to_epoch_seconds,
)
from astrometricslib.pipelines.shared.calibration_ingest import (
    assess_flat_group,
    build_ingest_report,
    flatten_frame_index,
)
from astrometricslib.pipelines.shared.quality.frame_selection import FrameSelection
from astrometricslib.utilities.parallel_batch import BatchRunSummary

if TYPE_CHECKING:
    from astrometricslib.api.targets import TargetCatalog

__all__ = ["CalibrationCatalog", "ProcessingPipelines", "QualityDiagnostics"]

TimeInput = str | datetime | Time | None
"""A time as these methods accept it: ISO 8601 text, `datetime` or `Time`."""

StackKind = Literal["imaging", "spectral"]
STACK_KINDS = ("imaging", "spectral")

_CalibrationKind = Literal["dark", "bias", "flat"]
_CALIBRATION_KINDS: tuple[str, ...] = ("dark", "bias", "flat")

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

CALIBRATION_DETAILS = ("counts", "target_match", "target_frames")
_CALIBRATION_ARGUMENTS = {
    "counts": ("kind",),
    "target_match": ("target", "camera_id"),
    "target_frames": ("target",),
}


def _make_selection(
    filter_name: str | None,
    first_file: str | None,
    last_file: str | None,
    since: TimeInput,
    until: TimeInput,
    include_spectra: bool,
) -> FrameSelection:
    """Build the frame choice that several methods share.

    Returns
    -------
    selection : `FrameSelection`
        The filter, file range and time window to keep.
    """
    return FrameSelection(
        filter_name=filter_name,
        first_file=first_file,
        last_file=last_file,
        since=to_epoch_seconds(since, "since"),
        until=to_epoch_seconds(until, "until"),
        include_spectra=include_spectra,
    )


def _clamp(value: int, lowest: int, highest: int) -> int:
    """Keep a whole number inside a range.

    Returns
    -------
    clamped : `int`
        ``value``, raised to ``lowest`` or lowered to ``highest``.
    """
    return max(lowest, min(int(value), highest))


class QualityDiagnostics:
    """Measure raw frames and finished stacks, without changing anything.

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

    def _label(self, target: Target | None, folder_path: str | None = None) -> str:
        """Name the subject of a job.

        Returns
        -------
        label : `str`
            The target's id, the folder, or ``"library"``.
        """
        return target.id if target is not None else (folder_path or "library")

    @background_job("diagnostics", grace_period_seconds=20.0)
    def frame_quality(
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
        selection = _make_selection(filter_name, first_file, last_file, since, until, "spectra" in sections)
        with registered_job(
            enabled=register_job, job_type="diagnostics", target_id=self._label(resolved, folder_path)
        ):
            if kind == "raw_check":
                report = frame_quality_report.raw_check_report(
                    resolved, folder_path, selection, limit, trend_frames, trend_threshold_percent
                )
            elif kind == "quarantine_preview":
                report = frame_quality_report.quarantine_preview_report(resolved, limit)
            else:
                report = frame_quality_report.input_quality_report(
                    resolved,
                    selection,
                    camera_id,
                    limit,
                    "fwhm" in sections,
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

    @background_job("diagnostics", grace_period_seconds=20.0)
    def spectral_frame_check(
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
        from astrometricslib.pipelines.shared.quality.spectral_frame_check import check_spectral_frames

        resolved = resolve_target(self._targets, target)
        selection = _make_selection(None, first_file, last_file, since, until, True)
        with registered_job(enabled=register_job, job_type="diagnostics", target_id=resolved.id):
            return check_spectral_frames(
                resolved, selection, exposure_seconds, predict_exposure_seconds, _clamp(limit, 1, 100)
            )

    @background_job("diagnostics", grace_period_seconds=20.0)
    def stack_quality(
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
            "rmse_outlier_sigma": srq.RMSE_OUTLIER_SIGMA,
            "zero_order_amplitude_outlier_sigma": srq.ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA,
            "zero_order_position_jump_outlier_sigma": srq.ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA,
        }


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
    def library(self) -> Any:
        """The calibration-data model, built the first time it is used.

        Returns
        -------
        library : `CalibrationLibrary`
            The underlying calibration library model.
        """
        if self._library is None:
            from astrometricslib.drivers.calibration_library import CalibrationLibrary

            self._library = CalibrationLibrary(app_config=self._config)
        return self._library

    def query(
        self,
        kind: _CalibrationKind | None = None,
        detail: Literal["counts", "target_match", "target_frames"] = "counts",
        target: str | Target | None = None,
        camera_id: str | None = None,
        refresh: bool = False,
    ) -> CalibrationQueryResult:
        """Report on the calibration library. Nothing is written.

        Arguments used by each detail (any other argument is refused):

        - ``"counts"``: ``kind``.
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
        target : `str` or `Target`, optional
            The target, for the target details.
        camera_id : `str`, optional
            For ``"target_match"``, only frames from this camera.
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
        from astrometricslib.pipelines.shared.quality import frame_statistics

        check_choice("detail", detail, CALIBRATION_DETAILS)
        if kind is not None:
            check_choice("kind", kind, _CALIBRATION_KINDS)
        reject_unused_arguments(
            detail,
            _CALIBRATION_ARGUMENTS,
            {"kind": kind is not None, "target": target is not None, "camera_id": camera_id is not None},
        )
        if refresh:
            self.library.load_library()
        if detail == "counts":
            stats = self.library.get_stats()
            wanted = {"dark": "darks", "bias": "biases", "flat": "flats"}
            rows = {key: stats.get(key, []) for key in wanted.values()}
            if kind is not None:
                rows = {wanted[kind]: rows[wanted[kind]]}
            return CalibrationQueryResult(detail=detail, kind=kind, **rows)
        if target is None:
            raise InvalidArgumentError(f"detail={detail!r} needs a target.")
        resolved = resolve_target(self._targets, target)
        if detail == "target_frames":
            lights = frame_statistics.get_frame_stats(resolved)["lights"]
            return CalibrationQueryResult(detail=detail, target_id=resolved.id, lights=lights)
        groups = frame_statistics.get_frame_stats_grouped(resolved, self, camera_id)
        return CalibrationQueryResult(detail=detail, target_id=resolved.id, groups=groups)

    def save(self) -> None:
        """Write the calibration library to its JSON file on disk."""
        self.library.save_library()

    def add(self, image_file: str, kind: _CalibrationKind, **kwargs: Any) -> None:
        """Add a calibration frame of the given kind (dark, bias or flat).

        Parameters
        ----------
        image_file : `str`
            Path to the calibration frame FITS file.
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Passed to the kind's own adder; ``flat`` accepts ``telescope``.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        method = getattr(self.library, f"add_{kind}_frame")
        method(image_file, **kwargs)

    def get(self, kind: _CalibrationKind, **kwargs: Any) -> list[str]:
        """Find the calibration frame files of the given kind.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Passed to the kind's own lookup. Common keys are ``camera``,
            ``exposure`` (darks only), ``telescope`` and ``filter_type``
            (flats only), and ``validate_paths``.

        Returns
        -------
        frames : `list` [`str`]
            Matching frame file paths.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        method = getattr(self.library, f"get_{kind}_frames")
        return method(**kwargs)

    def refresh(self, kind: _CalibrationKind, prune_missing: bool = False) -> CalibrationIngestReport:
        """Rescan one kind of calibration frame and report what was found.

        Use this after new calibration frames have been downloaded. The
        report says how many frames were found, which groups they were
        filed under and, for flats, whether each new set is good enough.

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
            The frames added and removed, per group, and the assessment of
            each flat set that gained frames.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        before = flatten_frame_index(getattr(self.library, f"{kind}_frames"))
        method = getattr(self.library, f"refresh_{kind}_frames")
        method(prune_missing=prune_missing)
        after = flatten_frame_index(getattr(self.library, f"{kind}_frames"))
        flat_groups = self.library.list_flat_groups() if kind == "flat" else ()
        return build_ingest_report(kind, before, after, flat_groups)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def assess_flats(
        self,
        telescope: str | None = None,
        camera_id: str | None = None,
        filter_type: str | None = None,
        gain: float | None = None,
        offset: float | None = None,
        register_job: bool = True,
    ) -> list[FlatSetAssessment]:
        """Check whether the flats in the library are good enough to use.

        Each set (one telescope, camera, filter, gain and offset) is
        measured on its own with the check the stacker runs: how many
        frames, how bright, how noisy the master flat will be, and any
        problems. Leave an argument out to include every value of it.

        Parameters
        ----------
        telescope : `str`, optional
            Only this telescope's flats.
        camera_id : `str`, optional
            Only this camera's flats.
        filter_type : `str`, optional
            Only flats for this filter, such as ``"L"`` or ``"SPEC"``.
        gain : `float`, optional
            Only flats at this gain.
        offset : `float`, optional
            With `gain`, only flats at this camera offset.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        assessments : `list` [`FlatSetAssessment`]
            One assessment per matching set. Empty if no flats match.
        """
        with registered_job(enabled=register_job, job_type="diagnostics", target_id="calibration"):
            groups = self.library.list_flat_groups(
                telescope=telescope, camera=camera_id, filter_type=filter_type, gain=gain, offset=offset
            )
            return [assess_flat_group(group) for group in groups]


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
    """

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        targets: TargetCatalog | None = None,
        calibration: CalibrationCatalog | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._target_catalog = targets
        lookup = targets if targets is not None else _TargetsOnDemand(self)
        self.calibration = calibration or CalibrationCatalog(config, storage, targets=lookup)
        self.diagnostics = QualityDiagnostics(config, storage, targets=lookup)

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
            `QualityDiagnostics.stack_quality`.

        Raises
        ------
        InvalidArgumentError
            If ``kind`` or ``denoise_strength`` is out of range, a time
            cannot be read, ``frames`` is combined with a frame choice, or
            the frames come from more than one camera.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings
        from astrometricslib.pipelines.stacking.stack_request import (
            SirilOptions,
            check_one_camera,
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
            check_one_camera(resolved, frames)
            chosen = list(frames)
        else:
            selection = _make_selection(filter_name, first_file, last_file, since, until, kind == "spectral")
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
            stacking_slot=self.acquire_stacking_slot,
            save_targets=self._targets.save,
        )

    @background_job("preview", grace_period_seconds=20.0)
    def remake_preview(
        self,
        target: str | Target,
        kind: StackKind = "imaging",
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
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
            If ``kind`` or ``denoise_strength`` is out of range.
        """
        from astrometricslib.pipelines.stacking.post_processing.preview_remake import remake_stack_preview
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings

        check_choice("kind", kind, STACK_KINDS)
        if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
            raise InvalidArgumentError("denoise_strength must be between 0 and 1.")
        resolved = self._resolve(target)
        settings = PreviewSettings(
            denoise=denoise, denoise_strength=denoise_strength, star_toning=star_toning
        )
        with (
            self.acquire_stacking_slot(),
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
          ``spectroscopy``, ``asteroids``.
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
            Options for the photometry stage: ``frames``, ``filter_type``,
            ``use_astrometry_seed`` and ``max_workers``.
        spectroscopy : `dict`, optional
            Options for the spectroscopy stage: ``path`` and ``limit`` (a
            cap on how many stars to process).
        asteroids : `dict`, optional
            Options for the asteroid search: ``moving_object_config`` (a
            `MovingObjectConfig`).
        camera_id : `str`, optional
            For several targets, only frames from this camera are processed.
        focal_length_mm : `float`, optional
            For several targets, only frames at this focal length.
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
            does not use is given.
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
            )

        reject_unused_arguments(
            "one target",
            {"one target": ()},
            {
                "camera_id": camera_id is not None,
                "focal_length_mm": focal_length_mm is not None,
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
        resolved = self._resolve(target)
        return tasks.run_target_stages(
            resolved,
            chosen_stages,
            {stage: options or {} for stage, options in stage_options.items()},
            self._storage,
            register_job,
        )

    def run_spectroscopy_by_session(
        self,
        astrometrics: Any,
        target: str | Target,
        frame_records: list[Any],
        max_workers: int | None = None,
        on_item_complete: Any | None = None,
    ) -> Any:
        """Analyze a target's spectroscopy frames, grouped by session.

        Parameters
        ----------
        astrometrics : `astrometricslib.Astrometrics`
            The parent astrometrics, needed to resolve session boundaries.
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
                astrometrics,
                resolved,
                frame_records,
                max_workers=max_workers,
                on_item_complete=on_item_complete,
                job_id=job.job_id,
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

    def discard_previous_stack(self, target: str | Target, kind: StackKind = "imaging") -> list[str]:
        """Delete the kept previous stack of a target.

        Use this once the new stack looks good. It deletes the old stack and
        its pictures, and it cannot be undone. The pipeline never does this by
        itself; it only replaces the previous stack with a newer one at the
        next restack.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose previous stack to delete.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.

        Returns
        -------
        removed : `list` [`str`]
            The files deleted. Empty if no previous stack was kept.
        """
        from astrometricslib.pipelines.stacking.post_processing.previous_stack import discard_previous_stack
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

        check_choice("kind", kind, STACK_KINDS)
        return discard_previous_stack(stack_path_of(self._resolve(target), kind))

    def swap_with_previous_stack(self, target: str | Target, kind: StackKind = "imaging") -> list[str]:
        """Put the previous stack back as the current one.

        Use this when the new stack turned out worse. The current stack
        moves into ``_previous`` in the same step, so calling this again undoes
        it. Only the files change places. The stored quality summary describes
        the stack that was current when it was written, so restack to refresh
        it.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose stacks to swap.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stacks.

        Returns
        -------
        restored : `list` [`str`]
            The files now current that came from the previous stack. Empty if
            no previous stack was kept, in which case nothing moves.
        """
        from astrometricslib.pipelines.stacking.post_processing.previous_stack import (
            swap_with_previous_stack,
        )
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

        check_choice("kind", kind, STACK_KINDS)
        return swap_with_previous_stack(stack_path_of(self._resolve(target), kind))

    def restore_excluded_frames(self, target: str | Target, apply: bool = False) -> RestoreReport:
        """List, or move back, the frames the stacker set aside for a target.

        By default nothing moves: the call only lists the frames. With
        ``apply=True`` it moves them back and reindexes the target, which
        saves it. The next stack may set the same frames aside again. To
        keep them in the stack, turn off ``quarantine_bad_frames_enabled`` in
        the configuration first. To only read the list, use
        ``QualityDiagnostics.frame_quality(include=["excluded"])``.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose frames to restore.
        apply : `bool`, optional
            Move the frames back. Defaults to `False`, which only lists them.

        Returns
        -------
        report : `RestoreReport`
            The frames that were set aside, and how many were moved back.
        """
        from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
            find_quarantine_folders,
            list_set_aside_frames,
            restore_quarantined_frames,
        )

        resolved = self._resolve(target)
        frames_path = str(self._config.get_frames_path())
        frames = list_set_aside_frames(frames_path, resolved.id)
        restored_count = 0
        if apply:
            for folder in find_quarantine_folders(frames_path, resolved.id):
                restored_count += len(restore_quarantined_frames(folder))
            if restored_count:
                self._targets.reindex_frames(resolved)
        return RestoreReport(
            target_id=resolved.id, applied=apply, frames=frames, restored_count=restored_count
        )

    # -- Limits on how much heavy work runs at once -------------------------

    def acquire_analysis_slot(self) -> AbstractContextManager:
        """Limit how many heavy jobs can run at the same time.

        Processing images takes a lot of CPU power. This makes sure the
        computer is not overwhelmed, by limiting how many jobs run at once.
        Shares its slot pool with stacking (see `acquire_stacking_slot`);
        both draw on ``max_concurrent_jobs``.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            analysis run's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())

    def acquire_stacking_slot(self) -> AbstractContextManager:
        """Limit how many stacking programs can run at the same time.

        Stacking images uses a lot of memory and CPU. This makes sure the
        computer does not run out, by limiting how many stacks run at once.
        Shares its slot pool with analysis (see `acquire_analysis_slot`);
        both draw on ``max_concurrent_jobs``.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            stacking run's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())
