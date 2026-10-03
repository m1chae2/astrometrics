"""Tests for how the stack's star width is compared with its input frames.

The check samples frames spread over the whole set, so a stack of several
nights is judged against all of them, and it compares the stack with the
width its inputs predict.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics
from astrometricslib.pipelines.stacking import stage

MEASURE = "astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm"


def make_summary() -> SimpleNamespace:
    """Build a stand-in summary holding empty stacking metrics.

    Returns
    -------
    summary : `SimpleNamespace`
        An object with a `stacking_metrics` attribute.
    """
    return SimpleNamespace(
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False, frames_submitted=10, frames_stacked=10
        )
    )


def test_the_sample_is_spread_over_all_frames_not_taken_from_the_start() -> None:
    """With 60 frames, every fourth one is measured, up to 15 of them."""
    frames = [SimpleNamespace(path=str(index)) for index in range(60)]
    measured: list[str] = []

    def fake_measure(path: str) -> float:
        """Record the path and give a fixed width.

        Returns
        -------
        width : `float`
            A width of 2 pixels for a frame and 2.2 for the stack.
        """
        measured.append(path)
        return 2.2 if path == "stack.fits" else 2.0

    with patch(MEASURE, side_effect=fake_measure):
        stage._measure_fwhm_degradation(make_summary(), "stack.fits", frames)

    frame_paths = [int(path) for path in measured if path != "stack.fits"]
    assert frame_paths == list(range(0, 60, 4))


def test_the_stack_is_compared_with_the_rms_of_its_inputs() -> None:
    """A stack matching the RMS of mixed frames is not flagged."""
    frames = [SimpleNamespace(path=str(index)) for index in range(10)]
    widths = {str(index): (2.0 if index < 5 else 4.0) for index in range(10)}
    summary = make_summary()

    with patch(MEASURE, side_effect=lambda path: 3.2 if path == "stack.fits" else widths[path]):
        stage._measure_fwhm_degradation(summary, "stack.fits", frames)

    metrics = summary.stacking_metrics
    assert metrics.expected_stack_fwhm_px == pytest.approx(10.0**0.5)
    assert metrics.stacked_fwhm_px == pytest.approx(3.2)
    assert metrics.median_input_fwhm_px == pytest.approx(3.0)
    assert metrics.fwhm_degraded is False


def test_a_stack_much_wider_than_its_inputs_is_flagged() -> None:
    """A stack 1.5 times wider than uniform frames is degraded."""
    frames = [SimpleNamespace(path=str(index)) for index in range(10)]
    summary = make_summary()

    with patch(MEASURE, side_effect=lambda path: 3.0 if path == "stack.fits" else 2.0):
        stage._measure_fwhm_degradation(summary, "stack.fits", frames)

    assert summary.stacking_metrics.fwhm_degraded is True
