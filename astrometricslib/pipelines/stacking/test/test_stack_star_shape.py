"""Tests for the star-shape rows of a stack (`stack_star_shape.py`).

The stacks are synthetic: Moffat stars of known stretch and angle, drawn by the
frame measurement's tests. The rows must equal what the frame measurement gives
for the same pixels, use the long axis divided by the short axis, and be absent
for a spectral stack. A failed measurement must give not-measured rows and
never an error. The stage step must run for a skipped stack only when no row
exists yet.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quality.star_profile_fitting import measure_star_profiles
from astrometricslib.pipelines.shared.quality.test.test_frame_measurement import _make_frame_pixels
from astrometricslib.pipelines.stacking import metrics as stacking_metrics
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.post_processing import stack_star_shape
from astrometricslib.pipelines.stacking.post_processing.stack_star_shape import (
    record_stack_star_shapes,
    stack_star_shape_measurements,
)

MEASURED_AT = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
TRUE_ELONGATION = 1.5
ANGLE_RAD = 0.5


def _context() -> PipelineRunContext:
    """Make the run facts of a stacking run.

    Returns
    -------
    context : `PipelineRunContext`
        A context for target ``M 13``.
    """
    return PipelineRunContext("M 13", "stacking", "1.0.0", "job-1", "job-1", MEASURED_AT)


@pytest.fixture(scope="module")
def stretched_stack(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Write a stack whose stars are stretched by a known amount.

    Returns
    -------
    path : `str`
        The FITS file.
    """
    path = tmp_path_factory.mktemp("stack") / "M13_stack.fits"
    pixels = _make_frame_pixels(elongation=TRUE_ELONGATION, angle_rad=ANGLE_RAD)
    fits.writeto(path, pixels.astype(np.float32), overwrite=True)
    return str(path)


def _by_name(rows: list[QualityMeasurement]) -> dict[str, QualityMeasurement]:
    """Index rows by metric name.

    Parameters
    ----------
    rows : `list` of `QualityMeasurement`
        The rows, one plane.

    Returns
    -------
    index : `dict`
        The row of each metric.
    """
    return {row.metric: row for row in rows}


def test_a_stretched_stack_gets_the_frame_values_at_stack_level(stretched_stack: str) -> None:
    """Stack rows hold long axis over short axis, as the frame code gives."""
    rows = _by_name(stack_star_shape_measurements(_context(), stretched_stack, is_spectral=False))
    expected = measure_star_profiles(fits.getdata(stretched_stack).astype(float), None)

    elongation = rows[stacking_metrics.STACKING_STAR_ELONGATION]
    assert elongation.status == MeasurementStatus.MEASURED
    assert elongation.subject.level.value == "stack"
    assert elongation.subject.subject_id == "M 13/M13_stack.fits"
    assert elongation.value == pytest.approx(TRUE_ELONGATION, abs=0.1)
    assert elongation.value == pytest.approx(expected.elongation)
    angle = rows[stacking_metrics.STACKING_STAR_ELONGATION_ANGLE_DEG]
    assert angle.value == pytest.approx(np.degrees(ANGLE_RAD), abs=5.0)
    coherence = rows[stacking_metrics.STACKING_STAR_ELONGATION_COHERENCE]
    assert coherence.value == pytest.approx(expected.angle_coherence)
    assert coherence.value > 0.5
    center = rows[stacking_metrics.STACKING_STAR_WIDTH_CENTER_PX]
    assert center.status == MeasurementStatus.MEASURED
    assert set(rows) == set(stacking_metrics.STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC.values())


def test_a_spectral_stack_gets_no_rows(stretched_stack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A spectral stack is not measured and nothing is stored."""
    stored: list[object] = []
    monkeypatch.setattr(
        stack_star_shape, "record_run_measurements", lambda rows, config=None: stored.append(rows)
    )

    assert stack_star_shape_measurements(_context(), stretched_stack, is_spectral=True) == []
    assert record_stack_star_shapes(_context(), stretched_stack, is_spectral=True) == 0
    assert stored == []


def test_a_stack_without_stars_records_not_measured_rows(tmp_path: Path) -> None:
    """Too few stars give not-measured rows with a reason, not an error."""
    path = tmp_path / "blank.fits"
    fits.writeto(path, np.random.default_rng(1).normal(1000.0, 5.0, (200, 200)).astype(np.float32))

    rows = stack_star_shape_measurements(_context(), str(path), is_spectral=False)

    assert len(rows) == len(stacking_metrics.STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC)
    assert all(row.status == MeasurementStatus.NOT_MEASURED and row.reason for row in rows)


def test_a_failed_measurement_records_not_measured_and_logs(
    stretched_stack: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An error in the measurement is logged and stored as not measured."""

    def fail(path: str) -> None:
        """Raise as a broken measurement would.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("boom")

    monkeypatch.setattr(stack_star_shape, "measure_image_star_profiles", fail)
    rows = stack_star_shape_measurements(_context(), stretched_stack, is_spectral=False)

    assert rows
    assert all(row.status == MeasurementStatus.NOT_MEASURED and "boom" in row.reason for row in rows)
    assert "could not be measured" in caplog.text


def test_a_store_failure_does_not_raise(stretched_stack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure while recording returns 0 and raises nothing."""

    def fail(*args: object, **kwargs: object) -> None:
        """Raise as a broken store would.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("store down")

    monkeypatch.setattr(stack_star_shape, "record_run_measurements", fail)
    assert record_stack_star_shapes(_context(), stretched_stack, is_spectral=False) == 0


def _stage_run(stack_path: str, *, has_spectral: bool = False) -> SimpleNamespace:
    """Make the parts of a stacking run the stage step reads.

    Parameters
    ----------
    stack_path : `str`
        Unused; kept so a test reads as one run per stack.
    has_spectral : `bool`, optional
        Whether the run made a spectral stack.

    Returns
    -------
    run : `types.SimpleNamespace`
        A stand-in for `StackRun`.
    """
    return SimpleNamespace(
        has_spectral=has_spectral,
        target=SimpleNamespace(id="M 13"),
        job_id="job-1",
        measured_at=MEASURED_AT,
        quality_recording=None,
    )


def test_the_stage_step_records_a_kept_stack_once(
    stretched_stack: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped stack gets rows when it has none, then is left alone."""
    stored: list[list[QualityMeasurement]] = []
    monkeypatch.setattr(
        stack_star_shape,
        "record_run_measurements",
        lambda rows, config=None: stored.append(rows) or len(rows),
    )
    recorded = {"value": False}
    monkeypatch.setattr(stack_star_shape, "_already_recorded", lambda *args: recorded["value"])
    run = _stage_run(stretched_stack)

    stage._record_stack_star_shapes(run, stretched_stack, only_if_missing=True)
    assert len(stored) == 1
    assert stored[0][0].subject.subject_id == "M 13/M13_stack.fits"

    recorded["value"] = True
    stage._record_stack_star_shapes(run, stretched_stack, only_if_missing=True)
    assert len(stored) == 1

    stage._record_stack_star_shapes(_stage_run(stretched_stack, has_spectral=True), stretched_stack)
    assert len(stored) == 1
