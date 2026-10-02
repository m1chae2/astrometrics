"""Purpose: Unit tests for capture pre-processing.

Description: Verifies how a night's exposures are matched to its frames, and
that each data-gathering check (missing frames, cancelled exposures, missing
measurements, sensor temperature) reports what the data shows and nothing
more.
"""

from typing import Any

import pytest

from wayfindinglib.session_analysis.capture.pre_processing.assess_capture_input_quality import (
    MATCH_WINDOW_SECONDS,
    assess_capture_input_quality,
    match_captures_to_frames,
)


def test_each_exposure_matches_the_frame_that_began_when_it_did(make_frame: Any, make_capture: Any) -> None:
    """Verify exposures and frames pair up despite the readout delay."""
    frames = [make_frame(index) for index in range(5)]
    captures = [make_capture(index) for index in range(5)]

    match = match_captures_to_frames(captures, frames)

    assert match.pairs == 5
    assert match.unmatched_captures == []
    assert match.unmatched_frames == []


def test_an_exposure_with_no_frame_is_reported_unmatched(make_frame: Any, make_capture: Any) -> None:
    """Verify a capture whose frame is not in the library is left over."""
    frames = [make_frame(index) for index in (0, 1, 3)]
    captures = [make_capture(index) for index in range(4)]

    match = match_captures_to_frames(captures, frames)

    assert match.pairs == 3
    assert len(match.unmatched_captures) == 1
    assert match.unmatched_captures[0].completed_at == make_capture(2).completed_at


def test_a_frame_with_no_exposure_is_reported_unmatched(make_frame: Any, make_capture: Any) -> None:
    """Verify a frame Ekos has no record of is left over."""
    frames = [make_frame(index) for index in range(3)]
    captures = [make_capture(index) for index in (0, 1)]

    match = match_captures_to_frames(captures, frames)

    assert len(match.unmatched_frames) == 1
    assert match.unmatched_frames[0].path.endswith("002.fits")


def test_a_frame_is_used_for_one_exposure_only(make_frame: Any, make_capture: Any) -> None:
    """Verify two exposures close together do not both claim one frame."""
    frames = [make_frame(0)]
    captures = [make_capture(0), make_capture(0, completed_at=make_capture(0).completed_at + 5.0)]

    match = match_captures_to_frames(captures, frames)

    assert match.pairs == 1
    assert len(match.unmatched_captures) == 1


def test_frames_beyond_the_window_do_not_match(make_frame: Any, make_capture: Any) -> None:
    """Verify an exposure and a frame far apart in time do not match."""
    frames = [make_frame(0, timestamp=make_frame(0).timestamp + MATCH_WINDOW_SECONDS + 5.0)]

    match = match_captures_to_frames([make_capture(0)], frames)

    assert match.pairs == 0


def test_a_night_with_every_exposure_in_the_library_has_no_gap(
    make_frame: Any, make_capture: Any, make_envelope: Any
) -> None:
    """Verify a complete night reports no missing frames."""
    frames = [make_frame(index) for index in range(12)]
    captures = [make_capture(index) for index in range(12)]

    quality = assess_capture_input_quality(frames, captures, 0, True, make_envelope(), "exact")

    assert quality.captures_without_frame == 0
    assert quality.frames_without_capture == 0
    assert quality.has_enough_frames


def test_only_light_kind_exposures_count_as_missing(
    make_frame: Any, make_capture: Any, make_envelope: Any
) -> None:
    """Verify darks and unclassified exposures are not counted as missing."""
    frames = [make_frame(index) for index in range(12)]
    captures = [make_capture(index) for index in range(12)]
    captures.append(make_capture(20))  # A light with no frame.
    captures.append(make_capture(21, file_path="/home/stellarmate/Pictures/Dark/Dark_001.fits"))
    captures.append(make_capture(22, file_path=""))  # No logged path: kind unknown.

    quality = assess_capture_input_quality(frames, captures, 0, True, make_envelope(), "exact")

    assert quality.ekos_calibration_captures == 1
    assert quality.captures_without_frame == 1
    assert quality.captures_without_frame_of_unknown_kind == 1


def test_no_ekos_record_skips_the_comparison_with_ekos(make_frame: Any, make_envelope: Any) -> None:
    """Verify checks that need Ekos's list report nothing without it."""
    frames = [make_frame(index) for index in range(12)]

    quality = assess_capture_input_quality(frames, [], 0, False, make_envelope(), "exact")

    assert not quality.has_ekos_record
    assert quality.captures_without_frame is None
    assert quality.abort_fraction is None


def test_frequent_aborts_are_judged_against_the_equipments_own_nights(
    make_frame: Any, make_capture: Any, make_envelope: Any
) -> None:
    """Verify the cancelled share is compared with the earlier-night limit."""
    frames = [make_frame(index) for index in range(12)]
    captures = [make_capture(index) for index in range(12)]
    envelope = make_envelope()
    limit = envelope.value("capture_abort_fraction_high_limit")

    few = assess_capture_input_quality(frames, captures, 0, True, envelope, "exact")
    many = assess_capture_input_quality(frames, captures, 8, True, envelope, "exact")

    assert limit is not None
    assert few.has_frequent_aborts is False
    assert many.abort_fraction == pytest.approx(8 / 20)
    assert many.has_frequent_aborts is True


def test_aborts_are_not_judged_without_a_baseline(
    make_frame: Any, make_capture: Any, make_envelope: Any
) -> None:
    """Verify a new piece of equipment is neither passed nor failed."""
    frames = [make_frame(index) for index in range(12)]
    captures = [make_capture(index) for index in range(12)]

    quality = assess_capture_input_quality(frames, captures, 8, True, make_envelope(baseline=None), "exact")

    assert quality.abort_fraction_limit is None
    assert quality.has_frequent_aborts is None


def test_missing_measurements_are_counted_per_measurement(make_frame: Any, make_envelope: Any) -> None:
    """Verify each lacking measurement is named with its count."""
    frames = [make_frame(index) for index in range(12)]
    frames[0] = make_frame(0, saturated_pixel_fraction=None)
    frames[1] = make_frame(1, saturated_pixel_fraction=None, pixel_scale_arcsec=None)

    quality = assess_capture_input_quality(frames, [], 0, False, make_envelope(), "exact")

    assert quality.has_missing_measurements
    assert quality.frames_missing_measurements == {"saturated_pixel_fraction": 2, "pixel_scale_arcsec": 1}


def test_star_width_is_never_counted_as_a_missing_measurement(make_frame: Any, make_envelope: Any) -> None:
    """Verify frames the science library did not register are not faulted."""
    frames = [make_frame(index, star_width_arcsec=None, roundness=None) for index in range(12)]

    quality = assess_capture_input_quality(frames, [], 0, False, make_envelope(), "exact")

    assert not quality.has_missing_measurements


def test_sensor_temperature_spread_counts_frames_outside_the_dark_tolerance(
    make_frame: Any, make_envelope: Any
) -> None:
    """Verify frames too far from the median temperature are counted."""
    frames = [make_frame(index, sensor_temperature_c=-10.0) for index in range(10)]
    frames.append(make_frame(10, sensor_temperature_c=0.0))
    frames.append(make_frame(11, sensor_temperature_c=-5.0))

    quality = assess_capture_input_quality(frames, [], 0, False, make_envelope(), "exact")

    assert quality.sensor_temperature_spread_c == pytest.approx(10.0)
    assert quality.dark_temperature_tolerance_c == pytest.approx(3.0)
    assert quality.frames_outside_dark_tolerance == 2


def test_too_few_frames_is_not_enough_to_analyse(make_frame: Any, make_envelope: Any) -> None:
    """Verify a night of a few frames is reported as not analysable."""
    quality = assess_capture_input_quality(
        [make_frame(index) for index in range(3)], [], 0, False, make_envelope(), "exact"
    )

    assert not quality.has_enough_frames


def test_spectral_and_imaging_frames_are_counted_separately(make_frame: Any, make_envelope: Any) -> None:
    """Verify the frame totals split by purpose."""
    frames = [make_frame(index, is_spectral=index < 4) for index in range(12)]

    quality = assess_capture_input_quality(frames, [], 0, False, make_envelope(), "exact")

    assert (quality.light_frames, quality.spectral_frames, quality.imaging_frames) == (12, 4, 8)
