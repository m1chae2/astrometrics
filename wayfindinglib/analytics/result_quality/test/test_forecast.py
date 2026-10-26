"""Purpose: Tests for the forecast check and the stop signal.

Description: Builds made-up histories of stack runs with known noise and
weights, and checks that the three numbers (planned, given the frames taken,
measured), their tolerance and the outcome come out as the plan describes.
Covers equal weights, a moonlit night under equal weights, star-width
weights, an unknown weighting mode, a missing noise value, a paired
tolerance, the run grouping, the stop signal, and the pinned metric versions.
"""

from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib import (
    Astrometrics,
    CaptureSetting,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from wayfindinglib.analytics.result_quality import forecast as fc
from wayfindinglib.analytics.result_quality.efficiency import FrameObservation
from wayfindinglib.analytics.result_quality.noise_growth import (
    StackFrame,
    WeightingMode,
    fit_noise_floor,
    forecast_stack_noise,
    frame_weights,
)
from wayfindinglib.models.planning.target_report import ForecastOutcome

SETTING = CaptureSetting(camera="CamA", telescope="Scope", filter_name="L", exposure_seconds=60.0)
START = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
APERTURES = 1000


def _observation(
    frame_id: str,
    sky: float = 1.0,
    width: float = 3.0,
    exposure: float = 60.0,
    read_noise: float | None = 3.0,
) -> FrameObservation:
    """Build the measurements of one frame.

    Parameters
    ----------
    frame_id : `str`
        The frame.
    sky : `float`, optional
        Sky rate in electrons per second per pixel.
    width : `float`, optional
        Effective star width in arcseconds.
    exposure : `float`, optional
        Exposure in seconds.
    read_noise : `float` or `None`, optional
        Read noise in electrons.

    Returns
    -------
    observation : `FrameObservation`
        The frame.
    """
    return FrameObservation(
        frame_id=frame_id,
        capture_setting=SETTING,
        exposure_seconds=exposure,
        star_width_effective_arcsec=width,
        sky_rate_e_per_s_per_px=sky,
        read_noise_e=read_noise,
    )


def _frames(observations: list[FrameObservation]) -> list[StackFrame]:
    """Turn observations into the forward model's frames.

    Parameters
    ----------
    observations : `list` [`FrameObservation`]
        The frames.

    Returns
    -------
    frames : `list` [`StackFrame`]
        The model frames.
    """
    return [
        StackFrame(
            frame_id=o.frame_id,
            exposure_seconds=o.exposure_seconds or 0.0,
            sky_rate_e_per_s_per_px=o.sky_rate_e_per_s_per_px or 0.0,
            read_noise_e=o.read_noise_e or 0.0,
            star_width=o.star_width_effective_arcsec,
        )
        for o in observations
    ]


def _row(
    metric: str,
    subject_id: str,
    value: float | None,
    run_id: str,
    when: datetime,
    *,
    level: SubjectLevel = SubjectLevel.STACK,
    version: int = 3,
    uncertainty: float | None = None,
    sample_count: int | None = None,
    row_id: int | None = None,
    variant: str | None = None,
) -> QualityMeasurement:
    """Build one stored row.

    Returns
    -------
    row : `QualityMeasurement`
        A measured row, or a not-measured row when `value` is `None`.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=version,
        subject=QualitySubject(level=level, subject_id=subject_id, target_id="T", variant=variant),
        status=MeasurementStatus.MEASURED if value is not None else MeasurementStatus.NOT_MEASURED,
        value=value,
        uncertainty=uncertainty,
        sample_count=sample_count,
        reason="" if value is not None else "not measured in the test",
        origin=MeasurementOrigin.MEASURED,
        run_id=run_id,
        measured_at=when,
        row_id=row_id,
    )


def _run_rows(
    run_id: str,
    day: int,
    members: list[str],
    weights: list[float | None],
    normalized: float | None,
    expected_noise: float = 1.0,
    row_id: int = 1,
) -> list[QualityMeasurement]:
    """Build the rows one stack run stores.

    Parameters
    ----------
    run_id : `str`
        The run.
    day : `int`
        Days after the start, for the time.
    members : `list` [`str`]
        The member frames.
    weights : `list` [`float` or `None`]
        The recorded weights.
    normalized : `float` or `None`
        The normalized noise.
    expected_noise : `float`, optional
        The expected noise in stack units.
    row_id : `int`, optional
        The row id of the normalized noise.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The stack and frame rows.
    """
    when = START + timedelta(days=day)
    stack = f"T/stack_{run_id}"
    rows = [
        _row(
            fc.NORMALIZED_NOISE_METRIC, stack, normalized, run_id, when, sample_count=APERTURES, row_id=row_id
        ),
        _row(
            fc.NOISE_METRIC,
            stack,
            None if normalized is None else normalized * 1000.0,
            run_id,
            when,
            sample_count=APERTURES,
            row_id=row_id + 1000,
        ),
        _row(
            fc.EXPECTED_NOISE_METRIC,
            stack,
            expected_noise,
            run_id,
            when,
            version=fc.PINNED_STACK_METRICS[fc.EXPECTED_NOISE_METRIC],
        ),
    ]
    rows += [
        _row(fc.FRAME_WEIGHT_METRIC, member, weight, run_id, when, level=SubjectLevel.FRAME, version=1)
        for member, weight in zip(members, weights, strict=True)
    ]
    return rows


def _history(
    old: list[FrameObservation],
    new: list[FrameObservation],
    mode: WeightingMode,
    measured_ratio: float | None = None,
    normalized_before: float = 0.010,
) -> tuple[list[fc.StackRunRecord], list[FrameObservation]]:
    """Build two runs: the old frames, then the old plus the new frames.

    Parameters
    ----------
    old, new : `list` [`FrameObservation`]
        The frames of the first run and the frames added later.
    mode : `WeightingMode`
        The mode the stacker used for both runs.
    measured_ratio : `float` or `None`, optional
        The measured noise ratio of the second run over the first. When
        ``None``, the forward model's own ratio is used (a perfect forecast).
    normalized_before : `float`, optional
        The first run's normalized noise.

    Returns
    -------
    runs, observations : `tuple`
        The run records and the frames' measurements.
    """
    everyone = old + new
    if measured_ratio is None:
        before = forecast_stack_noise(_frames(old), mode).noise
        after = forecast_stack_noise(_frames(everyone), mode).noise
        measured_ratio = after / before
    first_weights = frame_weights(_frames(old), mode)
    second_weights = frame_weights(_frames(everyone), mode)
    rows = _run_rows("run1", 0, [o.frame_id for o in old], first_weights, normalized_before)
    rows += _run_rows(
        "run2",
        5,
        [o.frame_id for o in everyone],
        second_weights,
        normalized_before * measured_ratio,
        row_id=2,
    )
    return fc.stack_runs_from_measurements(rows), everyone


def _dark(count: int, prefix: str, sky: float = 1.0, width: float = 3.0) -> list[FrameObservation]:
    """Build frames of a dark night.

    Returns
    -------
    frames : `list` [`FrameObservation`]
        `count` frames.
    """
    return [_observation(f"{prefix}{i}", sky=sky, width=width) for i in range(count)]


def test_equal_frames_give_the_planned_gain() -> None:
    """Doubling identical frames under equal weights cuts the noise by 29%."""
    runs, observations = _history(_dark(20, "a"), _dark(20, "b"), WeightingMode.EQUAL)

    (check,) = fc.forecast_check(runs, observations, configured_stack_weight=None)

    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.forecast_when_planned.noise_change_fraction == pytest.approx(-0.2929, abs=1e-3)
    assert check.forecast_given_frames_taken.noise_change_fraction == pytest.approx(-0.2929, abs=1e-3)
    assert check.measured.noise_change_fraction == pytest.approx(-0.2929, abs=1e-3)
    assert check.weighting_mode == "equal"
    assert not check.paired
    assert [reference.row_id for reference in check.evidence] == [1, 2]


def test_tolerance_is_the_unpaired_aperture_error() -> None:
    """With 1000 apertures each, the ratio error is 1.17/sqrt(1000) twice."""
    runs, observations = _history(_dark(20, "a"), _dark(20, "b"), WeightingMode.EQUAL)

    (check,) = fc.forecast_check(runs, observations, configured_stack_weight=None)

    ratio = 0.7071
    expected = ratio * (1.17 / 1000**0.5) * 2**0.5
    assert check.measured.tolerance == pytest.approx(expected, rel=0.02)
    assert check.forecast_when_planned.tolerance == check.measured.tolerance


def test_paired_error_replaces_the_unpaired_one() -> None:
    """A paired relative error is used and the check says it is paired."""
    runs, observations = _history(_dark(20, "a"), _dark(20, "b"), WeightingMode.EQUAL)

    (check,) = fc.forecast_check(
        runs, observations, paired_relative_error_by_run={"run2": 0.05}, configured_stack_weight=None
    )

    assert check.paired
    assert check.measured.tolerance == pytest.approx(0.7071 * 0.05, rel=0.02)


def test_moonlit_frames_under_equal_weights_are_a_known_reason() -> None:
    """Bright-sky new frames cut the gain; the equal weights show."""
    old = _dark(20, "a")
    moonlit = _dark(20, "b", sky=6.0)
    runs, observations = _history(old, moonlit, WeightingMode.EQUAL)

    (check,) = fc.forecast_check(runs, observations, configured_stack_weight=None)

    assert check.outcome == ForecastOutcome.SHORT_KNOWN_REASON
    planned = check.forecast_when_planned.noise_change_fraction
    given = check.forecast_given_frames_taken.noise_change_fraction
    assert planned is not None and given is not None
    assert given > planned
    assert "sky rate" in check.reason
    assert "equal weight" in check.reason


def test_a_shortfall_the_frames_do_not_explain_is_unknown() -> None:
    """Same-quality frames that gained little leave the cause unknown."""
    runs, observations = _history(_dark(20, "a"), _dark(20, "b"), WeightingMode.EQUAL, measured_ratio=0.97)

    (check,) = fc.forecast_check(runs, observations, configured_stack_weight=None)

    assert check.outcome == ForecastOutcome.SHORT_UNKNOWN_REASON
    assert "not known" in check.reason


def test_a_better_than_planned_gain_counts_as_forecast() -> None:
    """A noise that fell further than planned does not raise a warning."""
    runs, observations = _history(_dark(20, "a"), _dark(20, "b"), WeightingMode.EQUAL, measured_ratio=0.5)

    (check,) = fc.forecast_check(runs, observations, configured_stack_weight=None)

    assert check.outcome == ForecastOutcome.AS_FORECAST


def test_star_width_weights_are_modeled() -> None:
    """The wfwhm mode is recognized from the recorded weights and forecast."""
    old = [_observation(f"a{i}", width=2.5 + 0.1 * i) for i in range(10)]
    new = [_observation(f"b{i}", width=2.6 + 0.1 * i) for i in range(10)]
    runs, observations = _history(old, new, WeightingMode.WFWHM)

    (check,) = fc.forecast_check(runs, observations, configured_stack_weight=None)

    assert check.weighting_mode == "wfwhm"
    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.measured.noise_change_fraction == pytest.approx(
        check.forecast_given_frames_taken.noise_change_fraction, abs=1e-6
    )


def test_a_weighting_mode_that_was_not_recorded_is_not_checkable() -> None:
    """Weights that were not measured give no forecast, and say why."""
    old, new = _dark(10, "a"), _dark(10, "b")
    rows = _run_rows("run1", 0, [o.frame_id for o in old], [None] * 10, 0.01)
    rows += _run_rows("run2", 5, [o.frame_id for o in old + new], [None] * 20, 0.007, row_id=2)
    runs = fc.stack_runs_from_measurements(rows)

    (check,) = fc.forecast_check(runs, old + new, configured_stack_weight=None)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "weights were not recorded" in check.reason
    assert check.forecast_given_frames_taken.noise_change_fraction is None
    assert check.measured.noise_change_fraction == pytest.approx(-0.3)


def test_recorded_weights_are_used_directly_even_when_no_mode_matches() -> None:
    """Odd recorded weights give the forecast given the frames taken.

    The old code needed the weights to match a known mode, else it gave up.
    """
    old, new = _dark(10, "a"), _dark(10, "b")
    uneven = [1.0 + 0.3 * (i % 4) for i in range(20)]
    rows = _run_rows("run1", 0, [o.frame_id for o in old], uneven[:10], 0.01)
    rows += _run_rows("run2", 5, [o.frame_id for o in old + new], uneven, 0.0071, row_id=2)
    runs = fc.stack_runs_from_measurements(rows)

    (check,) = fc.forecast_check(runs, old + new, configured_stack_weight=None)

    assert check.outcome != ForecastOutcome.NOT_CHECKABLE
    assert check.weighting_mode == "recorded_weights"
    assert check.forecast_given_frames_taken.noise_change_fraction is not None


def test_missing_normalized_noise_is_not_checkable() -> None:
    """A stack whose noise was not measured gives a check that says so."""
    old, new = _dark(10, "a"), _dark(10, "b")
    rows = _run_rows("run1", 0, [o.frame_id for o in old], [1.0] * 10, 0.01)
    rows += _run_rows("run2", 5, [o.frame_id for o in old + new], [1.0] * 20, None, row_id=2)

    (check,) = fc.forecast_check(
        fc.stack_runs_from_measurements(rows), old + new, configured_stack_weight=None
    )

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "run2" in check.reason


def test_a_frame_without_read_noise_is_not_checkable() -> None:
    """A frame with no read noise cannot go into the forward model."""
    old = _dark(10, "a")
    new = [_observation(f"b{i}", read_noise=None) for i in range(10)]
    runs, observations = _history(_dark(10, "a"), _dark(10, "b"), WeightingMode.EQUAL)

    (check,) = fc.forecast_check(runs, old + new, configured_stack_weight=None)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "read noise" in check.reason
    assert observations


def test_fewer_than_two_runs_have_nothing_to_check() -> None:
    """One run has no forecast to compare with."""
    rows = _run_rows("run1", 0, ["a0", "a1"], [1.0, 1.0], 0.01)

    assert (
        fc.forecast_check(fc.stack_runs_from_measurements(rows), _dark(2, "a"), configured_stack_weight=None)
        == []
    )


def test_runs_ignore_subset_stacks_and_old_definitions() -> None:
    """A subset stack's rows and rows of another definition are not used."""
    rows = _run_rows("run1", 0, ["a0", "a1"], [1.0, 1.0], 0.01)
    rows.append(
        _row(fc.NORMALIZED_NOISE_METRIC, "T/subset", 0.02, "subset_run", START, variant="without_night_1")
    )
    old_definition = _run_rows("run2", 1, ["a0"], [1.0], 0.5)
    old_definition = [
        row.model_copy(update={"definition_version": 2}) if row.metric == fc.NORMALIZED_NOISE_METRIC else row
        for row in old_definition
    ]

    runs = fc.stack_runs_from_measurements(rows + old_definition)

    assert [run.run_id for run in runs] == ["run1", "run2"]
    assert runs[0].member_frame_ids == ["a0", "a1"]
    assert runs[0].normalized_noise == pytest.approx(0.01)
    assert runs[1].normalized_noise is None
    assert "version" in runs[1].problems["normalized_noise"]


def _noise_runs(measured_noise: list[float], expected_noise: list[float]) -> list[fc.StackRunRecord]:
    """Build runs for the floor fit.

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        One run for each measured and expected noise.
    """
    rows = []
    for index, (measured, expected) in enumerate(zip(measured_noise, expected_noise, strict=True)):
        rows += _run_rows(
            f"r{index}", index, [f"f{index}"], [1.0], measured / 1000.0, expected_noise=expected
        )
    return fc.stack_runs_from_measurements(rows)


def test_stop_signal_is_raised_by_a_floor_over_several_steps() -> None:
    """Noise that levels off while the model says it should fall is a stop."""
    expected = [8.0, 4.0, 2.0, 1.0]
    # measured variance = expected variance + a floor of 36.
    measured = [(e**2 + 36.0) ** 0.5 for e in expected]

    signal = fc.target_stop_signal(_noise_runs(measured, expected))

    assert signal.stopped is True
    assert signal.predicted_gain_fraction is not None


def test_stop_signal_is_absent_when_the_history_is_too_short() -> None:
    """One step never makes a floor, so there is no signal."""
    signal = fc.target_stop_signal(_noise_runs([5.0], [4.0]))

    assert signal.stopped is None


def test_stop_signal_says_so_with_no_noise_at_all() -> None:
    """A target without any measured noise has no signal and says why."""
    assert fc.target_stop_signal([]).stopped is None


def test_pinned_versions_match_the_registry() -> None:
    """The registry has not moved past a version this module reads."""
    registry = {spec.name: spec for spec in Astrometrics().quality.metrics(detail="full")}
    for metric, version in fc.PINNED_STACK_METRICS.items():
        assert registry[metric].definition_version == version, metric


def test_floor_fit_divides_each_stack_by_its_reference_flux() -> None:
    """Stacks with different pixel scales are fitted on the normalized scale.

    Two runs have the same true noise but different stack units (flux 1000
    and 3000). Without the division the second would look three times
    noisier and the fit would invent a floor.
    """
    runs = []
    for index, (flux, expected) in enumerate([(1000.0, 8.0), (3000.0, 4.0), (1000.0, 2.0), (3000.0, 1.0)]):
        base = (expected**2 + 36.0) ** 0.5
        runs.append(
            fc.StackRunRecord(
                run_id=f"r{index}",
                stack_subject_id=f"s{index}",
                measured_at=datetime(2026, 10, 1 + index, tzinfo=UTC),
                noise=base * flux / 1000.0,
                expected_noise=expected * flux / 1000.0,
                reference_flux=flux,
                aperture_count=400,
            )
        )
    steps = fc.noise_steps_from_runs(runs)
    assert all(step.normalized for step in steps)
    fit = fit_noise_floor(steps)
    assert fit.normalized
    assert fit.floor_variance == pytest.approx(36.0 / 1000.0**2, rel=1e-3)
    without = [run.model_copy(update={"reference_flux": None}) for run in runs]
    assert not any(step.normalized for step in fc.noise_steps_from_runs(without))
