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

import math
from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib import (
    EARLIER_NIGHTS_VARIANT,
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
from wayfindinglib.models.planning.target_report import (
    ForecastBasis,
    ForecastCheck,
    ForecastedChange,
    ForecastGate,
    ForecastOutcome,
)

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

    Measured 6.0 against expected 4.0 (1000 apertures) is a floor of 20^0.5
    that is 125% of the random variance. It stays a floor once the expected
    noise model's own error is counted (5.0 against 4.0 no longer does).
    Doubling the data then cuts the noise by about 12%, above the 10% limit,
    so the signal is "not stopped" with the gain stated; a bigger floor
    stops.
    """
    middle = fc.target_stop_signal(_noise_runs([6.0], [4.0]))
    large = fc.target_stop_signal(_noise_runs([12.0], [4.0]))

    assert middle.stopped is False
    assert middle.predicted_gain_fraction == pytest.approx(0.118, abs=0.005)
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


def _half_difference_rows(
    stack_run_id: str,
    day: float,
    difference_normalized: float,
    *,
    apertures: int = 100,
    aperture_identity: str = APERTURE_SET,
    variance_factor: float | None = 0.1,
    effective_count: float = 40.0,
) -> list[QualityMeasurement]:
    """Build the rows of the half-difference job on one library stack.

    The defaults are two disjoint halves of 20 equal frames: the difference
    has the variance factor 20 x (1/20)^2 x 2 = 0.1 and the library stack of
    40 frames has the effective count 40, so the full-stack equivalent is the
    difference noise divided by sqrt(40 x 0.1) = 2.

    Parameters
    ----------
    stack_run_id : `str`
        The stack run the job followed (it names the stack's subject).
    day : `float`
        Days after the start, for the time (the job runs after the stack).
    difference_normalized : `float`
        The difference's normalized noise.
    apertures : `int`, optional
        The apertures behind it.
    aperture_identity : `str`, optional
        The aperture set it was measured in.
    variance_factor : `float` or `None`, optional
        The stored variance factor.
    effective_count : `float`, optional
        The library stack's effective frame count (stored with the stack).

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The half-difference rows, plus the stack's effective-count row.
    """
    stack = f"T/stack_{stack_run_id}"
    when = START + timedelta(days=day)
    job = f"half_{stack_run_id}_{day}"
    return [
        _row(
            fc.HALF_DIFFERENCE_NORMALIZED_METRIC,
            stack,
            difference_normalized,
            job,
            when,
            version=1,
            sample_count=apertures,
        ),
        _row(
            fc.HALF_DIFFERENCE_APERTURE_SET_METRIC,
            stack,
            aperture_identity_to_number(aperture_identity),
            job,
            when,
            version=1,
        ),
        _row(fc.HALF_DIFFERENCE_VARIANCE_FACTOR_METRIC, stack, variance_factor, job, when, version=1),
        _row(fc.EFFECTIVE_FRAME_COUNT_METRIC, stack, effective_count, stack_run_id, START, version=1),
    ]


def _m13_rows(**half_arguments: float | str | None) -> list[QualityMeasurement]:
    """Build M 13's one stack with its half-difference rows.

    The full stack's normalized noise is 2.056e-4 over 100 apertures. The
    difference is 2 x 1.4295e-4, so the full-stack equivalent is 1.4295e-4
    (validation register entry STK-42). The stored expected noise is the
    model's 1.4298e-4 (times the 1000 that makes stack units here).

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The stack run's rows and the job's.
    """
    rows = _run_rows("run1", 0, ["a0"], [1.0], 2.056e-4, expected_noise=0.14298, apertures=100)
    arguments = {"day": 1, "difference_normalized": 2.859e-4, **half_arguments}
    return rows + _half_difference_rows("run1", **arguments)


def test_m13_half_difference_gives_a_floor_at_2_2_sigma() -> None:
    """M 13: ln F = 0.73 over an error of 0.33 is a floor, from the halves."""
    runs = fc.stack_runs_from_measurements(_m13_rows())

    floor = fc.single_stack_floor(runs[0])

    assert floor is not None
    assert floor.basis == "half_difference"
    assert floor.floor_detected
    assert floor.sigmas == pytest.approx(2.2, abs=0.1)
    assert floor.floor_variance_share == pytest.approx(1.0 - (1.4295 / 2.056) ** 2, abs=1e-3)
    assert floor.predicted_random_noise == pytest.approx(1.4295e-4 * 1000.0, rel=1e-3)
    assert "odd and even half stacks" in floor.reason


def test_m13_without_a_half_difference_has_no_floor_from_the_model() -> None:
    """The model test, with its own error, sees no floor on M 13."""
    runs = fc.stack_runs_from_measurements(_m13_rows()[:-4])

    floor = fc.single_stack_floor(runs[0])

    assert floor is not None
    assert floor.basis == "model"
    assert not floor.floor_detected
    assert "half-difference" not in floor.reason


def test_a_half_difference_equal_to_the_measured_noise_gives_no_floor() -> None:
    """When the random noise is the whole measured noise, there is no floor."""
    runs = fc.stack_runs_from_measurements(_m13_rows(difference_normalized=2.0 * 2.056e-4))

    floor = fc.single_stack_floor(runs[0])

    assert floor is not None
    assert floor.basis == "half_difference"
    assert not floor.floor_detected
    assert floor.sigmas == pytest.approx(0.0, abs=1e-9)


def _loss_account_unknown(record: fc.StackRunRecord) -> tuple[float, str]:
    """Run the loss account's unknown part on a run's half-difference check.

    Parameters
    ----------
    record : `StackRunRecord`
        The run.

    Returns
    -------
    seconds, reason : `tuple` [`float`, `str`]
        The unexplained seconds of 100 s of effective time, and the reason.
    """
    from wayfindinglib.analytics.result_quality.aggregates import MeasuredNoiseCheck, _unknown_part

    inputs = fc.half_difference_noise_inputs(record)
    assert inputs is not None
    seconds, reason = _unknown_part(100.0, MeasuredNoiseCheck.from_half_difference(*inputs))
    assert seconds is not None
    return seconds, reason


def test_m13_loss_account_reports_an_unexplained_part_from_the_halves() -> None:
    """M 13: the loss account and the floor both see the fixed pattern."""
    record = fc.stack_runs_from_measurements(_m13_rows())[0]

    seconds, reason = _loss_account_unknown(record)
    floor = fc.single_stack_floor(record)

    assert floor is not None
    assert floor.floor_detected
    assert seconds == pytest.approx(100.0 * (1.0 - (1.4295 / 2.056) ** 2), abs=0.1)
    assert "odd and even half stacks" in reason
    assert "fixed on the sky" in reason


def test_a_half_difference_equal_to_the_measured_noise_is_within_error_in_the_loss_account() -> None:
    """With the difference at the measured noise, nothing is unexplained."""
    record = fc.stack_runs_from_measurements(_m13_rows(difference_normalized=2.0 * 2.056e-4))[0]

    seconds, reason = _loss_account_unknown(record)

    assert seconds == pytest.approx(0.0)
    assert "within error" in reason
    assert "odd and even half stacks" in reason


def test_without_a_usable_half_difference_the_loss_account_keeps_the_model_basis() -> None:
    """Without a half-difference the model check is used."""
    from wayfindinglib.analytics.result_quality.aggregates import MeasuredNoiseCheck, _unknown_part

    record = fc.stack_runs_from_measurements(_m13_rows()[:-4])[0]
    other = fc.stack_runs_from_measurements(_m13_rows(aperture_identity=OTHER_APERTURE_SET))[0]

    assert fc.half_difference_noise_inputs(record) is None
    assert fc.half_difference_noise_inputs(other) is None
    seconds, reason = _unknown_part(100.0, MeasuredNoiseCheck(variance_ratio=1.5, variance_ratio_error=0.1))
    assert seconds == pytest.approx(0.0)
    assert "its frames explain" in reason
    assert "half stacks" not in reason


def test_the_floor_and_the_loss_account_agree_on_both_bases_across_ratios() -> None:
    """A significant floor and an unexplained part go together."""
    from wayfindinglib.analytics.result_quality import noise_growth as ng
    from wayfindinglib.analytics.result_quality.aggregates import MeasuredNoiseCheck, _unknown_part

    for ratio in (1.0, 1.1, 1.5, 1.9, 2.0, 2.5, 3.0, 4.0, 8.0):
        for apertures in (30, 100, 400):
            measured = math.sqrt(ratio) * 1e-3
            floor = ng.measure_stack_noise_floor_from_half_difference(measured, apertures, 1e-3, apertures)
            check = MeasuredNoiseCheck.from_half_difference(measured, apertures, 1e-3, apertures)
            seconds, _ = _unknown_part(100.0, check)
            assert floor.floor_detected == (seconds > 0.0), (ratio, apertures)
            model_floor = ng.measure_stack_noise_floor(measured, 1e-3, apertures)
            aperture_error = 2.0 * ng.aperture_noise_relative_error(apertures) * ratio
            model_seconds, _ = _unknown_part(
                100.0, MeasuredNoiseCheck(variance_ratio=ratio, variance_ratio_error=aperture_error)
            )
            assert model_floor.floor_detected == (model_seconds > 0.0), (ratio, apertures)


def test_a_half_difference_in_other_apertures_falls_back_to_the_model() -> None:
    """A half-difference of another aperture set is not used, and says so."""
    runs = fc.stack_runs_from_measurements(_m13_rows(aperture_identity=OTHER_APERTURE_SET))

    floor = fc.single_stack_floor(runs[0])

    assert floor is not None
    assert floor.basis == "model"
    assert "other apertures" in floor.reason


def test_an_older_half_difference_falls_back_to_the_model() -> None:
    """A half-difference made before the stack's noise row is not its own."""
    rows = _run_rows("run1", 5, ["a0"], [1.0], 2.056e-4, expected_noise=0.14298, apertures=100)
    rows += _half_difference_rows("run1", 1, 2.859e-4)

    runs = fc.stack_runs_from_measurements(rows)

    assert runs[0].half_difference is None
    floor = fc.single_stack_floor(runs[0])
    assert floor is not None
    assert floor.basis == "model"


def test_a_restack_does_not_inherit_the_earlier_stacks_half_difference() -> None:
    """A half-difference belongs to the run it followed, not a later one."""
    rows = _m13_rows()
    rows += _run_rows("run2", 3, ["a0"], [1.0], 2.056e-4, expected_noise=0.14298, apertures=100)
    runs = fc.stack_runs_from_measurements(rows)

    assert runs[0].half_difference is not None
    assert runs[1].half_difference is None


def test_m27_has_a_floor_from_the_halves_and_from_the_model() -> None:
    """M 27: 3.097 against a random 1.048 is a floor on either basis."""
    rows = _run_rows("run1", 0, ["a0"], [1.0], 3.097e-3, expected_noise=1.048, apertures=100)
    with_halves = rows + _half_difference_rows("run1", 1, 2.0 * 1.048e-3)

    from_halves = fc.single_stack_floor(fc.stack_runs_from_measurements(with_halves)[0])
    from_model = fc.single_stack_floor(fc.stack_runs_from_measurements(rows)[0])

    assert from_halves is not None and from_model is not None
    assert from_halves.floor_detected and from_model.floor_detected
    assert from_halves.basis == "half_difference"
    assert from_model.basis == "model"


def test_the_floor_sentence_names_the_basis() -> None:
    """The forecast check says which measurement decided the floor."""
    halves = fc.single_stack_floor(fc.stack_runs_from_measurements(_m13_rows())[0])
    model = fc.single_stack_floor(fc.stack_runs_from_measurements(_m13_rows()[:-4])[0])

    assert "odd and even half stacks" in fc._floor_sentence(halves)
    assert "noise model" in fc._floor_sentence(model)


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


# ---------------------------------------------------------------------------
# Decision D11: the earlier-nights subset as the earlier member of a pair
# ---------------------------------------------------------------------------


def _as_subset_rows(rows: list[QualityMeasurement]) -> list[QualityMeasurement]:
    """Put the stack-level rows of a run on the earlier-nights subject.

    Parameters
    ----------
    rows : `list` [`QualityMeasurement`]
        The rows `_run_rows` or `_group_run_rows` made.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        Copies whose stack-level subjects carry the earlier-nights variant.
        Frame rows are left as they are.
    """
    subset = []
    for row in rows:
        if row.subject.level == SubjectLevel.STACK:
            subject = row.subject.model_copy(update={"variant": EARLIER_NIGHTS_VARIANT})
            row = row.model_copy(update={"subject": subject})
        subset.append(row)
    return subset


def _subset_pair(runs: list[fc.StackRunRecord]) -> list[fc.StackRunRecord]:
    """Mark the first of two runs as the earlier-nights subset.

    Parameters
    ----------
    runs : `list` [`StackRunRecord`]
        Two runs, the earlier holding the frames of the earlier nights.

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        The same runs with the first marked as a subset.
    """
    return [runs[0].model_copy(update={"is_earlier_nights_subset": True}), runs[1]]


def _check_of(outcome: ForecastOutcome, basis: ForecastBasis) -> ForecastCheck:
    """Build a made-up forecast check.

    Parameters
    ----------
    outcome : `ForecastOutcome`
        The outcome.
    basis : `ForecastBasis`
        What the check compared.

    Returns
    -------
    check : `ForecastCheck`
        The check.
    """
    change = ForecastedChange(noiseChangeFraction=-0.2, tolerance=0.05)
    return ForecastCheck(
        stackRunId="r",
        forecastWhenPlanned=change,
        forecastGivenFramesTaken=change,
        measured=change,
        weightingMode="equal",
        paired=False,
        outcome=outcome,
        reason="" if outcome == ForecastOutcome.AS_FORECAST else "made up",
        basis=basis,
    )


def test_the_earlier_nights_run_is_read_and_marked() -> None:
    """The subset run is built and marked on request, else left out."""
    library = _run_rows("lib", 5, ["a0", "a1", "b0", "b1"], [1.0] * 4, 0.007, row_id=2)
    subset = _as_subset_rows(_run_rows("sub", 5, ["a0", "a1"], [1.0, 1.0], 0.010, row_id=3))
    other = _row(fc.NORMALIZED_NOISE_METRIC, "T/other", 0.02, "other", START, variant="without_night_1")

    default_runs = fc.stack_runs_from_measurements([*library, *subset, other])
    with_subset = fc.stack_runs_from_measurements([*library, *subset, other], include_earlier_nights=True)

    assert [run.run_id for run in default_runs] == ["lib"]
    assert {run.run_id: run.is_earlier_nights_subset for run in with_subset} == {"lib": False, "sub": True}
    subset_run = next(run for run in with_subset if run.is_earlier_nights_subset)
    assert subset_run.member_frame_ids == ["a0", "a1"]
    assert subset_run.normalized_noise == pytest.approx(0.010)
    assert subset_run.aperture_identity == APERTURE_SET
    assert fc.library_runs(with_subset)[0].run_id == "lib"


def test_a_subset_pair_is_checked_and_labeled() -> None:
    """A subset and its library stack make a labeled check."""
    runs, observations = _plain_pair()

    (check,) = fc.forecast_check(_subset_pair(runs), observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST
    assert check.basis == ForecastBasis.EARLIER_NIGHTS_SUBSET
    assert check.stack_run_id == "run2"


def test_two_library_stacks_keep_the_later_stack_label() -> None:
    """A pair of library stacks is labeled as before."""
    runs, observations = _plain_pair()

    (check,) = fc.forecast_check(runs, observations)

    assert check.basis == ForecastBasis.LATER_STACK


def test_added_frames_older_than_the_subset_run_are_fine_for_a_subset() -> None:
    """Only the night order matters, not the stacks' made times."""
    runs, observations = _plain_pair()
    assert (
        max(o.start_epoch_seconds or 0.0 for o in observations) < runs[0].measured_at.timestamp() + 10 * 86400
    )

    (check,) = fc.forecast_check(_subset_pair(runs), observations)

    assert check.outcome == ForecastOutcome.AS_FORECAST


def test_a_frame_from_the_same_night_as_the_subset_is_not_checkable() -> None:
    """An added frame must be from a later observing night."""
    runs, observations = _plain_pair(new_frame_epoch=OLD_FRAME_EPOCH + 3600.0)

    (check,) = fc.forecast_check(_subset_pair(runs), observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "not from a later observing night" in check.reason
    assert check.basis == ForecastBasis.EARLIER_NIGHTS_SUBSET


def test_a_subset_pair_needs_start_times_for_added_and_subset_frames() -> None:
    """Without a start time the night order cannot be shown."""
    runs, observations = _plain_pair()
    no_new_time = [
        o.model_copy(update={"start_epoch_seconds": None}) if o.frame_id.startswith("b") else o
        for o in observations
    ]
    no_old_time = [
        o.model_copy(update={"start_epoch_seconds": None}) if o.frame_id == "a0" else o for o in observations
    ]

    (new_check,) = fc.forecast_check(_subset_pair(runs), no_new_time)
    (old_check,) = fc.forecast_check(_subset_pair(runs), no_old_time)

    assert new_check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "have no start time" in new_check.reason
    assert old_check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "has no start time" in old_check.reason


def test_a_subset_with_a_frame_the_library_stack_lacks_is_not_checkable() -> None:
    """A subset not cut from the library stack is not compared."""
    runs, observations = _plain_pair()
    subset, library = _subset_pair(runs)
    changed = subset.model_copy(update={"member_frame_ids": ["z0", *subset.member_frame_ids[1:]]})
    observations = [*observations, _observation("z0")]

    (check,) = fc.forecast_check([changed, library], observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "was not cut from that stack" in check.reason


@pytest.mark.parametrize(
    ("first_identity", "second_identity", "changes", "expected_text"),
    [
        (OTHER_APERTURE_SET, APERTURE_SET, {}, "different aperture sets"),
        (None, APERTURE_SET, {}, "has no aperture-set identity"),
        (APERTURE_SET, APERTURE_SET, {"run1": {"flat": "flat_2.fits"}}, "The flat master changed"),
        (APERTURE_SET, APERTURE_SET, {"run2": {"bias": "bias_2.fits"}}, "The bias master changed"),
        (APERTURE_SET, APERTURE_SET, {"run2": {"siril": "1.5.0"}}, "The Siril version changed"),
        (APERTURE_SET, APERTURE_SET, {"run2": {"algorithm": 8}}, "The stacking algorithm version changed"),
    ],
)
def test_each_confound_still_applies_to_a_subset_pair(
    first_identity: str | None,
    second_identity: str,
    changes: dict[str, dict[str, object]],
    expected_text: str,
) -> None:
    """The aperture, calibration and software rules hold for a subset."""
    runs, observations = _plain_pair(
        run1_identity=first_identity, run2_identity=second_identity, calibration_changes=changes
    )

    (check,) = fc.forecast_check(_subset_pair(runs), observations)

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert expected_text in check.reason
    assert check.basis == ForecastBasis.EARLIER_NIGHTS_SUBSET


def test_a_subset_pair_with_no_calibration_record_or_another_noise_definition_is_confounded() -> None:
    """A missing calibration record or a new noise definition confounds."""
    runs, observations = _plain_pair(attach=False)
    (no_record,) = fc.forecast_check(_subset_pair(runs), observations)
    runs, observations = _plain_pair()
    subset, library = _subset_pair(runs)
    changed = subset.model_copy(update={"noise_definition_version": 4})

    (other_definition,) = fc.forecast_check([changed, library], observations)

    assert "has no calibration record" in no_record.reason
    assert "definition version 4 for run run1 and 3 for run run2" in other_definition.reason


def test_a_subset_pair_of_groups_follows_the_group_model_and_its_heaviest_group_rule() -> None:
    """A group subset uses the group model and the heaviest group rule."""
    runs, observations = _group_pair(_group_frames("b60_", 10, 60.0))
    (agreeing,) = fc.forecast_check(_subset_pair(runs), observations)
    runs, observations = _group_pair(_group_frames("b30_", 60, 30.0))

    (heavier,) = fc.forecast_check(_subset_pair(runs), observations)

    assert agreeing.outcome == ForecastOutcome.AS_FORECAST
    assert agreeing.weighting_mode == "group"
    assert agreeing.basis == ForecastBasis.EARLIER_NIGHTS_SUBSET
    assert heavier.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "heaviest exposure group changed from 60 s to 30 s" in heavier.reason


def _three_stacks(later_ratio: float) -> tuple[list[fc.StackRunRecord], list[FrameObservation]]:
    """Build a subset, its library stack and a later library stack.

    The subset (run ``sub``) and the library stack (run ``lib1``) were made on
    day 5; ``lib2`` was made on day 10 with 10 new frames.

    Parameters
    ----------
    later_ratio : `float`
        The measured noise of ``lib2`` over ``lib1``'s.

    Returns
    -------
    runs, observations : `tuple`
        The runs, with calibration records, and the frames.
    """
    night_one = _dark(20, "a")
    night_two = [_observation(f"b{i}") for i in range(20)]
    night_three = [
        _observation(f"c{i}", start_epoch_seconds=(START + timedelta(days=7)).timestamp()) for i in range(10)
    ]
    ids_one = [o.frame_id for o in night_one]
    ids_two = ids_one + [o.frame_id for o in night_two]
    ids_three = ids_two + [o.frame_id for o in night_three]
    lib1_noise = 0.010 * 0.5**0.5
    rows = _as_subset_rows(_run_rows("sub", 5, ids_one, [1.0] * 20, 0.010, row_id=1))
    rows += _run_rows("lib1", 5, ids_two, [1.0] * 40, lib1_noise, row_id=2)
    rows += _run_rows(
        "lib2", 10, ids_three, [1.0] * 50, lib1_noise * later_ratio * (40 / 50) ** 0.5, row_id=3
    )
    runs = fc.stack_runs_from_measurements(rows, include_earlier_nights=True)
    records = [_calibration(run.run_id) for run in runs]
    return fc.attach_calibration_identities(runs, records), night_one + night_two + night_three


def test_the_subset_pairs_with_the_library_run_it_was_cut_from() -> None:
    """The subset pairs with ``lib1``, not with the later ``lib2``."""
    runs, _observations = _three_stacks(1.0)

    ((subset, library),) = fc.subset_pairs(runs)

    assert (subset.run_id, library.run_id) == ("sub", "lib1")


def test_checks_come_in_time_order_with_the_subset_check_first_at_one_stack() -> None:
    """The subset check of ``lib1`` comes before the check ``lib2`` makes."""
    runs, observations = _three_stacks(1.0)

    checks = fc.forecast_check(runs, observations)

    assert [(c.stack_run_id, c.basis) for c in checks] == [
        ("lib1", ForecastBasis.EARLIER_NIGHTS_SUBSET),
        ("lib2", ForecastBasis.LATER_STACK),
    ]
    assert all(c.outcome == ForecastOutcome.AS_FORECAST for c in checks)
    assert fc.forecast_gate_state(checks) == ForecastGate.CONFIRMED


def test_a_later_real_check_that_agrees_confirms_the_gate() -> None:
    """A subset check opens the gate as exploratory; a real one confirms."""
    runs, observations = _three_stacks(1.0)
    only_subset = fc.forecast_check(runs[:2], observations)

    assert fc.forecast_gate_state(only_subset) == ForecastGate.EXPLORATORY
    assert fc.forecast_gate_state(fc.forecast_check(runs, observations)) == ForecastGate.CONFIRMED


def test_a_later_real_check_that_disagrees_closes_the_gate() -> None:
    """A real check that falls short closes the gate again."""
    runs, observations = _three_stacks(1.3)

    checks = fc.forecast_check(runs, observations)

    assert checks[0].outcome == ForecastOutcome.AS_FORECAST
    assert checks[1].outcome in (ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastOutcome.SHORT_KNOWN_REASON)
    assert fc.forecast_gate_state(checks) == ForecastGate.CLOSED


def test_a_subset_with_no_library_run_makes_no_pair() -> None:
    """A subset alone has nothing to be compared with."""
    runs, observations = _three_stacks(1.0)

    assert fc.subset_pairs(runs[:1]) == []
    assert fc.forecast_check(runs[:1], observations) == []


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ([], ForecastGate.CLOSED),
        ([(ForecastOutcome.AS_FORECAST, ForecastBasis.LATER_STACK)], ForecastGate.CONFIRMED),
        ([(ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastBasis.LATER_STACK)], ForecastGate.CLOSED),
        ([(ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET)], ForecastGate.EXPLORATORY),
        ([(ForecastOutcome.SHORT_KNOWN_REASON, ForecastBasis.EARLIER_NIGHTS_SUBSET)], ForecastGate.CLOSED),
        ([(ForecastOutcome.NOT_CHECKABLE, ForecastBasis.EARLIER_NIGHTS_SUBSET)], ForecastGate.CLOSED),
        (
            [
                (ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET),
                (ForecastOutcome.AS_FORECAST, ForecastBasis.LATER_STACK),
            ],
            ForecastGate.CONFIRMED,
        ),
        (
            [
                (ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET),
                (ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastBasis.LATER_STACK),
            ],
            ForecastGate.CLOSED,
        ),
        (
            [
                (ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET),
                (ForecastOutcome.NOT_CHECKABLE, ForecastBasis.LATER_STACK),
            ],
            ForecastGate.EXPLORATORY,
        ),
        (
            [
                (ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastBasis.LATER_STACK),
                (ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET),
            ],
            ForecastGate.EXPLORATORY,
        ),
        (
            [
                (ForecastOutcome.AS_FORECAST, ForecastBasis.LATER_STACK),
                (ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastBasis.EARLIER_NIGHTS_SUBSET),
            ],
            ForecastGate.CONFIRMED,
        ),
        (
            [
                (ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET),
                (ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastBasis.EARLIER_NIGHTS_SUBSET),
            ],
            ForecastGate.CLOSED,
        ),
    ],
)
def test_the_gate_state_follows_the_checks_in_order(
    sequence: list[tuple[ForecastOutcome, ForecastBasis]], expected: ForecastGate
) -> None:
    """The gate state follows the order of agreeing and failing checks."""
    checks = [_check_of(outcome, basis) for outcome, basis in sequence]

    assert fc.forecast_gate_state(checks) == expected


def _rows_as_the_library_writes_a_subset(rows: list[QualityMeasurement]) -> list[QualityMeasurement]:
    """Put a run's rows on the subjects the science library gives a subset.

    The formats are the library's: the subset stack's own subject
    ``T/_subsets/earlier-nights/earlier-nights.fits`` and a frame weight on
    ``<frame id>#earlier-nights``, all with the variant. The library does not
    export the frame-id helper at its top level, so the format is copied.

    Parameters
    ----------
    rows : `list` [`QualityMeasurement`]
        The rows `_run_rows` made for the subset's run.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        Copies on the subset's subjects.
    """
    moved = []
    for row in rows:
        if row.subject.level == SubjectLevel.STACK:
            subject_id = f"T/_subsets/{EARLIER_NIGHTS_VARIANT}/{EARLIER_NIGHTS_VARIANT}.fits"
        else:
            subject_id = f"{row.subject.subject_id}#{EARLIER_NIGHTS_VARIANT}"
        subject = row.subject.model_copy(update={"subject_id": subject_id, "variant": EARLIER_NIGHTS_VARIANT})
        moved.append(row.model_copy(update={"subject": subject}))
    return moved


class _StoreStandIn:
    """The ``quality`` part of the science library, over made-up rows."""

    def __init__(self, rows: list[QualityMeasurement], identities: list[CalibrationIdentities]) -> None:
        """Keep the rows and the calibration records.

        Parameters
        ----------
        rows : `list` [`QualityMeasurement`]
            Every stored row.
        identities : `list` [`CalibrationIdentities`]
            The stored calibration records.
        """
        self._rows = rows
        self._identities = identities

    def query(
        self,
        metric: str | None = None,
        level: str | None = None,
        variant: str | None = None,
        library_only: bool = False,
        **_: object,
    ) -> list[QualityMeasurement]:
        """Answer ``quality.query`` the way the library does for these filters.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The rows that match.
        """
        return [
            row
            for row in self._rows
            if (metric is None or row.metric == metric)
            and (level is None or row.subject.level.value == level)
            and (variant is None or row.subject.variant == variant)
            and not (library_only and row.subject.variant is not None)
        ]

    def calibration_identities(self, **_: object) -> list[CalibrationIdentities]:
        """Give the stored calibration records.

        Returns
        -------
        records : `list` [`CalibrationIdentities`]
            The records.
        """
        return self._identities


def test_rows_as_the_library_writes_a_subset_give_an_exploratory_gate() -> None:
    """Subset rows as the library writes them give an exploratory gate."""
    from types import SimpleNamespace

    from wayfindinglib.tasks.planning_tasks.target_report_inputs import forecast_gate_of_target

    old = _dark(20, "a")
    new = [_observation(f"b{i}") for i in range(20)]
    everyone = old + new
    library_rows = _run_rows(
        "run2", 5, [o.frame_id for o in everyone], [1.0] * 40, 0.010 * 0.5**0.5, row_id=2
    )
    subset_rows = _rows_as_the_library_writes_a_subset(
        _run_rows("sub", 5, [o.frame_id for o in old], [1.0] * 20, 0.010, row_id=1)
    )
    subset_subject = f"T/_subsets/{EARLIER_NIGHTS_VARIANT}/{EARLIER_NIGHTS_VARIANT}.fits"
    identities = [
        _calibration("run2"),
        _calibration("sub").model_copy(update={"subject_id": subset_subject}),
    ]
    store = _StoreStandIn([*library_rows, *subset_rows], identities)
    astrometrics = SimpleNamespace(quality=store)

    runs = fc.stack_runs_from_measurements([*library_rows, *subset_rows], include_earlier_nights=True)
    subset = next(run for run in runs if run.is_earlier_nights_subset)
    gate = forecast_gate_of_target(astrometrics, "T", store.query(library_only=True), everyone)

    assert subset.stack_subject_id == subset_subject
    assert subset.member_frame_ids == sorted(o.frame_id for o in old)
    assert gate == ForecastGate.EXPLORATORY
