"""Tests for the dither step and the pointing drift of a stacking run.

Siril's registration results are given as plain dictionaries with a 3 x 3
transform for each frame. The tests make series with a known drift, a known
dither and known bad transforms, and check the numbers and the rows.
"""

import math
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.registration_record import RegistrationRecord, build_registration_record
from astrometricslib.pipelines.stacking import metrics as stacking_metrics
from astrometricslib.pipelines.stacking.post_processing import registration_shifts
from astrometricslib.pipelines.stacking.post_processing.registration_shifts import (
    MAXIMUM_PLAUSIBLE_STEP_PX,
    MAXIMUM_SLOPE_PAIRS,
    FrameShift,
    ShiftSeries,
    drift_of_runs,
    drift_summary,
    fit_nights,
    pointing_measurements,
    shifts_from_registration,
    step_summary,
)

CONTEXT = PipelineRunContext(
    "M 99", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 10, 23, tzinfo=UTC)
)
# 22:00 local time on 2026-10-03, well inside one observing night in any zone.
NIGHT_ONE_START = time.mktime((2026, 10, 3, 22, 0, 0, 0, 0, -1))
NIGHT_TWO_START = NIGHT_ONE_START + 86400.0
EXPOSURE_GAP_S = 120.0
SIZE = (1000, 800)


def _homography(column_shift: float, row_shift: float, turn_deg: float = 0.0) -> list[float]:
    """Write a transform that moves the image center by a given shift.

    Parameters
    ----------
    column_shift, row_shift : `float`
        The shift of the middle of the image along the columns and along the
        rows, in pixels. Siril's y axis is the rows reversed, so the matrix
        carries the negative of the row shift.
    turn_deg : `float`, optional
        A turn about the middle of the image, in degrees.

    Returns
    -------
    homography : `list` [`float`]
        The nine numbers of the transform.
    """
    angle = math.radians(turn_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    center_x, center_y = SIZE[0] / 2.0, SIZE[1] / 2.0
    # A turn about the center, then the shift: p' = R (p - c) + c + shift.
    shift_x, shift_y = column_shift, -row_shift
    h02 = center_x - cosine * center_x + sine * center_y + shift_x
    h12 = center_y - sine * center_x - cosine * center_y + shift_y
    return [cosine, -sine, h02, sine, cosine, h12, 0.0, 0.0, 1.0]


def _run(
    shifts: list[tuple[float, float]],
    *,
    start: float = NIGHT_ONE_START,
    turn_deg: float = 0.0,
    prefix: str = "f",
) -> tuple[list[str], list[list[float]], dict[str, float]]:
    """Make frames taken 120 s apart with given pointings.

    Parameters
    ----------
    shifts : `list` [`tuple` [`float`, `float`]]
        The (column, row) pointing of each frame.
    start : `float`, optional
        The first frame's capture time.
    turn_deg : `float`, optional
        A turn of every frame about the middle of the image.
    prefix : `str`, optional
        The start of the frame names.

    Returns
    -------
    paths, homographies, timestamps : `tuple`
        The frame paths, their transforms and their capture times.
    """
    paths = [f"/frames/{prefix}{index:03d}.fits" for index in range(len(shifts))]
    homographies = [_homography(column, row, turn_deg) for column, row in shifts]
    timestamps = {path: start + EXPOSURE_GAP_S * index for index, path in enumerate(paths)}
    return paths, homographies, timestamps


def _series(
    shifts: list[tuple[float, float]], *, start: float = NIGHT_ONE_START, turn_deg: float = 0.0
) -> ShiftSeries:
    """Turn a list of pointings into a `ShiftSeries` at the image center.

    Parameters
    ----------
    shifts : `list` [`tuple` [`float`, `float`]]
        The pointings.
    start : `float`, optional
        The first capture time.
    turn_deg : `float`, optional
        A turn of every frame.

    Returns
    -------
    series : `ShiftSeries`
        The pointings.
    """
    paths, homographies, timestamps = _run(shifts, start=start, turn_deg=turn_deg)
    return shifts_from_registration(paths, homographies, [], timestamps, SIZE)


def _drift_line(count: int, column_rate: float, row_rate: float) -> list[tuple[float, float]]:
    """Make pointings that move steadily.

    Parameters
    ----------
    count : `int`
        Number of frames.
    column_rate, row_rate : `float`
        Pixels per exposure along the columns and along the rows.

    Returns
    -------
    shifts : `list` [`tuple` [`float`, `float`]]
        The pointings.
    """
    return [(column_rate * index, row_rate * index) for index in range(count)]


def test_the_pointing_is_taken_at_the_image_center_not_the_corner() -> None:
    """A small turn moves the corner far more than the middle of the image."""
    series = _series([(0.0, 0.0), (0.0, 0.0)], turn_deg=0.01)
    assert series.at_image_center
    for shift in series.shifts:
        assert shift.column_px == pytest.approx(0.0, abs=1e-9)
        assert shift.row_px == pytest.approx(0.0, abs=1e-9)
    paths, homographies, timestamps = _run([(0.0, 0.0), (0.0, 0.0)], turn_deg=0.01)
    corner = shifts_from_registration(paths, homographies, [], timestamps, None)
    assert not corner.at_image_center
    assert abs(corner.shifts[0].column_px) > 0.05


def test_the_row_shift_has_the_sign_of_the_array_not_of_siril() -> None:
    """Siril counts y up, the array counts rows down, so the shifts flip."""
    series = _series([(1.0, 2.0)])
    assert series.shifts[0].column_px == pytest.approx(1.0)
    assert series.shifts[0].row_px == pytest.approx(2.0)


def test_frames_siril_dropped_or_without_a_time_are_left_out() -> None:
    """A dropped frame and a frame with no capture time give no pointing."""
    paths, homographies, timestamps = _run(_drift_line(4, 1.0, 0.0))
    timestamps[paths[1]] = None
    series = shifts_from_registration(paths, homographies, [True, True, False, True], timestamps, SIZE)
    assert [shift.path for shift in series.shifts] == [paths[0], paths[3]]
    assert series.frames_without_time == 1


def test_a_steady_drift_is_a_step_and_not_a_dither() -> None:
    """A pure drift is the step, and nothing is left after removing it."""
    summary = step_summary(_series(_drift_line(30, 0.3, 0.4)))
    assert summary.median_px == pytest.approx(0.5)
    assert summary.jump_count == 0
    assert summary.mad_px == pytest.approx(0.0, abs=1e-9)
    assert summary.used_count == 29
    assert summary.degenerate_count == 0
    assert summary.after_drift_median_px == pytest.approx(0.0, abs=1e-6)


def test_a_dither_remains_after_the_drift_is_taken_out() -> None:
    """Random offsets of a pixel or more stay after the drift line goes."""
    generator = np.random.default_rng(3)
    jitter = generator.normal(0.0, 1.5, size=(40, 2))
    shifts = [(0.3 * i + jitter[i, 0], 0.4 * i + jitter[i, 1]) for i in range(40)]
    summary = step_summary(_series(shifts))
    assert summary.median_px is not None
    assert summary.median_px > 1.5
    assert summary.after_drift_median_px is not None
    assert summary.after_drift_median_px > 1.5


def test_a_step_longer_than_the_limit_is_counted_and_left_out() -> None:
    """A transform of thousands of pixels is degenerate, not a dither."""
    shifts = _drift_line(40, 0.2, 0.0)
    shifts[20] = (5000.0, 0.0)
    summary = step_summary(_series(shifts))
    assert summary.unusable_reason == ""
    assert summary.degenerate_count == 2
    assert summary.used_count == 39 - 2
    assert summary.median_px == pytest.approx(0.2)
    assert MAXIMUM_PLAUSIBLE_STEP_PX == pytest.approx(50.0)


def test_a_step_across_two_nights_is_not_a_dither() -> None:
    """Only pairs inside one observing night are steps."""
    first = _series(_drift_line(10, 0.2, 0.0))
    second = _series(_drift_line(10, 0.2, 0.0), start=NIGHT_TWO_START)
    second_shifts = tuple(
        FrameShift(
            shift.path.replace("/f", "/g"),
            shift.timestamp,
            shift.night,
            shift.column_px + 300.0,
            shift.row_px,
        )
        for shift in second.shifts
    )
    both = ShiftSeries(first.shifts + second_shifts, True)
    summary = step_summary(both)
    assert summary.other_night_count == 1
    assert summary.degenerate_count == 0
    assert summary.used_count == 18


def test_too_few_pairs_give_no_step() -> None:
    """Four pairs are not enough for a median."""
    summary = step_summary(_series(_drift_line(5, 0.2, 0.0)))
    assert summary.median_px is None
    assert summary.jump_count is None
    assert "fewer than 5" in summary.unusable_reason
    assert not summary.is_suspect


def test_a_dither_every_few_frames_is_counted_as_jumps_not_seen_in_the_median() -> None:
    """One step in five is a 6 px dither; the median stays at the drift."""
    shifts: list[tuple[float, float]] = []
    column = 0.0
    for index in range(60):
        column += 6.0 if index % 5 == 0 else 0.3
        shifts.append((column, 0.0))
    summary = step_summary(_series(shifts))
    assert summary.median_px == pytest.approx(0.3)
    assert summary.mad_px == pytest.approx(0.0, abs=1e-9)
    assert summary.jump_count == 11
    assert summary.jump_threshold_px == pytest.approx(0.3 + 5.0 * 0.1)


def test_a_dither_every_ten_frames_is_counted_when_the_90th_percentile_misses_it() -> None:
    """Five 6 px dithers in 60 frames (under 10% of steps) are counted."""
    shifts: list[tuple[float, float]] = []
    column = 0.0
    for index in range(60):
        column += 6.0 if index % 12 == 11 else 0.3
        shifts.append((column, 0.0))
    summary = step_summary(_series(shifts))
    assert summary.jump_count == 5
    assert summary.median_px == pytest.approx(0.3)
    assert float(np.percentile(np.hypot(np.diff([c for c, _ in shifts]), 0.0), 90)) < 1.0


def test_independent_jitter_and_a_pure_drift_have_no_jumps() -> None:
    """The threshold sits far above the steps of jitter, drift and both."""
    generator = np.random.default_rng(21)
    total = 0
    for _ in range(200):
        jitter = generator.normal(0.0, 0.3, size=(60, 2))
        shifts = [(0.2 * i + jitter[i, 0], jitter[i, 1]) for i in range(60)]
        total += step_summary(_series(shifts)).jump_count or 0
    assert total <= 5
    assert step_summary(_series(_drift_line(40, 0.3, 0.4))).jump_count == 0


def test_a_run_with_many_degenerate_pairs_is_refused_and_counted() -> None:
    """More than a tenth of the pairs above 50 px: not pointings."""
    shifts = _drift_line(40, 0.2, 0.0)
    for index in range(2, 40, 5):
        shifts[index] = (4000.0 + index, 0.0)
    summary = step_summary(_series(shifts))
    assert summary.is_suspect
    assert summary.median_px is None
    assert summary.jump_count is None
    assert summary.degenerate_count > 0.1 * (summary.degenerate_count + summary.used_count)
    assert "longer than 50 px" in summary.unusable_reason


def test_a_run_of_mostly_identity_transforms_is_refused() -> None:
    """Most frames with the identity transform were not registered."""
    shifts = [(0.0, 0.0)] * 30 + _drift_line(10, 0.3, 0.0)
    summary = step_summary(_series(shifts))
    assert summary.is_suspect
    assert summary.median_px is None
    assert "identity" in summary.unusable_reason


def test_the_nights_are_fitted_once_when_the_fits_are_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handing the fits to both summaries fits each night one time."""
    series = _series(_drift_line(30, 0.3, 0.4))
    calls: list[int] = []
    real = registration_shifts._fit_night

    def counting(frames: object) -> object:
        """Fit a night and count the call.

        Returns
        -------
        fit : `object`
            What `_fit_night` gives.
        """
        calls.append(1)
        return real(frames)

    monkeypatch.setattr(registration_shifts, "_fit_night", counting)
    fits = fit_nights(series)
    step_summary(series, fits)
    drift_summary(series, fits)
    assert len(calls) == 1


def test_a_long_night_uses_a_bounded_sample_of_pairs_and_the_same_line() -> None:
    """Above the pair limit the robust line comes from a fixed sample."""
    count = 500
    assert count * (count - 1) // 2 > MAXIMUM_SLOPE_PAIRS
    times = np.arange(count) / 60.0
    values = 0.5 * times + np.random.default_rng(2).normal(0.0, 0.1, count)
    values[10] = 3000.0
    residuals = registration_shifts._robust_residuals(times, values)
    assert abs(float(np.median(residuals))) < 0.1
    assert residuals[10] > 2900.0
    again = registration_shifts._robust_residuals(times, values)
    assert np.array_equal(residuals, again)


def test_the_drift_axis_and_rate_follow_the_pointing() -> None:
    """A drift of (3, 4) px per exposure has axis atan2(4, 3), 5 px each."""
    summary = drift_summary(_series(_drift_line(30, 3.0, 4.0)))
    assert summary.direction_deg == pytest.approx(math.degrees(math.atan2(4.0, 3.0)))
    assert summary.rate_px_per_hour == pytest.approx(5.0 * 3600.0 / EXPOSURE_GAP_S)
    assert summary.extent_px == pytest.approx(5.0 * 29)
    assert summary.direction_reason == ""


def test_the_axis_does_not_depend_on_the_sign_of_the_drift() -> None:
    """The same line walked backward has the same axis."""
    forward = drift_summary(_series(_drift_line(30, 1.0, 2.0)))
    backward = drift_summary(_series(_drift_line(30, -1.0, -2.0)))
    assert forward.direction_deg == pytest.approx(backward.direction_deg)
    assert 0.0 <= backward.direction_deg < 180.0


def test_a_drift_lost_in_the_noise_gives_no_axis() -> None:
    """With no drift the axis would be set by noise, so none is named."""
    generator = np.random.default_rng(5)
    noise = generator.normal(0.0, 0.5, size=(40, 2))
    summary = drift_summary(_series([(float(a), float(b)) for a, b in noise]))
    assert summary.direction_deg is None
    assert "noise" in summary.direction_reason
    assert summary.extent_px is None
    assert summary.rate_px_per_hour is not None


def test_nights_that_drift_along_different_axes_give_no_axis() -> None:
    """A night along the columns and a night along the rows do not agree."""
    first = _series(_drift_line(20, 2.0, 0.0))
    second = _series(_drift_line(20, 0.0, 2.0), start=NIGHT_TWO_START)
    renamed = tuple(
        FrameShift(
            shift.path.replace("/f", "/g"), shift.timestamp, shift.night, shift.column_px, shift.row_px
        )
        for shift in second.shifts
    )
    summary = drift_summary(ShiftSeries(first.shifts + renamed, True))
    assert summary.direction_deg is None
    assert "disagree" in summary.direction_reason
    assert len(summary.nights) == 2


def test_opposite_drifts_on_two_nights_share_one_axis() -> None:
    """A night one way and a night the other way agree on the axis."""
    first = _series(_drift_line(20, 2.0, 1.0))
    second = _series(_drift_line(20, -2.0, -1.0), start=NIGHT_TWO_START)
    renamed = tuple(
        FrameShift(
            shift.path.replace("/f", "/g"), shift.timestamp, shift.night, shift.column_px, shift.row_px
        )
        for shift in second.shifts
    )
    summary = drift_summary(ShiftSeries(first.shifts + renamed, True))
    assert summary.direction_deg == pytest.approx(math.degrees(math.atan2(1.0, 2.0)))


def test_a_bad_transform_does_not_tilt_the_drift_line() -> None:
    """One frame thousands of pixels away is dropped from the fit."""
    shifts = _drift_line(30, 1.0, 0.0)
    shifts[15] = (4000.0, 3000.0)
    summary = drift_summary(_series(shifts))
    assert summary.direction_deg == pytest.approx(0.0, abs=1e-6)
    assert summary.nights[0].frame_count == 29


def test_a_rate_lost_in_the_noise_says_it_is_biased_upward() -> None:
    """A zero drift still has a positive length; the summary warns of it."""
    generator = np.random.default_rng(8)
    noise = generator.normal(0.0, 0.5, size=(40, 2))
    summary = drift_summary(_series([(float(a), float(b)) for a, b in noise]))
    assert summary.rate_px_per_hour is not None
    assert "too large" in summary.rate_reason
    clean = drift_summary(_series(_drift_line(30, 3.0, 4.0)))
    assert clean.rate_reason == ""


def test_a_night_with_four_frames_is_not_fitted() -> None:
    """Fewer than five frames leave no degrees of freedom to judge the line."""
    summary = drift_summary(_series(_drift_line(4, 1.0, 0.0)))
    assert summary.rate_px_per_hour is None
    assert summary.direction_deg is None
    assert summary.nights == ()


def test_the_halves_of_two_runs_count_a_shared_frame_once() -> None:
    """The reference frame in both half runs gives one pointing."""
    paths, homographies, timestamps = _run(_drift_line(12, 1.0, 0.0))
    first = _record([*paths[0::2], paths[1]], [*(homographies[i] for i in range(0, 12, 2)), homographies[1]])
    second = _record(paths[1::2], [homographies[i] for i in range(1, 12, 2)])
    summary = drift_of_runs([first, None, second], timestamps, SIZE)
    assert summary.nights[0].frame_count == 12
    assert summary.direction_deg == pytest.approx(0.0, abs=1e-6)


def _frames(paths: list[str], timestamps: dict[str, float]) -> list[SimpleNamespace]:
    """Make frame records.

    Parameters
    ----------
    paths : `list` [`str`]
        The frame paths.
    timestamps : `dict` [`str`, `float`]
        Their capture times.

    Returns
    -------
    frames : `list` of `SimpleNamespace`
        Records with a path and a timestamp.
    """
    return [SimpleNamespace(path=path, timestamp=timestamps[path]) for path in paths]


def _record(
    paths: list[str],
    homographies: list[list[float]],
    *,
    is_spectral: bool = False,
    reference: str | None = None,
) -> RegistrationRecord | None:
    """Make the registration record of a run.

    Parameters
    ----------
    paths : `list` [`str`]
        The frame paths.
    homographies : `list` [`list` [`float`]]
        Their transforms.
    is_spectral : `bool`, optional
        Whether the stack is spectral.
    reference : `str`, optional
        The registration reference frame.

    Returns
    -------
    record : `RegistrationRecord` or `None`
        The record; `None` when the lists differ in length.
    """
    diagnostics = {
        "symlinked_light_paths": paths,
        "registration_frames": [
            {"dx": h[2], "dy": h[5], "star_count": 100, "homography": h} for h in homographies
        ],
        "registration_included": [True] * len(paths),
    }
    return build_registration_record(diagnostics, (0, 0), reference, is_spectral=is_spectral)


def _by_metric(rows: list[QualityMeasurement]) -> dict[str, QualityMeasurement]:
    """Index rows by metric name.

    Parameters
    ----------
    rows : `list` of `QualityMeasurement`
        The rows.

    Returns
    -------
    index : `dict` [`str`, `QualityMeasurement`]
        The rows by name.
    """
    return {row.metric: row for row in rows}


def test_the_rows_cover_all_eight_metrics_on_the_stack_subject() -> None:
    """A drifting, undithered run gives all eight values."""
    paths, homographies, timestamps = _run(_drift_line(30, 0.3, 0.4))
    rows = pointing_measurements(
        _frames(paths, timestamps), _record(paths, homographies), CONTEXT, "M99_Stacked.fits"
    )
    by_metric = _by_metric(rows)
    assert len(rows) == 8
    assert all(row.subject.subject_id == "M 99/M99_Stacked.fits" for row in rows)
    assert all(row.status == MeasurementStatus.MEASURED for row in rows)
    median = by_metric[stacking_metrics.STACKING_REGISTRATION_STEP_MEDIAN_PX]
    assert median.value == pytest.approx(0.5)
    assert median.sample_count == 29
    assert by_metric[stacking_metrics.STACKING_REGISTRATION_STEP_JUMP_COUNT].value == 0
    assert by_metric[stacking_metrics.STACKING_REGISTRATION_STEP_DEGENERATE_COUNT].value == 0
    drift_rate = by_metric[stacking_metrics.STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR]
    assert drift_rate.value == pytest.approx(0.5 * 3600.0 / EXPOSURE_GAP_S)
    assert "corner" in (drift_rate.reason or "")
    assert "too large" not in (drift_rate.reason or "")


def test_a_drift_rate_below_its_error_says_the_noise_biases_it() -> None:
    """The rate row warns of the upward bias, not of the axis."""
    generator = np.random.default_rng(5)
    noise = generator.normal(0.0, 0.5, size=(40, 2))
    paths, homographies, timestamps = _run([(float(a), float(b)) for a, b in noise])
    rows = pointing_measurements(
        _frames(paths, timestamps), _record(paths, homographies), CONTEXT, "M99_Stacked.fits"
    )
    by_metric = _by_metric(rows)
    rate = by_metric[stacking_metrics.STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR]
    direction = by_metric[stacking_metrics.STACKING_REGISTRATION_DRIFT_DIRECTION_DEG]
    assert rate.status == MeasurementStatus.MEASURED
    assert "too large" in (rate.reason or "")
    assert "axis would be set by noise" not in (rate.reason or "")
    assert direction.status == MeasurementStatus.NOT_MEASURED
    assert "axis would be set by noise" in (direction.reason or "")


def test_a_run_already_registered_is_not_measured() -> None:
    """When every transform is the identity, the pointings say nothing."""
    paths, _, timestamps = _run(_drift_line(10, 0.0, 0.0))
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    rows = pointing_measurements(
        _frames(paths, timestamps), _record(paths, [identity] * 10), CONTEXT, "M99_Stacked.fits"
    )
    assert len(rows) == 8
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("registered before" in (row.reason or "") for row in rows)


def test_registration_results_that_do_not_match_the_frames_are_not_measured() -> None:
    """No record means every row says why."""
    paths, homographies, timestamps = _run(_drift_line(10, 0.3, 0.0))
    assert _record(paths, homographies[:-1]) is None
    rows = pointing_measurements(_frames(paths, timestamps), None, CONTEXT, "M99_Stacked.fits")
    assert len(rows) == 8
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("could not be matched" in (row.reason or "") for row in rows)
    rows = pointing_measurements(
        _frames(paths, timestamps), None, CONTEXT, "M99_Stacked.fits", missing_reason="several groups"
    )
    assert all(row.reason == "several groups" for row in rows)


def test_a_missing_transform_gives_no_record() -> None:
    """A frame without its transform gives no record."""
    paths, homographies, _ = _run(_drift_line(10, 0.3, 0.0))
    diagnostics = {
        "symlinked_light_paths": paths,
        "registration_frames": [{"homography": h} for h in homographies],
        "registration_included": [True] * 10,
    }
    del diagnostics["registration_frames"][3]["homography"]
    assert build_registration_record(diagnostics, (0, 0), None) is None


def test_too_few_steps_give_reasons_and_the_drift_rows_are_still_tried() -> None:
    """Four frames give no step and no fit; every row says so."""
    paths, homographies, timestamps = _run(_drift_line(4, 0.3, 0.0))
    rows = pointing_measurements(
        _frames(paths, timestamps), _record(paths, homographies), CONTEXT, "M99_Stacked.fits"
    )
    by_metric = _by_metric(rows)
    median = by_metric[stacking_metrics.STACKING_REGISTRATION_STEP_MEDIAN_PX]
    assert median.status == MeasurementStatus.NOT_MEASURED
    assert "fewer than 5" in (median.reason or "")
    assert (
        by_metric[stacking_metrics.STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR].status
        == MeasurementStatus.NOT_MEASURED
    )
    assert all(row.reason for row in rows)


def test_a_dithered_run_with_no_drift_has_a_step_but_no_axis() -> None:
    """Random offsets give a step and a rate but no drift axis."""
    generator = np.random.default_rng(11)
    offsets = generator.normal(0.0, 2.0, size=(40, 2))
    paths, homographies, timestamps = _run([(float(a), float(b)) for a, b in offsets])
    rows = pointing_measurements(
        _frames(paths, timestamps), _record(paths, homographies), CONTEXT, "M99_Stacked.fits"
    )
    by_metric = _by_metric(rows)
    assert by_metric[stacking_metrics.STACKING_REGISTRATION_STEP_MEDIAN_PX].value > 1.5
    assert (
        by_metric[stacking_metrics.STACKING_REGISTRATION_DRIFT_DIRECTION_DEG].status
        == MeasurementStatus.NOT_MEASURED
    )
    assert "noise" in (by_metric[stacking_metrics.STACKING_REGISTRATION_DRIFT_DIRECTION_DEG].reason or "")


def test_a_suspect_run_has_no_step_rows_but_keeps_its_degenerate_count() -> None:
    """Many pairs above 50 px: step rows refused, count kept."""
    shifts = _drift_line(40, 0.2, 0.0)
    for index in range(2, 40, 5):
        shifts[index] = (4000.0 + index, 0.0)
    paths, homographies, timestamps = _run(shifts)
    rows = pointing_measurements(
        _frames(paths, timestamps), _record(paths, homographies), CONTEXT, "M99_Stacked.fits"
    )
    by_metric = _by_metric(rows)
    for name in (
        stacking_metrics.STACKING_REGISTRATION_STEP_MEDIAN_PX,
        stacking_metrics.STACKING_REGISTRATION_STEP_MAD_PX,
        stacking_metrics.STACKING_REGISTRATION_STEP_JUMP_COUNT,
        stacking_metrics.STACKING_REGISTRATION_STEP_AFTER_DRIFT_MEDIAN_PX,
    ):
        assert by_metric[name].status == MeasurementStatus.NOT_MEASURED
        assert "not pointings" in (by_metric[name].reason or "")
    count = by_metric[stacking_metrics.STACKING_REGISTRATION_STEP_DEGENERATE_COUNT]
    assert count.status == MeasurementStatus.MEASURED
    assert count.value == pytest.approx(16.0)


def test_a_spectral_stack_registered_by_shift_has_pointing_rows() -> None:
    """A spectral stack, registered by shift only, has pointing rows."""
    paths, homographies, timestamps = _run(_drift_line(30, 0.3, 0.4))
    record = _record(paths, homographies, is_spectral=True)
    assert record is not None and record.is_spectral
    rows = pointing_measurements(_frames(paths, timestamps), record, CONTEXT, "M99_Stacked.fits")
    assert len(rows) == 8
    assert all(row.status == MeasurementStatus.MEASURED for row in rows)
    median = _by_metric(rows)[stacking_metrics.STACKING_REGISTRATION_STEP_MEDIAN_PX]
    assert median.value == pytest.approx(0.5)
    assert "spectral stack, registered by shift only" in (median.reason or "")
