"""Tests for the public calls that compare, discard and swap stacks.

They use small real FITS files in a temporary folder.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import ProcessingPipelines, QualityDiagnostics
from astrometricslib.models.target import Target
from astrometricslib.pipelines.stacking.post_processing.previous_stack import (
    archive_current_stack,
    commit_archive,
)

SIZE = 256


def write_fits(path: Path, vignette: float, seed: int) -> None:
    """Write a small stack with noise and a radial vignette.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write.
    vignette : `float`
        Fractional dimming at the corners.
    seed : `int`
        Seed of the noise.
    """
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - SIZE / 2, x - SIZE / 2) / (SIZE / 2 * np.sqrt(2))
    sky = 1000.0 * (1.0 - vignette * radius**2)
    data = sky * (1.0 + 0.01 * np.random.default_rng(seed).standard_normal((SIZE, SIZE)))
    fits.writeto(path, data.astype(np.float32), overwrite=True)


@pytest.fixture
def pipelines() -> ProcessingPipelines:
    """Make pipelines on a stub configuration.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        Pipelines that need no real configuration for these calls.
    """
    return ProcessingPipelines(SimpleNamespace())


def target_with_stack(path: Path) -> Target:
    """Build a target whose imaging stack is `path`.

    Returns
    -------
    target : `Target`
        A target with its stacked image set.
    """
    target = Target(id="M 27")
    target.stacking.stacked_image = str(path)
    return target


def keep_old_stack(stack: Path) -> None:
    """Write an old stack, move it to `_previous`, and write a new one."""
    write_fits(stack, vignette=0.05, seed=1)
    commit_archive(str(stack), archive_current_stack(str(stack)))
    write_fits(stack, vignette=0.01, seed=2)


def test_compare_stacks_measures_two_files() -> None:
    """The diagnostics call returns both measurements and a summary."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        write_fits(Path(folder) / "a.fits", 0.05, 1)
        write_fits(Path(folder) / "b.fits", 0.01, 2)

        comparison = QualityDiagnostics(SimpleNamespace()).compare_stacks(
            str(Path(folder) / "a.fits"), str(Path(folder) / "b.fits")
        )

    assert comparison.changes["flatness_rms"] < -0.5
    assert len(comparison.summary) == 5


def test_compare_with_previous_stack_uses_the_kept_stack(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """The previous stack is ``before`` and the current one is ``after``."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    keep_old_stack(stack)

    comparison = pipelines.compare_with_previous_stack(target_with_stack(stack))

    assert comparison is not None
    assert comparison.before.path.endswith("_previous/M_27_L_Stacked.fits")
    assert comparison.after.path == str(stack)
    assert comparison.changes["flatness_rms"] < -0.5


def test_compare_with_previous_stack_is_none_when_nothing_is_kept(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """A target that was never restacked has no previous stack to compare."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)

    assert pipelines.compare_with_previous_stack(target_with_stack(stack)) is None


def test_discard_previous_stack_deletes_it(tmp_path: Path, pipelines: ProcessingPipelines) -> None:
    """Discarding removes the previous stack and leaves the current one."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    keep_old_stack(stack)
    target = target_with_stack(stack)

    removed = pipelines.discard_previous_stack(target)

    assert len(removed) == 1
    assert stack.exists()
    assert pipelines.compare_with_previous_stack(target) is None


def test_swap_with_previous_stack_restores_the_old_stack(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """After a swap the old, vignetted stack is current again."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    keep_old_stack(stack)
    target = target_with_stack(stack)
    before = QualityDiagnostics(SimpleNamespace()).compare_stacks(str(stack), str(stack)).before.flatness_rms

    restored = pipelines.swap_with_previous_stack(target)

    assert len(restored) == 1
    after = QualityDiagnostics(SimpleNamespace()).compare_stacks(str(stack), str(stack)).before.flatness_rms
    assert after > 3 * before


def test_a_target_without_a_stack_raises_a_clear_error(pipelines: ProcessingPipelines) -> None:
    """Asking about a missing stack is an error, not a silent no-op."""
    with pytest.raises(ValueError, match="has no stack"):
        pipelines.compare_with_previous_stack(Target(id="M 27"))
