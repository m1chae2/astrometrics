"""Purpose: Tests for the forecast check and the stop signal.

Description: Builds made-up histories of stack runs with known noise and
weights, and checks that the three numbers (planned, given the frames taken,
measured), their tolerance and the outcome come out as the plan describes.
Covers equal weights, a moonlit night under equal weights, star-width
weights, an unknown weighting mode, a missing noise value, a paired
tolerance, the run grouping, the stop signal, and the pinned metric versions.

Part 2b adds the rules that keep a confounded pair out of the check (aperture
set, calibration, software version, heaviest exposure group, old frames), the
group-mode pair check, and the noise floor applied to the forecast (with the
M 27, M 81 and M 57 validation numbers).
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
    aperture_identity_to_number,
    fold_exposure_lengths,
)
from astrometricslib.models.quality.calibration_identities import (
    CalibrationFileIdentity,
    CalibrationIdentities,
)
from wayfindinglib.analytics.result_quality import forecast as fc
from wayfindinglib.analytics.result_quality.efficiency import FrameObservation
from wayfindinglib.analytics.result_quality.noise_growth import (
    StackFrame,
    StackGroup,
    WeightingMode,
    fit_noise_floor,
    forecast_group_noise,
    forecast_stack_noise,
    frame_weights,
    group_coefficients,
    measure_stack_noise_floor,
    noise_ratio_with_floor,
)
from wayfindinglib.models.planning.target_report import ForecastOutcome

SETTING = CaptureSetting(camera="CamA", telescope="Scope", filter_name="L", exposure_seconds=60.0)
START = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
APERTURES = 1000
APERTURE_SET = "v3-156104df543d"
OTHER_APERTURE_SET = "v3-aaaaaaaaaaaa"
OLD_FRAME_EPOCH = (START - timedelta(days=1)).timestamp()
NEW_FRAME_EPOCH = (START + timedelta(days=2)).timestamp()


def _observation(
    frame_id: str,
    sky: float = 1.0,
    width: float = 3.0,
    exposure: float = 60.0,
    read_noise: float | None = 3.0,
    start_epoch_seconds: float | None = None,
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
    start_epoch_seconds : `float` or `None`, optional
        When the exposure began. Frames whose id starts with ``a`` were taken
        before the first stack was made, all others between the two stacks.

    Returns
    -------
    observation : `FrameObservation`
        The frame.
    """
    if start_epoch_seconds is None:
        start_epoch_seconds = OLD_FRAME_EPOCH if frame_id.startswith("a") else NEW_FRAME_EPOCH
    return FrameObservation(
        frame_id=frame_id,
        capture_setting=SETTING,
        exposure_seconds=exposure,
        star_width_effective_arcsec=width,
        sky_rate_e_per_s_per_px=sky,
        read_noise_e=read_noise,
        start_epoch_seconds=start_epoch_seconds,
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
    expected_noise: float | None = None,
    row_id: int = 1,
    aperture_identity: str | None = APERTURE_SET,
    apertures: int = APERTURES,
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
    expected_noise : `float` or `None`, optional
        The expected noise in stack units. When omitted it equals the
        measured noise (the stack's noise is all random: no noise floor).
    row_id : `int`, optional
        The row id of the normalized noise.
    aperture_identity : `str` or `None`, optional
        The aperture set the noise was measured in; ``None`` stores no
        identity row (a stack made before the row existed).
    apertures : `int`, optional
        The number of empty apertures behind the noise.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The stack and frame rows.
    """
    when = START + timedelta(days=day)
    stack = f"T/stack_{run_id}"
    if expected_noise is None:
        expected_noise = 1.0 if normalized is None else normalized * 1000.0
    rows = []
    if aperture_identity is not None:
        rows.append(
            _row(
                fc.APERTURE_SET_METRIC,
                stack,
                aperture_identity_to_number(aperture_identity),
                run_id,
                when,
                version=1,
            )
        )
    rows += [
        _row(
            fc.NORMALIZED_NOISE_METRIC, stack, normalized, run_id, when, sample_count=apertures, row_id=row_id
        ),
        _row(
            fc.NOISE_METRIC,
            stack,
            None if normalized is None else normalized * 1000.0,
            run_id,
            when,
            sample_count=apertures,
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


def _calibration(
    run_id: str,
    *,
    flat: str = "flat_1.fits",
    bias: str = "bias_1.fits",
    dark: str = "dark_1.fits",
    camera: str = "CamA",
    optic: str = "Scope",
    siril: str | None = "1.4.0",
    algorithm: int | None = 7,
) -> CalibrationIdentities:
    """Build the calibration record of a run.

    Parameters
    ----------
    run_id : `str`
        The run.
    flat, bias, dark : `str`, optional
        The master file names (size and time are fixed).
    camera, optic : `str`, optional
        The equipment names.
    siril : `str` or `None`, optional
        The Siril version.
    algorithm : `int` or `None`, optional
        The stacking algorithm version.

    Returns
    -------
    record : `CalibrationIdentities`
        The record, for the stack ``T/stack_<run_id>``.
    """
    return CalibrationIdentities(
        runId=run_id,
        subjectId=f"T/stack_{run_id}",
        targetId="T",
        camera=camera,
        optic=optic,
        focalLengthMm=400.0,
        flatMasters=[CalibrationFileIdentity(path=flat, sizeBytes=10, modifiedNs=1)],
        biasMasters=[CalibrationFileIdentity(path=bias, sizeBytes=10, modifiedNs=1)],
        darkMasters=[CalibrationFileIdentity(path=dark, sizeBytes=10, modifiedNs=1)],
        sirilVersion=siril,
        stackingAlgorithmVersion=algorithm,
    )


def _runs_from(
    rows: list[QualityMeasurement], **changes_by_run: dict[str, object]
) -> list[fc.StackRunRecord]:
    """Group rows into runs and give every run a calibration record.

    Parameters
    ----------
    rows : `list` [`QualityMeasurement`]
        The stored rows.
    **changes_by_run : `dict`
        For a run id, keyword changes for `_calibration`.

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        The runs, with calibrations attached.
    """
    runs = fc.stack_runs_from_measurements(rows)
    records = [_calibration(run.run_id, **changes_by_run.get(run.run_id, {})) for run in runs]  # type: ignore[arg-type]
    return fc.attach_calibration_identities(runs, records)


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
    return _runs_from(rows), everyone


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
    runs = _runs_from(rows)

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
    runs = _runs_from(rows)

    (check,) = fc.forecast_check(runs, old + new, configured_stack_weight=None)

    assert check.outcome != ForecastOutcome.NOT_CHECKABLE
    assert check.weighting_mode == "recorded_weights"
    assert check.forecast_given_frames_taken.noise_change_fraction is not None


def test_missing_normalized_noise_is_not_checkable() -> None:
    """A stack whose noise was not measured gives a check that says so."""
    old, new = _dark(10, "a"), _dark(10, "b")
    rows = _run_rows("run1", 0, [o.frame_id for o in old], [1.0] * 10, 0.01)
    rows += _run_rows("run2", 5, [o.frame_id for o in old + new], [1.0] * 20, None, row_id=2)

    (check,) = fc.forecast_check(_runs_from(rows), old + new, configured_stack_weight=None)

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
    """One stack with no floor gives no signal (too little history)."""
    signal = fc.target_stop_signal(_noise_runs([4.0], [4.0]))

    assert signal.stopped is None


def test_stop_signal_uses_the_one_stack_floor() -> None:
    """A floor found from the newest stack alone can raise the stop.

    Measured 5.0 against expected 4.0 (1000 apertures) is a floor of 3.0 that
    is 56% of the random variance. Doubling the data then cuts the noise by
    only 17%, above the 10% limit, so the signal is "not stopped" with the
    gain stated; a bigger floor stops.
    """
    middle = fc.target_stop_signal(_noise_runs([5.0], [4.0]))
    large = fc.target_stop_signal(_noise_runs([12.0], [4.0]))

    assert middle.stopped is False
    assert middle.predicted_gain_fraction == pytest.approx(0.175, abs=0.005)
    assert large.stopped is True


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


# ---------------------------------------------------------------------------
# Part 2b: confounded pairs
# ---------------------------------------------------------------------------


def _plain_pair(
    *,
    run1_identity: str | None = APERTURE_SET,
    run2_identity: str | None = APERTURE_SET,
    new_frame_epoch: float | None = None,
    calibration_changes: dict[str, dict[str, object]] | None = None,
    attach: bool = True,
) -> tuple[list[fc.StackRunRecord], list[FrameObservation]]:
    """Build two equal-weight runs (20 frames, then 20 more), with options.

    Parameters
    ----------
    run1_identity, run2_identity : `str` or `None`, optional
        The aperture set of each run's noise; ``None`` stores no row.
    new_frame_epoch : `float` or `None`, optional
        The start time given to every added frame.
    calibration_changes : `dict`, optional
        For a run id, changes to its calibration record (`_calibration`).
    attach : `bool`, optional
        Whether to attach calibration records at all.

    Returns
    -------
    runs, observations : `tuple`
        The runs and the frames' measurements.
    """
    old = _dark(20, "a")
    new = [_observation(f"b{i}", start_epoch_seconds=new_frame_epoch) for i in range(20)]
    everyone = old + new
    weights = [1.0] * 40
    normalized_after = 0.010 * 0.5**0.5
    rows = _run_rows("run1", 0, [o.frame_id for o in old], [1.0] * 20, 0.010, aperture_identity=run1_identity)
    rows += _run_rows(
        "run2",
        5,
        [o.frame_id for o in everyone],
        weights,
        normalized_after,
        row_id=2,
        aperture_identity=run2_identity,
    )
    if not attach:
        return fc.stack_runs_from_measurements(rows), everyone
    return _runs_from(rows, **(calibration_changes or {})), everyone


def test_the_plain_pair_is_checkable() -> None:
    """Nothing differs, so the baseline pair checks out."""
    runs, observations = _plain_pair()

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST


def test_the_identity_rows_are_read_into_the_run() -> None:
    """The identity row decodes to text; a missing row is a problem."""
    runs, _ = _plain_pair(run2_identity=None)

    assert runs[0].aperture_identity == APERTURE_SET
    assert runs[1].aperture_identity is None
    assert "aperture_identity" in runs[1].problems


def test_a_missing_aperture_identity_is_not_checkable() -> None:
    """A stack made before the identity row existed cannot be compared."""
    runs, observations = _plain_pair(run1_identity=None)

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert check.reason.startswith("Run run1 has no aperture-set identity")


def test_different_aperture_sets_are_not_checkable() -> None:
    """Noise measured in two aperture sets is not on one scale."""
    runs, observations = _plain_pair(run2_identity=OTHER_APERTURE_SET)

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "different aperture sets (v3-156104df543d and v3-aaaaaaaaaaaa)" in check.reason


def test_a_run_with_no_calibration_record_is_confounded() -> None:
    """With no calibration record, a change of flats cannot be ruled out."""
    runs, observations = _plain_pair(attach=False)

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "has no calibration record" in check.reason


@pytest.mark.parametrize(
    ("changes", "expected_text"),
    [
        ({"flat": "flat_2.fits"}, "The flat master changed"),
        ({"bias": "bias_2.fits"}, "The bias master changed"),
        ({"optic": "Other scope"}, "The telescope or lens changed"),
        ({"camera": "CamB"}, "The camera changed from CamA to CamB"),
        ({"siril": "1.5.0"}, "The Siril version changed from 1.4.0 to 1.5.0"),
        ({"algorithm": 8}, "The stacking algorithm version changed from 7 to 8"),
        ({"siril": None}, "The Siril version was not recorded"),
        ({"dark": "dark_2.fits"}, "The dark master changed"),
    ],
)
def test_a_calibration_change_is_not_checkable(changes: dict[str, object], expected_text: str) -> None:
    """Each change in the stored calibration confounds the pair."""
    runs, observations = _plain_pair(calibration_changes={"run2": changes})

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert expected_text in check.reason


def test_a_new_dark_set_does_not_confound_a_cooled_camera() -> None:
    """A new dark master alone does not confound a cooled camera."""
    runs, observations = _plain_pair(
        calibration_changes={
            "run1": {"camera": "ZWO ASI533MM Pro"},
            "run2": {"camera": "ZWO ASI533MM Pro", "dark": "dark_2.fits"},
        }
    )

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST


def test_a_different_noise_definition_version_is_not_checkable() -> None:
    """Two noise rows of different definitions are not the same measurement."""
    runs, observations = _plain_pair()
    runs[1] = runs[1].model_copy(update={"noise_definition_version": 4})

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "definition version 3 for run run1 and 4 for run run2" in check.reason


def test_frames_older_than_the_earlier_stack_are_not_new() -> None:
    """Frames taken before the earlier stack are not new data."""
    runs, observations = _plain_pair(new_frame_epoch=OLD_FRAME_EPOCH)

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "20 of the 20 added frames were taken before the earlier stack was made" in check.reason


def test_added_frames_with_no_start_time_are_not_checkable() -> None:
    """A frame with no start time cannot be shown to be new."""
    runs, observations = _plain_pair()
    unknown = [
        o.model_copy(update={"start_epoch_seconds": None}) if o.frame_id.startswith("b") else o
        for o in observations
    ]

    (check,) = fc.forecast_check(runs, unknown)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "no start time" in check.reason


# ---------------------------------------------------------------------------
# Part 2b: the noise floor in the pair check
# ---------------------------------------------------------------------------


def _floor_pair(
    measured_before: float, expected_before: float, measured_ratio: float, apertures: int
) -> tuple[list[fc.StackRunRecord], list[FrameObservation]]:
    """Build two equal-weight runs whose first stack has a given floor.

    Parameters
    ----------
    measured_before, expected_before : `float`
        The first stack's measured and expected noise, in stack units (the
        normalized noise is a thousandth of the measured one).
    measured_ratio : `float`
        The second stack's measured noise over the first's.
    apertures : `int`
        The apertures behind each noise.

    Returns
    -------
    runs, observations : `tuple`
        The runs (with calibration records) and the frames.
    """
    old, new = _dark(20, "a"), _dark(20, "b")
    normalized = measured_before / 1000.0
    rows = _run_rows(
        "run1",
        0,
        [o.frame_id for o in old],
        [1.0] * 20,
        normalized,
        expected_noise=expected_before,
        apertures=apertures,
    )
    rows += _run_rows(
        "run2",
        5,
        [o.frame_id for o in old + new],
        [1.0] * 40,
        normalized * measured_ratio,
        expected_noise=expected_before * 0.5**0.5,
        row_id=2,
        apertures=apertures,
    )
    return _runs_from(rows), old + new


def test_a_floor_in_the_earlier_stack_explains_a_small_gain() -> None:
    """M 27: measured 3.097 against expected 1.048 is a floor.

    Without the floor the forecast is a 29% fall and a fall of 3% would be
    "short for no known reason". With it the planned fall is about 3%.
    """
    ratio = (((3.097**2 - 1.048**2) + 0.5 * 1.048**2) / 3.097**2) ** 0.5
    runs, observations = _floor_pair(3.097, 1.048, ratio, apertures=400)

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.forecast_when_planned.noise_change_fraction == pytest.approx(ratio - 1.0, abs=1e-6)
    assert check.forecast_when_planned.noise_change_fraction > -0.05


def test_without_a_floor_the_forecast_is_the_plain_fall() -> None:
    """M 81: 1.126 against 0.921 is no floor at 100 apertures.

    The forecast is then the plain 29% fall, with nothing held back.
    """
    assert not measure_stack_noise_floor(1.126, 0.921, 100).floor_detected
    runs, observations = _floor_pair(1.126, 0.921, 0.75, apertures=100)

    (check,) = fc.forecast_check(runs, observations)

    assert check.forecast_when_planned.noise_change_fraction == pytest.approx(-0.2929, abs=1e-3)
    assert check.outcome == ForecastOutcome.AS_FORECAST


def test_a_small_gain_without_a_floor_is_short() -> None:
    """With no floor, a 3% fall against 29% planned is short."""
    runs, observations = _floor_pair(1.126, 1.126, 0.97, apertures=1000)

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.SHORT_UNKNOWN_REASON
    assert "no noise floor" in check.reason


def test_the_m57_numbers_make_a_floor() -> None:
    """M 57: 1.057 against 0.596 is a floor at 100 apertures."""
    floor = measure_stack_noise_floor(1.057, 0.596, 100)

    assert floor.floor_detected
    assert floor.floor_variance_share == pytest.approx(1.0 - (0.596 / 1.057) ** 2, abs=1e-6)


def test_single_stack_floor_needs_the_expected_noise_and_two_apertures() -> None:
    """No expected noise, or one aperture, gives no floor result."""
    runs, _ = _floor_pair(3.097, 1.048, 0.97, apertures=400)

    assert fc.single_stack_floor(runs[0]) is not None
    assert fc.single_stack_floor(runs[0].model_copy(update={"expected_noise": None})) is None
    assert fc.single_stack_floor(runs[0].model_copy(update={"aperture_count": 1})) is None


def test_the_floor_fit_ignores_runs_from_another_aperture_set() -> None:
    """The floor fit never mixes aperture sets."""
    expected = [8.0, 4.0, 2.0, 1.0]
    rows = []
    for index, value in enumerate(expected):
        rows += _run_rows(
            f"r{index}",
            index,
            [f"f{index}"],
            [1.0],
            (value**2 + 36.0) ** 0.5 / 1000.0,
            expected_noise=value,
            aperture_identity=OTHER_APERTURE_SET if index == 0 else APERTURE_SET,
        )
    runs = fc.stack_runs_from_measurements(rows)

    floor = fc.noise_floor_for_runs(runs)

    # Three steps in the same set (3 - 2 = 1 degree of freedom) are fitted.
    assert floor is not None
    assert floor.source == "fit"


# ---------------------------------------------------------------------------
# Part 2b: exposure-group stacks
# ---------------------------------------------------------------------------


def _group_frames(prefix: str, count: int, exposure: float, sky: float = 1.0) -> list[FrameObservation]:
    """Build frames of one exposure length with a spread of star widths.

    Returns
    -------
    frames : `list` [`FrameObservation`]
        `count` frames; the star widths differ so the width weights exist.
    """
    return [
        _observation(f"{prefix}{i}", sky=sky, width=2.5 + 0.05 * (i % 7), exposure=exposure)
        for i in range(count)
    ]


def _group_run_rows(
    run_id: str,
    day: int,
    observations: list[FrameObservation],
    frame_noises: dict[float, float],
    normalized: float,
    gains: dict[float, float] | None = None,
    row_id: int = 1,
    expected_noise: float | None = None,
    reference_exposures: dict[float, float] | None = None,
) -> list[QualityMeasurement]:
    """Build the rows of a group stack as part 2a stores them.

    The frame weights are the library's own group rule (``W_g w_i`` from the
    star widths), and each group has its three rows on its own subject. Frames
    are placed with the stacker's fold rule, so a length with fewer than 5
    frames lands in the nearest group, whose exposure row is then the mean of
    its frames.

    Parameters
    ----------
    run_id : `str`
        The run.
    day : `int`
        Days after the start.
    observations : `list` [`FrameObservation`]
        All the stack's frames.
    frame_noises : `dict` [`float`, `float`]
        ``s_g`` of each exposure group, in ADU.
    normalized : `float`
        The normalized noise.
    gains : `dict` [`float`, `float`], optional
        ``G_g`` of each group (1 when omitted).
    row_id : `int`, optional
        The row id of the normalized noise.
    expected_noise : `float`, optional
        The expected noise; the measured noise when omitted.
    reference_exposures : `dict` [`float`, `float`], optional
        The exposure of each group's reference frame, keyed by group length.
        No reference row is written for a group that is not in it.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The stack, group and frame rows.
    """
    when = START + timedelta(days=day)
    placed = fold_exposure_lengths([o.exposure_seconds for o in observations])
    lengths = sorted({float(length) for length in placed if length is not None})
    groups = []
    for length in lengths:
        members = [o for o, group_length in zip(observations, placed, strict=True) if group_length == length]
        groups.append(
            StackGroup(
                frames=tuple(_frames(members)),
                frame_noise_adu=frame_noises[length],
                gain=(gains or {}).get(length, 1.0),
            )
        )
    raw = group_coefficients(groups).raw_weights
    assert raw is not None
    ordered = [frame.frame_id for group in groups for frame in group.frames]
    rows = _run_rows(
        run_id,
        day,
        ordered,
        list(raw),
        normalized,
        expected_noise=expected_noise,
        row_id=row_id,
    )
    for group, length in zip(groups, lengths, strict=True):
        subject = f"T/groups/g{length:g}_{run_id}.fits"
        rows += [
            _row(fc.GROUP_EXPOSURE_METRIC, subject, group.mean_exposure_seconds, run_id, when, version=1),
            _row(fc.GROUP_FRAMES_STACKED_METRIC, subject, float(len(group.frames)), run_id, when, version=1),
            _row(fc.GROUP_GAIN_METRIC, subject, group.gain, run_id, when, version=1),
        ]
        if reference_exposures is not None and length in reference_exposures:
            rows.append(
                _row(
                    fc.GROUP_REFERENCE_EXPOSURE_METRIC,
                    subject,
                    reference_exposures[length],
                    run_id,
                    when,
                    version=1,
                )
            )
    return rows


def _group_pair(
    new_frames: list[FrameObservation],
    measured_ratio: float | None = None,
    gains: dict[float, float] | None = None,
    new_noises: dict[float, float] | None = None,
) -> tuple[list[fc.StackRunRecord], list[FrameObservation]]:
    """Build two group-stack runs (30 s and 60 s groups).

    Parameters
    ----------
    new_frames : `list` [`FrameObservation`]
        The frames added to the later stack.
    measured_ratio : `float`, optional
        The later stack's measured noise over the first's. The group model's
        own ratio (a perfect forecast) when omitted.
    gains : `dict` [`float`, `float`], optional
        The groups' gains.
    new_noises : `dict` [`float`, `float`], optional
        ``s_g`` of the later stack's groups, when it differs.

    Returns
    -------
    runs, observations : `tuple`
        The runs (with calibration records) and the frames.
    """
    old = _group_frames("a30_", 12, 30.0) + _group_frames("a60_", 20, 60.0)
    noises = {30.0: 30.0, 60.0: 40.0}
    everyone = old + new_frames
    if measured_ratio is None:

        def groups_of(frames: list[FrameObservation], noise_of: dict[float, float]) -> list[StackGroup]:
            """Build the model groups of some frames.

            Returns
            -------
            groups : `list` [`StackGroup`]
                One group per exposure length.
            """
            return [
                StackGroup(
                    frames=tuple(_frames([o for o in frames if o.exposure_seconds == length])),
                    frame_noise_adu=noise_of[length],
                    gain=(gains or {}).get(length, 1.0),
                )
                for length in sorted({o.exposure_seconds for o in frames if o.exposure_seconds})
            ]

        before = forecast_group_noise(groups_of(old, noises))
        after = forecast_group_noise(groups_of(everyone, new_noises or noises))
        # The measured normalized noise is a fraction of the stars' flux, which
        # follows the mean exposure, so that factor is left out of the ratio.
        measured_ratio = noise_ratio_with_floor(before, after, None)
        assert measured_ratio is not None
    rows = _group_run_rows("run1", 0, old, noises, 0.010, gains=gains)
    rows += _group_run_rows(
        "run2", 5, everyone, new_noises or noises, 0.010 * measured_ratio, gains=gains, row_id=2
    )
    return _runs_from(rows), everyone


def test_group_rows_are_read_into_the_run() -> None:
    """Group rows go on the combined stack's record, not into runs."""
    runs, _ = _group_pair(_group_frames("b60_", 10, 60.0))

    assert [run.run_id for run in runs] == ["run1", "run2"]
    assert [(g.exposure_seconds, g.frames_stacked) for g in runs[0].groups] == [(30.0, 12), (60.0, 20)]
    assert [(g.exposure_seconds, g.frames_stacked) for g in runs[1].groups] == [(30.0, 12), (60.0, 30)]


def test_group_frame_noise_is_recovered_from_the_stored_rows() -> None:
    """``s_g`` comes back from the summed weights and the counts."""
    runs, observations = _group_pair(_group_frames("b60_", 10, 60.0))
    by_id = {o.frame_id: o for o in observations}
    frames, _ = fc.stack_frames_from_observations(runs[0].member_frame_ids, by_id)

    rebuilt = fc.stack_groups_of_run(runs[0], frames)

    assert rebuilt.is_group_stack
    assert [group.frame_noise_adu for group in rebuilt.groups] == pytest.approx([30.0, 40.0])
    assert rebuilt.heaviest_exposure_seconds == pytest.approx(60.0)


def test_a_group_pair_that_follows_the_model_is_as_forecast() -> None:
    """New frames in an existing group follow the model."""
    runs, observations = _group_pair(_group_frames("b60_", 10, 60.0))

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.weighting_mode == "group"
    assert check.forecast_given_frames_taken.noise_change_fraction == pytest.approx(
        check.measured.noise_change_fraction, abs=1e-6
    )
    planned = check.forecast_when_planned.noise_change_fraction
    assert planned is not None and planned < -0.05


def test_a_group_pair_with_gains_uses_them() -> None:
    """A gain on the 30 s group is used in the forecast."""
    runs, observations = _group_pair(_group_frames("b60_", 10, 60.0), gains={30.0: 1.05, 60.0: 1.0})

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.weighting_mode == "group"


def test_a_group_pair_that_gains_less_than_planned_is_short() -> None:
    """A measured fall far below the group forecast is flagged."""
    runs, observations = _group_pair(_group_frames("b60_", 30, 60.0), measured_ratio=1.0)

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.SHORT_UNKNOWN_REASON


def test_a_floor_applies_to_the_group_pair() -> None:
    """A floor in the earlier group stack lowers the forecast fall."""
    old = _group_frames("a30_", 12, 30.0) + _group_frames("a60_", 20, 60.0)
    new = _group_frames("b60_", 10, 60.0)
    noises = {30.0: 30.0, 60.0: 40.0}
    rows = _group_run_rows("run1", 0, old, noises, 0.010, expected_noise=4.0)
    rows += _group_run_rows("run2", 5, old + new, noises, 0.0099, row_id=2, expected_noise=4.0)
    runs = _runs_from(rows)

    (check,) = fc.forecast_check(runs, old + new)

    # Floor variance 10^2 - 4^2 = 84 over a random variance of 16.
    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.forecast_when_planned.noise_change_fraction > -0.05


def test_a_changed_heaviest_group_is_not_checkable() -> None:
    """Many new 30 s frames make the 30 s group the heaviest."""
    runs, observations = _group_pair(_group_frames("b30_", 60, 30.0))

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "heaviest exposure group changed from 60 s to 30 s" in check.reason


def test_a_group_stack_after_a_single_group_stack_is_not_checkable() -> None:
    """A first stack of one group and a later one of two cannot be compared."""
    old = _group_frames("a60_", 20, 60.0)
    new = _group_frames("b30_", 12, 30.0)
    rows = _run_rows("run1", 0, [o.frame_id for o in old], [1.0] * 20, 0.010)
    rows += _group_run_rows("run2", 5, old + new, {30.0: 30.0, 60.0: 40.0}, 0.0095, row_id=2)
    runs = _runs_from(rows)

    (check,) = fc.forecast_check(runs, old + new)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "single group" in check.reason


def test_a_group_with_no_stored_row_is_not_checkable() -> None:
    """A run with no group rows says which group it lacks."""
    runs, observations = _group_pair(_group_frames("b60_", 10, 60.0))
    runs[1] = runs[1].model_copy(update={"groups": [g for g in runs[1].groups if g.exposure_seconds < 45.0]})

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "stored no row for its 60 s exposure group" in check.reason


def test_a_run_that_made_several_stacks_has_no_group_link() -> None:
    """A run of several stacks has no stored group-to-stack link."""
    old = _group_frames("a30_", 12, 30.0) + _group_frames("a60_", 20, 60.0)
    rows = _group_run_rows("run1", 0, old, {30.0: 30.0, 60.0: 40.0}, 0.010)
    rows += _run_rows("run1", 0, ["x0"], [1.0], 0.02, row_id=9)
    second = [
        row.model_copy(update={"subject": row.subject.model_copy(update={"subject_id": "T/stack_other"})})
        for row in _run_rows("run1", 0, ["x0"], [1.0], 0.02, row_id=9)
        if row.subject.level == SubjectLevel.STACK
    ]

    runs = fc.stack_runs_from_measurements(rows + second)

    assert all(run.groups == [] for run in runs)
    assert all("groups" in run.problems for run in runs)


# ---------------------------------------------------------------------------
# Folded groups (decision D10, option B)
# ---------------------------------------------------------------------------


def _folded_pair(
    reference_exposures: dict[float, float] | None,
    later_reference_exposures: dict[float, float] | str | None = "same",
    measured_ratio: float | None = None,
) -> tuple[list[fc.StackRunRecord], list[FrameObservation]]:
    """Build two group stacks; the later one folds 100 s frames into 60 s.

    Parameters
    ----------
    reference_exposures : `dict` [`float`, `float`] or `None`
        The reference-frame exposure rows of the earlier run (none when
        ``None``).
    later_reference_exposures : `dict` [`float`, `float`] or `None` or `str`
        The same for the later run; ``"same"`` copies the earlier run's.
    measured_ratio : `float`, optional
        The later stack's measured noise over the first's; the model's own
        ratio (a perfect forecast) when omitted.

    Returns
    -------
    runs, observations : `tuple`
        The runs (with calibration records) and the frames.
    """
    later = reference_exposures if later_reference_exposures == "same" else later_reference_exposures
    old = _group_frames("a30_", 12, 30.0) + _group_frames("a60_", 20, 60.0)
    new = _group_frames("b100_", 3, 100.0)
    noises = {30.0: 30.0, 60.0: 40.0}
    if measured_ratio is None:

        def model(frames: list[FrameObservation], references: dict[float, float] | None) -> object:
            """Forecast the group stack of some frames with the model.

            Returns
            -------
            forecast : `NoiseForecast`
                The group forecast.
            """
            placed = fold_exposure_lengths([o.exposure_seconds for o in frames])
            groups = []
            for length in sorted({float(value) for value in placed if value is not None}):
                members = _frames([o for o, key in zip(frames, placed, strict=True) if key == length])
                groups.append(
                    StackGroup(
                        frames=tuple(members),
                        frame_noise_adu=noises[length],
                        reference_exposure_seconds=None if references is None else references[length],
                        noise_length_seconds=length,
                    )
                )
            return forecast_group_noise(groups)

        measured_ratio = noise_ratio_with_floor(
            model(old, reference_exposures),
            model(old + new, later),
            None,  # type: ignore[arg-type]
        )
        assert measured_ratio is not None
    rows = _group_run_rows("run1", 0, old, noises, 0.010, reference_exposures=reference_exposures)
    rows += _group_run_rows(
        "run2", 5, old + new, noises, 0.010 * measured_ratio, row_id=2, reference_exposures=later
    )
    return _runs_from(rows), old + new


def test_the_reference_exposure_row_is_read_into_the_group() -> None:
    """The new group row is read at version 1 into the recorded group."""
    runs, _ = _folded_pair({30.0: 30.0, 60.0: 100.0})

    assert [g.reference_exposure_seconds for g in runs[1].groups] == [30.0, 100.0]
    assert runs[1].groups[1].exposure_seconds == pytest.approx((20 * 60.0 + 3 * 100.0) / 23)


def test_a_folded_group_is_forecast_and_checked_with_its_reference_exposure() -> None:
    """A later stack that folds frames into a group follows the model."""
    runs, observations = _folded_pair({30.0: 30.0, 60.0: 100.0})

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.weighting_mode == "group"
    assert check.forecast_given_frames_taken.noise_change_fraction == pytest.approx(
        check.measured.noise_change_fraction, abs=1e-6
    )


def test_a_folded_group_with_no_reference_exposure_row_is_not_checkable() -> None:
    """The folded frames' scaling is unknown, so there is no check."""
    runs, observations = _folded_pair(
        {30.0: 30.0, 60.0: 60.0}, later_reference_exposures=None, measured_ratio=0.9
    )

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "stored no reference-frame exposure" in check.reason
    assert "60 s group" in check.reason


def test_a_folded_group_whose_reference_exposure_row_was_not_measured_is_not_checkable() -> None:
    """A reference exposure row with no value is the same as no row."""
    runs, observations = _folded_pair({30.0: 30.0, 60.0: 60.0}, measured_ratio=0.9)
    groups = [
        group.model_copy(update={"reference_exposure_seconds": None}) if group.frames_stacked == 23 else group
        for group in runs[1].groups
    ]
    runs[1] = runs[1].model_copy(update={"groups": groups})

    (check,) = fc.forecast_check(runs, observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "reference-frame exposure" in check.reason
