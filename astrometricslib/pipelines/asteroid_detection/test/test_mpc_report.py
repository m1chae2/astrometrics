"""Tests for the MPC 80-column observation writer.

The line is checked against one that was laid out by hand, column by column,
from the Minor Planet Center's format description. The other tests check the
rounding at the edges: a time that rounds up to the next day, a right
ascension that rounds up to 24 hours, and a blank magnitude.
"""

from datetime import UTC, datetime

import pytest

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.pipelines.asteroid_detection.mpc_report import (
    format_mpc_date,
    format_mpc_declination,
    format_mpc_observation,
    format_mpc_right_ascension,
    mid_exposure_unix,
    mpc_lines_for_candidate,
)

# Laid out by hand. Columns 1-5 blank; 6-12 "TEST001"; 13-14 blank; 15 "C";
# 16-31 the date; 32 blank; 33-43 RA; 44 blank; 45-55 Dec; 56-65 blank;
# 66-69 magnitude; 70 blank; 71 band; 72-77 blank; 78-80 observatory code.
HAND_BUILT_LINE = "     TEST001  C2026 10 09.89653 10 00 00.00 -12 30 00.0          18.3 V      500"

# The exposure began at 21:30:00 UTC and lasted 120 s, so it was centred on
# 21:31:00 UTC, which is 77460 s into the day, or 0.89653 of a day.
EXPOSURE_START_UNIX = datetime(2026, 10, 9, 21, 30, 0, tzinfo=UTC).timestamp()


def test_the_writer_matches_a_hand_built_line() -> None:
    """Time, RA, Dec, magnitude, band and code land in their columns."""
    middle = mid_exposure_unix(EXPOSURE_START_UNIX, 120.0)

    line = format_mpc_observation("TEST001", middle, 150.0, -12.5, "500", magnitude=18.3, band="V")

    assert line == HAND_BUILT_LINE
    assert len(line) == 80
    assert line[14] == "C"
    assert line[15:31] == "2026 10 09.89653"
    assert line[32:43] == "10 00 00.00"
    assert line[44:55] == "-12 30 00.0"
    assert line[65:69] == "18.3"
    assert line[70] == "V"
    assert line[77:80] == "500"


def test_without_a_magnitude_the_magnitude_and_band_columns_are_blank() -> None:
    """With no calibrated magnitude, columns 66-71 are blank."""
    line = format_mpc_observation("TEST001", EXPOSURE_START_UNIX, 150.0, -12.5, "500", band="V")

    assert len(line) == 80
    assert line[65:71] == " " * 6


def test_mid_exposure_adds_half_the_exposure_and_falls_back_to_the_start() -> None:
    """Half of EXPTIME is added; with no EXPTIME the start time is returned."""
    assert mid_exposure_unix(EXPOSURE_START_UNIX, 120.0) == pytest.approx(EXPOSURE_START_UNIX + 60.0)
    assert mid_exposure_unix(EXPOSURE_START_UNIX, None) == pytest.approx(EXPOSURE_START_UNIX)


def test_a_time_that_rounds_to_the_end_of_a_day_moves_to_the_next_day() -> None:
    """One millisecond before midnight reads 10 10.00000, not 09.100000."""
    just_before_midnight = datetime(2026, 10, 9, 23, 59, 59, 999000, tzinfo=UTC).timestamp()

    assert format_mpc_date(just_before_midnight) == "2026 10 10.00000"


def test_a_right_ascension_that_rounds_up_to_24_hours_wraps_to_zero() -> None:
    """359.9999999 degrees is 00 00 00.00, not 24 00 00.00."""
    assert format_mpc_right_ascension(359.9999999) == "00 00 00.00"
    assert format_mpc_right_ascension(0.0) == "00 00 00.00"
    assert format_mpc_right_ascension(-0.25) == "23 59 00.00"


def test_declination_signs_and_limits() -> None:
    """The sign is always written; out-of-range values are refused."""
    assert format_mpc_declination(0.0) == "+00 00 00.0"
    assert format_mpc_declination(-0.00001) == "+00 00 00.0"
    assert format_mpc_declination(-0.5) == "-00 30 00.0"
    assert format_mpc_declination(89.99998) == "+89 59 59.9"
    with pytest.raises(ValueError, match="between -90 and"):
        format_mpc_declination(90.5)


def test_bad_designation_code_or_band_is_refused() -> None:
    """Fields that would break the fixed columns raise an error."""
    with pytest.raises(ValueError, match="Designation"):
        format_mpc_observation("TOOLONG01", EXPOSURE_START_UNIX, 1.0, 1.0, "500")
    with pytest.raises(ValueError, match="Observatory code"):
        format_mpc_observation("TEST001", EXPOSURE_START_UNIX, 1.0, 1.0, "50")
    with pytest.raises(ValueError, match="Band"):
        format_mpc_observation("TEST001", EXPOSURE_START_UNIX, 1.0, 1.0, "500", magnitude=18.0, band="VV")


def test_candidate_lines_are_in_time_order_and_count_frames_without_exposure_time() -> None:
    """Each detection becomes a line; frames with no EXPTIME are counted."""
    detections = [
        FrameDetection(
            frame_path=f"frame{index}.fits",
            timestamp=EXPOSURE_START_UNIX + 600.0 * index,
            pixel_x=10.0,
            pixel_y=10.0,
            right_ascension_deg=150.0,
            declination_deg=-12.5,
            exposure_seconds=exposure,
        )
        for index, exposure in ((2, None), (0, 120.0), (1, 120.0))
    ]
    candidate = AsteroidDetectionCandidate(
        id="c1",
        target_id="T",
        frame_detections=detections,
        cascade_stage=CascadeStage.RATE_LINEARITY_CONFIRMED,
    )

    lines, start_time_lines = mpc_lines_for_candidate(candidate, "TEST001", "500")

    assert len(lines) == 3
    assert [line[15:31] for line in lines] == [
        "2026 10 09.89653",  # 21:31:00, the middle of frame 0
        "2026 10 09.90347",  # 21:41:00, the middle of frame 1
        "2026 10 09.90972",  # frame 2 has no EXPTIME, so its start, 21:50:00
    ]
    assert start_time_lines == 1
