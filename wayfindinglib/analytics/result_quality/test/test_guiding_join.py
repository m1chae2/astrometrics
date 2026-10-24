"""Purpose: Tests for the pure guiding join.

Description: Checks that samples are cut to each frame's window, that the
statistics are right, that a frame with no samples gets no numbers, and that
the read range adds the edge margin.
"""

import pytest

from wayfindinglib.analytics.result_quality.guiding_join import (
    FrameWindow,
    guiding_read_range,
    join_frames_to_guiding,
    window_statistics,
)


def _samples() -> list[dict[str, float]]:
    """Make guide samples every 2 seconds from 0 to 98.

    Returns
    -------
    samples : `list` [`dict`]
        Rows with timestamp, dra and ddec.
    """
    return [{"timestamp": float(t), "dra": 3.0, "ddec": 4.0} for t in range(0, 100, 2)]


def test_window_statistics_gives_rms_and_peak() -> None:
    """A constant (3, 4) error has total 5 everywhere."""
    stats = window_statistics(_samples()[:10], 20.0)
    assert stats["guide_samples"] == 10
    assert stats["rms_ra_arcsec"] == pytest.approx(3.0)
    assert stats["rms_dec_arcsec"] == pytest.approx(4.0)
    assert stats["rms_total_arcsec"] == pytest.approx(5.0)
    assert stats["peak_total_arcsec"] == pytest.approx(5.0)
    assert stats["coverage_fraction"] == pytest.approx(1.0)


def test_no_samples_gives_only_a_count() -> None:
    """A window with no samples reports 0 and no error values."""
    assert window_statistics([], 30.0) == {"guide_samples": 0}


def test_join_cuts_the_samples_to_each_window() -> None:
    """The window is start <= t < end.

    An uncovered frame has no numbers.
    """
    windows = [
        FrameWindow(frame_id="a", start_epoch_seconds=10.0, exposure_seconds=10.0),
        FrameWindow(frame_id="b", start_epoch_seconds=500.0, exposure_seconds=10.0),
    ]
    rows = join_frames_to_guiding(windows, list(reversed(_samples())))
    assert rows[0]["guide_samples"] == 5
    assert rows[1] == {"guide_samples": 0}


def test_read_range_adds_the_margin() -> None:
    """The range adds 5 seconds before the start and after the end."""
    windows = [
        FrameWindow(frame_id="a", start_epoch_seconds=100.0, exposure_seconds=10.0),
        FrameWindow(frame_id="b", start_epoch_seconds=200.0, exposure_seconds=30.0),
    ]
    assert guiding_read_range(windows) == pytest.approx((95.0, 235.0))
    assert guiding_read_range([]) is None
