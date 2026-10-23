"""Tests that a bad number or a bug in a record step never fails a science run.

Each pipeline's ``record_*_quality`` function builds the run's measurements and
stores them. Both parts must be safe: a NaN in a summary field must become a
``not_measured`` record with a reason, and any exception while building the
rows must be logged and swallowed, as stacking's record step does. These tests
run the four pipelines that share the guard (`build_and_record_measurements`).
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from pytest_mock import MockerFixture

from astrometricslib.models.quality.gate_result import GateResult, GateStatus
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.asteroid_detection import record_quality as asteroid_record
from astrometricslib.pipelines.astrometry.post_processing import record_quality as astrometry_record
from astrometricslib.pipelines.astrometry.test.conformance_fixture import astrometry_fixture_run
from astrometricslib.pipelines.photometry.post_processing import record_quality as photometry_record
from astrometricslib.pipelines.photometry.test.conformance_fixture import (
    photometry_fixture_gates,
    photometry_fixture_inputs,
    photometry_fixture_metrics,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    NOT_FINITE_REASON,
    PipelineRunContext,
)
from astrometricslib.pipelines.spectroscopy.post_processing import record_quality as spectroscopy_record
from astrometricslib.pipelines.spectroscopy.test.conformance_fixture import (
    healthy_gates,
    healthy_summary_metrics,
    run_context,
)

STORE_FUNCTION = "astrometricslib.pipelines.shared.quality.pipeline_measurements.record_measurements"
MEASURED_AT = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
ASTEROID_COUNTS: dict[str, float] = {
    "frames_with_wcs_estimate": 8,
    "frames_excluded_missing_pointing_metadata": 0,
    "candidates_detected": 5,
    "candidates_persistence_confirmed": 4,
    "candidates_rate_linearity_confirmed": 2,
    "candidates_ephemeris_matched": 2,
    "ephemeris_queries_attempted": 4,
    "ephemeris_queries_failed": 0,
    "residual_chains_tested": 3,
    "residual_chains_rejected": 1,
    "residual_chains_non_monotonic": 2,
    "residual_assumed_error_detections": 0,
    "residual_worst_accepted_ratio": 1.4,
}


@dataclass(frozen=True)
class Scenario:
    """One pipeline's record step with a good run and a way to damage it.

    Parameters
    ----------
    name : `str`
        The pipeline, for the test id.
    record : `Callable`
        Takes ``damage`` (``"none"``, ``"nan"`` or ``"raise"``) and an unused
        flag, and calls the pipeline's ``record_*_quality`` with a fixed run,
        with a NaN in one summary number and every gate number, or with an
        object that raises when read.
    nan_metric : `str`
        The metric whose value the NaN scenario damages.
    """

    name: str
    record: Callable[[str, bool], int]
    nan_metric: str


def _record_astrometry(damage: str, _unused: bool) -> int:
    """Record the fixed astrometry run, optionally damaged.

    Parameters
    ----------
    damage : `str`
        ``"none"``, ``"nan"`` or ``"raise"``.
    _unused : `bool`
        Not used.

    Returns
    -------
    new_count : `int`
        What the record step returned.
    """
    metrics, gates, context = astrometry_fixture_run()
    if damage == "nan":
        metrics = metrics.model_copy(update={"gaia_fit_rms_arcsec": math.nan})
        gates = [
            gate.model_copy(update={"measured_value": math.nan, "limit": math.inf})
            if gate.measured_value
            else gate
            for gate in gates
        ]
    if damage == "raise":
        metrics = None  # type: ignore[assignment]
    return astrometry_record.record_astrometry_quality(metrics, gates, context, "M99_Stacked.fits")


def _record_photometry(damage: str, _unused: bool) -> int:
    """Record the fixed photometry run, optionally damaged.

    Parameters
    ----------
    damage : `str`
        ``"none"``, ``"nan"`` or ``"raise"``.
    _unused : `bool`
        Not used.

    Returns
    -------
    new_count : `int`
        What the record step returned.
    """
    metrics = photometry_fixture_metrics()
    gates = photometry_fixture_gates()
    if damage == "nan":
        metrics = metrics.model_copy(update={"light_curve_scatter_rms_mag": math.nan})
        gates = [gate.model_copy(update={"measured_value": math.nan}) for gate in gates]
    if damage == "raise":
        metrics = None  # type: ignore[assignment]
    context = PipelineRunContext("M 99", "photometry", "1.3.0", "job-1", "job-1", MEASURED_AT)
    return photometry_record.record_photometry_quality(metrics, gates, photometry_fixture_inputs(), context)


def _record_spectroscopy(damage: str, _unused: bool) -> int:
    """Record the fixed spectroscopy run, optionally damaged.

    Parameters
    ----------
    damage : `str`
        ``"none"``, ``"nan"`` or ``"raise"``.
    _unused : `bool`
        Not used.

    Returns
    -------
    new_count : `int`
        What the record step returned.
    """
    metrics = healthy_summary_metrics()
    gates = healthy_gates()
    if damage == "nan":
        metrics = metrics.model_copy(update={"median_trail_width_px": math.nan})
        gates = [gate.model_copy(update={"measured_value": math.nan}) for gate in gates]
    if damage == "raise":
        metrics = None  # type: ignore[assignment]
    return spectroscopy_record.record_spectroscopy_quality(metrics, gates, run_context())


def _record_asteroid(damage: str, _unused: bool) -> int:
    """Record the fixed asteroid-detection run, optionally damaged.

    Parameters
    ----------
    damage : `str`
        ``"none"``, ``"nan"`` or ``"raise"``.
    _unused : `bool`
        Not used.

    Returns
    -------
    new_count : `int`
        What the record step returned.
    """
    counts: Any = dict(ASTEROID_COUNTS)
    gates = asteroid_record.asteroid_run_gates(counts, 3, 0)
    if damage == "nan":
        counts["candidates_detected"] = math.nan
        gates = [gate.model_copy(update={"measured_value": math.nan}) for gate in gates]
    if damage == "raise":
        counts = None
    context = PipelineRunContext("M 99", "asteroid_detection", "1.1.0", "job-1", "job-1", MEASURED_AT)
    return asteroid_record.record_asteroid_quality(counts, gates, context)


SCENARIOS = [
    Scenario("astrometry", _record_astrometry, "astrometry.gaia_fit_rms_arcsec"),
    Scenario("photometry", _record_photometry, "photometry.light_curve_scatter_rms_median_mag"),
    Scenario("spectroscopy", _record_spectroscopy, "spectroscopy.trail_width_median_px"),
    Scenario("asteroid_detection", _record_asteroid, "asteroid_detection.candidates_detected_count"),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_a_nan_in_a_summary_field_or_a_gate_becomes_not_measured(
    scenario: Scenario, mocker: MockerFixture
) -> None:
    """A NaN is stored as not measured, with a reason."""
    stored: list[QualityMeasurement] = []

    def fake_store(_store: object, measurements: list[QualityMeasurement]) -> int:
        """Keep the rows the record step tried to store.

        Returns
        -------
        count : `int`
            The number of rows.
        """
        stored.extend(measurements)
        return len(measurements)

    mocker.patch(STORE_FUNCTION, side_effect=fake_store)
    mocker.patch("astrometricslib.pipelines.shared.quality.pipeline_measurements.QualityStore")

    assert scenario.record("nan", False) > 0

    by_metric = {row.metric: row for row in stored}
    damaged = by_metric[scenario.nan_metric]
    assert damaged.status is MeasurementStatus.NOT_MEASURED
    assert damaged.value is None
    assert damaged.reason == NOT_FINITE_REASON
    assert all(row.value is None or math.isfinite(row.value) for row in stored)
    gate_rows = [row for row in stored if ".gate_" in row.metric and row.check_outcome is not None]
    assert gate_rows
    assert all(row.value is None for row in gate_rows if row.reason == NOT_FINITE_REASON)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_an_exception_while_building_the_rows_is_logged_and_swallowed(
    scenario: Scenario, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """A bug in the record step returns 0 and logs the traceback."""
    store = mocker.patch(STORE_FUNCTION)
    mocker.patch("astrometricslib.pipelines.shared.quality.pipeline_measurements.QualityStore")

    with caplog.at_level(logging.ERROR):
        assert scenario.record("raise", False) == 0

    store.assert_not_called()
    assert any("were not stored" in record.message for record in caplog.records)


def test_a_gate_with_a_nan_number_keeps_its_outcome() -> None:
    """The outcome survives when the gate's number is not finite."""
    from astrometricslib.models.quality.gate_result import passed_gate
    from astrometricslib.models.quality.limit import LimitTier
    from astrometricslib.pipelines.asteroid_detection.metrics import ASTEROID_DETECTION_METRICS
    from astrometricslib.pipelines.shared.quality.pipeline_measurements import gate_measurement

    spec = next(spec for spec in ASTEROID_DETECTION_METRICS if spec.name.endswith("gate_search_frames"))
    gate: GateResult = passed_gate("search_frames", 5.0, 3.0, "setting").model_copy(
        update={"measured_value": math.nan, "limit": math.nan}
    )
    context = PipelineRunContext("M 99", "asteroid_detection", "1.1.0", "job-1", "job-1", MEASURED_AT)
    row = gate_measurement(gate, spec, context, context.run_subject(), limit_tier=LimitTier.GEOMETRY)
    assert row.status is MeasurementStatus.NOT_MEASURED
    assert row.check_outcome is GateStatus.PASSED
    assert row.check_limit is not None
    assert row.check_limit.value is None
