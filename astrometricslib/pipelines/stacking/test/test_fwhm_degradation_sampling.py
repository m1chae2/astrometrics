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


@pytest.fixture(autouse=True)
def measure_in_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measure serially so in-process fakes can record their calls."""
    monkeypatch.setattr(stage, "_fwhm_worker_count", lambda sample_size: 1)


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


def test_a_width_measured_by_the_combine_is_used_instead_of_measuring_the_file() -> None:
    """A stack combined from exposure groups brings its own width."""
    frames = [SimpleNamespace(path=str(index)) for index in range(4)]
    measured: list[str] = []

    def fake_measure(path: str) -> float:
        """Record the path and give a fixed width.

        Returns
        -------
        width : `float`
            A width of 2 pixels, as for a frame.
        """
        measured.append(path)
        return 2.0

    summary = make_summary()
    with patch(MEASURE, side_effect=fake_measure):
        stage._measure_fwhm_degradation(summary, "stack.fits", frames, stacked_fwhm=2.3)

    assert "stack.fits" not in measured
    assert summary.stacking_metrics.stacked_fwhm_px == pytest.approx(2.3)
    assert not summary.stacking_metrics.fwhm_degraded


def test_a_width_far_above_the_inputs_still_flags_the_stack() -> None:
    """A width given by the combine is checked the same way."""
    frames = [SimpleNamespace(path=str(index)) for index in range(4)]
    summary = make_summary()
    with patch(MEASURE, return_value=2.0):
        stage._measure_fwhm_degradation(summary, "stack.fits", frames, stacked_fwhm=3.4)

    assert summary.stacking_metrics.fwhm_degraded


def fake_width_by_name(path: str) -> float | None:
    """Give a width from the file name, for use in worker processes.

    Parameters
    ----------
    path : `str`
        A frame path; "bad" raises, "none" has no width.

    Returns
    -------
    width : `float` or `None`
        The number in the name, or `None` for "none".

    Raises
    ------
    ValueError
        For the path "bad".
    """
    if path == "bad":
        raise ValueError("unreadable")
    return None if path == "none" else float(path)


def test_parallel_measurement_keeps_order_and_skips_failures() -> None:
    """Worker processes give the same ordered widths as a serial loop."""
    paths = ["1.5", "bad", "3.5", "none", "2.5", "4.5"]
    with patch(MEASURE, side_effect=fake_width_by_name):
        serial = stage._measure_sample_fwhms(paths, 1)
        parallel = stage._measure_sample_fwhms(paths, 3)
    assert serial == [1.5, None, 3.5, None, 2.5, 4.5]
    assert parallel == serial


def test_parallel_widths_give_the_same_summary_as_serial() -> None:
    """The median and expected width do not depend on the worker count."""
    frames = [SimpleNamespace(path=str(1 + index / 10)) for index in range(12)]
    results = []
    for count in (1, 4):
        summary = make_summary()
        with (
            patch(MEASURE, side_effect=fake_width_by_name),
            patch.object(stage, "_fwhm_worker_count", return_value=count),
        ):
            stage._measure_fwhm_degradation(summary, "stack.fits", frames, stacked_fwhm=1.5)
        metrics = summary.stacking_metrics
        results.append((metrics.median_input_fwhm_px, metrics.expected_stack_fwhm_px))
    assert results[0] == results[1]


def test_a_sample_of_one_frame_needs_no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sample of one frame is measured in this process."""
    monkeypatch.undo()
    assert stage._fwhm_worker_count(1) == 1
