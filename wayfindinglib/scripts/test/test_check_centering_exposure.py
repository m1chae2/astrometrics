"""Tests for the centering exposure check script, on made-up numbers.

No library, catalog or real frame is used. The summary, the area factor, the
frame picker and the star counter are checked on synthetic values and a
synthetic image.
"""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from wayfindinglib.scripts import check_centering_exposure as script


def _row(predicted: float, detected: int) -> script.FrameComparison:
    """Make one comparison.

    Returns
    -------
    row : `script.FrameComparison`
        A frame with the given predicted and detected counts.
    """
    return script.FrameComparison("f.fits", 5.0, 15.0, 0, predicted, detected)


def test_a_square_frame_is_four_over_pi_the_circle() -> None:
    """The area ratio of a square to its inscribed circle is 4 / pi."""
    assert script.circle_to_frame_factor(3000, 3000) == pytest.approx(4.0 / math.pi)


def test_the_summary_reports_ratio_and_error() -> None:
    """Detected over predicted of 1, 0.5 and 2 gives a median of 1."""
    summary = script.summarize([_row(20, 20), _row(20, 10), _row(20, 40)])
    assert summary["median_ratio"] == pytest.approx(1.0)
    assert summary["worst_ratio"] == pytest.approx(0.5)
    assert summary["median_absolute_log2_error"] == pytest.approx(1.0)
    assert summary["fraction_within_factor_2"] == pytest.approx(1.0)


def test_frames_with_no_prediction_are_left_out_of_the_ratios() -> None:
    """A predicted count of 0 is counted as a frame but gives no ratio."""
    summary = script.summarize([_row(0, 3)])
    assert summary["frames"] == 1
    assert summary["median_ratio"] is None


def test_spread_picks_across_the_exposures() -> None:
    """Five frames from ten are spread from shortest to longest."""
    frames = [SimpleNamespace(exposure=str(seconds)) for seconds in range(10, 0, -1)]
    picked = script._spread(frames, 5)
    exposures = [float(frame.exposure) for frame in picked]
    assert exposures[0] == pytest.approx(1.0)
    assert exposures[-1] == pytest.approx(10.0)
    assert len(picked) == 5


def test_the_star_counter_finds_injected_stars() -> None:
    """Twelve bright Gaussian stars on noise are all found."""
    generator = np.random.default_rng(1)
    pixels = generator.normal(100.0, 5.0, (400, 400))
    rows, columns = np.mgrid[0:400, 0:400]
    sigma = 4.0 / 2.3548
    for index in range(12):
        x, y = 30 + 30 * index, 40 + 28 * index
        pixels += 2000.0 * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2.0 * sigma**2))
    assert script.count_detected_stars(pixels) == pytest.approx(12, abs=2)
