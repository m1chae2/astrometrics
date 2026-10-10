"""Tests for spotting a steady drift across recent frames."""

import numpy as np

from astrometricslib.pipelines.shared.quality.frame_trends import describe_trend, find_trends


def test_a_steady_rise_is_flagged() -> None:
    """A 30 percent ramp with some scatter is a trend."""
    generator = np.random.default_rng(1)
    values = [3.0 * (1 + 0.3 * index / 19) + generator.normal(0, 0.03) for index in range(20)]
    trend = describe_trend(values, threshold_percent=15.0)
    assert trend["flagged"]
    assert 25 < trend["change_percent"] < 35


def test_scatter_with_no_drift_is_not_flagged() -> None:
    """Sixty frames of noise around one level say nothing."""
    generator = np.random.default_rng(2)
    values = [3.0 + generator.normal(0, 0.15) for _ in range(60)]
    assert not describe_trend(values, threshold_percent=10.0)["flagged"]


def test_one_bad_frame_does_not_make_a_trend() -> None:
    """A single outlier does not move the median slope enough."""
    values = [3.0] * 19 + [9.0]
    assert not describe_trend(values, threshold_percent=15.0)["flagged"]


def test_a_change_below_the_threshold_is_not_flagged() -> None:
    """A steady but small change stays quiet at a higher threshold."""
    values = [100.0 + index * 0.1 for index in range(20)]
    assert not describe_trend(values, threshold_percent=15.0)["flagged"]
    assert describe_trend(values, threshold_percent=1.0)["flagged"]


def test_too_few_frames_give_no_verdict() -> None:
    """Three frames are not enough."""
    assert describe_trend([1.0, 2.0, 3.0], 1.0) == {"frames": 3, "flagged": False}


def test_only_the_worrying_direction_raises_an_alert() -> None:
    """A falling star width is no alert; a rising one is."""
    rows = [{"fwhm_px": 3.0 + 0.1 * index, "star_count": 3000 - 100 * index} for index in range(12)]
    rows_falling = [{"fwhm_px": 4.0 - 0.1 * index} for index in range(12)]
    worries = {"fwhm_px": "rising", "star_count": "falling"}
    trends = find_trends(rows, worries, window=12, threshold_percent=10)
    assert len(trends["alerts"]) == 2
    quiet = find_trends(rows_falling, {"fwhm_px": "rising"}, window=12, threshold_percent=10)
    assert quiet["alerts"] == []
    assert quiet["metrics"]["fwhm_px"]["direction"] == "falling"
