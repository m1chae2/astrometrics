"""Tests that stacking skips a stack whose inputs have not changed.

The stacking engine is replaced by a stand-in that writes a file and counts
its calls, so the tests check what the stage decides and nothing else. The
first run builds the stack and saves a record of its inputs. A second run with
the same inputs does nothing. A change to the frames, a forced restack or the
setting turned off builds it again. The record travels with the stack into the
``_previous`` folder.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics, StackQualitySummary
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks
from astrometricslib.pipelines.stacking.post_processing.previous_stack import previous_stack_path
from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import inputs_file_path

SIRIL_DRIVER = "astrometricslib.drivers.siril_interface.ImageProcessing"
SUMMARY_BUILDER = "astrometricslib.pipelines.stacking.stage._build_stack_quality_summary"
RUN_STACK = "astrometricslib.pipelines.stacking.stack_runner.run_stack"


class Engine:
    """A stand-in for the stacking engine that counts how often it runs."""

    def __init__(self, stacks_folder: Path) -> None:
        """Remember where the stack is written."""
        self.stacks_folder = stacks_folder
        self.calls = 0

    def __call__(
        self,
        engine: object,
        frames: object,
        target_id: str,
        output_file: str,
        *args: object,
        **kwargs: object,
    ) -> tuple[str, dict[str, object]]:
        """Write a stack where the stage expects it.

        Returns
        -------
        result : `tuple`
            The stack's path and empty diagnostics.
        """
        self.calls += 1
        path = self.stacks_folder / output_file
        path.write_text(f"stack {self.calls}")
        return str(path), {}


def make_target(light_files: list[Path]) -> Target:
    """Build a target whose luminance frames are the given files.

    Returns
    -------
    target : `Target`
        A target holding one frame record for each file.
    """
    return Target(
        id="M 52",
        frames=[
            FrameRecord(
                path=str(path),
                filter=FilterType.L,
                camera="ZWO ASI 533MM Pro",
                exposure="120.0",
                timestamp=float(index) * 120.0,
            )
            for index, path in enumerate(light_files)
        ],
    )


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[Path], Engine]:
    """Point the stacks path at a temporary folder and make ten light frames.

    Returns
    -------
    setup : `tuple`
        The target's stack folder, the light files, and the engine stand-in.
    """
    configuration = get_configuration()
    monkeypatch.setattr(configuration, "get_stacks_path", lambda: tmp_path / "stacks")
    monkeypatch.setattr(configuration, "get_quarantine_bad_frames_enabled", lambda: False)
    monkeypatch.setattr(configuration, "get_skip_unchanged_stacks_enabled", lambda: True)
    monkeypatch.delenv("ASTROMETRICS_FORCE_RESTACK", raising=False)
    folder = tmp_path / "stacks" / "lights" / "M 52"
    folder.mkdir(parents=True)
    lights = []
    for index in range(10):
        path = tmp_path / f"light_{index}.fits"
        path.write_bytes(b"frame")
        lights.append(path)
    return folder, lights, Engine(folder)


def run_stage(target: Target, engine: Engine, **options: object) -> str | None:
    """Run `stack_frames` with the engine stand-in.

    Returns
    -------
    stacked_path : `str` or `None`
        What the stage returned.
    """
    summary = StackQualitySummary(
        pipeline_name="stacking",
        pipeline_version="1.0.0",
        target_id="M 52",
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False, frames_submitted=10, frames_stacked=10
        ),
    )
    with (
        patch(SIRIL_DRIVER),
        patch(SUMMARY_BUILDER, return_value=summary),
        patch(RUN_STACK, side_effect=engine),
        patch.object(stacking_tasks, "write_stack_preview", MagicMock(return_value=None), create=True),
    ):
        return stacking_tasks.stack_frames(target, filter_type=FilterType.L, **options)


def test_the_first_run_builds_the_stack_and_saves_a_record(setup: tuple[Path, list[Path], Engine]) -> None:
    """With no stack on disk, the stack is built and its inputs recorded."""
    _, lights, engine = setup

    path = run_stage(make_target(lights), engine)

    assert engine.calls == 1
    assert path is not None
    assert os.path.isfile(inputs_file_path(path))


def test_a_second_run_with_the_same_inputs_does_nothing(setup: tuple[Path, list[Path], Engine]) -> None:
    """An unchanged stack is kept and its path returned."""
    folder, lights, engine = setup
    first = run_stage(make_target(lights), engine)
    assert first is not None
    kept = Path(first).read_text()

    target = make_target(lights)
    second = run_stage(target, engine)

    assert engine.calls == 1
    assert second == first
    assert Path(first).read_text() == kept
    assert target.stacking.stacked_image == first
    assert not (folder / "_previous").exists()


def test_an_added_frame_builds_the_stack_again_and_keeps_the_old_one(
    setup: tuple[Path, list[Path], Engine], tmp_path: Path
) -> None:
    """New frames change the inputs, so the stack is rebuilt."""
    folder, lights, engine = setup
    first = run_stage(make_target(lights), engine)
    assert first is not None
    extra = tmp_path / "light_new.fits"
    extra.write_bytes(b"frame")

    path = run_stage(make_target([*lights, extra]), engine)

    assert engine.calls == 2
    assert path is not None
    assert Path(path).read_text() == "stack 2"
    kept = previous_stack_path(path)
    assert kept is not None
    assert Path(kept).read_text() == "stack 1"
    # The old stack's record went to `_previous` with it; the new stack has
    # its own.
    assert os.path.isfile(folder / "_previous" / os.path.basename(inputs_file_path(path)))
    assert os.path.isfile(inputs_file_path(path))


def test_force_builds_the_stack_again(setup: tuple[Path, list[Path], Engine]) -> None:
    """``force=True`` rebuilds a stack whose inputs have not changed."""
    _, lights, engine = setup
    run_stage(make_target(lights), engine)

    run_stage(make_target(lights), engine, force=True)

    assert engine.calls == 2


def test_the_environment_variable_forces_a_rebuild(
    setup: tuple[Path, list[Path], Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--force-restack`` of the batch script reaches the stage this way."""
    _, lights, engine = setup
    run_stage(make_target(lights), engine)
    monkeypatch.setenv("ASTROMETRICS_FORCE_RESTACK", "1")

    run_stage(make_target(lights), engine)

    assert engine.calls == 2


def test_with_the_setting_off_every_run_builds_the_stack(
    setup: tuple[Path, list[Path], Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``skip_unchanged_stacks_enabled`` off, nothing is skipped."""
    _, lights, engine = setup
    monkeypatch.setattr(get_configuration(), "get_skip_unchanged_stacks_enabled", lambda: False)
    run_stage(make_target(lights), engine)

    run_stage(make_target(lights), engine)

    assert engine.calls == 2


def test_a_deleted_stack_is_built_again_even_though_its_record_remains(
    setup: tuple[Path, list[Path], Engine],
) -> None:
    """A record without its stack is not a stack."""
    _, lights, engine = setup
    first = run_stage(make_target(lights), engine)
    assert first is not None
    os.remove(first)

    run_stage(make_target(lights), engine)

    assert engine.calls == 2


def test_a_changed_setting_builds_the_stack_again(
    setup: tuple[Path, list[Path], Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A setting that changes the pixels invalidates the record."""
    _, lights, engine = setup
    run_stage(make_target(lights), engine)
    monkeypatch.setattr(get_configuration(), "get_trim_noisy_stack_edges_enabled", lambda: False)

    run_stage(make_target(lights), engine)

    assert engine.calls == 2


def test_a_stack_made_before_records_existed_is_built_once(setup: tuple[Path, list[Path], Engine]) -> None:
    """A stack with no record is rebuilt, and then skipped."""
    _, lights, engine = setup
    first = run_stage(make_target(lights), engine)
    assert first is not None
    os.remove(inputs_file_path(first))

    run_stage(make_target(lights), engine)
    run_stage(make_target(lights), engine)

    assert engine.calls == 2
