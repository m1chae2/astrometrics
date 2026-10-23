"""Tests that the spectroscopy pipeline stores its quality as measurements.

The pipeline declares its metrics in `metrics.py` and emits them in
`post_processing/record_quality.py`. These tests check that the record step
emits each declared metric exactly once, that a value the run could not make
is stored as not measured (never as a default), that gates keep their outcome
and limit, that no numeric checkpoint metric is left undeclared, and that the
single-image runner and the session batch both write one row per metric into a
temporary quality store.
"""

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.quality.gate_result import GateStatus, failed_gate
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import MeasurementOrigin, MeasurementStatus, SubjectLevel
from astrometricslib.models.quality.quality_summary import SpectroscopyPipelineQualityMetrics
from astrometricslib.models.target import Target
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.spectroscopy import batch, runner
from astrometricslib.pipelines.spectroscopy import metrics as spectroscopy_metrics
from astrometricslib.pipelines.spectroscopy.metrics import (
    SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT,
    SPECTROSCOPY_GATE_CATALOG_AGREEMENT,
    SPECTROSCOPY_GATE_SPECTRA_EXTRACTED,
    SPECTROSCOPY_GATE_WAVELENGTH_SCALE,
    SPECTROSCOPY_METRICS,
    SPECTROSCOPY_WAVELENGTH_SCALE_RMS_AFTER,
    gaia_xp_band_metric_name,
    stage_metric_name,
)
from astrometricslib.pipelines.spectroscopy.post_processing import record_quality as rg
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import (
    COMPARISON_BANDS_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.test.conformance_fixture import (
    MEASURED_AT,
    healthy_gates,
    healthy_summary_metrics,
    run_context,
)
from astrometricslib.pipelines.spectroscopy.test.post_processing.test_record_quality import make_star

PIPELINE_FOLDER = Path(spectroscopy_metrics.__file__).parent

# The checkpoint each source file adds numbers to.
STAGE_BY_SOURCE_FILE = {
    "pre_processing/assess_raw_frame_quality.py": "raw_frame",
    "pre_processing/assess_input_quality.py": "pre_processing",
    "pre_processing/measured_line_spread.py": "pre_processing",
    "pre_processing/wavelength_zero_point.py": "pre_processing",
    "processing/assess_processing_quality.py": "processing",
    "post_processing/assess_output_quality.py": "post_processing",
    "post_processing/compare_to_gaia_xp.py": "post_processing",
}

# The one unit a checkpoint uses for a yes/no verdict. Verdicts are not
# measurements (plan section 3.5), so they have no declared metric.
VERDICT_UNIT = "flag"

# Checkpoint metrics whose name is built from a variable, so the scan below
# cannot read it. Each is declared by name.
BUILT_NAME_METRICS = (
    ("processing", "hbeta_equivalent_width_angstrom"),
    ("processing", "halpha_equivalent_width_angstrom"),
)


def _declared_names() -> set[str]:
    """Give the names of every metric the pipeline declares.

    Returns
    -------
    names : `set` [`str`]
        The declared metric names.
    """
    return {spec.name for spec in SPECTROSCOPY_METRICS}


def _numeric_checkpoint_metrics() -> set[tuple[str, str]]:
    """Find every numeric checkpoint metric in the pipeline's source.

    Returns
    -------
    pairs : `set` [`tuple` [`str`, `str`]]
        ``(stage, metric name)`` for each ``metric("name", value, "unit")``
        call whose unit is not the verdict unit and whose name is a string
        literal.
    """
    pairs: set[tuple[str, str]] = set()
    for relative_path, stage in STAGE_BY_SOURCE_FILE.items():
        tree = ast.parse((PIPELINE_FOLDER / relative_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "metric"
            ):
                continue
            if not (
                node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            ):
                continue
            unit_node = node.args[2] if len(node.args) > 2 else None
            if isinstance(unit_node, ast.Constant) and unit_node.value == VERDICT_UNIT:
                continue
            pairs.add((stage, node.args[0].value))
    return pairs


def test_a_healthy_run_emits_one_measurement_per_declared_metric() -> None:
    """Every declared metric is emitted once, about the run, as measured."""
    measurements = rg.spectroscopy_run_measurements(healthy_summary_metrics(), healthy_gates(), run_context())

    names = [m.metric for m in measurements]
    assert len(names) == len(set(names))
    assert set(names) == _declared_names()
    assert all(m.subject.level is SubjectLevel.RUN for m in measurements)
    assert all(m.origin is MeasurementOrigin.MEASURED and m.run_id == "job-1" for m in measurements)
    assert all(m.pipeline_version == "1.6.0" and m.measured_at == MEASURED_AT for m in measurements)
    assert all(m.status is MeasurementStatus.MEASURED for m in measurements)


def test_every_numeric_checkpoint_metric_has_a_declared_median() -> None:
    """A numeric checkpoint metric cannot be left out of the table."""
    declared_pairs = set(spectroscopy_metrics.STAGE_METRIC_KEYS)
    found = _numeric_checkpoint_metrics() | set(BUILT_NAME_METRICS)

    assert found - declared_pairs == set(), "a checkpoint metric has no declared run median"
    assert declared_pairs - found == set(), "a declared run median matches no checkpoint metric"
    declared_names = _declared_names()
    assert all(stage_metric_name(stage, key) in declared_names for stage, key in declared_pairs)


def test_every_numeric_summary_field_is_declared() -> None:
    """Each numeric summary field has a metric; verdicts have none."""
    verdict_fields = {"zero_order_saturation_flagged", "trail_width_profile_available"}
    per_star_or_rollup = {"flagged_spectral_classifications", "stage_quality_summary"}
    assert verdict_fields | per_star_or_rollup <= set(SpectroscopyPipelineQualityMetrics.model_fields)
    declared = _declared_names()
    for name in (
        "zero_order_saturated_max_fraction",
        "dispersion_angle_first_star_deg",
        "trail_width_median_px",
        "catalog_matched_star_count",
        "position_only_star_count",
        "unresolved_star_count",
        "poor_match_classification_count",
        "ambiguous_classification_count",
    ):
        assert f"spectroscopy.{name}" in declared
    assert {
        f"spectroscopy.gate_{name}"
        for name in (
            rg.SPECTRA_GATE_NAME,
            rg.ZERO_ORDER_GATE_NAME,
            rg.CLASSIFICATION_GATE_NAME,
            rg.CATALOG_GATE_NAME,
            rg.FEATURE_GATE_NAME,
            rg.RESOLUTION_GATE_NAME,
            rg.PROCESSING_QUALITY_GATE_NAME,
            "wavelength_scale",
            "gaia_xp_agreement",
        )
    } <= declared


def test_the_declared_gaia_bands_are_the_comparison_bands() -> None:
    """The metrics file keeps its own copy of the band edges; it must agree."""
    assert tuple((int(start), int(end)) for start, end in COMPARISON_BANDS_ANGSTROM) == (
        spectroscopy_metrics.GAIA_XP_BAND_EDGES_ANGSTROM
    )


def test_a_run_with_nothing_measured_stores_not_measured_with_a_reason() -> None:
    """Missing roll-ups and totals are never stored as a default zero."""
    empty = SpectroscopyPipelineQualityMetrics()
    measurements = rg.spectroscopy_run_measurements(empty, [], run_context())
    by_metric = {m.metric: m for m in measurements}

    assert set(by_metric) == _declared_names() - {
        name for name in _declared_names() if name.startswith("spectroscopy.gate_")
    }
    for name in (
        SPECTROSCOPY_WAVELENGTH_SCALE_RMS_AFTER,
        stage_metric_name("processing", "classification_rms"),
        gaia_xp_band_metric_name(0, "median_ratio"),
        "spectroscopy.zero_order_saturated_max_fraction",
    ):
        assert by_metric[name].status is MeasurementStatus.NOT_MEASURED
        assert by_metric[name].value is None
        assert by_metric[name].reason
    # The two classification counts are real zero counts, not missing values.
    assert by_metric["spectroscopy.poor_match_classification_count"].value == pytest.approx(0.0)


def test_the_batch_path_does_not_store_default_identification_counts() -> None:
    """The session batch leaves the identification counts unmeasured."""
    measurements = rg.spectroscopy_run_measurements(
        healthy_summary_metrics(), healthy_gates(), run_context(), star_identification_counted=False
    )
    by_metric = {m.metric: m for m in measurements}

    assert by_metric[SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT].status is MeasurementStatus.NOT_MEASURED
    assert "does not count" in by_metric[SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT].reason
    assert by_metric["spectroscopy.unresolved_star_count"].value is None
    assert by_metric["spectroscopy.trail_width_median_px"].value == pytest.approx(3.1)


def test_gates_keep_their_outcome_value_and_limit() -> None:
    """A passed, a failed and an unchecked gate each read back correctly."""
    gates = healthy_gates()
    disagreeing = failed_gate(rg.CATALOG_GATE_NAME, "2 of 4 disagree", 2.0, 0.0, "within 20 subtype steps")
    gates = [disagreeing if gate.name == rg.CATALOG_GATE_NAME else gate for gate in gates]
    gates = [gate for gate in gates if gate.name != "wavelength_scale"] + [rg.wavelength_scale_gate(None)]
    by_metric = {
        m.metric: m for m in rg.spectroscopy_run_measurements(healthy_summary_metrics(), gates, run_context())
    }

    catalog = by_metric[SPECTROSCOPY_GATE_CATALOG_AGREEMENT]
    assert catalog.check_outcome is GateStatus.FAILED
    assert catalog.value == pytest.approx(2.0)
    assert catalog.check_limit is not None
    assert catalog.check_limit.value == pytest.approx(0.0)
    assert catalog.check_limit.tier is not LimitTier.VALIDATED_DATASET
    spectra = by_metric[SPECTROSCOPY_GATE_SPECTRA_EXTRACTED]
    assert spectra.check_outcome is GateStatus.PASSED
    assert spectra.value == pytest.approx(4.0)
    scale = by_metric[SPECTROSCOPY_GATE_WAVELENGTH_SCALE]
    assert scale.check_outcome is GateStatus.NOT_CHECKED
    assert scale.status is MeasurementStatus.NOT_MEASURED
    assert scale.value is None
    assert "at least" in scale.reason


def test_no_gate_limit_claims_a_validated_data_set() -> None:
    """Every limit here is a design estimate, so none may be validated."""
    measurements = rg.spectroscopy_run_measurements(healthy_summary_metrics(), healthy_gates(), run_context())
    limits = [m.check_limit for m in measurements if m.check_limit is not None]

    assert len(limits) == 9
    assert all(limit.tier is not LimitTier.VALIDATED_DATASET for limit in limits)


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


def test_the_single_image_runner_writes_one_row_per_metric_and_a_repeat_writes_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate_output stores the run once; storing again adds nothing."""
    from types import SimpleNamespace

    from astrometricslib.pipelines.shared.star_recording import StarIdentificationBreakdown

    store = _use_temporary_store(tmp_path, monkeypatch)
    spectroscopy = SimpleNamespace(
        last_run_zero_order_saturation_fractions=[0.0],
        config=SimpleNamespace(camera=SimpleNamespace(name="ZWO ASI 533MM Pro")),
    )
    result = Result(
        stellar_objects=[make_star(), make_star("K0V")],
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=2, position_only=0, unresolved=0
            ),
            "spectroscopy": spectroscopy,
            "flagged_spectral_classifications": [],
        },
    )
    target = Target(id="Row Target")
    request = PipelineRequest(
        target=target, catalog_access=None, options={"job_id": "job-7", "measured_at": MEASURED_AT}
    )

    summary = runner.SpectroscopyPipelineAdapter().validate_output(request, result)
    rows = [row for row in store.query(target_id=target.id, latest_only=False) if row.run_id == "job-7"]

    assert {row.metric for row in rows} == _declared_names()
    assert len(rows) == len(_declared_names())
    by_metric = {row.metric: row for row in rows}
    assert by_metric[SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT].value == pytest.approx(2.0)
    assert by_metric[stage_metric_name("processing", "classification_rms")].value == pytest.approx(0.05)
    context = PipelineRunContext(target.id, "spectroscopy", "1.6.0", "job-7", "job-7", MEASURED_AT)
    again = rg.record_spectroscopy_quality(summary.spectroscopy_metrics, list(summary.gates), context)
    assert again == 0


def test_a_run_without_a_time_stores_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no measured_at the runner stores no rows."""
    from types import SimpleNamespace

    from astrometricslib.pipelines.shared.star_recording import StarIdentificationBreakdown

    store = _use_temporary_store(tmp_path, monkeypatch)
    spectroscopy = SimpleNamespace(
        last_run_zero_order_saturation_fractions=[],
        config=SimpleNamespace(camera=SimpleNamespace(name="ZWO ASI 533MM Pro")),
    )
    result = Result(
        stellar_objects=[make_star()],
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=1, position_only=0, unresolved=0
            ),
            "spectroscopy": spectroscopy,
            "flagged_spectral_classifications": [],
        },
    )
    target = Target(id="Timeless Target")
    request = PipelineRequest(target=target, catalog_access=None, options={})

    summary = runner.SpectroscopyPipelineAdapter().validate_output(request, result)

    assert summary.gates
    assert store.query(target_id=target.id, latest_only=False) == []


def test_a_storage_failure_does_not_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """A broken store is logged and the summary is still returned."""
    from types import SimpleNamespace

    from astrometricslib.foundation.errors import StorageError
    from astrometricslib.pipelines.shared.star_recording import StarIdentificationBreakdown

    _use_temporary_store(tmp_path, monkeypatch)
    mocker.patch(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.record_measurements",
        side_effect=StorageError("disk full"),
    )
    spectroscopy = SimpleNamespace(
        last_run_zero_order_saturation_fractions=[],
        config=SimpleNamespace(camera=SimpleNamespace(name="ZWO ASI 533MM Pro")),
    )
    result = Result(
        stellar_objects=[make_star()],
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=1, position_only=0, unresolved=0
            ),
            "spectroscopy": spectroscopy,
            "flagged_spectral_classifications": [],
        },
    )
    request = PipelineRequest(
        target=Target(id="Broken Store"), catalog_access=None, options={"measured_at": MEASURED_AT}
    )

    summary = runner.SpectroscopyPipelineAdapter().validate_output(request, result)

    assert summary.gates


def test_the_session_batch_writes_one_row_per_metric(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The batch roll-up stores the run, identification counts unmeasured."""
    from astrometricslib.utilities import parallel_batch

    store = _use_temporary_store(tmp_path, monkeypatch)
    target = Target(id="Batch Target")
    summary = parallel_batch.BatchRunSummary(
        succeeded=["a.fits"],
        failed=[],
        results={
            "a.fits": {
                "status": "success",
                "stars_processed": 1,
                "dispersion_angles": [12.5],
                "trail_widths": [3.0, 4.0],
                "zero_order_saturation_fractions": [0.0],
            }
        },
    )
    from types import SimpleNamespace

    session = SimpleNamespace(id="Batch Target:2026-01-01:800:0", frame_paths=["a.fits"])

    batch._attach_spectroscopy_quality_summary(
        target, summary, [(session, SimpleNamespace())], "job-9", datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    )
    rows = [row for row in store.query(target_id=target.id, latest_only=False) if row.run_id == "job-9"]

    assert {row.metric for row in rows} == _declared_names()
    by_metric = {row.metric: row for row in rows}
    assert by_metric[SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT].value is None
    assert by_metric["spectroscopy.trail_width_median_px"].value == pytest.approx(3.5)
    assert by_metric["spectroscopy.dispersion_angle_first_star_deg"].value == pytest.approx(12.5)
