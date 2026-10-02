"""Purpose: Unit tests for capture processing.

Description: Verifies the three measurements: clipping per target and
exposure length (the science library's verdict wins, frame counts are the
fallback), star quality against the equipment's limits, and efficiency.
"""

from typing import Any

import pytest

from wayfindinglib.models.session.capture_frame import StackSaturationVerdict
from wayfindinglib.session_analysis.capture.processing.measure_capture_performance import (
    measure_capture_performance,
    measure_clipping,
    measure_efficiency,
    measure_star_quality,
)
from wayfindinglib.session_analysis.capture.test.conftest import SENSOR_PIXELS

_CLIPPED = 20 / SENSOR_PIXELS
"""A saturated-pixel fraction worth 20 saturated pixels, a clipped star."""

_HOT_PIXEL = 1 / SENSOR_PIXELS
"""A saturated-pixel fraction worth one saturated pixel: a hot pixel."""


def test_a_group_clips_when_half_its_frames_hold_a_saturated_star(make_frame: Any) -> None:
    """Verify the frame-count fallback follows the science library's rule."""
    frames = [
        make_frame(index, exposure_seconds=1.0, saturated_pixel_fraction=_CLIPPED) for index in range(3)
    ]
    frames += [
        make_frame(index + 3, exposure_seconds=1.0, saturated_pixel_fraction=0.0) for index in range(3)
    ]

    (group,) = measure_clipping(frames, [], SENSOR_PIXELS)

    assert group.frames == 6
    assert group.clipped_frames == 3
    assert group.is_clipped
    assert group.basis == "frame_pixel_count"


def test_a_lone_hot_pixel_is_not_a_clipped_star(make_frame: Any) -> None:
    """Verify fewer saturated pixels than the smallest star do not count."""
    frames = [make_frame(index, saturated_pixel_fraction=_HOT_PIXEL) for index in range(6)]

    (group,) = measure_clipping(frames, [], SENSOR_PIXELS)

    assert group.clipped_frames == 0
    assert not group.is_clipped


def test_the_science_librarys_verdict_overrides_the_frame_count(make_frame: Any) -> None:
    """Verify a stacked target's verdict is used, with its recommendation."""
    frames = [make_frame(index, saturated_pixel_fraction=0.0) for index in range(6)]
    verdict = StackSaturationVerdict(
        target_id="Target",
        is_spectral=False,
        exposure_seconds=30.0,
        saturated=True,
        recommended_exposure_seconds=12.5,
    )

    (group,) = measure_clipping(frames, [verdict], SENSOR_PIXELS)

    assert group.is_clipped
    assert group.clipped_frames == 0
    assert group.basis == "science_stack"
    assert group.science_recommended_exposure_seconds == pytest.approx(12.5)


def test_a_verdict_for_another_stack_kind_does_not_apply(make_frame: Any) -> None:
    """Verify an imaging verdict is not used for spectroscopy frames."""
    frames = [make_frame(index, is_spectral=True, saturated_pixel_fraction=0.0) for index in range(6)]
    verdict = StackSaturationVerdict(
        target_id="Target", is_spectral=False, exposure_seconds=30.0, saturated=True
    )

    (group,) = measure_clipping(frames, [verdict], SENSOR_PIXELS)

    assert not group.is_clipped
    assert group.basis == "frame_pixel_count"


def test_exposure_lengths_that_differ_by_rounding_form_one_group(make_frame: Any) -> None:
    """Verify exposure lengths that differ by rounding share a group."""
    frames = [
        make_frame(0, exposure_seconds=0.01),
        make_frame(1, exposure_seconds=0.010000000000000002),
    ]

    groups = measure_clipping(frames, [], SENSOR_PIXELS)

    assert len(groups) == 1
    assert groups[0].frames == 2


def test_binned_frames_have_fewer_pixels(make_frame: Any) -> None:
    """Verify a saturated count accounts for the binning of the frame."""
    fraction = 20 / (SENSOR_PIXELS / 4)
    frames = [make_frame(index, binning=2, saturated_pixel_fraction=fraction) for index in range(4)]

    (group,) = measure_clipping(frames, [], SENSOR_PIXELS)

    assert group.clipped_frames == 4


def test_without_a_sensor_size_only_the_science_verdict_remains(make_frame: Any) -> None:
    """Verify frame counts are skipped when they cannot be made into counts."""
    frames = [make_frame(index, saturated_pixel_fraction=_CLIPPED) for index in range(4)]

    assert measure_clipping(frames, [], None) == []


def test_groups_are_kept_per_target_and_exposure(make_frame: Any) -> None:
    """Verify each target and exposure length is its own group."""
    frames = [
        make_frame(0, target_id="A", exposure_seconds=1.0),
        make_frame(1, target_id="A", exposure_seconds=2.0),
        make_frame(2, target_id="B", exposure_seconds=1.0),
    ]

    groups = measure_clipping(frames, [], SENSOR_PIXELS)

    assert [(g.target_id, g.exposure_seconds) for g in groups] == [("A", 1.0), ("A", 2.0), ("B", 1.0)]


def test_star_quality_uses_long_imaging_frames_only(make_frame: Any, make_envelope: Any) -> None:
    """Verify spectral, short and unmeasured frames do not set the width."""
    good = [make_frame(index, star_width_arcsec=5.0, roundness=0.9) for index in range(12)]
    spectral = [make_frame(20 + index, is_spectral=True, star_width_arcsec=30.0) for index in range(12)]
    short = [make_frame(40 + index, exposure_seconds=1.0, star_width_arcsec=30.0) for index in range(12)]
    unmeasured = [make_frame(60 + index, star_width_arcsec=None) for index in range(12)]

    quality = measure_star_quality(good + spectral + short + unmeasured, make_envelope(), "exact")

    assert quality.frames == 12
    assert quality.median_star_width_arcsec == pytest.approx(5.0)
    assert quality.median_roundness == pytest.approx(0.9)
    assert quality.minimum_exposure_seconds == pytest.approx(9.6)


def test_star_quality_needs_enough_frames(make_frame: Any, make_envelope: Any) -> None:
    """Verify nine frames are too few for a median."""
    quality = measure_star_quality([make_frame(index) for index in range(9)], make_envelope(), "exact")

    assert quality.frames == 9
    assert quality.median_star_width_arcsec is None


def test_star_quality_needs_the_guide_cycle_of_the_equipment(make_frame: Any, make_envelope: Any) -> None:
    """Verify equipment that has never guided has no frames that qualify."""
    frames = [make_frame(index) for index in range(12)]

    quality = measure_star_quality(frames, make_envelope(cadence=None), "exact")

    assert quality.minimum_exposure_seconds is None
    assert quality.frames == 0
    assert quality.median_star_width_arcsec is None


def test_star_quality_carries_the_earlier_nights_limits(make_frame: Any, make_envelope: Any) -> None:
    """Verify the limits the night is judged against travel with the result."""
    frames = [make_frame(index) for index in range(12)]

    quality = measure_star_quality(frames, make_envelope(), "exact")

    assert quality.star_width_limit_arcsec is not None
    assert quality.roundness_limit is not None
    assert quality.star_width_limit_arcsec > 5.4
    assert quality.roundness_limit < 0.86


def test_no_limits_apply_to_other_equipment(make_frame: Any, make_envelope: Any) -> None:
    """Verify a night of other equipment is measured but not judged."""
    frames = [make_frame(index) for index in range(12)]

    quality = measure_star_quality(frames, make_envelope(), "none")

    assert quality.minimum_exposure_seconds is None
    assert quality.star_width_limit_arcsec is None


def test_efficiency_is_exposure_time_over_the_span(make_frame: Any) -> None:
    """Verify the duty cycle is light seconds divided by the span."""
    frames = [make_frame(index) for index in range(3)]  # 30 s every 60 s.

    efficiency = measure_efficiency(frames)

    assert efficiency.light_exposure_seconds == pytest.approx(90.0)
    assert efficiency.span_seconds == pytest.approx(150.0)
    assert efficiency.duty_cycle == pytest.approx(0.6)


def test_efficiency_of_one_frame_has_no_duty_cycle(make_frame: Any) -> None:
    """Verify a single frame gives no ratio."""
    assert measure_efficiency([make_frame(0)]).duty_cycle is None


def test_efficiency_of_no_frames_is_empty() -> None:
    """Verify an empty night reports zeros."""
    efficiency = measure_efficiency([])

    assert efficiency.light_exposure_seconds == pytest.approx(0.0)
    assert efficiency.duty_cycle is None


def test_the_three_measurements_come_together(make_frame: Any, make_envelope: Any) -> None:
    """Verify the result holds clipping, star quality and efficiency."""
    frames = [make_frame(index) for index in range(12)]

    performance = measure_capture_performance(frames, [], SENSOR_PIXELS, make_envelope(), "exact")

    assert len(performance.clipping) == 1
    assert performance.star_quality.frames == 12
    assert performance.efficiency.duty_cycle is not None
