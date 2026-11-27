"""Purpose: Measure raw frames for `quality.frames.measure`, storing nothing.

Description: `measure_frame_quality` is the owner behind
``astrometrics.quality.frames.measure``. It takes the frames a caller chose
(frame records, or paths of files in the library or not) and works out from
their filters whether they are imaging or spectrum frames:

* Imaging frames get the batch check (`raw_frame_check`), which flags the
  frames that stand out, with the slow drifts across the newest frames
  (`find_trends`). With the ``"metrics"`` section they also get the frame
  metric set that `quality.frames.record` stores
  (`folder_frame_measurement`), about 12 s a frame.
* Spectrum frames get the spectrum check (`spectral_frame_check`).

`frame_records_of` turns the caller's ``frames=`` argument into frame
records. Nothing here saves anything.
"""

import os
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.page import Page
from astrometricslib.models.quality.quality_reports import FrameQualityReport, RawFrameCheckReport
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_choice import frames_from_choice
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.quality.frame_trends import (
    TREND_THRESHOLD_PERCENT,
    TREND_WINDOW_FRAMES,
    find_trends,
)
from astrometricslib.pipelines.shared.quality.star_width_labels import (
    RAW_CHECK_WIDTH_ESTIMATOR,
    no_ratio_reason,
)

__all__ = ["FRAME_QUALITY_SECTIONS", "frame_records_of", "measure_frame_quality"]

FRAME_QUALITY_SECTIONS = ("metrics",)
"""The heavy parts `measure_frame_quality` adds when asked: ``"metrics"``, the
stored frame metric set."""

SKY_LEVEL_KEY = "sky_median_adu"
"""The row key of a frame's sky level in the batch check."""


def _exposure_seconds(exposure: object) -> float | None:
    """Read a frame's exposure text as seconds.

    Parameters
    ----------
    exposure : `object`
        The frame record's exposure, a number or text such as ``"30"``.

    Returns
    -------
    seconds : `float` or `None`
        The exposure rounded to a millisecond, or `None` if unreadable.
    """
    try:
        return round(float(exposure), 3)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None


def frame_records_of(frames: Any, config: AppConfiguration) -> list[FrameRecord]:
    """Turn a ``frames=`` argument into frame records, reading paths' headers.

    Parameters
    ----------
    frames : `Page` or `Sequence` [`FrameRecord`] or `Sequence` [`str`]
        The page a frame query returned, frame records, or file paths. A
        path need not be a library frame.
    config : `AppConfiguration`
        The settings, for the camera a header names.

    Returns
    -------
    records : `list` [`FrameRecord`]
        The frames, in the order given.

    Raises
    ------
    InvalidArgumentError
        If ``frames`` is a page cut short, holds nothing, or is not a list of
        frame records or paths.
    NotFoundError
        If a path names no file.
    """
    from astrometricslib.pipelines.shared.frame_scanning import create_frame_record_from_fits

    if isinstance(frames, Page) or (
        isinstance(frames, Sequence)
        and not isinstance(frames, str | bytes)
        and all(isinstance(item, FrameRecord) for item in frames)
    ):
        records = frames_from_choice(frames)
    elif (
        isinstance(frames, Sequence)
        and not isinstance(frames, str | bytes)
        and all(isinstance(item, str | os.PathLike) for item in frames)
    ):
        records = []
        for item in frames:
            path = os.fspath(item)
            if not os.path.isfile(path):
                raise NotFoundError(f"No frame at {path}.")
            records.append(create_frame_record_from_fits(path, config=config))
    else:
        raise InvalidArgumentError(
            "frames must be the page targets.frames.query returns, a list of frame records, "
            "or a list of paths."
        )
    if not records:
        raise InvalidArgumentError("frames is empty: there is nothing to measure.")
    return records


def _check_trend_metrics(frames: Sequence[FrameRecord]) -> tuple[dict[str, str], list[str]]:
    """Choose which numbers of the batch check to look at for a slow drift.

    The sky level in ADU grows with the exposure length, so it is trended
    only when every frame has the same exposure.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames checked.

    Returns
    -------
    metrics : `dict` [`str`, `str`]
        The row keys to trend, mapped to the direction that is a worry.
    notes : `list` [`str`]
        Plain sentences saying what was left out and why.
    """
    metrics = {"fwhm_px": "rising", "star_count": "falling"}
    if len({_exposure_seconds(frame.exposure_s) for frame in frames}) <= 1:
        metrics[SKY_LEVEL_KEY] = "either"
        return metrics, []
    return metrics, [
        "The sky level was not trended: the frames have different exposure lengths. "
        "The frame metric set (include=['metrics']) gives the sky rate per second."
    ]


def _check_imaging_frames(
    frames: Sequence[FrameRecord], trend_frames: int, trend_threshold_pct: float
) -> RawFrameCheckReport:
    """Run the batch check on imaging frames.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames, in time order.
    trend_frames : `int`
        How many of the newest frames to look at for a drift.
    trend_threshold_pct : `float`
        How far a number must move, as a percentage, to count as a drift.

    Returns
    -------
    report : `RawFrameCheckReport`
        The rows, the batch medians and the drifts.
    """
    from astrometricslib.pipelines.shared.quality.raw_frame_check import check_raw_frames

    checked = check_raw_frames(paths=[frame.path for frame in frames])
    rows = checked.get("frames", [])
    metrics, notes = _check_trend_metrics(frames)
    trends = find_trends(rows, metrics, trend_frames, trend_threshold_pct)
    if notes:
        trends["notes"] = notes
    return RawFrameCheckReport(
        frames_matching=len(frames),
        frames_checked=len(rows),
        fwhm_estimator=RAW_CHECK_WIDTH_ESTIMATOR,
        fwhm_not_on_scale_reason=no_ratio_reason(RAW_CHECK_WIDTH_ESTIMATOR),
        frames=rows,
        batch=checked.get("batch", {"frame_count": 0}),
        trends=trends,
    )


def measure_frame_quality(
    frames: Sequence[FrameRecord],
    config: AppConfiguration,
    measured_at: datetime,
    *,
    include: frozenset[str] = frozenset(),
    trend_frames: int | None = None,
    trend_threshold_pct: float | None = None,
    bias_level_adu: float | None = None,
    predict_exposure_s: float | None = None,
    calibration_library: Callable[[], Any] | None = None,
    drivers: Drivers | None = None,
) -> FrameQualityReport:
    """Measure raw frames, storing nothing.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames (see `frame_records_of`), all imaging or all spectral.
    config : `AppConfiguration`
        The settings.
    measured_at : `datetime`
        When the measurement is made, with a time zone.
    include : `frozenset` [`str`], optional
        ``"metrics"`` adds the stored frame metric set (imaging only).
    trend_frames : `int`, optional
        How many of the newest frames to look at for a drift. The tuned
        `TREND_WINDOW_FRAMES` when left out.
    trend_threshold_pct : `float`, optional
        How far a number must move, as a percentage of its starting level, to
        count as a drift. The tuned `TREND_THRESHOLD_PERCENT` when left out.
    bias_level_adu : `float`, optional
        The bias level for the frame metric set's sky numbers. The
        calibration library's bias frames when left out.
    predict_exposure_s : `float`, optional
        Spectrum frames only: an exposure to predict the peaks at.
    calibration_library : `Callable`, optional
        Gives the calibration library, for the bias level.
    drivers : `Drivers`, optional
        The outside services, for the spectrum check.

    Returns
    -------
    report : `FrameQualityReport`
        The checks of the frames, and the metric set if asked.

    Raises
    ------
    InvalidArgumentError
        If the frames mix imaging and spectrum frames, or a section or
        option does not apply to their kind.
    """
    lights = sorted(frames, key=lambda frame: (frame.timestamp is None, frame.timestamp or 0.0))
    spectral_count = sum(1 for frame in lights if frame_is_spectral(frame))
    if 0 < spectral_count < len(lights):
        raise InvalidArgumentError(
            f"These frames mix imaging and spectrum frames ({len(lights) - spectral_count} imaging, "
            f"{spectral_count} spectral). Measure each kind on its own.",
            details={"imaging_frames": len(lights) - spectral_count, "spectral_frames": spectral_count},
        )
    if spectral_count:
        if include:
            raise InvalidArgumentError("include applies to imaging frames only; these are spectrum frames.")
        from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_frame_check import (
            check_spectral_frames,
        )

        spectral = check_spectral_frames(lights, predict_exposure_s, drivers=drivers)
        return FrameQualityReport(
            kind="spectral", frames_measured=spectral.frames_measured, spectral=spectral
        )
    if predict_exposure_s is not None:
        raise InvalidArgumentError("predict_exposure_s applies to spectrum frames only.")
    window = int(resolve(TREND_WINDOW_FRAMES).value) if trend_frames is None else int(trend_frames)
    threshold = (
        float(resolve(TREND_THRESHOLD_PERCENT).value)
        if trend_threshold_pct is None
        else float(trend_threshold_pct)
    )
    checks = _check_imaging_frames(lights, window, threshold)
    metrics = None
    if "metrics" in include:
        from astrometricslib.pipelines.stacking.folder_frame_measurement import measure_frame_metrics
        from astrometricslib.pipelines.stacking.frame_measurement_run import BiasLevelFinder

        bias_finder = None
        if bias_level_adu is None and calibration_library is not None:
            bias_finder = BiasLevelFinder(calibration_library())
        metrics = measure_frame_metrics(
            lights, config, measured_at, bias_level_adu=bias_level_adu, bias_finder=bias_finder
        )
    return FrameQualityReport(
        kind="imaging", frames_measured=checks.frames_checked, checks=checks, metrics=metrics
    )
