"""Purpose: Tests for the fixed-pattern account and the setup history.

Description: Builds made-up stored rows for several targets, with the odd-even
difference rows, the per-aperture record's path, the line-pattern scatters and
the master noise rows, and checks:

* the run record reads the new rows (decision D21, D22);
* the account of a run names the shape that its per-aperture record shows and
  says how much the master flat and dark explain;
* the loss account's "cause unknown" text names the shape;
* a target with no fixed-pattern noise of its own starts from the median share
  of other targets of the same camera, optic and flat set, only with at least
  3 of them, never from another flat set, and the statement says so (D23).
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from astrometricslib import (
    CaptureSetting,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
    aperture_identity_to_number,
)
from astrometricslib.models.quality.calibration_identities import (
    CalibrationFileIdentity,
    CalibrationIdentities,
)
from wayfindinglib.analytics.result_quality import forecast as fc
from wayfindinglib.analytics.result_quality.aggregates import MeasuredNoiseCheck, _unknown_part
from wayfindinglib.analytics.result_quality.capture_overheads import CaptureOverheads
from wayfindinglib.analytics.result_quality.fixed_pattern_location import FixedPatternShape
from wayfindinglib.analytics.result_quality.noise_growth import (
    FIXED_PATTERN_BASIS_SETUP_HISTORY,
    MINIMUM_HISTORY_TARGETS,
    StackFrame,
    TakeMoreResult,
    WeightingMode,
    frames_to_cut_noise,
)
from wayfindinglib.analytics.result_quality.report_rules import (
    FIXED_PATTERN_SHAPE_RULES,
    fixed_pattern_shape_statements,
    more_frames_statement,
)

SETTING = CaptureSetting(camera="CamA", telescope="Scope", filter_name="L", exposure_seconds=60.0)
START = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
APERTURE_SET = "v3-156104df543d"
RECORD_PATH = "/library/T0/T0_stack_odd_even_apertures.json"
MEASURED = 1.0e-3
"""The normalized noise of every made-up stack."""


def _row(
    metric: str,
    subject_id: str,
    value: float | None,
    run_id: str,
    when: datetime,
    *,
    target_id: str = "T0",
    level: SubjectLevel = SubjectLevel.STACK,
    sample_count: int | None = None,
    reason: str = "",
) -> QualityMeasurement:
    """Build one stored row at definition version 1 (or the pinned one).

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=fc.PINNED_STACK_METRICS.get(metric, 1),
        subject=QualitySubject(level=level, subject_id=subject_id, target_id=target_id),
        status=MeasurementStatus.MEASURED if value is not None else MeasurementStatus.NOT_MEASURED,
        value=value,
        sample_count=sample_count,
        reason=reason,
        origin=MeasurementOrigin.MEASURED,
        run_id=run_id,
        measured_at=when,
    )


def _target_rows(
    target_id: str,
    share: float,
    *,
    day: int = 0,
    detail: bool = False,
    flat_noise: float | None = None,
    dark_noise: float | None = None,
) -> list[QualityMeasurement]:
    """Build the rows of one target's stack whose fixed-pattern share is known.

    The stack has 100 apertures of noise ``MEASURED`` (normalized, reference
    flux 1000 so stack units are 1000 times larger). The odd-even difference is
    set so that the full-stack random variance is ``(1 - share)`` of the
    measured one: with 40 effective frames and a variance factor of 0.1 the
    full-stack equivalent is the difference noise over 2.

    Parameters
    ----------
    target_id : `str`
        The target.
    share : `float`
        The fixed-pattern share of the measured variance, from 0 to 1.
    day : `int`, optional
        Days after the start.
    detail : `bool`, optional
        Add the per-aperture record, the four line-pattern rows and the master
        noise rows.
    flat_noise, dark_noise : `float` or `None`, optional
        The master noises in stack units, stored when ``detail``.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The stack's rows and the job's rows.
    """
    stack = f"{target_id}/{target_id}_stack"
    run = f"run_{target_id}"
    job = f"half_{target_id}"
    when = START + timedelta(days=day)
    later = when + timedelta(hours=1)

    def stack_row(metric: str, value: float | None, **extra: object) -> QualityMeasurement:
        """Build a row of the stack's own run.

        Returns
        -------
        row : `QualityMeasurement`
            The row.
        """
        return _row(metric, stack, value, run, when, target_id=target_id, **extra)  # type: ignore[arg-type]

    def job_row(metric: str, value: float | None, **extra: object) -> QualityMeasurement:
        """Build a row of the odd-even difference job.

        Returns
        -------
        row : `QualityMeasurement`
            The row.
        """
        return _row(metric, stack, value, job, later, target_id=target_id, **extra)  # type: ignore[arg-type]

    difference = 2.0 * MEASURED * float(np.sqrt(1.0 - share))
    rows = [
        stack_row(fc.APERTURE_SET_METRIC, aperture_identity_to_number(APERTURE_SET)),
        stack_row(fc.NORMALIZED_NOISE_METRIC, MEASURED, sample_count=100),
        stack_row(fc.NOISE_METRIC, MEASURED * 1000.0, sample_count=100),
        stack_row(fc.EXPECTED_NOISE_METRIC, MEASURED * 1000.0),
        stack_row(fc.REFERENCE_FLUX_METRIC, 1000.0),
        stack_row(fc.EFFECTIVE_FRAME_COUNT_METRIC, 40.0),
        job_row(fc.ODD_EVEN_DIFFERENCE_NORMALIZED_METRIC, difference, sample_count=100),
        job_row(fc.ODD_EVEN_DIFFERENCE_APERTURE_SET_METRIC, aperture_identity_to_number(APERTURE_SET)),
        job_row(fc.ODD_EVEN_DIFFERENCE_VARIANCE_FACTOR_METRIC, 0.1),
    ]
    if detail:
        rows += [
            job_row(
                fc.ODD_EVEN_APERTURE_RECORD_COUNT_METRIC,
                100.0,
                sample_count=100,
                reason=f"Per-aperture record: {RECORD_PATH}",
            ),
            job_row(fc.ODD_EVEN_DIFFERENCE_ROW_SCATTER_METRIC, 1.0, sample_count=200),
            job_row(fc.ODD_EVEN_DIFFERENCE_COLUMN_SCATTER_METRIC, 1.0, sample_count=300),
            job_row(fc.ODD_EVEN_SUM_ROW_SCATTER_METRIC, 1.7, sample_count=200),
            job_row(fc.ODD_EVEN_SUM_COLUMN_SCATTER_METRIC, 1.0, sample_count=300),
            stack_row(fc.FLAT_NOISE_IN_APERTURE_METRIC, flat_noise),
            stack_row(fc.EXPECTED_MASTER_DARK_NOISE_METRIC, dark_noise),
        ]
    return rows


def _identity(target_id: str, flat: str = "flat_1.fits") -> CalibrationIdentities:
    """Build the calibration record of one target's stack.

    Parameters
    ----------
    target_id : `str`
        The target.
    flat : `str`, optional
        The flat master's file name, which names the flat set.

    Returns
    -------
    record : `CalibrationIdentities`
        The record.
    """
    return CalibrationIdentities(
        runId=f"run_{target_id}",
        subjectId=f"{target_id}/{target_id}_stack",
        targetId=target_id,
        camera="CamA",
        optic="Scope",
        focalLengthMm=400.0,
        flatMasters=[CalibrationFileIdentity(path=flat, sizeBytes=10, modifiedNs=1)],
        biasMasters=[],
        darkMasters=[],
        sirilVersion="1.4.0",
        stackingAlgorithmVersion=7,
    )


def _library(
    shares: dict[str, float], flats: dict[str, str] | None = None
) -> tuple[list[QualityMeasurement], list[CalibrationIdentities]]:
    """Build the rows and calibration records of several targets.

    Parameters
    ----------
    shares : `dict` [`str`, `float`]
        The fixed-pattern share of each target.
    flats : `dict` [`str`, `str`], optional
        The flat master of a target; ``flat_1.fits`` for the others.

    Returns
    -------
    rows, identities : `tuple`
        All the rows and all the calibration records.
    """
    flats = flats or {}
    rows: list[QualityMeasurement] = []
    identities = []
    for target_id, share in shares.items():
        rows += _target_rows(target_id, share)
        identities.append(_identity(target_id, flats.get(target_id, "flat_1.fits")))
    return rows, identities


def _run_of(target_id: str, rows: list[QualityMeasurement], flat: str = "flat_1.fits") -> fc.StackRunRecord:
    """Build one target's newest run record with its calibration.

    Returns
    -------
    run : `StackRunRecord`
        The run.
    """
    runs = fc.attach_calibration_identities(
        fc.stack_runs_from_measurements([r for r in rows if r.subject.target_id == target_id]),
        [_identity(target_id, flat)],
    )
    return runs[-1]


def test_the_run_record_reads_the_record_path_the_scatters_and_the_master_noise() -> None:
    """The new rows reach the run: path, scatters, flat and dark."""
    rows = _target_rows("T0", 0.5, detail=True, flat_noise=0.9, dark_noise=0.4)
    run = fc.stack_runs_from_measurements(rows)[0]
    half = run.odd_even_difference
    assert half is not None
    assert half.aperture_record_path == RECORD_PATH
    assert half.aperture_record_count == 100
    assert half.line_scatters is not None
    assert half.line_scatters.sum_row == pytest.approx(1.7)
    assert half.line_scatters.row_count == 200
    assert half.line_scatters.column_count == 300
    assert run.flat_noise == pytest.approx(0.9)
    assert run.master_dark_noise == pytest.approx(0.4)
    assert not [name for name in half.problems if "scatter" in name or "record" in name]


def test_a_run_without_the_new_rows_still_reads() -> None:
    """Old stored runs have no record path, no scatters and no master noise."""
    run = fc.stack_runs_from_measurements(_target_rows("T0", 0.5))[0]
    assert run.odd_even_difference is not None
    assert run.odd_even_difference.aperture_record_path is None
    assert run.odd_even_difference.line_scatters is None
    assert run.flat_noise is None


def _record(apertures: int = 150, seed: int = 4) -> dict:
    """Make a per-aperture record with a flat-field-like radial excess.

    Returns
    -------
    record : `dict`
        The file content `load_odd_even_aperture_record` would give.
    """
    rng = np.random.default_rng(seed)
    entries = []
    for index in range(apertures):
        x = rng.uniform(0.0, 6000.0)
        y = rng.uniform(0.0, 4000.0)
        center = float(np.hypot(x - 3000.0, y - 2000.0))
        scale = 1.0 + 5.0 * (center / 3600.0)
        entries.append({
            "index": index,
            "x": x,
            "y": y,
            "distance_from_center_px": center,
            "distance_from_target_px": float(np.hypot(x - 1200.0, y - 900.0)),
            "sum": float(rng.normal() * scale),
            "difference": float(rng.normal()),
        })
    return {"version": 1, "apertures": entries}


def test_the_account_names_the_radial_shape_and_the_masters_share() -> None:
    """A radial record and master noise give the location and breakdown."""
    rows = _target_rows("T0", 0.5, detail=True, flat_noise=0.5, dark_noise=0.45)
    run = fc.stack_runs_from_measurements(rows)[0]
    fixed_pattern = fc.single_stack_fixed_pattern_noise(run)
    assert fixed_pattern is not None
    assert fixed_pattern.fixed_pattern_noise_detected
    seen = []

    def load(path: str) -> dict:
        """Hand back the made-up record and remember the path asked for.

        Returns
        -------
        record : `dict`
            The record.
        """
        seen.append(path)
        return _record()

    account = fc.fixed_pattern_account_for_run(run, fixed_pattern, load_record=load)
    assert seen == [RECORD_PATH]
    assert account is not None
    assert account.location is not None
    assert FixedPatternShape.RADIAL_TREND in account.location.significant_shapes
    assert account.calibration is not None
    # Fixed-pattern variance in stack units squared: half of (1.0e-3 x 1000)^2.
    assert account.calibration.fixed_pattern_variance == pytest.approx(0.5)
    assert account.calibration.flat_variance == pytest.approx(0.25)
    assert account.calibration.consistent_with_zero
    assert "lower bound" in account.text


def test_the_account_runs_the_shape_tests_only_for_a_found_pattern() -> None:
    """An absent pattern has no shape to name; the loader is not called."""
    rows = _target_rows("T0", 0.0, detail=True, flat_noise=0.1, dark_noise=0.1)
    run = fc.stack_runs_from_measurements(rows)[0]
    fixed_pattern = fc.single_stack_fixed_pattern_noise(run)
    assert fixed_pattern is not None
    assert not fixed_pattern.fixed_pattern_noise_detected

    def load(path: str) -> dict:
        """Fail if the record is read.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("the record must not be read")

    assert fc.fixed_pattern_account_for_run(run, fixed_pattern, load_record=load) is None


def test_the_loss_account_names_the_shape_in_place_of_no_listed_cause() -> None:
    """The cause-unknown text carries the account, not 'no listed cause'."""
    rows = _target_rows("T0", 0.5, detail=True, flat_noise=0.5, dark_noise=0.45)
    run = fc.stack_runs_from_measurements(rows)[0]
    account = fc.fixed_pattern_account_for_run(
        run, fc.single_stack_fixed_pattern_noise(run), load_record=lambda _path: _record()
    )
    inputs = fc.odd_even_difference_noise_inputs(run)
    assert inputs is not None
    check = MeasuredNoiseCheck.from_odd_even_difference(*inputs).model_copy(
        update={"fixed_pattern_account": account}
    )
    seconds, reason = _unknown_part(100.0, check)
    assert seconds is not None
    assert seconds > 0.0
    assert "trend with distance from the image center" in reason
    assert "No listed cause" not in reason
    plain, plain_reason = _unknown_part(100.0, MeasuredNoiseCheck.from_odd_even_difference(*inputs))
    assert plain == seconds
    assert "No listed cause accounts for it." in plain_reason


def test_shape_advice_is_given_for_radial_gradient_and_banding_only() -> None:
    """Flats, gradient and line pattern advice; nothing near a target."""
    rows = _target_rows("T0", 0.5, detail=True, flat_noise=0.5, dark_noise=0.45)
    run = fc.stack_runs_from_measurements(rows)[0]
    account = fc.fixed_pattern_account_for_run(
        run, fc.single_stack_fixed_pattern_noise(run), load_record=lambda _path: _record()
    )
    statements = fixed_pattern_shape_statements(account, SETTING)
    names = {statement.rule for statement in statements}
    assert FIXED_PATTERN_SHAPE_RULES[FixedPatternShape.RADIAL_TREND] in names
    assert FIXED_PATTERN_SHAPE_RULES[FixedPatternShape.BANDING] in names
    flats = next(s for s in statements if s.rule == "retake_flats")
    assert flats.message.startswith("Retake flats")
    assert flats.proposed_action is not None
    assert flats.proposed_action.calibration_frame_type == "flat"
    banding = next(s for s in statements if s.rule == "use_line_pattern_removal")
    assert banding.message.startswith("Use line pattern removal")
    assert FixedPatternShape.NEAR_TARGET not in FIXED_PATTERN_SHAPE_RULES
    assert fixed_pattern_shape_statements(None, SETTING) == []


def test_the_gradient_advice_text() -> None:
    """A gradient asks to check the gradient removal."""
    rows = _target_rows("T0", 0.5, detail=True, flat_noise=0.5, dark_noise=0.45)
    run = fc.stack_runs_from_measurements(rows)[0]
    rng = np.random.default_rng(8)
    entries = [
        {
            "x": (x := float(rng.uniform(0, 6000))),
            "y": (y := float(rng.uniform(0, 4000))),
            "distance_from_center_px": float(np.hypot(x - 3000, y - 2000)),
            "distance_from_target_px": float(np.hypot(x - 1200, y - 900)),
            "sum": float(rng.normal() + 2.5 * x / 6000.0 + 1.5 * y / 4000.0),
            "difference": float(rng.normal()),
        }
        for _ in range(120)
    ]
    account = fc.fixed_pattern_account_for_run(
        run,
        fc.single_stack_fixed_pattern_noise(run),
        load_record=lambda _path: {"version": 1, "apertures": entries},
    )
    statements = fixed_pattern_shape_statements(account, SETTING)
    assert any(
        s.rule == "check_gradient_removal" and s.message.startswith("Check gradient removal")
        for s in statements
    )


def test_the_share_of_a_run_is_one_minus_random_over_measured_variance() -> None:
    """Half its variance fixed gives a share of 0.5; none gives 0."""
    half = fc.stack_runs_from_measurements(_target_rows("T0", 0.5))[0]
    none = fc.stack_runs_from_measurements(_target_rows("T1", 0.0))[0]
    assert fc.fixed_pattern_share_of_run(half) == pytest.approx(0.5, abs=1e-9)
    assert fc.fixed_pattern_share_of_run(none) == pytest.approx(0.0, abs=1e-9)


def test_with_fewer_than_three_other_targets_there_is_no_history() -> None:
    """Two other targets of the setup are not enough to borrow from."""
    rows, identities = _library({"T1": 0.2, "T2": 0.3})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    run = _run_of("T0", _target_rows("T0", 0.0))
    assert MINIMUM_HISTORY_TARGETS == 3
    assert fc.history_fixed_pattern_noise(history, run, "T0") is None


def test_three_other_targets_give_their_median_and_the_result_is_marked() -> None:
    """The median share of 3 or more others starts the forecast, marked."""
    rows, identities = _library({"T1": 0.2, "T2": 0.3, "T3": 0.6, "T4": 0.4})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    run = _run_of("T0", _target_rows("T0", 0.0))
    result = fc.history_fixed_pattern_noise(history, run, "T0")
    assert result is not None
    assert result.basis == FIXED_PATTERN_BASIS_SETUP_HISTORY
    assert result.history_target_count == 4
    assert result.fixed_pattern_noise_detected
    assert result.fixed_pattern_variance_share == pytest.approx(0.35, abs=1e-9)
    assert result.fixed_pattern_to_random_variance_ratio == pytest.approx(0.35 / 0.65, abs=1e-9)
    assert "like other targets of this setup (median of 4 targets), not measured on this one" in result.reason


def test_the_target_s_own_share_is_left_out_of_its_median() -> None:
    """A target does not borrow from itself."""
    rows, identities = _library({"T0": 0.9, "T1": 0.2, "T2": 0.3, "T3": 0.4})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    run = _run_of("T0", rows)
    result = fc.history_fixed_pattern_noise(history, run, "T0")
    assert result is not None
    assert result.history_target_count == 3
    assert result.fixed_pattern_variance_share == pytest.approx(0.3, abs=1e-9)


def test_a_different_flat_set_has_no_history() -> None:
    """New flats change the key, so the target starts with no history."""
    rows, identities = _library({"T1": 0.2, "T2": 0.3, "T3": 0.4, "T4": 0.5})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    run = _run_of("T0", _target_rows("T0", 0.0), flat="flat_2.fits")
    assert fc.history_fixed_pattern_noise(history, run, "T0") is None


def test_targets_on_another_flat_set_do_not_count() -> None:
    """Only targets with the same flat set are pooled."""
    rows, identities = _library({"T1": 0.2, "T2": 0.3, "T3": 0.4, "T4": 0.9}, flats={"T4": "flat_2.fits"})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    run = _run_of("T0", _target_rows("T0", 0.0))
    result = fc.history_fixed_pattern_noise(history, run, "T0")
    assert result is not None
    assert result.history_target_count == 3
    assert result.fixed_pattern_variance_share == pytest.approx(0.3, abs=1e-9)


def test_a_zero_median_assumes_no_fixed_pattern() -> None:
    """Other targets with none give a marked result with nothing detected."""
    rows, identities = _library({"T1": 0.0, "T2": 0.0, "T3": 0.0})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    result = fc.history_fixed_pattern_noise(history, _run_of("T0", _target_rows("T0", 0.0)), "T0")
    assert result is not None
    assert not result.fixed_pattern_noise_detected


def test_history_is_used_only_when_the_target_has_no_measurement_of_its_own() -> None:
    """A measured share (even a measured none) is never replaced by history."""
    rows, identities = _library({"T1": 0.5, "T2": 0.5, "T3": 0.5})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    own_rows = _target_rows("T0", 0.0)
    own_run = _run_of("T0", own_rows)
    measured = fc.fixed_pattern_noise_for_runs([own_run], None, history, "T0")
    assert measured is not None
    assert measured.basis != FIXED_PATTERN_BASIS_SETUP_HISTORY
    # Remove the noise values so that nothing can be measured on the target.
    bare = own_run.model_copy(update={"odd_even_difference": None, "expected_noise": None})
    assumed = fc.fixed_pattern_noise_for_runs([bare], None, history, "T0")
    assert assumed is not None
    assert assumed.basis == FIXED_PATTERN_BASIS_SETUP_HISTORY
    assert fc.fixed_pattern_noise_for_runs([bare], None, None, "T0") is None


def _overheads() -> CaptureOverheads:
    """Build plain overheads for a statement.

    Returns
    -------
    overheads : `CaptureOverheads`
        Download, settle and autofocus times.
    """
    return CaptureOverheads(
        download_seconds=5.0,
        dither_settle_seconds=20.0,
        autofocus_seconds=120.0,
        download_from_records=False,
        dither_from_records=False,
        autofocus_from_records=False,
        gaps_used=0,
    )


def test_a_take_more_statement_on_assumed_fixed_pattern_noise_says_so() -> None:
    """The count's message and plain message carry the history sentence."""
    rows, identities = _library({"T1": 0.1, "T2": 0.15, "T3": 0.2})
    history = fc.fixed_pattern_shares_by_setup(rows, identities)
    assumed = fc.history_fixed_pattern_noise(history, _run_of("T0", _target_rows("T0", 0.0)), "T0")
    assert assumed is not None
    frames = [
        StackFrame(frame_id=f"f{i}", exposure_seconds=60.0, sky_rate_e_per_s_per_px=1.0, read_noise_e=3.0)
        for i in range(30)
    ]
    result = frames_to_cut_noise(
        frames, frames, WeightingMode.EQUAL, new_exposure_seconds=60.0, fixed_pattern=assumed
    )
    assert isinstance(result, TakeMoreResult)
    assert result.fixed_pattern_basis == FIXED_PATTERN_BASIS_SETUP_HISTORY
    assert result.fixed_pattern_history_targets == 3
    statement = more_frames_statement(result, SETTING, _overheads(), [])
    assert statement is not None
    sentence = (
        "assumes fixed-pattern noise like other targets of this setup (median of 3 targets), "
        "not measured on this one"
    )
    assert sentence in statement.message
    assert sentence in statement.plain_message


def test_the_cause_sentence_of_a_fixed_pattern_statement_gets_the_account() -> None:
    """The report adds the shape and the masters' share after the cause."""
    from wayfindinglib.analytics.result_quality.report_rules import (
        FIXED_PATTERN_CAUSE_TEXT,
        fixed_pattern_limited_statement,
    )
    from wayfindinglib.tasks.planning_tasks.target_report_tasks import _add_fixed_pattern_accounts

    rows = _target_rows("T0", 0.5, detail=True, flat_noise=0.5, dark_noise=0.45)
    run = fc.stack_runs_from_measurements(rows)[0]
    account = fc.fixed_pattern_account_for_run(
        run, fc.single_stack_fixed_pattern_noise(run), load_record=lambda _path: _record()
    )
    assert account is not None
    result = TakeMoreResult(
        target_noise_ratio=0.707,
        fixed_pattern_limited=True,
        lowest_reachable_ratio=0.8,
        fixed_pattern_variance_share=0.5,
        fixed_pattern_noise=0.7,
        fixed_pattern_basis="odd_even_difference",
    )
    statement = fixed_pattern_limited_statement(result, SETTING, [])
    assert FIXED_PATTERN_CAUSE_TEXT in statement.message
    updated = _add_fixed_pattern_accounts([statement], {SETTING: account})[0]
    assert account.text in updated.message
    assert "consistent with zero" in updated.reason
    untouched = _add_fixed_pattern_accounts([statement], {})[0]
    assert untouched.message == statement.message
