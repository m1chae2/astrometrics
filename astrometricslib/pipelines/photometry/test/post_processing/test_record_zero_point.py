"""Tests that a run's catalog zero points become the right measurements.

The measurements must say which band they are in, keep a missing value as a
``not_measured`` row with its reason, file one spread for each session under
its own subject, and pass the registry's checks when stored.
"""

from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib.models.quality.measurement import MeasurementStatus, SubjectLevel
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_CATALOG_ZERO_POINT_COLOUR_SLOPE,
    PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_SIMBAD_V_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    PHOTOMETRY_FRAME_AIRMASS,
)
from astrometricslib.pipelines.photometry.post_processing.record_zero_point import (
    NO_AIRMASS_REASON,
    zero_point_measurements,
)
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import (
    CatalogBand,
    FrameZeroPoint,
    FrameZeroPointEstimate,
    SessionZeroPoints,
)
from astrometricslib.pipelines.photometry.test.conformance_fixture import _fixture_session_zero_points
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quality.record_measurements import check_measurement_against_registry

CONTEXT = PipelineRunContext(
    "M 99", "photometry", "1.3.0", "job-7", "job-7", datetime(2026, 10, 23, tzinfo=UTC)
)


def by_metric(measurements: list, metric: str) -> list:
    """Pick the measurements of one metric.

    Parameters
    ----------
    measurements : `list`
        The measurements.
    metric : `str`
        The metric name.

    Returns
    -------
    picked : `list`
        The measurements of that metric, in order.
    """
    return [measurement for measurement in measurements if measurement.metric == metric]


def test_a_gaia_session_fills_the_gaia_metrics_and_passes_the_registry_checks() -> None:
    """Twelve frames give 12 zero points, slopes, airmasses and one spread."""
    session = _fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")
    measurements = zero_point_measurements([session], CONTEXT)
    for measurement in measurements:
        check_measurement_against_registry(measurement)
    zero_points = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)
    assert len(zero_points) == 12
    assert by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG) == []
    assert all(row.subject.level is SubjectLevel.FRAME for row in zero_points)
    assert zero_points[0].subject.subject_id.startswith("M 99/")
    assert zero_points[0].uncertainty == pytest.approx(0.004)
    assert zero_points[0].sample_count == 60
    slopes = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_COLOUR_SLOPE)
    assert len(slopes) == 12
    assert all(row.status is MeasurementStatus.NOT_MEASURED and "color term" in row.reason for row in slopes)
    assert len(by_metric(measurements, PHOTOMETRY_FRAME_AIRMASS)) == 12
    spread = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG)
    assert len(spread) == 1
    assert spread[0].subject.level is SubjectLevel.RUN
    assert spread[0].subject.subject_id.endswith("/night-1")
    assert spread[0].sample_count == 12
    assert spread[0].value is not None
    assert spread[0].subject.observing_night_id == "2026-10-02"


def test_a_v_session_fills_the_v_metrics_and_the_slope() -> None:
    """A SIMBAD V session uses the V metrics and keeps its color slope."""
    session = _fixture_session_zero_points(CatalogBand.SIMBAD_V, "night-2")
    measurements = zero_point_measurements([session], CONTEXT)
    assert len(by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG)) == 12
    assert len(by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_SIMBAD_V_MAG)) == 1
    slopes = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_COLOUR_SLOPE)
    assert all(row.value == pytest.approx(0.08) for row in slopes)


def test_two_sessions_of_one_run_keep_separate_spreads() -> None:
    """Each session has its own run-level subject, so no spread is combined."""
    first = _fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")
    second = _fixture_session_zero_points(CatalogBand.GAIA_G, "night-2")
    measurements = zero_point_measurements([first, second], CONTEXT)
    spreads = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG)
    assert len({row.subject.subject_id for row in spreads}) == 2


def test_a_frame_without_a_zero_point_or_airmass_is_not_measured_with_the_reason() -> None:
    """A missing value is a not-measured row; the stand-in is not stored."""
    reason = "only 3 of 40 central catalog stars passed the cuts"
    frame = FrameZeroPoint(
        "/library/lights/M 99/Apertura 75Q/m99_001.fits",
        datetime(2026, 10, 3, 4, 0),
        None,
        FrameZeroPointEstimate(None, None, 3, reason, None, reason),
    )
    session = SessionZeroPoints("night-1", CatalogBand.GAIA_G, "", (frame,))
    measurements = zero_point_measurements([session], CONTEXT)
    zero_point = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)[0]
    assert zero_point.status is MeasurementStatus.NOT_MEASURED
    assert zero_point.value is None
    assert reason in zero_point.reason
    airmass = by_metric(measurements, PHOTOMETRY_FRAME_AIRMASS)[0]
    assert airmass.status is MeasurementStatus.NOT_MEASURED
    assert airmass.reason == NO_AIRMASS_REASON
    spread = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG)[0]
    assert spread.status is MeasurementStatus.NOT_MEASURED
    assert "at least 10" in spread.reason


def test_a_session_with_no_band_is_recorded_as_not_measured_with_the_band_reason() -> None:
    """With no usable band every row says why, under the Gaia G metrics."""
    reason = "no star is usable for a zero point"
    start = datetime(2026, 10, 3, 4, 0)
    frames = tuple(
        FrameZeroPoint(
            f"/library/lights/M 99/Apertura 75Q/m99_{index:03d}.fits",
            start + timedelta(minutes=index),
            1.2,
            FrameZeroPointEstimate(None, None, 0, reason, None, reason),
        )
        for index in range(3)
    )
    measurements = zero_point_measurements([SessionZeroPoints("night-1", None, reason, frames)], CONTEXT)
    zero_points = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)
    assert len(zero_points) == 3
    assert all(row.status is MeasurementStatus.NOT_MEASURED and reason in row.reason for row in zero_points)
    spread = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG)[0]
    assert reason in spread.reason


def test_no_session_gives_no_measurement() -> None:
    """A run with no zero points stores nothing for them."""
    assert zero_point_measurements([], CONTEXT) == []
    assert zero_point_measurements([SessionZeroPoints("night-1", None, "x", ())], CONTEXT) == []


def test_the_run_record_step_adds_the_zero_points_to_the_run_measurements() -> None:
    """With zero points in the inputs, every declared metric is emitted."""
    from astrometricslib.pipelines.photometry.metrics import PHOTOMETRY_METRICS
    from astrometricslib.pipelines.photometry.test.conformance_fixture import (
        photometry_emitted_measurements,
    )

    emitted = {measurement.metric for measurement in photometry_emitted_measurements()}
    assert emitted == {spec.name for spec in PHOTOMETRY_METRICS}
