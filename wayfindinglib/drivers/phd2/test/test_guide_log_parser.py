"""Purpose: Unit tests for PHD2-format guide log parsing.

Description: Verifies that guide logs written by PHD2 and by the Ekos
internal guider are parsed into samples with arcsecond errors, signed
guide pulses, star mass and SNR, plus the per-section equipment context.

The sample text below is cut from real Ekos guide logs (KStars 3.8.0 and
3.8.3, ASI120MC-S on a 121.05 mm guide scope). Earlier versions of these
tests used an invented column layout, which let two real bugs through: the
parser treated pixel distances as arcseconds, and it lost the sign of every
pulse because real logs write directions as single letters.
"""

from pathlib import Path

import pytest

from wayfindinglib.drivers.phd2.guide_log_parser import parse_guide_log_file, parse_phd2_guide_log

_EKOS_LOG = """\
KStars version 3.8.0. PHD2 log version 2.5. Log enabled at 2026-01-14 18:32:48

Calibration Begins at 2026-01-14 18:32:48
Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm
@@POINTING_CALIBRATION@@
Direction,Step,dx,dy,x,y,Dist
West,1,0.000,0.000,439.207,347.898,0.000
West calibration complete. Angle = 8.8 deg
Calibration guide speeds: RA = 5.1 a-s/s, Dec = 7.5 a-s/s
Calibration complete

Guiding Begins at 2026-01-14 18:34:21
Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm
@@POINTING_GUIDING@@
Mount = mount, xAngle = 8.8, xRate = 5.061, yAngle = 87.3, yRate = 7.530
Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode
1,2.975,"Mount",0.000,0.000,0.035,0.069,-0.035,0.068,44,W,58,N,,,242421,348.15,0
2,5.805,"Mount",-0.909,-0.362,0.861,0.301,-0.860,0.301,1086,W,255,N,,,250554,353.94,0
4,12.827,"Mount",1.128,0.454,-1.135,-0.468,1.134,-0.468,1432,E,397,S,,,210142,324.15,0
Guiding Ends at 2026-01-14 21:54:04
"""

_EKOS_LOG = _EKOS_LOG.replace(
    "@@POINTING_CALIBRATION@@",
    "RA = 9.97 hr, Dec = 69.6 deg, Hour angle = N/A hr, Pier side = West, "
    "Rotator pos = N/A, Alt = 32.5 deg, Az = 20.4 deg",
).replace(
    "@@POINTING_GUIDING@@",
    "RA = 9.97 hr, Dec = 69.5 deg, Hour angle = N/A hr, Pier side = West, "
    "Rotator pos = N/A, Alt = 32.6 deg, Az = 20.5 deg",
)

_LOG_WITH_LOST_STAR_ROWS = """\
KStars version 3.8.3. PHD2 log version 2.5. Log enabled at 2026-09-24 20:43:58

Guiding Begins at 2026-09-24 20:45:55
Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm
Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode
1,2.0,"Mount",0.1,0.1,0.100,0.100,0.1,0.1,10,E,10,N,,,250000,40.0,0
2,4.0,"DROP",0.000,0.000,0.000,0.000,0.000,0.000,0,,0,,,,0,0.00,7
3,6.0,"Mount",0.1,0.1,0.200,0.200,0.1,0.1,20,E,20,N,,,250000,41.0,0
Guiding Ends at 2026-09-24 20:50:00
"""


def _write(tmp_path: Path, name: str, text: str) -> str:
    """Write `text` to a file in `tmp_path`.

    Returns
    -------
    file_path : `str`
        Path to the written file.
    """
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_errors_are_converted_from_pixels_to_arcseconds(tmp_path: Path) -> None:
    """Verify distances are multiplied by the section's pixel scale.

    Regression test: the old parser stored the pixel values as if they
    were arcseconds, understating every guiding error by a factor of 6.39
    on this equipment.
    """
    samples = parse_phd2_guide_log(_write(tmp_path, "guide_log.txt", _EKOS_LOG))

    assert len(samples) == 3
    assert samples[1]["dra"] == pytest.approx(0.861 * 6.39)
    assert samples[1]["ddec"] == pytest.approx(0.301 * 6.39)


def test_pulses_keep_their_sign_from_single_letter_directions(tmp_path: Path) -> None:
    """Verify W and S are negative, E and N positive.

    Regression test: real logs write ``W``/``E``/``N``/``S``. The old
    parser only recognised the whole words, so every pulse came out
    positive.
    """
    samples = parse_phd2_guide_log(_write(tmp_path, "guide_log.txt", _EKOS_LOG))

    assert samples[0]["pulse_ra"] == pytest.approx(-44.0)
    assert samples[0]["pulse_dec"] == pytest.approx(58.0)
    assert samples[2]["pulse_ra"] == pytest.approx(1432.0)
    assert samples[2]["pulse_dec"] == pytest.approx(-397.0)


def test_timestamps_are_the_section_start_plus_the_frame_time(tmp_path: Path) -> None:
    """Verify each sample's epoch time is section start + elapsed seconds."""
    parsed_log = parse_guide_log_file(_write(tmp_path, "guide_log.txt", _EKOS_LOG))
    (section,) = parsed_log.sections

    assert section.samples[1]["timestamp"] == pytest.approx(section.started_at + 5.805)
    assert section.ended_at > section.started_at


def test_star_mass_and_snr_are_kept(tmp_path: Path) -> None:
    """Verify star mass and SNR come through as numbers."""
    samples = parse_phd2_guide_log(_write(tmp_path, "guide_log.txt", _EKOS_LOG))

    assert samples[0]["star_mass"] == pytest.approx(242421.0)
    assert samples[0]["snr"] == pytest.approx(348.15)


def test_ekos_written_logs_are_labelled_as_such(tmp_path: Path) -> None:
    """Verify the sample source says the Ekos guider wrote the file."""
    parsed_log = parse_guide_log_file(_write(tmp_path, "guide_log.txt", _EKOS_LOG))

    assert parsed_log.written_by_ekos is True
    assert {sample["source"] for sample in parsed_log.sections[0].samples} == {"ekos_guide_log"}


def test_native_phd2_logs_are_labelled_as_phd2(tmp_path: Path) -> None:
    """Verify a log whose first line is not KStars's is a PHD2 log."""
    phd2_text = _EKOS_LOG.replace(
        "KStars version 3.8.0. PHD2 log version 2.5. Log enabled at 2026-01-14 18:32:48",
        "PHD2 version 2.6.11, Log version 2.5. Log enabled at 2026-01-14 18:32:48",
    )
    parsed_log = parse_guide_log_file(_write(tmp_path, "PHD2_GuideLog.txt", phd2_text))

    assert parsed_log.written_by_ekos is False
    assert {sample["source"] for sample in parsed_log.sections[0].samples} == {"phd2_guide_log"}


def test_section_context_records_the_equipment_and_sky_position(tmp_path: Path) -> None:
    """Verify the pixel scale, focal length and pointing are kept."""
    (section,) = parse_guide_log_file(_write(tmp_path, "guide_log.txt", _EKOS_LOG)).sections

    assert section.pixel_scale_arcsec_per_px == pytest.approx(6.39)
    assert section.focal_length_mm == pytest.approx(121.05)
    assert section.binning == 1
    assert section.pier_side == "West"
    assert section.altitude_degrees == pytest.approx(32.6)
    assert section.azimuth_degrees == pytest.approx(20.5)
    assert section.declination_degrees == pytest.approx(69.5)
    assert section.hour_angle_hours is None  # the log says N/A


def test_measured_guide_speeds_are_kept(tmp_path: Path) -> None:
    """Verify calibrated mount speeds are read from both places."""
    parsed_log = parse_guide_log_file(_write(tmp_path, "guide_log.txt", _EKOS_LOG))

    (calibration,) = parsed_log.calibrations
    assert calibration.ra_rate_arcsec_per_second == pytest.approx(5.1)
    assert calibration.dec_rate_arcsec_per_second == pytest.approx(7.5)
    (section,) = parsed_log.sections
    assert section.ra_rate_arcsec_per_second == pytest.approx(5.061)
    assert section.dec_rate_arcsec_per_second == pytest.approx(7.530)


def test_lost_star_rows_are_counted_but_not_sampled(tmp_path: Path) -> None:
    """Verify rows with a non-zero error code are left out and counted.

    Real logs write a zero-filled ``DROP`` row with error code 7 when the
    guide star is lost. Sampling it would record a perfect zero error.
    """
    (section,) = parse_guide_log_file(_write(tmp_path, "guide_log.txt", _LOG_WITH_LOST_STAR_ROWS)).sections

    assert section.frames_total == 3
    assert section.frames_with_error_code == 1
    assert len(section.samples) == 2


def test_rows_are_not_guessed_when_no_pixel_scale_is_known(tmp_path: Path) -> None:
    """Verify a section with no pixel scale yields no samples, and says why."""
    text = _EKOS_LOG.replace("Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm\n", "")
    (section,) = parse_guide_log_file(_write(tmp_path, "guide_log.txt", text)).sections

    assert section.samples == []
    assert section.frames_without_pixel_scale == 3


def test_a_fallback_pixel_scale_is_used_when_the_log_has_none(tmp_path: Path) -> None:
    """Verify the caller can supply the scale from its equipment catalog."""
    text = _EKOS_LOG.replace("Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm\n", "")
    samples = parse_phd2_guide_log(
        _write(tmp_path, "guide_log.txt", text), fallback_pixel_scale_arcsec_per_px=2.0
    )

    assert samples[1]["dra"] == pytest.approx(0.861 * 2.0)


def test_a_log_cut_off_before_guiding_ends_still_parses(tmp_path: Path) -> None:
    """Verify a file with no "Guiding Ends" line is still readable."""
    text = _EKOS_LOG.split("Guiding Ends")[0]
    (section,) = parse_guide_log_file(_write(tmp_path, "guide_log.txt", text)).sections

    assert section.ended_at is None
    assert len(section.samples) == 3


def test_parse_phd2_guide_log_nonexistent_file() -> None:
    """Verify nonexistent file safely returns an empty list."""
    assert parse_phd2_guide_log("/non/existent/path/PHD2_GuideLog_dummy.txt") == []


def test_parse_phd2_guide_log_skips_malformed_lines(tmp_path: Path) -> None:
    """Verify that corrupt rows within a section are safely skipped."""
    text = _EKOS_LOG.replace('4,12.827,"Mount"', '5,abc,not,a,number\n4,12.827,"Mount"')
    samples = parse_phd2_guide_log(_write(tmp_path, "guide_log.txt", text))

    assert len(samples) == 3
