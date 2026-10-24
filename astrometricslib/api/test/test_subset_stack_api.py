"""Purpose: Tests for the subset-stack methods of `ProcessingPipelines`.

Description: `stack_subset`, `measure_noise_growth`,
`measure_night_contributions` and `measure_half_difference_noise` are thin:
they resolve the target, find its
library stack, record a job and hand the work to
`pipelines/stacking/subset_noise_run.py` with a measurement time from the
clock. The science is tested there. These tests check the wiring with the
pipeline functions replaced: the job is recorded, the time has a time zone and
the job id, the night contributions run only when asked, the half-difference
noise runs inside the growth job unless turned off and also as its own job
(with or without a job record), and a target with no stack is refused.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.quality.subset_stacks import (
    HalfDifferenceNoiseResult,
    NightContributionResult,
    NoiseGrowthResult,
    SubsetStackResult,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import subset_noise_run

LIBRARY_STACK = "/stacks/lights/M 99/M99_L_Stacked.fits"


@pytest.fixture
def isolated_job_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the job database and job log files at a throwaway folder.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's folder.
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the configuration lookups.

    Yields
    ------
    logs_database_path : `str`
        Path to the throwaway logs database the test reads back.
    """
    library_path = tmp_path / "library"
    library_path.mkdir(parents=True, exist_ok=True)
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(config, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: config)
    yield config.get_logs_db_path()


def _read_jobs(logs_database_path: str, target_id: str) -> list:
    """Read back every job recorded for one target.

    Parameters
    ----------
    logs_database_path : `str`
        The throwaway logs database.
    target_id : `str`
        The target.

    Returns
    -------
    jobs : `list`
        The stored jobs, newest first.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    return JobStore(logs_database_path).get_jobs_by_target(target_id)


def _target() -> Target:
    """Build a target with a library stack and one frame.

    Returns
    -------
    target : `Target`
        The target.
    """
    target = Target(id="SubsetTarget", frames=[FrameRecord(path="/frames/a.fits", camera="Cam")])
    target.stacking.stacked_image = LIBRARY_STACK
    return target


def _pipelines() -> ProcessingPipelines:
    """Build the processing API with a stand-in catalog.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        The API.
    """
    return ProcessingPipelines(AppConfiguration(), MagicMock(), targets=MagicMock())


def test_the_growth_job_records_a_job_and_runs_the_nights_only_when_asked(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The context carries the job id and a zoned time; nights are opt-in."""
    seen: dict[str, Any] = {"nights": 0}

    def growth(target: Target, stack_path: str, **keywords: Any) -> NoiseGrowthResult:
        """Record the call.

        Returns
        -------
        result : `NoiseGrowthResult`
            An empty series.
        """
        seen.update(keywords, stack_path=stack_path)
        return NoiseGrowthResult(targetId=target.id, libraryStackPath=stack_path)

    def nights(target: Target, stack_path: str, **keywords: Any) -> NightContributionResult:
        """Record the call.

        Returns
        -------
        result : `NightContributionResult`
            An empty result.
        """
        seen["nights"] += 1
        return NightContributionResult(targetId=target.id, libraryStackPath=stack_path)

    monkeypatch.setattr(subset_noise_run, "run_noise_growth", growth)
    monkeypatch.setattr(subset_noise_run, "run_night_contributions", nights)
    monkeypatch.setattr(
        subset_noise_run,
        "run_half_difference_noise",
        lambda target, stack_path, **keywords: HalfDifferenceNoiseResult(
            targetId=target.id, libraryStackPath=stack_path
        ),
    )
    pipelines = _pipelines()

    result = pipelines.measure_noise_growth(_target(), maximum_steps=3)
    assert result.night_contributions is None
    assert seen["nights"] == 0
    assert seen["stack_path"] == LIBRARY_STACK
    assert seen["maximum_steps"] == 3
    jobs = _read_jobs(isolated_job_logging, "SubsetTarget")
    assert [job.status for job in jobs] == ["completed"]
    assert jobs[0].job_type == "stacking"
    context = seen["context"]
    assert context.job_id == jobs[0].id
    assert context.measured_at.utcoffset() == timedelta(0)

    result = pipelines.measure_noise_growth(_target(), include_night_contributions=True)
    assert result.night_contributions is not None
    assert seen["nights"] == 1


def test_the_growth_job_runs_the_half_difference_unless_turned_off(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half difference is on by default, can be off, keeps the series."""
    calls: list[dict[str, Any]] = []

    def half(target: Target, stack_path: str, **keywords: Any) -> HalfDifferenceNoiseResult:
        """Record the call.

        Returns
        -------
        result : `HalfDifferenceNoiseResult`
            An empty result.
        """
        calls.append(keywords)
        return HalfDifferenceNoiseResult(targetId=target.id, libraryStackPath=stack_path)

    monkeypatch.setattr(
        subset_noise_run,
        "run_noise_growth",
        lambda target, stack_path, **keywords: NoiseGrowthResult(
            targetId=target.id, libraryStackPath=stack_path
        ),
    )
    monkeypatch.setattr(subset_noise_run, "run_half_difference_noise", half)
    pipelines = _pipelines()

    assert pipelines.measure_noise_growth(_target()).half_difference is not None
    assert len(calls) == 1
    assert pipelines.measure_noise_growth(_target(), include_half_difference=False).half_difference is None
    assert len(calls) == 1

    def fail(target: Target, stack_path: str, **keywords: Any) -> HalfDifferenceNoiseResult:
        """Raise an error.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("Siril stopped")

    monkeypatch.setattr(subset_noise_run, "run_half_difference_noise", fail)
    result = pipelines.measure_noise_growth(_target())
    assert result.half_difference is None
    assert any("half-difference noise was not measured" in note for note in result.notes)


def test_the_half_difference_job_runs_with_and_without_a_job_record(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standalone job passes the library stack, a context and keep flag."""
    seen: list[dict[str, Any]] = []

    def half(target: Target, stack_path: str, **keywords: Any) -> HalfDifferenceNoiseResult:
        """Record the call.

        Returns
        -------
        result : `HalfDifferenceNoiseResult`
            An empty result.
        """
        seen.append({"stack_path": stack_path, **keywords})
        return HalfDifferenceNoiseResult(targetId=target.id, libraryStackPath=stack_path)

    monkeypatch.setattr(subset_noise_run, "run_half_difference_noise", half)
    pipelines = _pipelines()

    result = pipelines.measure_half_difference_noise(_target(), keep_halves=True)
    assert result.library_stack_path == LIBRARY_STACK
    assert seen[0]["keep_halves"] is True
    jobs = _read_jobs(isolated_job_logging, "SubsetTarget")
    assert [job.status for job in jobs] == ["completed"]
    assert seen[0]["context"].job_id == jobs[0].id

    pipelines.measure_half_difference_noise(_target(), register_job=False)
    assert seen[1]["keep_halves"] is False
    assert len(_read_jobs(isolated_job_logging, "SubsetTarget")) == 1
    with pytest.raises(NotFoundError):
        pipelines.measure_half_difference_noise(Target(id="BareTarget", frames=[]))


def test_the_night_job_runs_the_night_function(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standalone job calls the night function with the library stack."""
    calls: list[str] = []

    def nights(target: Target, stack_path: str, **keywords: Any) -> NightContributionResult:
        """Record the stack path.

        Returns
        -------
        result : `NightContributionResult`
            An empty result.
        """
        calls.append(stack_path)
        return NightContributionResult(targetId=target.id, libraryStackPath=stack_path)

    monkeypatch.setattr(subset_noise_run, "run_night_contributions", nights)
    _pipelines().measure_night_contributions(_target())
    assert calls == [LIBRARY_STACK]


def test_a_target_without_a_stack_is_refused_and_steps_are_checked(
    isolated_job_logging: str,
) -> None:
    """No stack is NotFoundError; no steps is InvalidArgumentError."""
    bare = Target(id="BareTarget", frames=[])
    with pytest.raises(NotFoundError):
        _pipelines().measure_noise_growth(bare)
    with pytest.raises(NotFoundError):
        _pipelines().measure_night_contributions(bare)
    with pytest.raises(InvalidArgumentError, match="maximum_steps"):
        _pipelines().measure_noise_growth(_target(), maximum_steps=0)


def test_stack_subset_passes_the_frames_and_the_library_stack(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A subset uses the target's stack folder, or a default one."""
    seen: dict[str, Any] = {}

    def chosen(
        target: Target, frames: list[FrameRecord], label: str, stack_path: str, **keywords: Any
    ) -> SubsetStackResult:
        """Record the call.

        Returns
        -------
        result : `SubsetStackResult`
            An empty result.
        """
        seen.update(keywords, label=label, stack_path=stack_path, frames=frames, target=target)
        return SubsetStackResult(targetId=target.id, label=label)

    monkeypatch.setattr(subset_noise_run, "stack_chosen_subset", chosen)
    target = _target()
    result = _pipelines().stack_subset(target, target.frames, label="mine", output_folder=str(tmp_path))
    assert result.label == "mine"
    assert seen["stack_path"] == LIBRARY_STACK
    assert seen["output_folder"] == str(tmp_path)
    assert seen["frames"] == target.frames

    bare = Target(id="Bare Target", frames=[])
    _pipelines().stack_subset(bare, [], label="x")
    assert seen["stack_path"].endswith("Bare Target/Bare_Target_Stacked.fits")
