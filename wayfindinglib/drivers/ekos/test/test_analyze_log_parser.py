"""Purpose: Unit tests for the Ekos analyze log parser.

Description: Verifies that an Ekos analyze log is read into a session
context and guide statistics with correct times, units and meanings. The
sample text is cut from a real log (KStars 3.8.3, session of 2026-09-23).
"""

from pathlib import Path

import pytest

from wayfindinglib.drivers.ekos.analyze_log_parser import parse_ekos_analyze_log

_ANALYZE_LOG = """\
#KStars version 3.8.3. Analyze log version 1.0.

AnalyzeStartTime,2026-09-23 20:31:48.442,MDT
MountState,2.629,Parked
MountCoords,2.758,19.8746,90.0000,0.0000,45.6817,0,-90.0000
MountState,60.814,Tracking
MountCoords,1487.798,300.1984,22.8036,170.2917,66.8779,1,-364.1194
Temperature,15.313,18.200
AlignState,65.548,In Progress
AlignState,83.326,Successful
AutofocusStarting,176.898,Luminance,18.4,1,
AutofocusAborted,246.150,18.4,1,,Luminance,30031|3.985|3.985|0|30011|3.299|3.299|0|29911|-1.000|-1.000|0,1,20,
AutofocusComplete,345.668,18.7,1,,Luminance,30011|3.361|3.361|0|29951|2.538|2.538|0|29905|1.044|1.044|0,1,1|11;30011
GuideState,672.968,Idle
GuideState,676.282,Selecting star
GuideStats,1133.128,1.158,-7.954,-1,3,38.259,30.809,242
CaptureStarting,564.401,30.000,Luminance
CaptureAborted,522.968,30.000
CaptureComplete,597.265,30.000,Luminance,1.262,/home/stellarmate/Pictures/M_27/Light/Luminance/M_27_Light_Luminance_001.fits,238,460,0.452
CaptureComplete,662.988,30.000,Luminance,-1.000,/home/stellarmate/Pictures/M_27/Light/Luminance/M_27_Light_Luminance_003.fits,0,464,-1.000
"""

_START_EPOCH = 1790217108.442
"""2026-09-23 20:31:48.442 MDT, which is UTC-6, as seconds since the epoch."""


def _write(tmp_path: Path, text: str, name: str = "ekos-2026-09-23T20-31-48.analyze") -> str:
    """Write `text` to an analyze file in `tmp_path`.

    Returns
    -------
    file_path : `str`
        Path to the written file.
    """
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _parse(tmp_path: Path, text: str = _ANALYZE_LOG):  # ruff: ignore[missing-return-type-private-function]
    """Parse `text` as an analyze file.

    Returns
    -------
    parsed : `tuple`
        The session context and the guide statistics.
    """
    parsed = parse_ekos_analyze_log(_write(tmp_path, text))
    assert parsed is not None
    return parsed


def test_the_start_time_is_converted_using_the_written_time_zone(tmp_path: Path) -> None:
    """Verify MDT means UTC-6, so the session lands on the right instant."""
    context, _ = _parse(tmp_path)

    assert context.started_at == pytest.approx(_START_EPOCH)


def test_record_times_are_the_start_plus_the_elapsed_seconds(tmp_path: Path) -> None:
    """Verify each record's time is offset from the start line."""
    context, _ = _parse(tmp_path)

    assert context.captures[0].completed_at == pytest.approx(_START_EPOCH + 597.265)


def test_the_session_is_named_after_the_file_and_the_observing_night(tmp_path: Path) -> None:
    """Verify the id is from the file name, the night from the start."""
    context, _ = _parse(tmp_path)

    assert context.id == "2026-09-23T20-31-48"
    assert context.session_id == "2026-09-23"
    assert context.source_file_name == "ekos-2026-09-23T20-31-48.analyze"


def test_captures_keep_their_measurements_and_turn_not_measured_into_none(tmp_path: Path) -> None:
    """Verify -1 (Ekos's "could not measure") becomes `None`, not a number."""
    context, _ = _parse(tmp_path)
    measured, unmeasured = context.captures

    assert measured.half_flux_radius_px == pytest.approx(1.262)
    assert measured.star_count == 238
    assert measured.median_adu == pytest.approx(460.0)
    assert measured.eccentricity == pytest.approx(0.452)
    assert measured.filter_name == "Luminance"
    assert measured.file_path.endswith("M_27_Light_Luminance_001.fits")
    assert unmeasured.half_flux_radius_px is None
    assert unmeasured.eccentricity is None
    assert unmeasured.star_count == 0


def test_aborted_captures_are_kept_separately(tmp_path: Path) -> None:
    """Verify a cancelled exposure is recorded, but not as a capture."""
    context, _ = _parse(tmp_path)

    assert len(context.captures) == 2
    (aborted,) = context.aborted_captures
    assert aborted.exposure_seconds == pytest.approx(30.0)


def test_a_finished_autofocus_run_separates_the_chosen_position_from_the_curve(tmp_path: Path) -> None:
    """Verify a finished run's last point is the answer, not a sample."""
    context, _ = _parse(tmp_path)
    finished = next(run for run in context.autofocus_runs if run.succeeded)

    assert [sample.position for sample in finished.curve] == [30011, 29951]
    assert finished.final_position == 29905
    assert finished.final_half_flux_radius_px == pytest.approx(1.044)
    assert finished.temperature_c == pytest.approx(18.7)
    assert finished.filter_name == "Luminance"


def test_an_aborted_autofocus_run_has_no_chosen_position_and_marks_failed_points(tmp_path: Path) -> None:
    """Verify an aborted run keeps every point; failures are `None`."""
    context, _ = _parse(tmp_path)
    aborted = next(run for run in context.autofocus_runs if not run.succeeded)

    assert aborted.final_position is None
    assert [sample.position for sample in aborted.curve] == [30031, 30011, 29911]
    assert aborted.curve[-1].half_flux_radius_px is None


def test_mount_positions_keep_the_reliable_fields_and_name_the_pier_side(tmp_path: Path) -> None:
    """Verify Dec/Alt/Az and pier side are kept; RA and hour angle are not."""
    context, _ = _parse(tmp_path)
    parked, tracking = context.mount_positions

    assert parked.altitude_degrees == pytest.approx(45.6817)
    assert parked.pier_side == "West"
    assert tracking.declination_degrees == pytest.approx(22.8036)
    assert tracking.azimuth_degrees == pytest.approx(170.2917)
    assert tracking.altitude_degrees == pytest.approx(66.8779)
    assert tracking.pier_side == "East"
    assert not hasattr(tracking, "right_ascension_hours")
    assert not hasattr(tracking, "hour_angle_degrees")


def test_state_changes_are_recorded_per_module(tmp_path: Path) -> None:
    """Verify align, guider and mount state events are kept apart, in order."""
    context, _ = _parse(tmp_path)

    assert [event.state for event in context.align_events] == ["In Progress", "Successful"]
    assert [event.state for event in context.guide_state_events] == ["Idle", "Selecting star"]
    assert [event.state for event in context.mount_state_events] == ["Parked", "Tracking"]


def test_guide_statistics_are_returned_with_their_meaning(tmp_path: Path) -> None:
    """Verify the error, SNR, background and star count fields line up."""
    _, guide_stats = _parse(tmp_path)
    (stat,) = guide_stats

    assert stat.timestamp == pytest.approx(_START_EPOCH + 1133.128)
    assert stat.ra_error_arcsec == pytest.approx(1.158)
    assert stat.dec_error_arcsec == pytest.approx(-7.954)
    assert stat.snr == pytest.approx(38.259)
    assert stat.sky_background == pytest.approx(30.809)
    assert stat.star_count == 242


def test_the_end_time_is_the_last_event(tmp_path: Path) -> None:
    """Verify the session's end is the time of its last readable record."""
    context, _ = _parse(tmp_path)

    assert context.ended_at == pytest.approx(_START_EPOCH + 1487.798)


def test_trailing_nul_padding_is_ignored(tmp_path: Path) -> None:
    """Verify a file ending in NUL bytes parses like a clean one."""
    context, guide_stats = _parse(tmp_path, _ANALYZE_LOG + "\x00" * 200)

    assert len(context.captures) == 2
    assert len(guide_stats) == 1


def test_an_unreadable_record_is_skipped_without_losing_the_others(tmp_path: Path) -> None:
    """Verify one corrupt line does not stop the rest of the file."""
    text = _ANALYZE_LOG.replace("GuideState,672.968,Idle", "GuideStats,abc,def\nCaptureComplete,1.0,x")
    context, guide_stats = _parse(tmp_path, text)

    assert len(context.captures) == 2
    assert len(guide_stats) == 1


def test_a_file_with_no_start_time_cannot_be_anchored(tmp_path: Path) -> None:
    """Verify such a file is rejected: no record time would mean anything."""
    text = "\n".join(line for line in _ANALYZE_LOG.split("\n") if not line.startswith("AnalyzeStartTime"))

    assert parse_ekos_analyze_log(_write(tmp_path, text)) is None


def test_an_empty_or_missing_file_gives_none(tmp_path: Path) -> None:
    """Verify empty and nonexistent files are handled safely."""
    assert parse_ekos_analyze_log(_write(tmp_path, "")) is None
    assert parse_ekos_analyze_log(str(tmp_path / "missing.analyze")) is None
