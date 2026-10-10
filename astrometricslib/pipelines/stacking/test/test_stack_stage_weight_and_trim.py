"""Tests that the stage weights mixed exposures and trims noisy stack edges.

The stacking engine is replaced by a stand-in, so the tests check what the
stage asks of the engine and what it does to the stack afterwards.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics, StackQualitySummary
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks

SIRIL_DRIVER = "astrometricslib.drivers.siril_interface.ImageProcessing"
SUMMARY_BUILDER = "astrometricslib.pipelines.stacking.stage._build_stack_quality_summary"
RUN_STACK = "astrometricslib.pipelines.stacking.stack_runner.run_stack"
PREVIEW = "astrometricslib.pipelines.stacking.post_processing.stack_preview.write_stack_preview"
SIZE = 600


def make_target(exposures: list[float]) -> Target:
    """Build a target with luminance frames of the given exposures.

    Returns
    -------
    target : `Target`
        A target whose frames exist only as records.
    """
    return Target(
        id="M 52",
        frames=[
            FrameRecord(
                path=f"/fake/l_{index}.fits",
                filter=FilterType.L,
                camera="ZWO ASI 533MM Pro",
                exposure=str(exposure),
                timestamp=float(index) * 200.0,
            )
            for index, exposure in enumerate(exposures)
        ],
    )


@pytest.fixture
def stacks_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the stacks path at a temporary folder; turn the quarantine off.

    Returns
    -------
    folder : `pathlib.Path`
        The target's folder under the temporary stacks path.
    """
    configuration = get_configuration()
    monkeypatch.setattr(configuration, "get_stacks_path", lambda: tmp_path)
    monkeypatch.setattr(configuration, "get_quarantine_bad_frames_enabled", lambda: False)
    monkeypatch.setattr(configuration, "get_stack_weight", lambda: None)
    folder = tmp_path / "lights" / "M 52"
    folder.mkdir(parents=True)
    return folder


def run_stage(target: Target, fake_run_stack: object) -> str | None:
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
            is_spectral=False, frames_submitted=len(target.frames), frames_stacked=len(target.frames)
        ),
    )
    with (
        patch(SIRIL_DRIVER),
        patch(SUMMARY_BUILDER, return_value=summary),
        patch(RUN_STACK, side_effect=fake_run_stack),
        patch(PREVIEW, MagicMock(return_value=None)),
    ):
        return stacking_tasks.stack_frames(target, filter_type=FilterType.L)


def test_mixed_exposures_are_stacked_with_noise_weighting(stacks_folder: Path) -> None:
    """Frames of 30 s and 120 s ask the engine for noise weighting."""
    seen: dict[str, object] = {}

    def fake_run_stack(*args: object, **kwargs: object) -> tuple[None, dict[str, object]]:
        """Record the weight and make no stack.

        Returns
        -------
        result : `tuple`
            No stack and no diagnostics.
        """
        seen.update(kwargs)
        return None, {}

    run_stage(make_target([30.0] * 5 + [120.0] * 5), fake_run_stack)

    assert seen["stack_weight"] == "noise"


def test_equal_exposures_ask_for_no_weighting(stacks_folder: Path) -> None:
    """Frames of one length keep the engine's own default."""
    seen: dict[str, object] = {}

    def fake_run_stack(*args: object, **kwargs: object) -> tuple[None, dict[str, object]]:
        """Record the weight and make no stack.

        Returns
        -------
        result : `tuple`
            No stack and no diagnostics.
        """
        seen.update(kwargs)
        return None, {}

    run_stage(make_target([120.0] * 10), fake_run_stack)

    assert seen["stack_weight"] is None


def test_a_stack_with_noisy_edges_is_trimmed_before_it_is_recorded(stacks_folder: Path) -> None:
    """The finished stack and its rejection map lose the noisy strip."""
    scale = np.full((SIZE, SIZE), 0.01)
    scale[:, :100] *= 1.5
    data = (1000.0 * (1.0 + scale * np.random.default_rng(0).standard_normal((SIZE, SIZE)))).astype(
        np.float32
    )

    def fake_run_stack(
        engine: object, frames: object, target_id: str, output_file: str, *args: object, **kwargs: object
    ) -> tuple[str, dict[str, object]]:
        """Write a stack with a noisy left edge, and its rejection map.

        Returns
        -------
        result : `tuple`
            The path of the stack and empty diagnostics.
        """
        stack = stacks_folder / output_file
        fits.writeto(stack, data)
        fits.writeto(str(stack).replace(".fits", "_RejMap.fits"), np.zeros_like(data))
        return str(stack), {}

    path = run_stage(make_target([120.0] * 10), fake_run_stack)

    assert path is not None
    with fits.open(path, memmap=False) as hdul:
        assert hdul[0].data.shape[1] < SIZE - 50
    with fits.open(path.replace(".fits", "_RejMap.fits"), memmap=False) as hdul:
        assert hdul[0].data.shape[1] < SIZE - 50


def test_the_trim_can_be_turned_off(stacks_folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the setting off, the stack keeps every edge."""
    monkeypatch.setattr(get_configuration(), "get_trim_noisy_stack_edges_enabled", lambda: False)
    scale = np.full((SIZE, SIZE), 0.01)
    scale[:, :100] *= 1.5
    data = (1000.0 * (1.0 + scale * np.random.default_rng(0).standard_normal((SIZE, SIZE)))).astype(
        np.float32
    )

    def fake_run_stack(
        engine: object, frames: object, target_id: str, output_file: str, *args: object, **kwargs: object
    ) -> tuple[str, dict[str, object]]:
        """Write a stack with a noisy left edge.

        Returns
        -------
        result : `tuple`
            The path of the stack and empty diagnostics.
        """
        fits.writeto(stacks_folder / output_file, data)
        return str(stacks_folder / output_file), {}

    path = run_stage(make_target([120.0] * 10), fake_run_stack)

    with fits.open(path, memmap=False) as hdul:
        assert hdul[0].data.shape == (SIZE, SIZE)
