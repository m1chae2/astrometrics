"""Purpose: Tests for the notice row of a preview step that was skipped.

Description: A skipped GraXpert or Cosmic Clarity step used to leave only a log
line. These tests check that it becomes a failed notice row on the stack, with
the job's id, so `Jobs.query(detail="warnings")` can list it.
"""

from datetime import UTC, datetime

import pytest

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.post_processing import preview_notices
from astrometricslib.pipelines.stacking.post_processing.preview_notices import (
    preview_step_skipped_measurements,
    record_preview_step_notices,
)
from astrometricslib.pipelines.stacking.post_processing.stack_preview import SkippedPreviewStep

MEASURED_AT = datetime(2026, 11, 1, tzinfo=UTC)
SKIPPED = [
    SkippedPreviewStep("GraXpert", "GraXpert did not finish"),
    SkippedPreviewStep("Cosmic Clarity", "Cosmic Clarity could not run (no such file)"),
]


def _context() -> PipelineRunContext:
    """Build the shared facts of a stacking run in job ``job-7``.

    Returns
    -------
    context : `PipelineRunContext`
        The context for target M 31.
    """
    return PipelineRunContext.from_options(
        "M 31", "stacking", "1", {"job_id": "job-7", "measured_at": MEASURED_AT}
    )


def test_skipped_steps_become_one_failed_notice_of_the_job() -> None:
    """One row counts the steps and says what happened to each."""
    (row,) = preview_step_skipped_measurements(SKIPPED, _context(), "M31_Stacked.fits")

    assert row.metric == "stacking.notice_preview_step_skipped"
    assert row.value == pytest.approx(2.0)
    assert row.check_outcome == GateStatus.FAILED
    assert row.job_id == "job-7"
    assert "GraXpert did not finish" in row.reason
    assert "Cosmic Clarity could not run" in row.reason
    assert row.subject.subject_id == "M 31/M31_Stacked.fits"


def test_no_skipped_step_gives_no_row() -> None:
    """A preview that ran every step stores nothing."""
    assert preview_step_skipped_measurements([], _context(), "M31_Stacked.fits") == []


def test_the_row_is_stored_with_the_jobs_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorder hands the row, carrying the job id, to the store."""
    stored: list = []
    monkeypatch.setattr(
        preview_notices, "record_run_measurements", lambda rows, config=None: stored.extend(rows)
    )

    record_preview_step_notices(SKIPPED, "M 31", "M31_Stacked.fits", "job-7", MEASURED_AT, None)

    assert [row.job_id for row in stored] == ["job-7"]


def test_nothing_is_stored_without_a_measurement_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline code does not read the clock, so no time means no row."""
    stored: list = []
    monkeypatch.setattr(
        preview_notices, "record_run_measurements", lambda rows, config=None: stored.extend(rows)
    )

    record_preview_step_notices(SKIPPED, "M 31", "M31_Stacked.fits", "job-7", None, None)

    assert stored == []
