"""Quality diagnostics: measure raw frames and finished stacks.

`QualityDiagnostics` is the child of `ProcessingPipelines` that measures
without changing anything. It is reached as
`astrometrics.processing.diagnostics`.
The frame choice helper and the time input type it shares with
`ProcessingPipelines` live here too, so `processing.py` imports from this
file and never the other way round.
"""

from datetime import datetime
from typing import Literal

from astropy.time import Time

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, registered_job
from astrometricslib.models.quality.excluded_frames import QuarantinePreview
from astrometricslib.models.quality.quality_reports import (
    InputQualityReport,
    RawFrameCheckReport,
    SpectralFrameCheckReport,
    StackQualityReport,
)
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.api_arguments import (
    TargetLookup,
    check_choice,
    check_include,
    reject_unused_arguments,
    resolve_target,
    to_epoch_seconds,
)
from astrometricslib.pipelines.shared.quality.frame_selection import FrameSelection

__all__ = ["QualityDiagnostics"]

TimeInput = str | datetime | Time | None
"""A time as these methods accept it: ISO 8601 text, `datetime` or `Time`."""

StackKind = Literal["imaging", "spectral"]
STACK_KINDS = ("imaging", "spectral")

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
        from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_frame_check import (
            check_spectral_frames,
        )

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
