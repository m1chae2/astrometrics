"""Tests for the public calls that measure, compare, discard and swap stacks.

They use small real FITS files in a temporary folder.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import ProcessingPipelines, QualityDiagnostics
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
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
    return ProcessingPipelines(AppConfiguration(), MagicMock())


def _diagnostics() -> QualityDiagnostics:
    """Make diagnostics on a default configuration.

    Returns
    -------
    diagnostics : `QualityDiagnostics`
        Diagnostics that need no target catalog for stack paths.
    """
    return QualityDiagnostics(AppConfiguration(), MagicMock())


def _flatness(stack: Path) -> float:
    """Measure how uneven the sky of a stack is.

    Returns
    -------
    flatness : `float`
        The flatness number of the stack compared with itself.
    """
    report = _diagnostics().stack_quality(str(stack), compare_to=str(stack), register_job=False)
    return report.comparison.before.flatness_rms


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


def test_stack_quality_compares_two_stack_files() -> None:
    """The diagnostics call returns both measurements and a summary."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        write_fits(Path(folder) / "a.fits", 0.05, 1)
        write_fits(Path(folder) / "b.fits", 0.01, 2)

        report = _diagnostics().stack_quality(
            str(Path(folder) / "b.fits"), compare_to=str(Path(folder) / "a.fits"), register_job=False
        )
        comparison = report.comparison

    assert comparison.changes["flatness_rms"] < -0.5
    assert len(comparison.summary) == 5


def test_comparing_with_the_previous_stack_uses_the_kept_stack(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """The previous stack is ``before`` and the current one is ``after``."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    keep_old_stack(stack)

    comparison = pipelines.diagnostics.stack_quality(
        target_with_stack(stack), compare_to="previous", register_job=False
    ).comparison

    assert comparison is not None
    assert comparison.before.path.endswith("_previous/M_27_L_Stacked.fits")
    assert comparison.after.path == str(stack)
    assert comparison.changes["flatness_rms"] < -0.5


def test_comparing_with_the_previous_stack_notes_when_nothing_is_kept(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """A target that was never restacked has no previous stack to compare."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)

    report = pipelines.diagnostics.stack_quality(
        target_with_stack(stack), compare_to="previous", register_job=False
    )
    assert report.comparison is None
    assert "No previous stack" in report.notes[0]


def test_discard_previous_stack_deletes_it(tmp_path: Path, pipelines: ProcessingPipelines) -> None:
    """Discarding removes the previous stack and leaves the current one."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    keep_old_stack(stack)
    target = target_with_stack(stack)

    removed = pipelines.discard_previous_stack(target)

    assert len(removed) == 1
    assert stack.exists()
    report = pipelines.diagnostics.stack_quality(target, compare_to="previous", register_job=False)
    assert report.comparison is None


def test_swap_with_previous_stack_restores_the_old_stack(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """After a swap the old, vignetted stack is current again."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    keep_old_stack(stack)
    target = target_with_stack(stack)
    before = _flatness(stack)

    restored = pipelines.swap_with_previous_stack(target)

    assert len(restored) == 1
    after = _flatness(stack)
    assert after > 3 * before


def test_a_target_without_a_stack_raises_a_clear_error(pipelines: ProcessingPipelines) -> None:
    """Asking about a missing stack is an error, not a silent no-op."""
    with pytest.raises(NotFoundError, match="has no stack"):
        pipelines.diagnostics.stack_quality(Target(id="M 27"), compare_to="previous", register_job=False)


def test_a_kind_is_refused_with_a_stack_path(tmp_path: Path) -> None:
    """``kind`` only applies to a target's stack, so a path with it raises."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)
    with pytest.raises(InvalidArgumentError):
        _diagnostics().stack_quality(str(stack), kind="spectral", register_job=False)
    with pytest.raises(InvalidArgumentError):
        _diagnostics().stack_quality(str(stack), include=["colour"], register_job=False)


def test_the_registration_section_reports_a_missing_seq_file(tmp_path: Path) -> None:
    """A section that cannot be measured is `None`, with a note saying why."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)
    report = _diagnostics().stack_quality(
        str(stack), include=["registration", "rejected_fraction"], register_job=False
    )
    assert report.registration is None
    assert report.rejected_fraction is None
    assert report.included == ["registration", "rejected_fraction"]
    assert len(report.notes) == 2
