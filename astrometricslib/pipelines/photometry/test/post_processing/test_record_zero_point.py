"""Tests that a run's catalog zero points become the right measurements.

The measurements must say which band they are in, keep a missing value as a
``not_measured`` row with its reason, mark rows whose errors assumed unit
gain, leave out a frame that has no path, keep two frames that share an
exposure start apart, pass the registry's checks when stored, and survive a
failure in the zero point step without dropping the run's other rows. The
real `validate_output` is run with two sessions in the payload to show that
each frame of both sessions is stored once.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement, SubjectLevel
from astrometricslib.models.target import Target
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE,
    PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_SIMBAD_V_MAG,
    PHOTOMETRY_METRICS,
    PHOTOMETRY_ZERO_POINT_METRIC_NAMES,
)
from astrometricslib.pipelines.photometry.post_processing import record_quality as rq
from astrometricslib.pipelines.photometry.post_processing.record_zero_point import zero_point_measurements
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import (
    CatalogBand,
    FrameZeroPoint,
    FrameZeroPointEstimate,
    SessionZeroPoints,
)
from astrometricslib.pipelines.photometry.test.conformance_fixture import (
    _fixture_session_zero_points,
    photometry_emitted_measurements,
    photometry_fixture_gates,
    photometry_fixture_inputs,
    photometry_fixture_metrics,
)
from astrometricslib.pipelines.pipeline_base import PipelineRequest
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


def test_a_gaia_session_fills_the_gaia_metrics_and_has_no_slope_row() -> None:
    """Twelve frames give 12 zero points and 12 star errors, no slopes."""
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
    errors = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG)
    assert len(errors) == 12
    assert errors[0].value == pytest.approx(0.02)
    assert errors[0].sample_count == 80
    assert by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE) == []
    assert len(measurements) == 24


def test_a_v_session_fills_the_v_metrics_and_the_slope() -> None:
    """A SIMBAD V session uses the V metrics and keeps its color slope."""
    session = _fixture_session_zero_points(CatalogBand.SIMBAD_V, "night-2")
    measurements = zero_point_measurements([session], CONTEXT)
    assert len(by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG)) == 12
    assert len(by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_SIMBAD_V_MAG)) == 12
    slopes = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE)
    assert len(slopes) == 12
    assert all(row.value == pytest.approx(0.08) for row in slopes)


def test_no_airmass_and_no_night_spread_metric_is_declared() -> None:
    """Airmass and the night spread are not declared here."""
    names = {spec.name for spec in PHOTOMETRY_METRICS}
    banned = ("airmass", "scatter_gaia", "scatter_simbad")
    assert not any(word in name for name in names for word in banned)
    assert len(PHOTOMETRY_ZERO_POINT_METRIC_NAMES) == 5


def test_a_frame_without_a_zero_point_is_not_measured_with_the_reason() -> None:
    """A missing value is a not-measured row; so is its star sample error."""
    reason = "only 3 of 40 stars of the session's fixed set passed the cuts in this frame"
    frame = FrameZeroPoint(
        "/library/lights/M 99/Apertura 75Q/m99_001.fits",
        datetime(2026, 10, 3, 4, 0),
        FrameZeroPointEstimate(None, None, 3, reason, None, reason),
    )
    session = SessionZeroPoints("night-1", CatalogBand.GAIA_G, "", (frame,), star_set_size=40)
    measurements = zero_point_measurements([session], CONTEXT)
    zero_point = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)[0]
    assert zero_point.status is MeasurementStatus.NOT_MEASURED
    assert zero_point.value is None
    assert reason in zero_point.reason
    error = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG)[0]
    assert error.status is MeasurementStatus.NOT_MEASURED
    assert reason in error.reason


def test_a_session_with_no_band_is_recorded_under_gaia_g_with_the_band_reason() -> None:
    """With no band every frame says why, under Gaia G, with no slope row."""
    reason = "no catalog band had enough stars: 3 Gaia G and 2 SIMBAD V"
    start = datetime(2026, 10, 3, 4, 0)
    frames = tuple(
        FrameZeroPoint(
            f"/library/lights/M 99/Apertura 75Q/m99_{index:03d}.fits",
            start + timedelta(minutes=index),
            FrameZeroPointEstimate(None, None, 0, reason, None, reason),
        )
        for index in range(3)
    )
    measurements = zero_point_measurements([SessionZeroPoints("night-1", None, reason, frames)], CONTEXT)
    zero_points = by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)
    assert len(zero_points) == 3
    assert all(row.status is MeasurementStatus.NOT_MEASURED and reason in row.reason for row in zero_points)
    assert len(measurements) == 3


def test_assumed_unit_gain_marks_the_zero_point_assumed() -> None:
    """A value built on errors that assumed 1 e-/ADU is stored as assumed."""
    session = _fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")
    assumed = SessionZeroPoints(
        session.session_id,
        session.band,
        "",
        session.frames,
        session.star_set_size,
        errors_assume_unit_gain=True,
    )
    rows = zero_point_measurements([assumed], CONTEXT)
    zero_points = by_metric(rows, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)
    assert all(row.status is MeasurementStatus.ASSUMED for row in zero_points)
    assert all("1 e-/ADU" in row.reason for row in zero_points)
    for row in zero_points:
        QualityMeasurement.model_validate(row.model_dump(by_alias=True))


def test_a_frame_without_a_path_is_left_out_and_never_stored_as_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No row has an 'Unknown' subject; the log counts the frames left out."""
    session = _fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")
    frames = (
        FrameZeroPoint(None, session.frames[0].timestamp, session.frames[0].estimate),
        *session.frames[1:],
    )
    unnamed = SessionZeroPoints("night-1", CatalogBand.GAIA_G, "", frames, 80)
    measurements = zero_point_measurements([unnamed], CONTEXT)
    assert len(by_metric(measurements, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)) == 11
    assert not any("Unknown" in row.subject.subject_id for row in measurements)
    assert "1 frames of session night-1 have no known path" in caplog.text


def test_two_frames_with_the_same_exposure_start_get_two_subjects() -> None:
    """The subject comes from the path, so a shared DATE-OBS is fine."""
    session = _fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")
    first, second = session.frames[0], session.frames[1]
    twin = FrameZeroPoint(second.frame_path, first.timestamp, second.estimate)
    shared = SessionZeroPoints("night-1", CatalogBand.GAIA_G, "", (first, twin), 80)
    rows = zero_point_measurements([shared], CONTEXT)
    zero_points = by_metric(rows, PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG)
    assert len({row.subject.subject_id for row in zero_points}) == 2


def test_no_session_gives_no_measurement() -> None:
    """A run with no zero points stores nothing for them."""
    assert zero_point_measurements([], CONTEXT) == []
    assert zero_point_measurements([SessionZeroPoints("night-1", None, "x", ())], CONTEXT) == []


def test_every_declared_zero_point_metric_is_emitted_by_the_fixture_run() -> None:
    """The fixture run emits every declared metric, zero points included."""
    emitted = {measurement.metric for measurement in photometry_emitted_measurements()}
    assert emitted == {spec.name for spec in PHOTOMETRY_METRICS}


def _use_temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualityStore:
    """Point the quality store at a temporary library.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The temporary library folder.
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the library path for the test.

    Returns
    -------
    store : `QualityStore`
        The store on the temporary library's database.
    """
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    return QualityStore(str(tmp_path / "astrometrics.db"))


def test_the_runner_stores_the_frames_of_two_sessions_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate_output with two sessions stores every frame of both, once."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    request = PipelineRequest(
        target=Target(id="M 99"),
        catalog_access=None,
        options={"job_id": "job-9", "measured_at": datetime(2026, 10, 23, 12, 0, tzinfo=UTC)},
    )
    result = runner._empty_photometry_result("no frames for the requested filter")
    result.payload["all_session_zero_points"] = [
        _fixture_session_zero_points(CatalogBand.GAIA_G, "night-1"),
        _fixture_session_zero_points(CatalogBand.SIMBAD_V, "night-2"),
    ]
    adapter = runner.PhotometryPipelineAdapter()
    summary = adapter.validate_output(request, result)

    stored = store.query(target_id="M 99", latest_only=False)
    rows = [row for row in stored if row.run_id == "job-9"]
    gaia = [row for row in rows if row.metric == PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG]
    simbad = [row for row in rows if row.metric == PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG]
    assert len(gaia) == 12
    assert len(simbad) == 12
    assert len({row.subject.subject_id for row in gaia + simbad}) == 24
    assert len([row for row in rows if row.metric == PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE]) == 12
    again = rq.record_photometry_quality(
        summary.photometry_metrics,
        list(summary.gates),
        rq.PhotometryRunInputs(session_zero_points=result.payload["all_session_zero_points"]),
        PipelineRunContext(
            "M 99",
            "photometry",
            runner.PHOTOMETRY_PIPELINE_VERSION,
            "job-9",
            "job-9",
            datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
        ),
    )
    assert again == 0


def test_an_error_in_the_zero_point_step_does_not_drop_the_run_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The zero points have a guarded call of their own."""
    store = _use_temporary_store(tmp_path, monkeypatch)

    def explode(*_arguments: object) -> list:
        """Fail like a bug in the zero point step.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("zero point step broke")

    monkeypatch.setattr(rq, "zero_point_measurements", explode)
    new_count = rq.record_photometry_quality(
        photometry_fixture_metrics(), photometry_fixture_gates(), photometry_fixture_inputs(), CONTEXT
    )
    rows = store.query(target_id="M 99", latest_only=False)
    assert new_count == len(rows) > 0
    assert not any(row.metric in PHOTOMETRY_ZERO_POINT_METRIC_NAMES for row in rows)
    assert "photometry zero points on M 99 were not stored" in caplog.text
