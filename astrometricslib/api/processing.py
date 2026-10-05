"""Main interfaces for running processing pipelines and calibration data.

This module provides the primary ways to trigger large processing jobs like
stacking images, measuring stars (photometry), and analyzing light
(spectroscopy). It also provides tools to check the quality of processed
images and manage calibration frames (darks, biases, and flats) which are
used to remove noise from raw telescope images.
"""

import os
from contextlib import AbstractContextManager
from typing import Any, Literal

from astrometricslib.drivers.job_logging import JobHandle, background_job, capture_job_logs, registered_job
from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
from astrometricslib.drivers.siril_interface import ImageProcessing
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.calibration_ingest import CalibrationIngestReport, FlatSetAssessment
from astrometricslib.models.excluded_frames import QuarantinePreview, RestoreReport, SetAsideFrame
from astrometricslib.models.stack_comparison import StackComparison
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.calibration_ingest import (
    assess_flat_group,
    build_ingest_report,
    flatten_frame_index,
)
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings
from astrometricslib.pipelines.stacking.stack_runner import run_siril_stack
from astrometricslib.pipelines.stacking.stage import stack_frames

__all__ = [
    "CalibrationCatalog",
    "DbLogHandler",
    "ImageProcessing",
    "JobHandle",
    "LoggerInterface",
    "ProcessingPipelines",
    "QualityDiagnostics",
    "capture_job_logs",
    "registered_job",
    "run_siril_stack",
    "stack_frames",
]

_CalibrationKind = Literal["dark", "bias", "flat"]
_CALIBRATION_KINDS: frozenset[str] = frozenset({"dark", "bias", "flat"})


def _exposure_matches(recorded: Any, wanted: float) -> bool:
    """Say whether a frame's recorded exposure equals a wanted length.

    Returns
    -------
    matches : `bool`
        `True` if the two agree to a thousandth of a second.
    """
    try:
        return abs(float(recorded) - wanted) < 0.001
    except TypeError, ValueError:
        return False


class QualityDiagnostics:
    """Tools for checking the quality of processed images.

    This class is used to measure things like star sharpness (FWHM) and
    how much noisy data had to be thrown away during image stacking.
    This helps to figure out if an observation session had good tracking
    and clear skies, or if the resulting data is poor quality.
    """

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with application configuration.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        """
        self._config = config

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_stack_fwhm(self, path: str) -> float | None:
        """Get the median FWHM (pixels) of the brightest stars in a FITS image.

        A stack combined from several exposure groups has its bright star
        cores patched from a shorter exposure. Those stars are left out, as
        the stacking stage leaves them out, so this gives the stage's number.

        Parameters
        ----------
        path : `str`
            Path to the stacked FITS image to measure.

        Returns
        -------
        fwhm : `float` or `None`
            Median FWHM in pixels across the measured stars, or `None`
            if it could not be measured.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_comparison import measure_stack_fwhm

        return measure_stack_fwhm(path)

    def check_raw_frames(self, folder_path: str, last_count: int | None = None) -> dict[str, Any]:
        """Check raw light frames in a folder and flag the ones that stand out.

        Works on frames that are not in the library yet, such as a staging
        folder filled during an observing session. For each frame it
        measures the star count, star width and roundness, the longest star
        streak, the sky level, the saturated pixels, and how far the star
        field moved since the previous frame. A frame is flagged when it
        differs from the batch median: too few stars, trailed, soft,
        elongated, or moved a long way.

        Parameters
        ----------
        folder_path : `str`
            A folder of ``*.fits`` light frames, read in file-name order.
        last_count : `int`, optional
            Check only the newest this many frames.

        Returns
        -------
        report : `dict`
            ``frames`` (the measurements and ``flags`` for each frame) and
            ``batch`` (frame count, median star count and width, and how
            many frames were flagged).
        """
        from astrometricslib.pipelines.shared.quality.raw_frame_check import check_raw_frames

        return check_raw_frames(folder=folder_path, last_count=last_count)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def frame_quality(
        self,
        target: Target | None = None,
        folder_path: str | None = None,
        mode: str = "input_quality",
        include_fwhm: bool = False,
        remeasure: bool = False,
        camera_name: str | None = None,
        limit: int = 50,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: str | None = None,
        until: str | None = None,
        include_spectra: bool = False,
        trend_frames: int = 10,
        trend_threshold_percent: float = 15.0,
    ) -> dict[str, Any]:
        """Measure raw frames, or preview what the stacker would set aside.

        Nothing is saved. The frames and the target are only read. For a
        target the measurements are made on a copy, so they never reach the
        saved catalog.

        Parameters
        ----------
        target : `Target`, optional
            The target whose light frames to measure. Needed for
            ``input_quality`` and ``quarantine_preview``.
        folder_path : `str`, optional
            A folder of ``*.fits`` frames, for ``raw_check``. It need not be
            in the library, such as a staging folder from tonight.
        mode : `str`, optional
            ``"input_quality"`` (default): sky background, saturated pixels
            and optionally star width for the newest frames of a target,
            with a summary (count, minimum, median, maximum) per number.
            ``"raw_check"``: the batch check of a folder (or of a target's
            frames when no folder is given), flagging frames
            with few stars, trailing, soft or elongated stars, or a large
            jump. ``"quarantine_preview"``: which frames the stacker would
            set aside, without moving any.
        include_fwhm : `bool`, optional
            For ``input_quality``, also measure star width. This is about
            50 times slower per frame.
        remeasure : `bool`, optional
            For ``input_quality``, measure again frames that already have
            stored values.
        camera_name : `str`, optional
            For ``input_quality``, only frames from this camera (matched
            case-insensitively as a substring).
        limit : `int`, optional
            How many frames to measure and list, from 1 to 300. Defaults
            to 50. A frame takes about a second to measure. With no range
            or time given these are the newest frames; with a range or a
            time bound they are the first ones inside it.
        filter_name : `str`, optional
            Only frames whose filter matches this text, ignoring case, such
            as ``"L"``. Spectroscopy frames are left out unless
            ``include_spectra`` is set or this names one.
        first_file : `str`, optional
            Only frames from this file onward, by file name. A bare number
            such as ``"013"`` means frame 013 of the night.
        last_file : `str`, optional
            Only frames up to and including this file, by file name or
            number.
        since : `str`, optional
            Only frames taken at or after this ISO 8601 time. No offset
            means UTC.
        until : `str`, optional
            Only frames taken at or before this ISO 8601 time.
        include_spectra : `bool`, optional
            Also measure spectroscopy frames. Defaults to `False`; their
            smeared stars are flagged as trailing, which is not a fault.
        trend_frames : `int`, optional
            For ``raw_check`` and ``input_quality``, how many of the newest
            measured frames to check for a slow drift. Defaults to 10.
        trend_threshold_percent : `float`, optional
            How far, as a percentage of its starting level, a number must
            move across those frames to count as a trend. Defaults to 15.

        Returns
        -------
        report : `dict` [`str`, `Any`]
            The mode and the results. They include ``trends``: for star
            width, star count and sky level, how far each moved across the
            newest frames and whether it moved steadily, with a plain
            sentence in ``alerts`` for each worrying drift (a widening
            star width, falling star count, or a sky level moving either
            way). Frames are taken in file-name order, so the order is time
            order. A problem comes back under ``"error"``.
        """
        from astrometricslib.pipelines.shared.quality import frame_selection, frame_statistics
        from astrometricslib.pipelines.shared.quality.frame_trends import find_trends

        limit = max(1, min(int(limit), 300))
        try:
            selection = frame_selection.FrameSelection(
                filter_name=filter_name,
                first_file=first_file,
                last_file=last_file,
                since=frame_selection.parse_iso_time(since),
                until=frame_selection.parse_iso_time(until),
                include_spectra=include_spectra,
            )
        except ValueError as error:
            return {"error": f"since and until must be ISO 8601 times: {error}"}
        if mode == "raw_check":
            if folder_path:
                paths = frame_selection.select_folder_paths(folder_path, selection)
            elif target is not None:
                paths = [
                    frame.path
                    for frame in frame_selection.select_library_frames(
                        [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"],
                        selection,
                    )
                ]
            else:
                return {"error": "mode='raw_check' needs a folder_path or a target."}
            matching = len(paths)
            paths = paths[:limit] if selection.has_bounds else paths[-limit:]
            if not paths:
                return {"mode": mode, "frames_matching": 0, "frames": [], "batch": {"frame_count": 0}}
            from astrometricslib.pipelines.shared.quality.raw_frame_check import check_raw_frames

            report = check_raw_frames(paths=paths)
            trends = find_trends(
                report.get("frames", []),
                {"fwhm_px": "rising", "star_count": "falling", "sky_median_adu": "either"},
                trend_frames,
                trend_threshold_percent,
            )
            return {
                "mode": mode,
                "folder_path": folder_path,
                "frames_matching": matching,
                "frames_checked": len(paths),
                **report,
                "trends": trends,
            }
        if mode not in ("input_quality", "quarantine_preview"):
            return {"error": "mode must be one of: input_quality, raw_check, quarantine_preview."}
        if target is None:
            return {"error": f"mode={mode!r} needs a target."}

        lights = [
            frame
            for frame in target.frames
            if str(frame.role).upper() == "LIGHT" and not frame_is_spectral(frame)
        ]
        if mode == "quarantine_preview":
            from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
                decision_to_set_aside_frame,
                find_frames_to_quarantine,
            )

            report = find_frames_to_quarantine(lights)
            preview = QuarantinePreview(
                target_id=target.id,
                frames_checked=len(lights),
                would_move=[decision_to_set_aside_frame(decision) for decision in report.moved],
                notes=report.notes,
                unreadable=report.unreadable,
            )
            answer = preview.model_dump(mode="json")
            answer["would_move_total"] = len(answer["would_move"])
            answer["would_move"] = answer["would_move"][:limit]
            return {"mode": mode, **answer}

        wanted = (camera_name or "").lower()
        every_light = [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"]
        chosen = [
            frame
            for frame in frame_selection.select_library_frames(every_light, selection)
            if wanted in (frame.camera or "").lower()
        ]
        frames_matching = len(chosen)
        chosen = chosen[:limit] if selection.has_bounds else chosen[-limit:]
        working = target.model_copy(deep=True)
        working.frames = [frame.model_copy(deep=True) for frame in chosen]
        counts = frame_statistics.measure_frame_input_quality(working, include_fwhm, remeasure, None)
        metrics = ("background_level", "saturated_pixel_fraction", "measured_fwhm_px")
        rows = [
            {
                "file": os.path.basename(frame.path),
                "camera": frame.camera,
                "exposure": frame.exposure,
                "filter": str(frame.filter),
                **{name: getattr(frame.measurements, name) for name in metrics},
            }
            for frame in working.frames
        ]
        summary = {}
        for name in metrics:
            values = sorted(row[name] for row in rows if row[name] is not None)
            summary[name] = (
                {
                    "frames": len(values),
                    "minimum": values[0],
                    "median": values[len(values) // 2],
                    "maximum": values[-1],
                }
                if values
                else {"frames": 0}
            )
        return {
            "mode": mode,
            "target_id": target.id,
            "light_frames_in_target": len(lights),
            "spectral_light_frames_in_target": sum(1 for frame in every_light if frame_is_spectral(frame)),
            "frames_matching": frames_matching,
            "frames_measured": len(rows),
            "counts": counts,
            "summary": summary,
            "trends": find_trends(
                rows,
                {"measured_fwhm_px": "rising", "background_level": "either"},
                trend_frames,
                trend_threshold_percent,
            ),
            "frames": rows,
            "note": "Nothing was saved. Frames that were already measured keep their stored values.",
        }

    @background_job("diagnostics", grace_period_seconds=20.0)
    def spectral_frame_check(
        self,
        target: Target,
        first_file: str | None = None,
        last_file: str | None = None,
        since: str | None = None,
        until: str | None = None,
        exposure_seconds: float | None = None,
        predict_exposure_seconds: float | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
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
        target : `Target`
            The target whose spectrum frames to measure.
        first_file : `str`, optional
            Only frames from this file onward. A bare number such as
            ``"013"`` means frame 013.
        last_file : `str`, optional
            Only frames up to this file or number.
        since : `str`, optional
            Only frames taken at or after this ISO 8601 time (UTC if no
            offset).
        until : `str`, optional
            Only frames taken at or before this ISO 8601 time.
        exposure_seconds : `float`, optional
            Only frames with this exposure length.
        predict_exposure_seconds : `float`, optional
            An exposure to predict the peaks at, in seconds.
        limit : `int`, optional
            How many frames to measure, from 1 to 100. Defaults to 30. With
            a range or a time bound these are the first frames inside it,
            otherwise the newest.

        Returns
        -------
        report : `dict` [`str`, `Any`]
            ``frames`` (one row each) and ``summary``. A problem comes back
            under ``error``.
        """
        from astrometricslib.pipelines.shared.quality import frame_selection, spectral_frame_check
        from astrometricslib.pipelines.stacking.processing.group_derotation import (
            MINIMUM_TRAIL_CONTRAST_SIGMA,
        )

        limit = max(1, min(int(limit), 100))
        try:
            selection = frame_selection.FrameSelection(
                first_file=first_file,
                last_file=last_file,
                since=frame_selection.parse_iso_time(since),
                until=frame_selection.parse_iso_time(until),
                include_spectra=True,
            )
        except ValueError as error:
            return {"error": f"since and until must be ISO 8601 times: {error}"}
        lights = [
            frame
            for frame in target.frames
            if str(frame.role).upper() == "LIGHT" and frame_is_spectral(frame)
        ]
        chosen = frame_selection.select_library_frames(lights, selection)
        if exposure_seconds is not None:
            chosen = [frame for frame in chosen if _exposure_matches(frame.exposure, exposure_seconds)]
        matching = len(chosen)
        if not chosen:
            return {"target_id": target.id, "frames_matching": 0, "frames": [], "summary": {}}
        chosen = chosen[:limit] if selection.has_bounds else chosen[-limit:]

        from astrometricslib.drivers.job_logging import get_current_job

        job = get_current_job()
        geometry_by_camera: dict[str, dict[str, Any]] = {}
        rows = []
        for index, frame in enumerate(chosen):
            if job is not None:
                job.mark(
                    "running",
                    index,
                    progress_total=len(chosen),
                    message=f"Measured {index} of {len(chosen)} spectrum frames",
                )
            row: dict[str, Any] = {
                "file": os.path.basename(frame.path),
                "exposure_seconds": float(frame.exposure),
                "pier_side": frame.pier_side,
            }
            try:
                if frame.camera not in geometry_by_camera:
                    geometry_by_camera[frame.camera] = spectral_frame_check.load_dispersion_geometry(
                        frame.camera
                    )
                row.update(
                    spectral_frame_check.measure_spectral_frame_file(
                        frame.path,
                        frame.camera,
                        row["exposure_seconds"],
                        geometry_by_camera[frame.camera],
                        predict_exposure_seconds,
                    )
                )
            except (OSError, ValueError) as error:
                row["error"] = str(error)
            rows.append(row)
        return {
            "target_id": target.id,
            "frames_matching": matching,
            "frames_measured": len(rows),
            "summary": spectral_frame_check.summarize_spectral_frames(rows, MINIMUM_TRAIL_CONTRAST_SIGMA),
            "frames": rows,
            "note": "Nothing was saved. Peaks above 65,000 ADU are lower bounds; predictions scale linearly.",
        }

    @background_job("diagnostics", grace_period_seconds=20.0)
    def compare_stacks(self, before_path: str, after_path: str) -> StackComparison:
        """Measure two stacks and say how they differ.

        Both stacks are measured the same way: sky level, pixel noise, how
        flat the sky is across the frame, and star width. The result gives the
        numbers, the change in each, and a plain sentence for each. It does
        not pick a winner, because that depends on what the change was for.
        New flats should lower the flatness number and leave the noise alone;
        more frames should lower the noise and leave the flatness alone.

        Parameters
        ----------
        before_path : `str`
            Path of the older stack's FITS file.
        after_path : `str`
            Path of the newer stack's FITS file.

        Returns
        -------
        comparison : `StackComparison`
            The measurements of both stacks and the change between them.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_comparison import compare_stacks

        return compare_stacks(before_path, after_path)

    def measure_stack_rejected_fraction(self, stacked_path: str) -> float | None:
        """Get the mean per-pixel rejected-frame fraction from the rejmap.

        Parameters
        ----------
        stacked_path : `str`
            Path to the stacked FITS image; its sibling rejmap file is
            resolved from this path.

        Returns
        -------
        fraction : `float` or `None`
            Mean rejected-pixel fraction over the rejmap, or `None` if
            the sibling rejmap file does not exist.
        """
        from astrometricslib.pipelines.shared.quality.quality_metrics import measure_rejected_fraction

        return measure_rejected_fraction(stacked_path)

    def parse_stack_registration_seq(self, seq_path: str) -> list[dict[str, float]]:
        """Parse registration-summary lines from a Siril .seq file.

        Parameters
        ----------
        seq_path : `str`
            Path to the Siril `.seq` file to parse.

        Returns
        -------
        frames : `list` of `dict`
            One dict per registered frame, in original submission order.
        """
        from astrometricslib.drivers.siril_output_parsing import parse_seq_file

        return parse_seq_file(seq_path)

    def parse_stack_zero_order_star(self, lst_path: str) -> dict[str, float] | None:
        """Get the brightest star's stats from a Siril per-frame .lst list.

        Parameters
        ----------
        lst_path : `str`
            Path to the Siril `.lst` file to parse.

        Returns
        -------
        result : `dict` or `None`
            Stats for the brightest star, or `None` if the file does
            not exist or has no data rows.
        """
        from astrometricslib.drivers.siril_output_parsing import parse_zero_order_star

        return parse_zero_order_star(lst_path)

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
    """A catalog for managing calibration frames (darks, biases, and flats).

    Telescope cameras produce noise. Calibration frames are special pictures
    taken with the lens cap on (darks/biases) or pointed at a flat white
    surface (flats) to map out and remove this noise. This class tracks
    these files so they can be applied to real images later.
    """

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with application configuration.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        """
        self._config = config
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

    @staticmethod
    def _validate_kind(kind: str) -> None:
        """Raise `ValueError` if `kind` is not a supported calibration kind.

        Parameters
        ----------
        kind : `str`
            The calibration kind to validate.

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """
        if kind not in _CALIBRATION_KINDS:
            raise ValueError(
                f"Unknown calibration kind {kind!r}; expected one of {sorted(_CALIBRATION_KINDS)}"
            )

    def load(self) -> None:
        """Load the calibration library from its on-disk JSON file."""
        self.library.load_library()

    def save(self) -> None:
        """Record the calibration library to its on-disk JSON file."""
        self.library.save_library()

    def stats(self) -> dict[str, Any]:
        """Return aggregated per-camera/exposure/filter counts for all kinds.

        Returns
        -------
        stats : `dict`
            Dict with ``"darks"``, ``"biases"``, and ``"flats"`` keys,
            each a list of per-group count summaries.
        """
        return self.library.get_stats()

    def add(self, image_file: str, kind: _CalibrationKind, **kwargs: Any) -> None:
        """Add a calibration frame of the given kind (dark/bias/flat).

        Parameters
        ----------
        image_file : `str`
            Path to the calibration frame FITS file.
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Forwarded to the kind-specific adder -- ``flat`` accepts
            `telescope`.

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """  # ruff: ignore[docstring-extraneous-exception] -- genuinely raised by self._validate_kind
        self._validate_kind(kind)
        method = getattr(self.library, f"add_{kind}_frame")
        method(image_file, **kwargs)

    def get(self, kind: _CalibrationKind, **kwargs: Any) -> list[str]:
        """Retrieve calibration frame paths of the given kind.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Forwarded to the kind-specific getter -- common keys are
            `camera`, `exposure` (dark only), `telescope`/
            `filter_type` (flat only), and `validate_paths`.

        Returns
        -------
        frames : `list` [`str`]
            Matching frame file paths.

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """  # ruff: ignore[docstring-extraneous-exception] -- genuinely raised by self._validate_kind
        self._validate_kind(kind)
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

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """  # ruff: ignore[docstring-extraneous-exception] -- genuinely raised by self._validate_kind
        self._validate_kind(kind)
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
        camera: str | None = None,
        filter_type: str | None = None,
        gain: float | None = None,
        offset: float | None = None,
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
        camera : `str`, optional
            Only this camera's flats.
        filter_type : `str`, optional
            Only flats for this filter, such as ``"L"`` or ``"SPEC"``.
        gain : `float`, optional
            Only flats at this gain.
        offset : `float`, optional
            With `gain`, only flats at this camera offset.

        Returns
        -------
        assessments : `list` [`FlatSetAssessment`]
            One assessment per matching set. Empty if no flats match.
        """
        groups = self.library.list_flat_groups(
            telescope=telescope, camera=camera, filter_type=filter_type, gain=gain, offset=offset
        )
        return [assess_flat_group(group) for group in groups]


class ProcessingPipelines:
    """Main interface for triggering image stacking and analysis pipelines."""

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with application configuration.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        """
        self._config = config
        self.diagnostics = QualityDiagnostics(config)
        self.calibration = CalibrationCatalog(config)

    # -- Pipeline execution, one method per pipeline type -----------------

    @background_job("stacking", grace_period_seconds=5.0)
    def run_stacking(
        self,
        target: Target,
        frames_to_stack: list[Any] | None = None,
        filter_type: Any | None = None,
        rejection_sigma: tuple[float, float] | None = None,
        filter_wfwhm: str | None = None,
        filter_round: str | None = None,
        stack_weight: str | None = None,
        output_file: str | None = None,
        log_file: str | None = None,
        generate_rejmap: bool | None = None,
        force: bool = False,
        preview_settings: PreviewSettings | None = None,
    ) -> str | None:
        """Stack multiple images into one clean image.

        Stacking combines many faint, noisy images into one clear image.
        To also plate-solve the result, call `run_astrometry` afterward
        with the same target.

        Called through the MCP server, this runs as a background job (see
        `astrometricslib.drivers.job_logging.background_job`) rather than
        blocking the caller until Siril finishes -- called directly, it
        behaves exactly as before.

        Parameters
        ----------
        target : `Target`
            The target whose frames should be stacked.
        frames_to_stack : `list`, optional
            Explicit frame records to stack; defaults to the target's
            eligible light frames.
        filter_type : `astrometricslib.foundation.enums.FilterType`, optional
            Restrict stacking to frames captured with this filter.
        rejection_sigma : `tuple` [`float`, `float`], optional
            Low/high sigma-clipping rejection bounds.
        filter_wfwhm : `str`, optional
            Weighted-FWHM frame filter expression.
        filter_round : `str`, optional
            Roundness frame filter expression.
        stack_weight : `str`, optional
            Per-frame stacking weight expression.
        output_file : `str`, optional
            Output path override.
        log_file : `str`, optional
            Path to write the Siril process log to.
        generate_rejmap : `bool`, optional
            Whether to also generate a rejection map alongside the
            stack.
        force : `bool`, optional
            Rebuild the stack even if its frames, calibration frames and
            settings are the same as when the stack on disk was made.
            Without it, an unchanged stack is kept and its path returned
            (see the setting ``skip_unchanged_stacks_enabled``).
        preview_settings : `PreviewSettings`, optional
            Choices for this run's preview picture that replace the saved
            settings. Never written to the settings.

        Returns
        -------
        stacked_path : `str` or `None`
            The path to the stacked output file, or `None` if
            stacking did not produce an output. For a stack kept as
            unchanged, the path of the stack already on disk.
        """
        from astrometricslib.pipelines.stacking import stage as stacking_tasks

        with registered_job(
            enabled=True,
            job_type="stacking",
            target_id=target.id,
            log_file=log_file,
            completed_message=f"[{target.id}] Stacking completed successfully.",
            failed_message=f"[{target.id}] Stacking failed.",
        ) as job:
            stacked_path = stacking_tasks.stack_frames(
                target,
                log_file=log_file,
                frames_to_stack=frames_to_stack,
                filter_type=filter_type,
                rejection_sigma=rejection_sigma,
                filter_wfwhm=filter_wfwhm,
                filter_round=filter_round,
                stack_weight=stack_weight,
                generate_rejmap=generate_rejmap,
                output_file=output_file,
                job_id=job.job_id,
                force=force,
                preview_settings=preview_settings,
            )
            # Stacking can finish without raising and still produce no
            # image, so the outcome is decided here rather than left to
            # the context manager's "no exception means success" default.
            job.mark("completed" if stacked_path else "failed", 100)
            return stacked_path

    @background_job("astrometry", grace_period_seconds=5.0)
    def run_astrometry(
        self,
        target: Target,
        *,
        path: str | None = None,
        catalog_access: Any = None,
        register_job: bool = True,
    ) -> dict[str, Any]:
        """Run astrometric plate-solving and catalog cross-matching.

        See `astrometricslib.pipelines.tasks.analyze_target` for the full
        return documentation. Astrometry resolves its own input image from
        `target.stacking.stacked_image` (falling back to the target's first
        frame) when `path` is omitted, so a bare `run_astrometry(target)` call
        is normally enough.

        Called through the MCP server, this runs as a background job (see
        `astrometricslib.drivers.job_logging.background_job`) rather than
        blocking the caller until astrometry finishes -- called directly,
        it behaves exactly as before.

        Parameters
        ----------
        target : `Target`
            The target to run astrometry against.
        path : `str`, optional
            The FITS image to plate-solve; `target.stacking.stacked_image`
            (or the target's first frame) is used when omitted.
        catalog_access : `Any`, optional
            Override for the star catalog reader/writer; the default is
            used when omitted.
        register_job : `bool`, optional
            Whether this run should show up in the job tracker. Defaults
            to `True`; pass `False` when the caller already tracks its own
            job (to avoid double-counting).

        Returns
        -------
        result : `dict[str, Any]`
            Astrometry results and status fields.
        """
        from astrometricslib.pipelines.tasks import analyze_target

        return analyze_target(
            target,
            pipeline_type="astrometry",
            path=path,
            catalog_access=catalog_access,
            register_job=register_job,
        )

    @background_job("photometry", grace_period_seconds=5.0)
    def run_photometry(
        self,
        target: Target,
        *,
        frames: list[FrameRecord] | None = None,
        filter_type: str | None = None,
        use_astrometry_seed: bool = True,
        max_workers: int | None = None,
        catalog_access: Any = None,
        register_job: bool = True,
    ) -> dict[str, Any]:
        """Run ensemble differential photometry.

        See `astrometricslib.pipelines.tasks.analyze_target` for the full
        return documentation. Photometry resolves its own frames from
        `target.frames` when `frames` is omitted.

        Called through the MCP server, this runs as a background job (see
        `astrometricslib.drivers.job_logging.background_job`) rather than
        blocking the caller until photometry finishes -- called directly,
        it behaves exactly as before.

        Parameters
        ----------
        target : `Target`
            The target to run photometry against.
        frames : `list` [`FrameRecord`], optional
            The frames to use; `target.frames` is used when omitted.
        filter_type : `str`, optional
            Only frames with this filter are used; all frames are eligible
            when omitted.
        use_astrometry_seed : `bool`, optional
            Whether to seed each session's plate solve from astrometry's
            already-solved WCS when one exists. Defaults to `True`.
        max_workers : `int`, optional
            Maximum parallel workers per observing session.
        catalog_access : `Any`, optional
            Override for the star catalog reader/writer; the default is
            used when omitted.
        register_job : `bool`, optional
            Whether this run should show up in the job tracker. Defaults
            to `True`; pass `False` when the caller already tracks its own
            job (to avoid double-counting).

        Returns
        -------
        result : `dict[str, Any]`
            Photometry results and status fields.
        """
        from astrometricslib.pipelines.tasks import analyze_target

        return analyze_target(
            target,
            pipeline_type="photometry",
            frames=frames,
            filter_type=filter_type,
            catalog_access=catalog_access,
            register_job=register_job,
            use_astrometry_seed=use_astrometry_seed,
            max_workers=max_workers,
        )

    def run_spectroscopy(
        self,
        target: Target,
        *,
        path: str | None = None,
        limit: int | None = None,
        catalog_access: Any = None,
        register_job: bool = True,
        photometry_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run spectroscopy extraction and calibration.

        See `astrometricslib.pipelines.tasks.analyze_target` for the full
        return documentation. Spectroscopy resolves its own input image
        from `target.spectral_stacking.stacked_image` (falling back to the
        target's first frame) when `path` is omitted.

        Parameters
        ----------
        target : `Target`
            The target to run spectroscopy against.
        path : `str`, optional
            The spectral FITS image to extract from;
            `target.spectral_stacking.stacked_image` (or the target's
            first frame) is used when omitted.
        limit : `int`, optional
            A cap on how many candidate stars to process; pass a number
            only to deliberately cap a run (for example a quick
            interactive check). All candidates are processed when omitted.
        catalog_access : `Any`, optional
            Override for the star catalog reader/writer; the default is
            used when omitted.
        register_job : `bool`, optional
            Whether this run should show up in the job tracker. Defaults
            to `True`; pass `False` when the caller already tracks its own
            job (to avoid double-counting).
        photometry_result : `dict[str, Any]`, optional
            Not consumed by the pipeline yet -- reserved so a future
            spectroscopy dependency on photometry's output has a real
            parameter to fill in, rather than one added later across
            several files. Passing it today is a safe no-op.

        Returns
        -------
        result : `dict[str, Any]`
            Spectroscopy results and status fields.
        """
        from astrometricslib.pipelines.tasks import analyze_target

        return analyze_target(
            target,
            pipeline_type="spectroscopy",
            path=path,
            catalog_access=catalog_access,
            register_job=register_job,
            limit=limit,
            photometry_result=photometry_result,
        )

    @background_job("process_target", grace_period_seconds=5.0)
    def process_target(
        self,
        target: Target,
        *,
        stages: frozenset[str] = frozenset({"astrometry", "photometry", "spectroscopy"}),
        photometry: dict[str, Any] | None = None,
        spectroscopy: dict[str, Any] | None = None,
        register_job: bool = True,
    ) -> dict[str, Any]:
        """Run astrometry, then photometry, then spectroscopy for one target.

        This is the shorter path for "just process my target the right
        way" -- each of the three stages is still available independently
        as `run_astrometry`/`run_photometry`/`run_spectroscopy` for when a
        caller wants to run (or customize) only one of them.

        Called through the MCP server, this runs as a background job (see
        `astrometricslib.drivers.job_logging.background_job`) rather than
        blocking the caller until all three stages finish -- called
        directly, it behaves exactly as before.

        Execution order is always astrometry, then photometry, then
        spectroscopy, regardless of `stages`' order -- `stages` only
        selects which ones run, it does not resequence them. Each stage's
        own options are passed as a plain dict (`photometry=`,
        `spectroscopy=`) rather than flattened onto this method, so an
        option meant for one stage can never accidentally reach another.
        Spectroscopy is skipped with a `{"status": "skipped", ...}` result
        (not an error) when the target has no spectral data at all, and
        otherwise receives photometry's result as `photometry_result` (see
        `run_spectroscopy`'s `photometry_result` parameter).

        Parameters
        ----------
        target : `Target`
            The target to process.
        stages : `frozenset` [`str`], optional
            Which of `{"astrometry", "photometry", "spectroscopy"}` to
            run. Defaults to all three.
        photometry : `dict[str, Any]`, optional
            Extra keyword arguments forwarded to `run_photometry` (for
            example `{"filter_type": "L", "frames": my_frames}`).
        spectroscopy : `dict[str, Any]`, optional
            Extra keyword arguments forwarded to `run_spectroscopy`.
        register_job : `bool`, optional
            Whether each stage's run should show up in the job tracker.
            Defaults to `True`.

        Returns
        -------
        results : `dict[str, Any]`
            One entry per stage actually run, keyed by stage name.
        """
        results: dict[str, Any] = {}

        if "astrometry" in stages:
            results["astrometry"] = self.run_astrometry(target, register_job=register_job)

        if "photometry" in stages:
            results["photometry"] = self.run_photometry(
                target, register_job=register_job, **(photometry or {})
            )

        if "spectroscopy" in stages:
            has_spectral_input = bool(target.spectral_stacking.stacked_image) or any(
                frame_is_spectral(frame) for frame in target.frames or []
            )
            if has_spectral_input:
                results["spectroscopy"] = self.run_spectroscopy(
                    target,
                    register_job=register_job,
                    photometry_result=results.get("photometry"),
                    **(spectroscopy or {}),
                )
            else:
                results["spectroscopy"] = {
                    "status": "skipped",
                    "reason": "target has no spectral data",
                }

        return results

    def run_spectroscopy_by_session(
        self,
        astrometrics: Any,
        target: Target,
        frame_records: list[Any],
        max_workers: int | None = None,
        on_item_complete: Any | None = None,
    ) -> Any:
        """Analyze a target's spectroscopy frames, grouped by session.

        Parameters
        ----------
        astrometrics : `astrometricslib.Astrometrics`
            The parent astrometrics, needed to resolve session boundaries.
        target : `Target`
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
        from astrometricslib.pipelines.spectroscopy import (
            batch as spectroscopy_batch_operations,
        )

        with registered_job(
            enabled=True,
            job_type="spectroscopy_session",
            target_id=target.id,
            completed_message=f"[{target.id}] Session-based spectroscopy completed successfully.",
            failed_message=f"[{target.id}] Session-based spectroscopy failed.",
        ) as job:
            return spectroscopy_batch_operations.process_spectroscopy_frames_by_session(
                astrometrics,
                target,
                frame_records,
                max_workers=max_workers,
                on_item_complete=on_item_complete,
                job_id=job.job_id,
            )

    def scan_target_directory(self, target: Target, frames_root_path: str) -> None:
        """Scan a folder to find and catalog any new image frames for a target.

        Parameters
        ----------
        target : `Target`
            The target to index frames into.
        frames_root_path : `str`
            Root directory to scan for FITS files.
        """
        from astrometricslib.pipelines.shared.frame_scanning import scan_target_directory

        scan_target_directory(target, frames_root_path)

    def preview_quarantine(self, target: Target) -> QuarantinePreview:
        """Show which frames the stacker would set aside, without moving any.

        Before each stack, the pipeline moves light frames with clouds or
        trailed stars into an `_excluded` folder. This runs the same
        check and reports the result only. It measures every light frame
        (about a second each), so a large target takes a while.

        Parameters
        ----------
        target : `Target`
            The target to check.

        Returns
        -------
        preview : `QuarantinePreview`
            The frames the check would move, with the measurements behind
            each, the batches it would leave alone, and any frame it could
            not read.
        """
        from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
            decision_to_set_aside_frame,
            find_frames_to_quarantine,
        )

        light_frames = [
            frame
            for frame in target.frames
            if str(frame.role).upper() == "LIGHT" and not frame_is_spectral(frame)
        ]
        report = find_frames_to_quarantine(light_frames)
        return QuarantinePreview(
            target_id=target.id,
            frames_checked=len(light_frames),
            would_move=[decision_to_set_aside_frame(decision) for decision in report.moved],
            notes=report.notes,
            unreadable=report.unreadable,
        )

    def list_excluded_frames(self, target: Target) -> list[SetAsideFrame]:
        """List the frames the stacker has set aside for a target.

        Parameters
        ----------
        target : `Target`
            The target to look up.

        Returns
        -------
        frames : `list` [`SetAsideFrame`]
            One entry per frame now in an `_excluded` folder, with why it
            was moved and the measurements behind that.
        """
        from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import list_set_aside_frames

        return list_set_aside_frames(str(self._config.get_frames_path()), target.id)

    def restore_excluded_frames(self, target: Target, apply: bool = False) -> RestoreReport:
        """List, or move back, the frames the stacker set aside for a target.

        By default nothing moves: the call only lists the frames. With
        ``apply=True`` it moves them back and re-scans the target so it
        lists them again. The target is changed in memory, so save it
        afterwards. The next stack may set the same frames aside again. To
        keep them in the stack, turn off ``quarantine_bad_frames_enabled``
        in the configuration first.

        Parameters
        ----------
        target : `Target`
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

        frames_path = str(self._config.get_frames_path())
        frames = list_set_aside_frames(frames_path, target.id)
        restored_count = 0
        if apply:
            for folder in find_quarantine_folders(frames_path, target.id):
                restored_count += len(restore_quarantined_frames(folder))
            if restored_count:
                self.scan_target_directory(target, frames_path)
        return RestoreReport(target_id=target.id, applied=apply, frames=frames, restored_count=restored_count)

    @staticmethod
    def _stack_path_of(target: Target, spectral: bool) -> str:
        """Find the path of a target's current stack.

        Returns
        -------
        path : `str`
            The path of the spectral stack if `spectral`, otherwise the
            imaging stack.

        Raises
        ------
        ValueError
            If the target has no such stack.
        """
        stacking = target.spectral_stacking if spectral else target.stacking
        path = getattr(stacking, "stacked_image", None)
        if not path:
            raise ValueError(f"Target '{target.id}' has no {'spectral ' if spectral else ''}stack.")
        return str(path)

    def stack_summary(self, target: Target, spectral: bool = False) -> dict[str, Any]:
        """Summarize a target's current stack in one short answer.

        Reads the numbers the stacking stage saved with the stack, so nothing
        is measured again. The answer covers how many frames went in and how
        many were set aside, the share of pixels rejected, the star width
        against what the input frames predict, the flags, and what happened
        to each exposure group (its frames, whether it clipped, how far it
        was moved to line up, and why it was left out if it was).

        Parameters
        ----------
        target : `Target`
            The target to summarize.
        spectral : `bool`, optional
            Summarize the spectral stack rather than the imaging stack.

        Returns
        -------
        summary : `dict` [`str`, `Any`]
            The summary, or ``{"error": ...}`` when the target has no stack
            or no saved summary for it.
        """
        stacking = target.spectral_stacking if spectral else target.stacking
        quality = getattr(stacking, "quality_summary", None)
        metrics = getattr(quality, "stacking_metrics", None)
        if quality is None or metrics is None:
            kind = "spectral stack" if spectral else "stack"
            return {"error": f"Target '{target.id}' has no saved summary for its {kind}."}
        groups = [
            {
                "exposure_seconds": group.exposure_seconds,
                "frames_submitted": group.frames_submitted,
                "frames_stacked": group.frames_stacked,
                "saturated": group.saturated,
                "clipped_at_zero": group.clipped_at_zero,
                "alignment_shift_pixels": group.alignment_shift_pixels,
                "left_out_reason": group.left_out_reason,
            }
            for group in metrics.exposure_groups
        ]
        excluded = metrics.excluded_frames
        return {
            "target_id": target.id,
            "stack_path": getattr(stacking, "stacked_image", None),
            "made_at": quality.created_at.isoformat(),
            "frames_submitted": metrics.frames_submitted,
            "frames_stacked": metrics.frames_stacked,
            "frames_skipped": len(excluded),
            "skipped_reasons": [
                {"file": frame.path.rsplit("/", 1)[-1], "reason": frame.reason} for frame in excluded[:10]
            ],
            "frames_set_aside_before_stacking": quality.input_quality.frames_quarantined,
            "sessions": [
                {
                    "session": session.session_id,
                    "frames": session.frames_contributed,
                    "clipped": session.frames_clipped,
                }
                for session in quality.target_session_breakdown
            ],
            "rejected_pixel_fraction": metrics.rejected_pixel_fraction,
            "rejected_fraction_flagged": metrics.rejected_fraction_flagged,
            "star_width_px": {
                "stack": metrics.stacked_fwhm_px,
                "expected_from_inputs": metrics.expected_stack_fwhm_px,
                "median_of_inputs": metrics.median_input_fwhm_px,
                "degraded": metrics.fwhm_degraded,
            },
            "saturated_pixel_fraction": metrics.saturated_pixel_fraction,
            "zero_pixel_fraction": metrics.zero_pixel_fraction,
            "exposure_groups": groups,
            "flagged": quality.flagged,
            "flag_reasons": quality.flag_reasons,
            "calibration_mismatches": len(metrics.calibration_mismatch_flags),
        }

    @background_job("diagnostics", grace_period_seconds=20.0)
    def compare_with_previous_stack(self, target: Target, spectral: bool = False) -> StackComparison | None:
        """Compare a target's stack with the one the last restack replaced.

        A restack keeps the stack it replaces in a ``_previous`` folder (the
        setting ``keep_previous_stack_enabled``, on by default). Only one
        previous version is kept.

        Parameters
        ----------
        target : `Target`
            The target whose stack to compare.
        spectral : `bool`, optional
            Compare the spectral stack instead of the imaging stack.

        Returns
        -------
        comparison : `StackComparison` or `None`
            The previous stack as ``before`` and the current one as ``after``.
            `None` if no previous stack is kept.
        """
        from astrometricslib.pipelines.stacking.post_processing.previous_stack import previous_stack_path
        from astrometricslib.pipelines.stacking.post_processing.stack_comparison import compare_stacks

        current = self._stack_path_of(target, spectral)
        previous = previous_stack_path(current)
        if previous is None:
            return None
        return compare_stacks(previous, current)

    def discard_previous_stack(self, target: Target, spectral: bool = False) -> list[str]:
        """Delete the kept previous stack of a target.

        Use this once the new stack looks good. It deletes the old stack and
        its pictures, and it cannot be undone. The pipeline never does this by
        itself; it only replaces the previous stack with a newer one at the
        next restack.

        Parameters
        ----------
        target : `Target`
            The target whose previous stack to delete.
        spectral : `bool`, optional
            Delete the previous spectral stack instead of the imaging stack.

        Returns
        -------
        removed : `list` [`str`]
            The files deleted. Empty if no previous stack was kept.
        """
        from astrometricslib.pipelines.stacking.post_processing.previous_stack import discard_previous_stack

        return discard_previous_stack(self._stack_path_of(target, spectral))

    def swap_with_previous_stack(self, target: Target, spectral: bool = False) -> list[str]:
        """Put the previous stack back as the current one.

        Use this when the new stack turned out worse. The current stack
        moves into ``_previous`` in the same step, so calling this again undoes
        it. Only the files change places. The stored quality summary describes
        the stack that was current when it was written, so restack to refresh
        it.

        Parameters
        ----------
        target : `Target`
            The target whose stacks to swap.
        spectral : `bool`, optional
            Swap the spectral stacks instead of the imaging stacks.

        Returns
        -------
        restored : `list` [`str`]
            The files now current that came from the previous stack. Empty if
            no previous stack was kept, in which case nothing moves.
        """
        from astrometricslib.pipelines.stacking.post_processing.previous_stack import (
            swap_with_previous_stack,
        )

        return swap_with_previous_stack(self._stack_path_of(target, spectral))

    def create_frame_record(self, path: str, camera: str | None = None) -> Any:
        """Create a frame record by reading a FITS image's header data.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to parse.
        camera : `str`, optional
            Camera name override; parsed from the header when omitted.

        Returns
        -------
        frame_record : `astrometricslib.models.target.FrameRecord`
            The frame record derived from the FITS header at `path`.
        """
        from astrometricslib.pipelines.shared.frame_scanning import create_frame_record_from_fits

        return create_frame_record_from_fits(path, camera)

    def acquire_analysis_slot(self) -> AbstractContextManager:
        """Limit how many heavy jobs can run at the same time.

        Processing images takes a lot of CPU power. This function ensures
        the computer is not overwhelmed by limiting how many jobs can run
        simultaneously. Shares its slot pool with stacking (see
        `acquire_stacking_slot`) -- both draw on `max_concurrent_jobs`.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            analysis run's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())

    def acquire_stacking_slot(self) -> AbstractContextManager:
        """Limit how many heavy jobs can run at the same time.

        Stacking images uses massive amounts of RAM and CPU. This function
        ensures the computer is not crashed by limiting how many stacking
        programs can run simultaneously. Shares its slot pool with
        analysis (see `acquire_analysis_slot`) -- both draw on
        `max_concurrent_jobs`.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            stacking run's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())
