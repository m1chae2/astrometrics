"""Tests that a restack keeps the stack it replaces and restores it on failure.

The stacking engine is replaced by a stand-in that writes a file, so the tests
check the stage's handling of the old and new stacks and nothing else.
"""

import os
from pathlib import Path
from typing import NoReturn
from unittest.mock import MagicMock, patch

import pytest

from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics, StackQualitySummary
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks
from astrometricslib.pipelines.stacking.post_processing.previous_stack import previous_stack_path
from astrometricslib.utilities.config_loader import get_configuration
from astrometricslib.utilities.enums import FilterType

SIRIL_DRIVER = "astrometricslib.drivers.siril_interface.ImageProcessing"
SUMMARY_BUILDER = "astrometricslib.pipelines.stacking.stage._build_stack_quality_summary"
RUN_STACK = "astrometricslib.pipelines.stacking.stack_runner.run_stack"


def make_target() -> Target:
    """Build a target with luminance frames that exist only as records.

    Returns
    -------
    target : `Target`
        A target holding ten frames.
    """
    return Target(
        id="M 52",
        frames=[
            FrameRecord(
                path=f"/fake/l_{index}.fits",
                filter=FilterType.L,
                camera="ZWO ASI 533MM Pro",
                exposure="120.0",
                timestamp=float(index) * 120.0,
            )
            for index in range(10)
        ],
    )


@pytest.fixture
def stacks_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the stacks path at a temporary folder and turn the quarantine off.

    Returns
    -------
    folder : `pathlib.Path`
        The target's folder under the temporary stacks path.
    """
    configuration = get_configuration()
    monkeypatch.setattr(configuration, "get_stacks_path", lambda: tmp_path)
    monkeypatch.setattr(configuration, "get_quarantine_bad_frames_enabled", lambda: False)
    folder = tmp_path / "lights" / "M 52"
    folder.mkdir(parents=True)
    return folder


def run_stage(fake_run_stack: object) -> str | None:
    """Run `stack_frames` with the engine replaced by `fake_run_stack`.

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
        patch(RUN_STACK, side_effect=fake_run_stack),
    ):
        with patch.object(stacking_tasks, "write_stack_preview", MagicMock(return_value=None), create=True):
            return stacking_tasks.stack_frames(make_target(), filter_type=FilterType.L)


def stack_name_used(stacks_folder: Path) -> str:
    """Run the stage once to learn the file name it writes the stack to.

    Returns
    -------
    name : `str`
        The output file name the stage passed to the stacking engine.
    """
    seen: list[str] = []

    def probe(
        engine: object, frames: object, target_id: str, output_file: str, *args: object, **kwargs: object
    ) -> tuple[None, dict[str, object]]:
        """Record the name and write nothing.

        Returns
        -------
        result : `tuple`
            No stack and no diagnostics.
        """
        seen.append(output_file)
        return None, {}

    run_stage(probe)
    return seen[0]


def test_a_restack_keeps_the_old_stack_in_previous(stacks_folder: Path) -> None:
    """After a successful restack, the old stack and its picture are kept."""
    name = stack_name_used(stacks_folder)
    stack = stacks_folder / name
    stack.write_text("old")
    (stacks_folder / name.replace(".fits", "_preview.jpg")).write_text("old picture")

    def fake_run_stack(
        engine: object, frames: object, target_id: str, output_file: str, *args: object, **kwargs: object
    ) -> tuple[str, dict[str, object]]:
        """Write a new stack where the stage expects it.

        Returns
        -------
        result : `tuple`
            The path of the new stack and empty diagnostics.
        """
        (stacks_folder / output_file).write_text("new")
        return str(stacks_folder / output_file), {}

    run_stage(fake_run_stack)

    assert stack.read_text() == "new"
    kept = previous_stack_path(str(stack))
    assert kept is not None
    assert Path(kept).read_text() == "old"
    assert (stacks_folder / "_previous" / name.replace(".fits", "_preview.jpg")).read_text() == "old picture"


def test_a_failed_restack_puts_the_old_stack_back(stacks_folder: Path) -> None:
    """If the engine raises, the old stack stays and nothing is kept."""
    name = stack_name_used(stacks_folder)
    stack = stacks_folder / name
    stack.write_text("old")

    def failing_run_stack(*args: object, **kwargs: object) -> NoReturn:
        """Raise as a crashed stack would.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("siril crashed")

    with pytest.raises(RuntimeError, match="siril crashed"):
        run_stage(failing_run_stack)

    assert stack.read_text() == "old"
    assert not (stacks_folder / "_previous").exists()
    assert not (stacks_folder / "_previous.staging").exists()


def test_a_restack_that_makes_no_stack_puts_the_old_one_back(stacks_folder: Path) -> None:
    """If the engine returns no stack, the old stack is restored."""
    name = stack_name_used(stacks_folder)
    stack = stacks_folder / name
    stack.write_text("old")

    run_stage(lambda *args, **kwargs: (None, {}))

    assert stack.read_text() == "old"
    assert not (stacks_folder / "_previous").exists()


def test_with_the_setting_off_the_old_stack_is_overwritten(
    stacks_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``keep_previous_stack_enabled`` off, no previous stack is kept."""
    name = stack_name_used(stacks_folder)
    monkeypatch.setattr(get_configuration(), "get_keep_previous_stack_enabled", lambda: False)
    (stacks_folder / name).write_text("old")

    def fake_run_stack(
        engine: object, frames: object, target_id: str, output_file: str, *args: object, **kwargs: object
    ) -> tuple[str, dict[str, object]]:
        """Write a new stack where the stage expects it.

        Returns
        -------
        result : `tuple`
            The path of the new stack and empty diagnostics.
        """
        (stacks_folder / output_file).write_text("new")
        return str(stacks_folder / output_file), {}

    run_stage(fake_run_stack)

    assert (stacks_folder / name).read_text() == "new"
    assert not os.path.exists(stacks_folder / "_previous")
