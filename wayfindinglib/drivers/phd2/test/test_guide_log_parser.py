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


def _find_contract_fixture() -> Path:
    """Find the PHD2 contract guide log, wherever the fixtures live now.

    Returns
    -------
    path : `pathlib.Path`
        The fixture file.
    """
    root = Path(__file__).resolve().parents[4]
    candidates = [
        root / "simulator" / "test" / "fixtures" / "phd2_contract_guide_log.txt",
        root / "build" / "tpm" / "fixtures" / "phd2_contract_guide_log.txt",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


_RECORDED_PHD2_LOG = _find_contract_fixture()


def test_ekos_calibration_speeds_and_section_rates_are_unchanged(tmp_path: Path) -> None:
    """Verify an Ekos log keeps its guide speeds and arcsec/s rates."""
    parsed_log = parse_guide_log_file(_write(tmp_path, "guide_log.txt", _EKOS_LOG))

    (calibration,) = parsed_log.calibrations
    assert calibration.ra_rate_arcsec_per_second == pytest.approx(5.1)
    assert calibration.dec_rate_arcsec_per_second == pytest.approx(7.5)
    (section,) = parsed_log.sections
    assert section.ra_rate_arcsec_per_second == pytest.approx(5.061)
    assert section.dec_rate_arcsec_per_second == pytest.approx(7.530)


def test_phd2_written_log_gives_calibration_speeds_and_angle() -> None:
    """Verify a PHD2 2.6.14 log gives guide speeds and camera angle."""
    parsed_log = parse_guide_log_file(str(_RECORDED_PHD2_LOG))

    assert parsed_log.written_by_ekos is False
    (calibration,) = parsed_log.calibrations
    # "RA Guide Speed = 7.5 a-s/s, Dec Guide Speed = 7.5 a-s/s"
    assert calibration.ra_rate_arcsec_per_second == pytest.approx(7.5)
    assert calibration.dec_rate_arcsec_per_second == pytest.approx(7.5)
    # "West calibration complete. Angle = -178.1 deg"
    assert calibration.camera_angle_deg == pytest.approx(-178.1)


def test_phd2_written_log_section_rates_are_converted_from_pixels_per_second() -> None:
    """Verify PHD2's px/s rates become arcsec/s at 1.24 arcsec/px."""
    parsed_log = parse_guide_log_file(str(_RECORDED_PHD2_LOG))

    section = parsed_log.sections[0]
    assert section.pixel_scale_arcsec_per_px == pytest.approx(1.24)
    assert section.ra_rate_arcsec_per_second == pytest.approx(4.239 * 1.24)
    assert section.dec_rate_arcsec_per_second == pytest.approx(5.850 * 1.24)


def test_phd2_written_log_without_pixel_scale_leaves_section_rates_empty(tmp_path: Path) -> None:
    """Verify px/s rates stay empty when the pixel scale is unknown."""
    text = _RECORDED_PHD2_LOG.read_text(encoding="utf-8")
    scaleless = "\n".join(line for line in text.splitlines() if not line.startswith("Pixel scale = "))
    section = parse_guide_log_file(_write(tmp_path, "PHD2_GuideLog.txt", scaleless)).sections[0]

    assert section.ra_rate_arcsec_per_second is None
    assert section.dec_rate_arcsec_per_second is None


def test_section_rate_matches_the_guide_speed_times_cosine_of_calibration_declination() -> None:
    """The log's section rates agree with its guide speeds, within 10 percent.

    PHD2 writes the rates in pixels per second. The parser multiplies them by
    the pixel scale, so the RA rate is the on-sky rate at the calibration
    declination: the guide speed (a fraction of the sidereal rate) times
    cos(Cal Dec). The Dec rate matches the Dec guide speed with no factor.
    """
    import math
    import re

    fixture = _find_contract_fixture()
    parsed = parse_guide_log_file(str(fixture))
    calibration_declination = float(re.search(r"Cal Dec = (-?[\d.]+)", fixture.read_text()).group(1))
    calibration = parsed.calibrations[0]
    section = parsed.sections[0]

    assert section.pixel_scale_arcsec_per_px is not None
    assert section.ra_rate_arcsec_per_second == pytest.approx(
        calibration.ra_rate_arcsec_per_second * math.cos(math.radians(calibration_declination)), rel=0.10
    )
    assert section.dec_rate_arcsec_per_second == pytest.approx(
        calibration.dec_rate_arcsec_per_second, rel=0.10
    )


_PHD2_HEADER_LOG = """\
PHD2 version 2.6.13 [Linux], Log version 2.5. Log enabled at 2026-11-06 20:00:00

Guiding Begins at 2026-11-06 20:01:00
Equipment Profile = Rig
Pixel scale = 3.74 arc-sec/px, Binning = 1, Focal length = 200 mm
Camera = ZWO ASI120MM, gain = 95, full size = 1280 x 960, pixel size = 3.75 um
Exposure = 2500 ms
Mount = EQMod Mount, xAngle = 12.0, xRate = 2.0, yAngle = -78.0, yRate = 2.1
X guide algorithm = Hysteresis, Hysteresis = 0.100, Aggression = 0.700, Minimum move = 0.150
Y guide algorithm = Resist Switch, Minimum move = 0.200, Aggression = 100.000
Max RA duration = 1500, Max DEC duration = 1200, DEC guide mode = Auto
RA = 12.00 hr, Dec = 40.0 deg, Hour angle = 1.0 hr, Pier side = East, Alt = 55.5 deg, Az = 277.6 deg
Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode
1,2.5,"Mount",0.1,0.1,0.100,0.100,0.1,0.1,10,E,10,N,,,250000,40.0,0
Guiding Ends at 2026-11-06 20:02:00
"""


def test_phd2_header_gives_the_guide_controller_settings(tmp_path: Path) -> None:
    """The exposure, algorithms, gains and pulse limits are read."""
    parsed = parse_guide_log_file(_write(tmp_path, "phd2.txt", _PHD2_HEADER_LOG))
    controller = parsed.sections[0].controller
    assert controller.exposure_seconds == pytest.approx(2.5)
    assert controller.ra_axis is not None
    assert controller.ra_axis.algorithm == "Hysteresis"
    assert controller.ra_axis.hysteresis == pytest.approx(0.1)
    assert controller.ra_axis.aggressiveness == pytest.approx(0.7)
    assert controller.ra_axis.minimum_move_px == pytest.approx(0.15)
    assert controller.dec_axis is not None
    assert controller.dec_axis.algorithm == "Resist Switch"
    assert controller.dec_axis.hysteresis is None
    assert controller.dec_axis.minimum_move_px == pytest.approx(0.2)
    assert controller.maximum_ra_duration_ms == pytest.approx(1500.0)
    assert controller.maximum_dec_duration_ms == pytest.approx(1200.0)


def test_an_aggressiveness_written_as_a_percent_is_read_as_a_fraction(tmp_path: Path) -> None:
    """An older PHD2 writes 100.000 for full aggression; it reads as 1.0."""
    parsed = parse_guide_log_file(_write(tmp_path, "phd2.txt", _PHD2_HEADER_LOG))
    assert parsed.sections[0].controller.dec_axis.aggressiveness == pytest.approx(1.0)


def test_header_lines_do_not_disturb_the_samples_or_rates(tmp_path: Path) -> None:
    """Controller lines change neither the equipment context nor the rows."""
    section = parse_guide_log_file(_write(tmp_path, "phd2.txt", _PHD2_HEADER_LOG)).sections[0]
    assert len(section.samples) == 1
    assert section.pixel_scale_arcsec_per_px == pytest.approx(3.74)
    assert section.ra_rate_arcsec_per_second == pytest.approx(2.0 * 3.74)


def test_a_log_without_controller_lines_leaves_the_settings_empty(tmp_path: Path) -> None:
    """The Ekos guider writes no controller lines; each setting is `None`."""
    section = parse_guide_log_file(_write(tmp_path, "ekos.txt", _EKOS_LOG)).sections[0]
    assert section.controller.exposure_seconds is None
    assert section.controller.ra_axis is None
    assert section.controller.dec_axis is None


def test_the_run_summary_carries_the_controller_settings(tmp_path: Path) -> None:
    """Ingestion copies the settings into the guiding run record."""
    from wayfindinglib.tasks.control_tasks.ekos_log_ingestion import _summarize_run

    section = parse_guide_log_file(_write(tmp_path, "phd2.txt", _PHD2_HEADER_LOG)).sections[0]
    run = _summarize_run(section, 1, "phd2.txt", False)
    assert run.guide_exposure_seconds == pytest.approx(2.5)
    assert run.ra_algorithm == "Hysteresis"
    assert run.dec_algorithm == "Resist Switch"
    assert run.ra_aggressiveness == pytest.approx(0.7)
    assert run.ra_hysteresis == pytest.approx(0.1)
    assert run.dec_minimum_move_px == pytest.approx(0.2)
    assert run.maximum_dec_duration_ms == pytest.approx(1200.0)


def test_the_real_phd2_log_header_gives_the_controller_settings() -> None:
    """A log written by the real PHD2 (2.6.14dev1) states its controller.

    The fixture is a trimmed guide log that PHD2 wrote against the INDI guide
    camera and telescope simulators, in a simulated world (see
    ``simulator/test/test_phd2_live.py``). The profile was written with
    aggressiveness 0.45, hysteresis 0.25 and a 1 s exposure, and the header
    states them: the right ascension axis ("X") runs Hysteresis with a
    fraction, the declination axis ("Y") runs Resist Switch and writes its
    aggressiveness as a percent. PHD2 replaced the minimum move written in the
    profile (0.4) with its own value (0.277) when calibration ended, so the
    header holds the value the guiding really used.
    """
    fixture = Path(__file__).parent / "fixtures" / "phd2_real_simulated_guide_log.txt"
    parsed = parse_guide_log_file(str(fixture))
    section = next(section for section in parsed.sections if section.samples)
    controller = section.controller
    assert controller.exposure_seconds == pytest.approx(1.0)
    assert controller.ra_axis is not None
    assert (
        controller.ra_axis.algorithm,
        controller.ra_axis.aggressiveness,
        controller.ra_axis.hysteresis,
    ) == (
        "Hysteresis",
        pytest.approx(0.45),
        pytest.approx(0.25),
    )
    assert controller.ra_axis.minimum_move_px == pytest.approx(0.277)
    assert controller.dec_axis is not None
    assert controller.dec_axis.algorithm == "Resist Switch"
    assert controller.dec_axis.aggressiveness == pytest.approx(0.45)
    assert controller.dec_axis.hysteresis is None
    assert controller.maximum_ra_duration_ms == pytest.approx(2500.0)
    assert controller.maximum_dec_duration_ms == pytest.approx(2500.0)
    # The speed PHD2 measured in its calibration (the simulator moves at 7.5).
    assert section.dec_rate_arcsec_per_second == pytest.approx(7.25, abs=0.02)
    assert len(section.samples) >= 20
