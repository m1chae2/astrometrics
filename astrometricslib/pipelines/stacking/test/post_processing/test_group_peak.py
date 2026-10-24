"""Tests for the brightest star's peak against the ceiling of a group.

The group's number is built from `FrameSaturation` records, so the tests give
those records directly: a measured peak, an estimated one, a frame with no
peak, and a group with no frames.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import (
    group_peak_fields,
    summarize_group_peak,
)
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    FrameSaturation,
    checked_estimate_range,
)


def _frame(
    peak: float | None, sky: float = 500.0, ceiling: float = 65000.0, estimated: bool = False
) -> FrameSaturation:
    """Build one frame's saturation record.

    Parameters
    ----------
    peak : `float` or `None`
        The peak above the sky.
    sky : `float`, optional
        The sky level.
    ceiling : `float`, optional
        The ceiling.
    estimated : `bool`, optional
        Whether the peak is estimated from the wings of a clipped star.

    Returns
    -------
    frame : `FrameSaturation`
        The record.
    """
    return FrameSaturation(estimated, ceiling, sky, peak, estimated)


def test_the_fraction_is_the_peak_over_the_room_to_the_ceiling() -> None:
    """A peak of 32,250 ADU over a sky of 500 under 65,000 is half."""
    peak = summarize_group_peak([_frame(32250.0)] * 3)
    assert peak.brightest_star_peak_fraction == pytest.approx(0.5)
    assert peak.brightest_star_peak_above_sky_adu == pytest.approx(32250.0)
    assert peak.peak_sky_adu == pytest.approx(500.0)
    assert peak.peak_clip_ceiling_adu == pytest.approx(65000.0)
    assert (peak.peak_frames_with_peak, peak.peak_frames_estimated, peak.peak_frames_sampled) == (3, 0, 3)
    assert peak.peak_frames_on_profile_ceiling == 3
    assert peak.peak_largest_estimate_to_room_ratio is None


def test_the_group_value_is_the_median_of_the_frames() -> None:
    """One bad frame (a cosmic ray) does not move the median."""
    peak = summarize_group_peak([_frame(10000.0), _frame(10200.0), _frame(60000.0)])
    assert peak.brightest_star_peak_fraction == pytest.approx(10200.0 / 64500.0)


def test_estimated_peaks_are_counted_and_may_exceed_one() -> None:
    """A clipped star's estimated peak can lie above the ceiling."""
    peak = summarize_group_peak([_frame(90000.0, estimated=True)] * 2 + [_frame(30000.0)])
    assert peak.peak_frames_estimated == 2
    assert peak.peak_frames_clipped == 2
    assert peak.brightest_star_peak_fraction > 1.0
    assert peak.peak_largest_estimate_to_room_ratio == pytest.approx(90000.0 / 64500.0)
    assert peak.peak_frames_on_profile_ceiling == 1


def test_frames_without_a_peak_are_left_out_of_the_median() -> None:
    """A frame with no peak is sampled but gives no number."""
    peak = summarize_group_peak([_frame(None), _frame(20000.0)])
    assert peak.peak_frames_with_peak == 1
    assert peak.peak_frames_sampled == 2
    assert peak.brightest_star_peak_fraction == pytest.approx(20000.0 / 64500.0)


def test_a_group_with_no_usable_frame_has_no_numbers() -> None:
    """No frames, or only frames without a peak, give `None` and never zero."""
    for frames in ([], [_frame(None), _frame(0.0)]):
        peak = summarize_group_peak(frames, 60000.0)
        assert peak.brightest_star_peak_fraction is None
        assert peak.brightest_star_peak_above_sky_adu is None
        assert peak.peak_frames_with_peak == 0
        assert peak.peak_linearity_limit_adu == pytest.approx(60000.0)


def test_a_clipped_frame_with_no_believed_peak_is_still_counted_as_clipped() -> None:
    """A refused estimate gives no peak but still counts as clipped."""
    refused = FrameSaturation(True, 65000.0, 500.0, None, True)
    peak = summarize_group_peak([refused, _frame(20000.0)])
    assert peak.peak_frames_clipped == 1
    assert peak.peak_frames_with_peak == 1
    assert peak.peak_frames_estimated == 0


def test_the_fields_carry_the_camera_profiles_limit_and_where_it_came_from() -> None:
    """The ASI533MM Pro profile states a photometric linearity limit."""
    frames = [SimpleNamespace(camera="ZWO ASI 533MM Pro")]
    fields = group_peak_fields(frames, [_frame(20000.0)])
    assert fields["peak_linearity_limit_adu"] == pytest.approx(60000.0, rel=0.1)
    assert fields["brightest_star_peak_fraction"] == pytest.approx(20000.0 / 64500.0)
    assert fields["peak_clip_ceiling_provenance"] in {"measured", "datasheet", "assumed"}
    assert fields["peak_linearity_limit_provenance"] in {"measured", "datasheet", "assumed"}


def test_a_profile_error_gives_no_limit_and_does_not_stop_the_stack(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unreadable camera profile leaves the limit empty and is logged."""

    def broken(*_arguments: object, **_keywords: object) -> None:
        """Fail like a profile file that cannot be parsed.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("bad profile")

    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.exposure_group_report.resolve_camera_profile",
        broken,
    )
    fields = group_peak_fields([SimpleNamespace(camera="Any")], [_frame(20000.0)])
    assert fields["peak_linearity_limit_adu"] is None
    assert fields["peak_linearity_limit_provenance"] is None
    assert fields["peak_clip_ceiling_provenance"] is None
    assert fields["brightest_star_peak_fraction"] == pytest.approx(20000.0 / 64500.0)
    assert "could not be read" in caplog.text


def test_only_the_camera_with_checked_evidence_has_a_range() -> None:
    """Only the ASI533MM Pro has a range; the D5300 and others have none."""
    asi = group_peak_fields([SimpleNamespace(camera="ZWO ASI 533MM Pro")], [_frame(20000.0)])
    nikon = group_peak_fields([SimpleNamespace(camera="Nikon DSLR DSC D5300")], [_frame(20000.0)])
    unlisted = group_peak_fields([SimpleNamespace(camera="Mystery camera")], [_frame(20000.0)])
    assert asi["peak_checked_range_room_multiples"] == pytest.approx(80.0)
    assert nikon["peak_checked_range_room_multiples"] == pytest.approx(0.0)
    assert unlisted["peak_checked_range_room_multiples"] == pytest.approx(0.0)
    assert checked_estimate_range(None) == pytest.approx(0.0)
