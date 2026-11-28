"""Tests for the public calls that measure, compare, discard and swap stacks.

They use small real FITS files in a temporary folder.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api._quality_frames import QualityStacks
from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.api.quality import QualityRecords
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


def _diagnostics() -> QualityStacks:
    """Make the stack checks on a default configuration.

    Returns
    -------
    stacks : `QualityStacks`
        ``astrometrics.quality.stacks``, which needs no target catalog.
    """
    return QualityRecords(AppConfiguration(), MagicMock()).stacks


def _flatness(stack: Path) -> float:
    """Measure how uneven the sky of a stack is.

    Returns
    -------
    flatness : `float`
        The flatness number of the stack compared with itself.
    """
    report = _diagnostics().measure(str(stack), compare_to=str(stack))
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

        report = _diagnostics().measure(str(Path(folder) / "b.fits"), compare_to=str(Path(folder) / "a.fits"))
        comparison = report.comparison

    assert comparison.changes["flatness_rms"] < -0.5
    assert len(comparison.summary) == 6
    assert any("noise in empty sky: not measured" in line for line in comparison.summary)


def test_comparing_with_the_previous_stack_uses_the_kept_stack(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """The previous stack is ``before`` and the current one is ``after``."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    keep_old_stack(stack)

    comparison = _diagnostics().measure(target_with_stack(stack), compare_to="previous").comparison

    assert comparison is not None
    assert Path(comparison.before.path).as_posix().endswith("_previous/M_27_L_Stacked.fits")
    assert comparison.after.path == str(stack)
    assert comparison.changes["flatness_rms"] < -0.5


def test_comparing_with_the_previous_stack_notes_when_nothing_is_kept(
    tmp_path: Path, pipelines: ProcessingPipelines
) -> None:
    """A target that was never restacked has no previous stack to compare."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)

    report = _diagnostics().measure(target_with_stack(stack), compare_to="previous")
    assert report.comparison is None
    assert "No previous stack" in report.notes[0]


def test_a_target_without_a_stack_raises_a_clear_error(pipelines: ProcessingPipelines) -> None:
    """Asking about a missing stack is an error, not a silent no-op."""
    with pytest.raises(NotFoundError, match="has no stack"):
        _diagnostics().measure(Target(id="M 27"), compare_to="previous")


def test_a_stack_choice_is_refused_with_a_stack_path(tmp_path: Path) -> None:
    """``stack`` chooses among a target's stacks, so a path with it raises."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)
    with pytest.raises(InvalidArgumentError):
        _diagnostics().measure(str(stack), stack=str(stack))
    with pytest.raises(InvalidArgumentError):
        _diagnostics().measure(str(stack), include=["colour"])
    with pytest.raises(InvalidArgumentError):
        _diagnostics().measure("M 27")


def test_another_stack_of_a_target_is_measured_and_its_kind_inferred(tmp_path: Path) -> None:
    """``stack`` picks the spectral stack, and the target stays named."""
    imaging = tmp_path / "M_27_L_Stacked.fits"
    spectral = tmp_path / "M_27_SPEC_Stacked.fits"
    write_fits(imaging, 0.01, 2)
    write_fits(spectral, 0.01, 3)
    target = target_with_stack(imaging)
    target.spectral_stacking.stacked_image = str(spectral)

    report = _diagnostics().measure(target, stack=str(spectral), compare_to=str(spectral))

    assert report.stack_path == str(spectral)
    assert report.target_id == "M 27"
    assert report.comparison is not None


def test_the_registration_section_reports_a_missing_seq_file(tmp_path: Path) -> None:
    """A section that cannot be measured is `None`, with a note saying why."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)
    report = _diagnostics().measure(str(stack), include=["registration", "rejected_fraction"])
    assert report.registration is None
    assert report.rejected_fraction is None
    assert report.included == ["registration", "rejected_fraction"]
    assert len(report.notes) == 2


def test_the_registration_section_returns_typed_rows_from_a_real_format_seq_file(tmp_path: Path) -> None:
    """A real registration line becomes a typed row."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    write_fits(stack, 0.01, 2)
    # The line is copied from a real M 106 stack.
    (tmp_path / "M_27_L_Stacked_Registration.seq").write_text(
        "I 0 1\n"
        "R1 2.83064 3.27475 0.886847 0 0.0238392 1163 H 1 -1.53863e-05 6.3825 1.73631e-05 "
        "1.00003 -29.2903 -2.69105e-09 4.67203e-09 1\n"
    )
    report = _diagnostics().measure(str(stack), include=["registration"])
    assert report.registration is not None
    (row,) = report.registration
    assert row.star_count == 1163
    assert row.dx == pytest.approx(6.3825)
    assert len(row.homography) == 9
    assert row.model_dump(by_alias=True)["weightedFwhmPx"] == pytest.approx(3.27475)
