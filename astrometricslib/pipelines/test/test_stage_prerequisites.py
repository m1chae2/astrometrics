"""Purpose: Unit tests for the analysis stage order and the astrometry rule.

Description: Astrometry is the core layer. Photometry and spectroscopy only
read its plate solution and never run astrometry; without a usable solution
they are skipped, with the reason ``needs astrometry: ...``, one job warning
(a notice row) and one INFO log line. Spectroscopy is still a prerequisite
of spectral variation. These tests check the `STAGE_PREREQUISITES` table,
the rule `stage_result_is_current` uses to tell whether a saved astrometry
result still matches the imaging stack, the solution each reading stage
needs (`missing_astrometry_reason`), and how `run_target_stages` (the stage
runner behind every pipeline's public ``run``) skips a stage that lacks
one. Two tests run `processing.photometry.run` and
`processing.spectroscopy.run` themselves. The stage runners are replaced by
a recorder, so no image is solved; the few FITS files written hold only a
header.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, ProcessingError
from astrometricslib.foundation.run_abandonment import RunAbandonedError, RunAbandonment, bind_abandonment
from astrometricslib.models.processing_results import ProcessTargetResult
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.quality_summary import (
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    SpectroscopyQualitySummary,
    StackQualitySummary,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines import tasks
from astrometricslib.pipelines.shared.provenance_recording import stacked_image_entity_id
from astrometricslib.pipelines.shared.target_center_hint import resolve_spectral_stack_solution

_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
_RUN_ASTROMETRY = "run processing.astrometry.run(target) first"


def _astrometry_summary(
    created_at: datetime, upstream_entity_id: str | None = None, solved: bool = True
) -> AstrometryQualitySummary:
    """Build an astrometry summary with only the fields the rules read.

    Parameters
    ----------
    created_at : `datetime`
        When the summary was made.
    upstream_entity_id : `str`, optional
        The stack the run recorded.
    solved : `bool`, optional
        Whether the run solved the stack.

    Returns
    -------
    summary : `AstrometryQualitySummary`
        The summary.
    """
    return AstrometryQualitySummary.model_construct(
        created_at=created_at,
        upstream_entity_id=upstream_entity_id,
        astrometry_metrics=AstrometryPipelineQualityMetrics.model_construct(plate_solve_succeeded=solved),
    )


class _RecordingPipelineMatch:
    """A stand-in for the stage dispatch that records every call."""

    def __init__(self) -> None:
        """Start with no calls and no failure."""
        self.calls: list[dict] = []
        self.failures: dict[str, Exception] = {}
        self.results: dict[str, dict] = {}
        # What a stand-in astrometry run leaves on the target: a solved
        # result (True), an unsolved one (False), or nothing (None).
        self.astrometry_solves: bool | None = None

    def __call__(
        self,
        target: Target,
        frames: object,
        pipeline_type: str,
        filter_type: object,
        catalog_access: object,
        path: object,
        **kwargs: object,
    ) -> dict:
        """Record the call, raise a chosen failure, or return a stub result.

        Returns
        -------
        result : `dict`
            The result chosen for the stage, or a plain finished marker.
        """
        self.calls.append({"pipeline_type": pipeline_type, "path": path, **kwargs})
        if pipeline_type in self.failures:
            raise self.failures[pipeline_type]
        if pipeline_type == "astrometry" and self.astrometry_solves is not None:
            target.quality.astrometry = _astrometry_summary(
                _NOW + timedelta(hours=2), solved=self.astrometry_solves
            )
        return self.results.get(pipeline_type, {"status": "finished", "pipeline_type": pipeline_type})

    @property
    def stages(self) -> list[str]:
        """The stages called, in order.

        Returns
        -------
        stages : `list` [`str`]
            The pipeline types.
        """
        return [call["pipeline_type"] for call in self.calls]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _RecordingPipelineMatch:
    """Replace the stage dispatch with a recorder.

    Returns
    -------
    recorder : `_RecordingPipelineMatch`
        The recorder.
    """
    recording = _RecordingPipelineMatch()
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", recording)
    return recording


@pytest.fixture
def quality_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualityStore:
    """Send the stored notice rows to a quality store in the test folder.

    Returns
    -------
    store : `QualityStore`
        The store the rows go to.
    """
    database = str(tmp_path / "quality.db")
    monkeypatch.setattr(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.QualityStore.from_configuration",
        lambda config=None: QualityStore(database),
    )
    return QualityStore(database)


class _StageRunner:
    """Run chosen stages through `tasks.run_target_stages` on a stand-in."""

    def run(
        self,
        target: Target,
        stages: list[str],
        run_prerequisites: bool = True,
        measured_at: datetime | None = None,
        **options: dict,
    ) -> ProcessTargetResult:
        """Run the stages, each with its options dictionary.

        Parameters
        ----------
        target : `Target`
            The target.
        stages : `list` [`str`]
            The stages asked for.
        run_prerequisites : `bool`, optional
            Whether to add the missing or old prerequisites.
        measured_at : `datetime`, optional
            The time stamped on stored rows; `None` stores none.
        **options : `dict`
            Each stage's options, by stage name.

        Returns
        -------
        result : `ProcessTargetResult`
            The run's result.
        """
        return tasks.run_target_stages(
            target,
            tuple(stages),
            options,
            MagicMock(),
            run_prerequisites=run_prerequisites,
            measured_at=measured_at,
        )


@pytest.fixture
def runner() -> _StageRunner:
    """Give the stage runner.

    Returns
    -------
    runner : `_StageRunner`
        The runner.
    """
    return _StageRunner()


def _stack_file(tmp_path: Path, name: str = "stack.fits") -> str:
    """Make an empty file that stands in for an imaging stack.

    Returns
    -------
    path : `str`
        The file's path.
    """
    path = tmp_path / name
    path.write_bytes(b"")
    return str(path)


def _fits_file(
    path: Path, *, solved: bool = False, camera: str = "ZWO ASI533MM Pro", focal_length: float = 400.0
) -> str:
    """Write a small FITS image with a camera, an optic and maybe a solution.

    Parameters
    ----------
    path : `Path`
        Where to write it.
    solved : `bool`, optional
        Whether the header holds a plate solution, as astrometry writes it.
    camera : `str`, optional
        The ``INSTRUME`` card.
    focal_length : `float`, optional
        The ``FOCALLEN`` card, in millimeters.

    Returns
    -------
    path : `str`
        The file's path.
    """
    header = fits.Header()
    header["INSTRUME"] = camera
    header["FOCALLEN"] = focal_length
    header["STACKCNT"] = 10
    if solved:
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 250.4
        header["CRVAL2"] = 36.46
        header["CRPIX1"] = 8.0
        header["CRPIX2"] = 8.0
        header["CD1_1"] = -0.0005
        header["CD1_2"] = 0.0
        header["CD2_1"] = 0.0
        header["CD2_2"] = 0.0005
        header["PLTSOLVD"] = (True, "sky map written by a plate solve")
    fits.PrimaryHDU(data=np.zeros((16, 16), dtype=np.float32), header=header).writeto(path)
    return str(path)


def _target_with_astrometry(
    stack_path: str,
    astrometry_at: datetime | None,
    stack_at: datetime | None = _NOW,
    upstream_entity_id: str | None = None,
    spectral: bool = False,
    solved: bool = True,
) -> Target:
    """Build a target with a stack and, optionally, a saved astrometry result.

    Returns
    -------
    target : `Target`
        The target.
    """
    target = Target(
        id="PrerequisiteTarget",
        stacked_image=stack_path,
        stacked_spectral_target="/fake/spec.fits" if spectral else "",
    )
    if stack_at is not None:
        target.stacking.quality_summary = StackQualitySummary.model_construct(created_at=stack_at)
    if astrometry_at is not None:
        target.quality.astrometry = _astrometry_summary(astrometry_at, upstream_entity_id, solved)
    return target


def _notice_rows(store: QualityStore, stage: str) -> list:
    """Read the stored "needs astrometry" notices of a stage.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The rows.
    """
    return store.query(metric=f"{stage}.notice_needs_astrometry", latest_only=False)


def _skip_lines(caplog: pytest.LogCaptureFixture, stage: str) -> list[logging.LogRecord]:
    """Pick the INFO log lines that report a stage skipped for astrometry.

    Returns
    -------
    records : `list` [`logging.LogRecord`]
        The matching records.
    """
    return [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and f"Skipping {stage}: needs astrometry" in record.getMessage()
    ]


# -- The tables -------------------------------------------------------------


def test_photometry_and_spectroscopy_read_astrometry_and_add_no_stage() -> None:
    """Verify the table has every stage and no stage adds astrometry."""
    assert set(tasks.STAGE_PREREQUISITES) == set(tasks.ANALYSIS_STAGES)
    assert tasks.STAGE_PREREQUISITES["photometry"] == ()
    assert tasks.STAGE_PREREQUISITES["spectroscopy"] == ()
    assert tasks.STAGE_PREREQUISITES["astrometry"] == ()
    assert tasks.STAGE_PREREQUISITES["asteroids"] == ()
    assert all("astrometry" not in needed for needed in tasks.STAGE_PREREQUISITES.values())
    assert frozenset({"photometry", "spectroscopy"}) == tasks.STAGES_THAT_READ_ASTROMETRY


# -- When a saved astrometry result is current --------------------------------


def test_a_missing_result_is_not_current(tmp_path: Path) -> None:
    """Verify a target with no astrometry result needs astrometry."""
    target = _target_with_astrometry(_stack_file(tmp_path), astrometry_at=None)

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_a_result_older_than_the_stack_is_not_current(tmp_path: Path) -> None:
    """Verify a result made before the stack finished is stale."""
    target = _target_with_astrometry(_stack_file(tmp_path), astrometry_at=_NOW - timedelta(minutes=1))

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_a_result_made_after_the_stack_is_current(tmp_path: Path) -> None:
    """Verify a newer result, with no recorded stack, is current."""
    target = _target_with_astrometry(_stack_file(tmp_path), astrometry_at=_NOW + timedelta(minutes=1))

    assert tasks.stage_result_is_current(target, "astrometry")


def test_a_result_that_names_this_stack_is_current(tmp_path: Path) -> None:
    """Verify a recorded stack that matches the current path is current."""
    stack_path = _stack_file(tmp_path)
    entity_id = stacked_image_entity_id("PrerequisiteTarget", stack_path)
    target = _target_with_astrometry(stack_path, _NOW + timedelta(minutes=1), upstream_entity_id=entity_id)

    assert tasks.stage_result_is_current(target, "astrometry")


def test_a_result_that_names_another_stack_is_not_current(tmp_path: Path) -> None:
    """Verify a recorded stack with a different path is stale."""
    other_id = stacked_image_entity_id("PrerequisiteTarget", "/somewhere/else.fits")
    target = _target_with_astrometry(
        _stack_file(tmp_path), _NOW + timedelta(minutes=1), upstream_entity_id=other_id
    )

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_without_a_stack_summary_the_file_time_decides(tmp_path: Path) -> None:
    """Verify the stack file's modification time stands in for its summary."""
    stack_path = _stack_file(tmp_path)
    stack_time = datetime.fromtimestamp(os.path.getmtime(stack_path), UTC)
    older = _target_with_astrometry(stack_path, stack_time - timedelta(hours=1), stack_at=None)
    newer = _target_with_astrometry(stack_path, stack_time + timedelta(hours=1), stack_at=None)

    assert not tasks.stage_result_is_current(older, "astrometry")
    assert tasks.stage_result_is_current(newer, "astrometry")


def test_without_a_stack_on_disk_nothing_is_current() -> None:
    """Verify a result cannot be current when the stack file is gone."""
    target = _target_with_astrometry("/fake/missing.fits", _NOW + timedelta(hours=1))

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_a_caller_given_image_is_never_current(tmp_path: Path) -> None:
    """Verify a result is never taken as made on an image the caller names."""
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW + timedelta(hours=1))

    assert not tasks.stage_result_is_current(target, "astrometry", image_path="/chosen/image.fits")


def test_only_astrometry_and_spectroscopy_have_a_staleness_rule(tmp_path: Path) -> None:
    """Verify asking about another stage is an error."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    with pytest.raises(InvalidArgumentError, match="No staleness rule"):
        tasks.stage_result_is_current(target, "photometry")


def test_only_photometry_and_spectroscopy_read_astrometry(tmp_path: Path) -> None:
    """Verify asking about a stage that reads no solution is an error."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    with pytest.raises(InvalidArgumentError, match="does not read astrometry"):
        tasks.missing_astrometry_reason(target, "asteroids", {})


# -- Photometry reads astrometry's result and never runs it -------------------


def test_a_current_solved_result_lets_photometry_run_alone(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a current astrometry result leaves photometry to run alone."""
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW + timedelta(hours=1))

    result = runner.run(target, ["photometry"])

    assert recorder.stages == ["photometry"]
    assert result.prerequisites_run == []
    assert result.stages_skipped == {}


@pytest.mark.parametrize(
    ("astrometry_at", "solved", "why"),
    [
        (None, True, "its result is missing"),
        (_NOW - timedelta(hours=1), True, "its result is older than the stack"),
        (_NOW + timedelta(hours=1), False, "the stack could not be plate solved"),
    ],
)
def test_photometry_without_a_usable_solution_is_skipped_once_with_a_warning(
    runner: _StageRunner,
    recorder: _RecordingPipelineMatch,
    quality_store: QualityStore,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    astrometry_at: datetime | None,
    solved: bool,
    why: str,
) -> None:
    """Verify the skip: reason, one warning, one log line, no run."""
    target = _target_with_astrometry(_stack_file(tmp_path), astrometry_at, solved=solved)
    expected = f"needs astrometry: {why}; {_RUN_ASTROMETRY}"

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        result = runner.run(target, ["photometry"], measured_at=_NOW)

    assert recorder.calls == []
    assert result.stages_run == []
    assert result.prerequisites_run == []
    assert result.stages_skipped == {"photometry": expected}
    assert result.results["photometry"] == {"status": "skipped", "reason": expected}
    assert len(_skip_lines(caplog, "photometry")) == 1
    rows = _notice_rows(quality_store, "photometry")
    assert len(rows) == 1
    assert rows[0].reason == expected
    assert rows[0].check_outcome == GateStatus.FAILED
    assert rows[0].action_taken == "stage skipped"


def test_no_stack_and_no_image_skips_photometry(
    runner: _StageRunner, recorder: _RecordingPipelineMatch
) -> None:
    """Verify a target with nothing solved skips photometry with the reason."""
    target = Target(id="NoStackTarget")

    result = runner.run(target, ["photometry"])

    assert recorder.calls == []
    assert (
        result.stages_skipped["photometry"] == f"needs astrometry: its result is missing; {_RUN_ASTROMETRY}"
    )


def test_a_requested_astrometry_runs_first_and_photometry_reads_its_result(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify astrometry the caller asked for runs once, before photometry."""
    recorder.astrometry_solves = True
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = runner.run(target, ["astrometry", "photometry"])

    assert recorder.stages == ["astrometry", "photometry"]
    assert result.prerequisites_run == []
    assert result.stages_skipped == {}


def test_a_requested_astrometry_that_cannot_solve_skips_photometry(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a stack astrometry left unsolved skips photometry."""
    recorder.astrometry_solves = False
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = runner.run(target, ["astrometry", "photometry"])

    assert recorder.stages == ["astrometry"]
    assert "could not be plate solved" in result.stages_skipped["photometry"]


def test_a_named_image_without_a_solution_skips_photometry(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a named image is read, not solved, and the call is named."""
    image_path = _fits_file(tmp_path / "other_camera_stack.fits")
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW + timedelta(hours=1))

    result = runner.run(target, ["photometry"], astrometry={"path": image_path})

    assert recorder.calls == []
    assert result.stages_skipped["photometry"] == (
        f"needs astrometry: the image {image_path} has no plate solution; "
        f"run processing.astrometry.run(target, {image_path!r}) first"
    )


def test_a_named_image_that_is_missing_skips_photometry(
    runner: _StageRunner, recorder: _RecordingPipelineMatch
) -> None:
    """Verify a named image that is not on disk skips photometry."""
    target = Target(id="NoStackTarget")

    result = runner.run(target, ["photometry"], astrometry={"path": "/gone/image.fits"})

    assert recorder.calls == []
    assert "does not exist" in result.stages_skipped["photometry"]


def test_a_named_solved_image_lets_photometry_run_without_astrometry(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a named image that astrometry solved is read as it is."""
    image_path = _fits_file(tmp_path / "solved_frame.fits", solved=True)
    target = Target(id="NoStackTarget")

    result = runner.run(target, ["photometry"], astrometry={"path": image_path})

    assert recorder.stages == ["photometry"]
    assert result.stages_skipped == {}


def test_run_prerequisites_false_still_never_runs_astrometry(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify prerequisites off does not let photometry run unsolved."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = runner.run(target, ["photometry"], run_prerequisites=False)

    assert recorder.calls == []
    assert "photometry" in result.stages_skipped


def test_the_public_photometry_run_skips_without_running_astrometry(
    recorder: _RecordingPipelineMatch,
    quality_store: QualityStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify `processing.photometry.run` skips on a stale solution."""
    from contextlib import nullcontext

    from astrometricslib.pipelines import pipeline_runs

    monkeypatch.setattr(pipeline_runs, "acquire_resource_slot", lambda *_args, **_kwargs: nullcontext())
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW - timedelta(hours=1))

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        result = ProcessingPipelines(AppConfiguration(), MagicMock()).photometry.run(target)

    assert recorder.calls == []
    assert result.prerequisites_run == []
    assert result.stages_skipped["photometry"].startswith(
        "needs astrometry: its result is older than the stack"
    )
    assert result.results["photometry"]["status"] == "skipped"
    assert len(_skip_lines(caplog, "photometry")) == 1
    assert len(_notice_rows(quality_store, "photometry")) == 1


# -- Spectroscopy: borrowed solution, else its own, else skipped --------------


def _spectral_target(tmp_path: Path, *, imaging_solved: bool, spectral_solved: bool) -> Target:
    """Build a target with an imaging stack and a spectral stack of one setup.

    Parameters
    ----------
    tmp_path : `Path`
        The test folder.
    imaging_solved : `bool`
        Whether the imaging stack holds a plate solution.
    spectral_solved : `bool`
        Whether the spectral stack holds its own plate solution.

    Returns
    -------
    target : `Target`
        The target. Its astrometry result is current.
    """
    imaging = _fits_file(tmp_path / "imaging_stack.fits", solved=imaging_solved)
    spectral = _fits_file(tmp_path / "spectral_stack.fits", solved=spectral_solved)
    target = _target_with_astrometry(imaging, _NOW + timedelta(hours=1), solved=imaging_solved)
    target.spectral_stacking.stacked_image = spectral
    return target


def test_spectroscopy_borrows_the_solved_imaging_stacks_solution(tmp_path: Path) -> None:
    """Verify the imaging stack of the same setup is the first source."""
    target = _spectral_target(tmp_path, imaging_solved=True, spectral_solved=True)

    solution = resolve_spectral_stack_solution(target, target.spectral_stacking.stacked_image)

    assert solution is not None
    assert solution.borrowed
    assert solution.stack_path == target.stacking.stacked_image


def test_spectroscopy_uses_the_spectral_stacks_own_solution_next(tmp_path: Path) -> None:
    """Verify a solved spectral stack is used when nothing is borrowed."""
    target = _spectral_target(tmp_path, imaging_solved=False, spectral_solved=True)

    solution = resolve_spectral_stack_solution(target, target.spectral_stacking.stacked_image)

    assert solution is not None
    assert not solution.borrowed
    assert solution.stack_path == target.spectral_stacking.stacked_image
    assert solution.wcs.has_celestial


def test_a_solved_stack_of_another_camera_is_not_borrowed(tmp_path: Path) -> None:
    """Verify only the same camera and optic lend their solution."""
    imaging = _fits_file(tmp_path / "nikon_stack.fits", solved=True, camera="Nikon D5300")
    spectral = _fits_file(tmp_path / "spectral_stack.fits")
    target = _target_with_astrometry(imaging, _NOW + timedelta(hours=1))
    target.spectral_stacking.stacked_image = spectral

    assert resolve_spectral_stack_solution(target, spectral) is None


@pytest.mark.parametrize(("imaging_solved", "spectral_solved"), [(True, False), (False, True)])
def test_spectroscopy_with_a_solution_runs_without_astrometry(
    runner: _StageRunner,
    recorder: _RecordingPipelineMatch,
    tmp_path: Path,
    imaging_solved: bool,
    spectral_solved: bool,
) -> None:
    """Verify spectroscopy runs alone on a borrowed or its own solution."""
    target = _spectral_target(tmp_path, imaging_solved=imaging_solved, spectral_solved=spectral_solved)

    result = runner.run(target, ["spectroscopy"])

    assert recorder.stages == ["spectroscopy"]
    assert result.stages_skipped == {}


def test_spectroscopy_without_any_solution_is_skipped_once_with_a_warning(
    runner: _StageRunner,
    recorder: _RecordingPipelineMatch,
    quality_store: QualityStore,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify the skip: reason, one warning, one log line, no run."""
    target = _spectral_target(tmp_path, imaging_solved=False, spectral_solved=False)

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        result = runner.run(target, ["spectroscopy"], measured_at=_NOW)

    reason = result.stages_skipped["spectroscopy"]
    assert recorder.calls == []
    assert reason.startswith("needs astrometry: no imaging stack of the same camera and optic")
    assert reason.endswith(_RUN_ASTROMETRY)
    assert result.results["spectroscopy"] == {"status": "skipped", "reason": reason}
    assert len(_skip_lines(caplog, "spectroscopy")) == 1
    rows = _notice_rows(quality_store, "spectroscopy")
    assert [row.reason for row in rows] == [reason]


def test_a_spectral_only_target_is_told_to_solve_its_spectral_stack(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify the sentence names the spectral stack when it is all there is."""
    spectral = _fits_file(tmp_path / "spectral_stack.fits")
    target = Target(id="SpectralOnly", stacked_spectral_target=spectral)

    result = runner.run(target, ["spectroscopy"])

    assert recorder.calls == []
    assert result.stages_skipped["spectroscopy"].endswith(
        f"run processing.astrometry.run(target, {spectral!r}) first"
    )


def test_spectroscopy_with_no_spectral_data_is_skipped_for_that_reason(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a stage that has nothing to run is skipped as before."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = runner.run(target, ["spectroscopy"])

    assert recorder.calls == []
    assert result.results["spectroscopy"] == {"status": "skipped", "reason": "target has no spectral data"}
    assert result.stages_skipped == {}


def test_the_public_spectroscopy_run_skips_without_running_astrometry(
    recorder: _RecordingPipelineMatch, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `processing.spectroscopy.run` skips when nothing is solved."""
    from contextlib import nullcontext

    from astrometricslib.pipelines import pipeline_runs

    monkeypatch.setattr(pipeline_runs, "acquire_resource_slot", lambda *_args, **_kwargs: nullcontext())
    target = _spectral_target(tmp_path, imaging_solved=False, spectral_solved=False)

    result = ProcessingPipelines(AppConfiguration(), MagicMock()).spectroscopy.run(target)

    assert recorder.calls == []
    assert result.stages_skipped["spectroscopy"].startswith("needs astrometry: ")


# -- Spectral variation still adds spectroscopy ----------------------------


def _spectral_variation_target(
    tmp_path: Path, spectroscopy_at: datetime | None, with_raw_frames: bool = True, solved: bool = True
) -> Target:
    """Build a target with a solved stack, a spectral stack and raw frames.

    Returns
    -------
    target : `Target`
        The target. The spectral stack's recorded time is `_NOW`, and its
        saved spectroscopy summary (if asked for) is made at
        `spectroscopy_at`.
    """
    target = _spectral_target(tmp_path, imaging_solved=solved, spectral_solved=False)
    target.spectral_stacking.quality_summary = StackQualitySummary.model_construct(created_at=_NOW)
    if with_raw_frames:
        target.frames = [
            FrameRecord(path=f"/lib/raw_{index}.fits", role="LIGHT", filter="SPEC", timestamp=1000.0 + index)
            for index in range(3)
        ]
    if spectroscopy_at is not None:
        target.quality.spectroscopy = SpectroscopyQualitySummary.model_construct(
            created_at=spectroscopy_at, upstream_quality_summary_reference="stacking"
        )
    return target


def test_spectral_variation_needs_spectroscopy() -> None:
    """Verify spectroscopy is the prerequisite of spectral variation."""
    assert tasks.STAGE_PREREQUISITES["spectral_variation"] == ("spectroscopy",)
    assert (
        tasks.ANALYSIS_STAGES.index("spectral_variation") == tasks.ANALYSIS_STAGES.index("spectroscopy") + 1
    )


def test_a_current_spectroscopy_result_is_not_rerun(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify spectral variation alone runs when spectroscopy is current."""
    target = _spectral_variation_target(tmp_path, _NOW + timedelta(hours=1))

    result = runner.run(target, ["spectral_variation"])

    assert recorder.stages == ["spectral_variation"]
    assert result.prerequisites_run == []


@pytest.mark.parametrize("spectroscopy_at", [None, _NOW - timedelta(hours=1)])
def test_a_missing_or_old_spectroscopy_result_adds_spectroscopy_only(
    runner: _StageRunner,
    recorder: _RecordingPipelineMatch,
    tmp_path: Path,
    spectroscopy_at: datetime | None,
) -> None:
    """Verify spectroscopy, not astrometry, runs before spectral variation."""
    target = _spectral_variation_target(tmp_path, spectroscopy_at)

    result = runner.run(target, ["spectral_variation"])

    assert recorder.stages == ["spectroscopy", "spectral_variation"]
    assert result.prerequisites_run == ["spectroscopy"]


def test_an_added_spectroscopy_without_a_solution_skips_spectral_variation(
    runner: _StageRunner,
    recorder: _RecordingPipelineMatch,
    quality_store: QualityStore,
    tmp_path: Path,
) -> None:
    """Verify the chain stops with one warning and no astrometry run."""
    target = _spectral_variation_target(tmp_path, None, solved=False)

    result = runner.run(target, ["spectral_variation"], measured_at=_NOW)

    assert recorder.calls == []
    assert result.stages_skipped["spectral_variation"].startswith("needs spectroscopy: needs astrometry: ")
    assert len(_notice_rows(quality_store, "spectroscopy")) == 1


def test_a_spectroscopy_summary_from_raw_frames_is_not_current(tmp_path: Path) -> None:
    """Verify a per-frame summary does not count as the stack's result."""
    target = _spectral_variation_target(tmp_path, _NOW + timedelta(hours=1))
    target.quality.spectroscopy.upstream_quality_summary_reference = "raw_frames"

    assert tasks.stage_staleness_reason(target, "spectroscopy") == (
        "its result came from raw frames, not from the spectral stack"
    )


def test_spectral_variation_with_no_raw_frames_is_skipped_and_adds_nothing(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a target with no raw frames skips the stage with a reason."""
    target = _spectral_variation_target(tmp_path, None, with_raw_frames=False)

    result = runner.run(target, ["spectral_variation"])

    assert recorder.stages == []
    assert result.results["spectral_variation"]["status"] == "skipped"
    assert result.results["spectral_variation"]["reason"] == "target has no raw spectral frames"


def test_a_failed_spectroscopy_prerequisite_skips_spectral_variation(
    runner: _StageRunner, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify spectral variation is skipped when spectroscopy fails."""
    target = _spectral_variation_target(tmp_path, None)
    recorder.failures["spectroscopy"] = ProcessingError("no spectra")

    result = runner.run(target, ["spectral_variation"])

    assert "spectral_variation" not in recorder.stages
    assert result.stages_skipped["spectral_variation"].startswith("needs spectroscopy:")


def test_a_cancelled_run_stops_before_the_next_stage(
    runner: _StageRunner,
    recorder: _RecordingPipelineMatch,
    tmp_path: Path,
) -> None:
    """Verify a run given up on during astrometry never starts photometry."""
    abandonment = RunAbandonment()
    original_call = recorder.__class__.__call__

    def cancel_during_astrometry(self: _RecordingPipelineMatch, *args: object, **kwargs: object) -> dict:
        """Run the stand-in stage, then give up on the run.

        Returns
        -------
        result : `dict`
            The stand-in stage's result.
        """
        result = original_call(self, *args, **kwargs)
        abandonment.abandon()
        return result

    recorder.astrometry_solves = True
    target = _target_with_astrometry(_stack_file(tmp_path), None)
    with (
        bind_abandonment(abandonment),
        pytest.MonkeyPatch.context() as patch,
    ):
        patch.setattr(_RecordingPipelineMatch, "__call__", cancel_during_astrometry)
        with pytest.raises(RunAbandonedError):
            runner.run(target, ["astrometry", "photometry"])

    assert recorder.stages == ["astrometry"]


def test_naming_the_targets_own_spectral_stack_does_not_make_spectroscopy_stale(tmp_path: Path) -> None:
    """Verify the stack passed as the image counts as no other image.

    The app always sends the spectral stack's path with the spectroscopy
    options, so a current result must stay current.
    """
    target = _spectral_variation_target(tmp_path, _NOW + timedelta(hours=1))
    stack_path = target.spectral_stacking.stacked_image

    assert tasks.stage_staleness_reason(target, "spectroscopy", stack_path) is None
    assert tasks.stage_staleness_reason(target, "spectroscopy", str(tmp_path / "other.fits")) == (
        "an image was named, so the saved result may not match it"
    )


# -- The start-to-finish batch run reads astrometry the same way -----------


def test_the_batch_photometry_stage_skips_without_a_solution(
    recorder: _RecordingPipelineMatch,
    quality_store: QualityStore,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify the batch photometry step skips when astrometry failed."""
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW + timedelta(hours=1), solved=False)

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        result = tasks._run_photometry_stage(target, MagicMock(), MagicMock(), [], None, 1, _NOW)

    assert recorder.calls == []
    assert result["status"] == "skipped"
    assert result["reason"].startswith("needs astrometry: the stack could not be plate solved")
    assert len(_skip_lines(caplog, "photometry")) == 1
    assert len(_notice_rows(quality_store, "photometry")) == 1


def test_the_batch_spectroscopy_stage_skips_without_a_solution(
    recorder: _RecordingPipelineMatch, quality_store: QualityStore, tmp_path: Path
) -> None:
    """Verify the batch spectroscopy step skips with no solution."""
    target = _spectral_target(tmp_path, imaging_solved=False, spectral_solved=False)
    frames = [FrameRecord(path="/lib/raw_0.fits", role="LIGHT", filter="SPEC", timestamp=1000.0)]

    tasks._run_spectroscopy_stage(target, MagicMock(), MagicMock(), frames, 1, _NOW)

    assert recorder.calls == []
    assert len(_notice_rows(quality_store, "spectroscopy")) == 1
