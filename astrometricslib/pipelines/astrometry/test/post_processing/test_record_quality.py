"""Red-team tests for the astrometry gates and their measurements.

Each gate has to fail on a run that is truly bad, pass on a good one, and
read "not checked" when it could not look.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import StorageError
from astrometricslib.models.quality.gate_result import GateStatus, failed_gate, passed_gate
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import MeasurementOrigin, MeasurementStatus, SubjectLevel
from astrometricslib.models.quality.quality_summary import AstrometryPipelineQualityMetrics
from astrometricslib.pipelines.astrometry.metrics import (
    ASTROMETRY_GATE_ASTROMETRIC_RESIDUAL,
    ASTROMETRY_GATE_CATALOG_LOOKUP,
    ASTROMETRY_GATE_CATALOG_MATCHES,
    ASTROMETRY_GATE_PLATE_SOLVE,
    ASTROMETRY_METRICS,
    ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL,
    ASTROMETRY_PLATE_SOLVE_ATTEMPTS,
    ASTROMETRY_STAR_WIDTH_GAUSSIAN_PX,
)
from astrometricslib.pipelines.astrometry.post_processing import record_quality as rg
from astrometricslib.pipelines.astrometry.test.conformance_fixture import astrometry_emitted_measurements
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext, make_run_id


def make_metrics(**changes: object) -> AstrometryPipelineQualityMetrics:
    """Build the metrics of a healthy run, with some fields changed.

    Parameters
    ----------
    **changes
        Fields to replace.

    Returns
    -------
    metrics : `AstrometryPipelineQualityMetrics`
        A run that found 80 stars and made 10 lookups, none failed.
    """
    fields: dict[str, object] = {
        "sources_detected": 80,
        "solve_attempted": True,
        "plate_solve_succeeded": True,
        "simbad_matched_count": 40,
        "remote_catalog_queries_attempted": 10,
        "remote_catalog_queries_failed": 0,
        "remote_catalog_circuit_breaker_tripped": False,
        "catalog_matched_star_count": 150,
        "catalog_match_separation_rms_arcsec": 1.0,
        "plate_scale_arcsec_per_pixel": 1.9,
        "star_fwhm_px": 3.0,
    }
    fields.update(changes)
    return AstrometryPipelineQualityMetrics(**fields)


def gates_for(**changes: object) -> dict:
    """Build the gates for a run with some metrics changed.

    Returns
    -------
    gates : `dict` [`str`, `GateResult`]
        The gates keyed by name.
    """
    return {gate.name: gate for gate in rg.astrometry_run_gates(make_metrics(**changes))}


def test_a_healthy_run_passes_both_gates() -> None:
    """A good solve, stars found and lookups working give all passes."""
    gates = gates_for()

    assert {gate.status for gate in gates.values()} == {GateStatus.PASSED}


def test_source_detection_gate_goes_red_when_no_star_was_found() -> None:
    """An image with no detected star fails and says so."""
    gate = gates_for(sources_detected=0)[rg.SOURCE_DETECTION_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.detail == "no stars were detected in the image"


def test_catalog_gate_goes_red_when_the_circuit_breaker_tripped() -> None:
    """Giving up on a catalog service fails the gate."""
    gate = gates_for(remote_catalog_circuit_breaker_tripped=True)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.FAILED


def test_catalog_gate_goes_red_when_half_the_lookups_failed() -> None:
    """Five of ten lookups failing is at the limit, so it fails."""
    gate = gates_for(remote_catalog_queries_failed=5)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert "5 of 10" in gate.detail


def test_catalog_gate_tolerates_an_occasional_failed_lookup() -> None:
    """One failure in ten passes, and the failure is on record."""
    gate = gates_for(remote_catalog_queries_failed=1)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.detail == "1 of 10 catalog lookup(s) failed"


def test_catalog_gate_is_not_checked_when_no_lookup_was_attempted() -> None:
    """No lookup means no check; that is not a pass."""
    gate = gates_for(remote_catalog_queries_attempted=0)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_a_breaker_that_was_already_open_means_no_lookup_was_made_not_a_failure() -> None:
    """With the breaker open and no lookup attempted, the run did not fail."""
    gate = gates_for(
        remote_catalog_circuit_breaker_tripped=True,
        remote_catalog_queries_attempted=0,
        remote_catalog_queries_failed=0,
    )[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
    assert "already marked unreachable" in gate.detail


def test_the_residual_gate_goes_red_when_the_fit_misses_by_more_than_half_a_star() -> None:
    """A 5 arcsec residual with 1.9 arcsec pixels and 3 pixel stars is 88%."""
    gate = gates_for(catalog_match_separation_rms_arcsec=5.0)[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(5.0 / (1.9 * 3.0))
    assert "may be wrong" in gate.detail


def test_the_residual_gate_passes_a_good_fit_with_the_ratio_on_record() -> None:
    """A 1 arcsec residual is 18% of a star's width."""
    gate = gates_for()[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(1.0 / (1.9 * 3.0))


@pytest.mark.parametrize(
    "missing",
    [
        {"catalog_match_separation_rms_arcsec": None},
        {"plate_scale_arcsec_per_pixel": None},
        {"star_fwhm_px": None},
    ],
)
def test_the_residual_gate_is_not_checked_when_a_number_it_needs_is_missing(missing: dict) -> None:
    """Without the residual, plate scale or star width, the fit is unjudged."""
    assert gates_for(**missing)[rg.RESIDUAL_GATE_NAME].status is GateStatus.NOT_CHECKED


def test_the_matches_gate_goes_red_when_too_few_stars_were_matched() -> None:
    """Nineteen matched stars is one short of the minimum."""
    gate = gates_for(catalog_matched_star_count=19)[rg.MATCHED_STARS_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert "only 19 star(s)" in gate.detail
    assert gates_for(catalog_matched_star_count=20)[rg.MATCHED_STARS_GATE_NAME].status is GateStatus.PASSED


def test_the_matches_gate_is_not_checked_when_the_image_was_not_solved() -> None:
    """An unsolved image has no matches to count; plate_solve reports it."""
    gate = gates_for(plate_solve_succeeded=False, catalog_matched_star_count=0)[rg.MATCHED_STARS_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_the_residual_gate_judges_the_plate_solve_fit_when_the_solver_gave_one() -> None:
    """A good fit passes even when the catalog separation alone would fail."""
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=0.5,
        plate_solve_matched_star_count=42,
        catalog_match_separation_rms_arcsec=8.0,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(0.5 / (1.9 * 3.0))
    assert "plate-solve fit residual" in gate.detail
    assert "42 matched stars" in gate.detail
    assert "catalog match separation" not in gate.detail


def test_the_residual_gate_fails_a_bad_fit_even_when_the_catalog_separation_is_small() -> None:
    """A fit that misses by 5 arcsec fails, whatever the catalog proxy says."""
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=5.0,
        catalog_match_separation_rms_arcsec=1.0,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(5.0 / (1.9 * 3.0))
    assert "plate-solve fit residual" in gate.detail


def test_the_residual_gate_falls_back_to_the_catalog_separation_and_says_so() -> None:
    """With no fit residual, the catalog proxy is judged and named."""
    passed = gates_for(catalog_match_separation_rms_arcsec=1.0)[rg.RESIDUAL_GATE_NAME]
    failed = gates_for(catalog_match_separation_rms_arcsec=5.0)[rg.RESIDUAL_GATE_NAME]

    assert passed.status is GateStatus.PASSED
    assert "catalog match separation" in passed.detail
    assert "reported no fit residual" in passed.detail
    assert failed.status is GateStatus.FAILED
    assert "catalog match separation" in failed.detail
    assert "reported no fit residual" in failed.detail


def test_the_residual_gate_reads_the_older_field_when_the_new_proxy_is_absent() -> None:
    """A summary saved before the rename still gives the gate its proxy."""
    gate = gates_for(catalog_match_separation_rms_arcsec=None, astrometric_residual_rms_arcsec=5.0)[
        rg.RESIDUAL_GATE_NAME
    ]

    assert gate.status is GateStatus.FAILED
    assert "catalog match separation" in gate.detail


def test_the_residual_gate_is_not_checked_with_neither_a_fit_nor_a_catalog_separation() -> None:
    """No fit residual and no catalog match leave nothing to judge."""
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=None,
        catalog_match_separation_rms_arcsec=None,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_the_residual_gate_judges_the_gaia_fit_before_the_solvers_own_residual() -> None:
    """A solution that fits Gaia badly fails, whatever the solver reports.

    Alnath's solution agreed with itself (a small fit residual) and was 2.7
    arcseconds from Gaia. The gate had judged only the solver's residual.
    """
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=0.5,
        plate_solve_matched_star_count=24,
        gaia_fit_rms_arcsec=7.0,
        gaia_fit_star_count=200,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert "200 Gaia stars" in gate.detail
    assert "independent of the plate solver" in gate.detail


def test_a_good_gaia_fit_passes_even_when_the_catalog_separation_is_large() -> None:
    """The Gaia fit is the residual judged; the separation is a side number."""
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=None,
        catalog_match_separation_rms_arcsec=8.0,
        gaia_fit_rms_arcsec=0.3,
        gaia_fit_star_count=150,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert "150 Gaia stars" in gate.detail


# --- Measurements stored next to the gates ---------------------------------

MEASURED_AT = datetime(2026, 10, 22, tzinfo=UTC)


def _declared_names() -> set[str]:
    """Name every metric the astrometry pipeline declares.

    Returns
    -------
    names : `set` [`str`]
        The declared metric names.
    """
    return {spec.name for spec in ASTROMETRY_METRICS}


def _context(job_id: str | None = "job-1") -> PipelineRunContext:
    """Build a run context for the tests.

    Returns
    -------
    context : `PipelineRunContext`
        A context for target ``M 99`` at a fixed time.
    """
    return PipelineRunContext("M 99", "astrometry", "1.4.0", make_run_id(job_id), job_id, MEASURED_AT)


def _all_gates(metrics: AstrometryPipelineQualityMetrics) -> list:
    """Build the plate-solve gate and the four run gates.

    Returns
    -------
    gates : `list` [`GateResult`]
        The five gates, as the runner records them.
    """
    first = (
        passed_gate(rg.PLATE_SOLVE_GATE_NAME)
        if metrics.plate_solve_succeeded
        else failed_gate(rg.PLATE_SOLVE_GATE_NAME, "plate solve failed")
    )
    return [first, *rg.astrometry_run_gates(metrics)]


def test_the_record_step_emits_every_declared_metric_once() -> None:
    """A fully measured run gives one measurement per declared metric."""
    measurements = astrometry_emitted_measurements()

    assert [m.metric for m in measurements].count(ASTROMETRY_GATE_PLATE_SOLVE) == 1
    assert {m.metric for m in measurements} == _declared_names()
    assert len(measurements) == len(_declared_names())
    assert {m.origin for m in measurements} == {MeasurementOrigin.MEASURED}
    level_by_metric = {m.metric: m.subject.level for m in measurements}
    assert level_by_metric[ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL] is SubjectLevel.STACK
    assert level_by_metric[ASTROMETRY_STAR_WIDTH_GAUSSIAN_PX] is SubjectLevel.STACK
    assert level_by_metric[ASTROMETRY_PLATE_SOLVE_ATTEMPTS] is SubjectLevel.RUN
    assert level_by_metric[ASTROMETRY_GATE_PLATE_SOLVE] is SubjectLevel.RUN
    stack_ids = {m.subject.subject_id for m in measurements if m.subject.level is SubjectLevel.STACK}
    assert stack_ids == {"M 99/M99_Stacked.fits"}


def test_a_run_with_no_stack_file_name_stores_no_stack_values() -> None:
    """A stack value is never filed under the run when it has no name."""
    metrics = make_metrics()
    measurements = rg.astrometry_run_measurements(metrics, _all_gates(metrics), _context(), None)

    assert {m.subject.level for m in measurements} == {SubjectLevel.RUN}
    assert ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL not in {m.metric for m in measurements}


def test_an_unsolved_run_leaves_missing_values_not_measured_with_a_reason() -> None:
    """No solve means no scale, no residual and a failed plate-solve gate."""
    metrics = make_metrics(plate_solve_succeeded=False, plate_scale_arcsec_per_pixel=None)
    by_metric = {
        m.metric: m
        for m in rg.astrometry_run_measurements(metrics, _all_gates(metrics), _context(), "M99_Stacked.fits")
    }

    scale = by_metric[ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL]
    assert scale.status is MeasurementStatus.NOT_MEASURED
    assert scale.value is None
    assert scale.reason
    solve = by_metric[ASTROMETRY_GATE_PLATE_SOLVE]
    assert solve.check_outcome is GateStatus.FAILED
    assert solve.value == pytest.approx(0.0)
    matches = by_metric[ASTROMETRY_GATE_CATALOG_MATCHES]
    assert matches.check_outcome is GateStatus.NOT_CHECKED
    assert matches.status is MeasurementStatus.NOT_MEASURED


def test_the_residual_gate_stores_the_fraction_of_a_star_width_and_its_limit() -> None:
    """The stored number is residual over star width, with the 0.5 limit."""
    metrics = make_metrics(catalog_match_separation_rms_arcsec=5.7)
    by_metric = {
        m.metric: m
        for m in rg.astrometry_run_measurements(metrics, _all_gates(metrics), _context(), "M99_Stacked.fits")
    }

    residual = by_metric[ASTROMETRY_GATE_ASTROMETRIC_RESIDUAL]
    assert residual.check_outcome is GateStatus.FAILED
    assert residual.value == pytest.approx(5.7 / (1.9 * 3.0))
    assert residual.check_limit is not None
    assert residual.check_limit.value == pytest.approx(rg.MAXIMUM_RESIDUAL_FRACTION_OF_FWHM)
    assert residual.check_limit.tier is LimitTier.PHYSICAL_BUDGET


def test_the_lookup_gate_always_stores_the_failed_share() -> None:
    """A tripped breaker stores the failed share, as a pass does."""
    tripped = make_metrics(
        remote_catalog_circuit_breaker_tripped=True,
        remote_catalog_queries_attempted=10,
        remote_catalog_queries_failed=3,
    )
    by_metric = {
        m.metric: m
        for m in rg.astrometry_run_measurements(tripped, _all_gates(tripped), _context(), "M99_Stacked.fits")
    }

    lookup = by_metric[ASTROMETRY_GATE_CATALOG_LOOKUP]
    assert lookup.check_outcome is GateStatus.FAILED
    assert lookup.value == pytest.approx(0.3)


def _use_temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualityStore:
    """Point the quality store at a temporary library.

    Returns
    -------
    store : `QualityStore`
        The store on the temporary library's database.
    """
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    return QualityStore(str(tmp_path / "astrometrics.db"))


def test_a_record_stores_one_row_per_metric_and_a_repeat_stores_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first record writes every metric; the second writes none."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    metrics = make_metrics()
    gates = _all_gates(metrics)

    first = rg.record_astrometry_quality(metrics, gates, _context("job-9"), "M99_Stacked.fits")
    second = rg.record_astrometry_quality(metrics, gates, _context("job-9"), "M99_Stacked.fits")
    rows = [row for row in store.query(target_id="M 99", latest_only=False) if row.run_id == "job-9"]

    assert first == len(_declared_names())
    assert second == 0
    assert {row.metric for row in rows} == _declared_names()
    assert len(rows) == len(_declared_names())


def test_validate_output_records_the_run_and_a_storage_failure_does_not_fail_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """validate_output stores the run and survives a broken store."""
    from astrometricslib.models.target import Target
    from astrometricslib.pipelines.astrometry.test.test_plate_solve_gate import validate_with_options

    store = _use_temporary_store(tmp_path, monkeypatch)
    stacked = SimpleNamespace(header={}, path=str(tmp_path / "Test Target_Stacked.fits"))
    summary = validate_with_options(
        monkeypatch, {"job_id": "job-3", "measured_at": MEASURED_AT}, object(), image=stacked
    )
    rows = store.query(target_id=Target(id="Test Target").id, latest_only=False)

    assert summary.gates
    assert {row.metric for row in rows} == _declared_names()
    stack_subjects = {row.subject.subject_id for row in rows if row.subject.level is SubjectLevel.STACK}
    assert stack_subjects == {"Test Target/Test Target_Stacked.fits"}

    mocker.patch(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.record_measurements",
        side_effect=StorageError("disk full"),
    )
    broken = validate_with_options(monkeypatch, {"job_id": "job-4", "measured_at": MEASURED_AT}, object())
    assert broken.gates
