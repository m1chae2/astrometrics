"""Purpose: Unit tests for PHD2 guide log text file parsing.

Description: Verifies that native PHD2_GuideLog_*.txt files are parsed into
normalized dictionaries containing epoch timestamps, signed guide pulse
durations, star mass, SNR, and optical drift coordinates.
"""

from pathlib import Path

import pytest

from wayfindinglib.drivers.phd2.guide_log_parser import parse_phd2_guide_log


def test_parse_phd2_guide_log_with_valid_file(tmp_path: Path) -> None:
    """Verify parsing a standard PHD2 guide log file with headers and rows."""
    log_file = tmp_path / "PHD2_GuideLog_2026-09-24_220000.txt"
    sample_content = (
        "PHD2 version 2.6.11\n"
        "Guiding Begins at 2026-09-24 22:00:00\n"
        "# Equipment profile: ZWO ASI290MM Mini\n"
        "Frame, Time, mount, dx, dy, RARawDistance, DECRawDistance, "
        "RADistanceGuide, DECDistanceGuide, RADuration, RADirection, "
        "DECDuration, DECDirection, StarMass, SNR\n"
        "1, 2.500, Mount, 0.12, -0.08, 0.15, -0.10, 0.14, -0.09, 50, East, "
        "25, North, 4500.0, 32.4\n"
        "2, 5.000, Mount, -0.22, 0.18, -0.20, 0.21, -0.19, 0.20, 100, West, "
        "60, South, 4420.0, 31.0\n"
        "Guiding Ends at 2026-09-24 22:05:00\n"
    )
    log_file.write_text(sample_content, encoding="utf-8")

    samples = parse_phd2_guide_log(str(log_file), target_name="M31")
    assert len(samples) == 2

    first = samples[0]
    assert first["target_name"] == "M31"
    assert first["dra"] == pytest.approx(0.14)
    assert first["ddec"] == pytest.approx(-0.09)
    assert first["pulse_ra"] == pytest.approx(50.0)  # East is positive
    assert first["pulse_dec"] == pytest.approx(25.0)  # North is positive
    assert first["star_mass"] == pytest.approx(4500.0)
    assert first["snr"] == pytest.approx(32.4)

    second = samples[1]
    assert second["target_name"] == "M31"
    assert second["dra"] == pytest.approx(-0.19)
    assert second["ddec"] == pytest.approx(0.20)
    assert second["pulse_ra"] == pytest.approx(-100.0)  # West is negative
    assert second["pulse_dec"] == pytest.approx(-60.0)  # South is negative
    assert second["star_mass"] == pytest.approx(4420.0)
    assert second["snr"] == pytest.approx(31.0)


def test_parse_phd2_guide_log_nonexistent_file() -> None:
    """Verify nonexistent file safely returns an empty list."""
    samples = parse_phd2_guide_log("/non/existent/path/PHD2_GuideLog_dummy.txt")
    assert samples == []


def test_parse_phd2_guide_log_skips_malformed_lines(tmp_path: Path) -> None:
    """Verify that corrupt rows within a section are safely skipped."""
    log_file = tmp_path / "PHD2_GuideLog_corrupt.txt"
    sample_content = (
        "Guiding Begins at 2026-09-24 22:00:00\n"
        "Frame, Time, mount, RADistanceGuide, DECDistanceGuide, "
        "RADuration, RADirection, DECDuration, DECDirection\n"
        "1, 2.0, Mount, 0.10, 0.10, 20, East, 10, North\n"
        "invalid, line, not, numbers, at, all\n"
        "2, 4.0, Mount, -0.05, 0.05, 15, West, 0, None\n"
        "Guiding Ends at 2026-09-24 22:05:00\n"
    )
    log_file.write_text(sample_content, encoding="utf-8")

    samples = parse_phd2_guide_log(str(log_file))
    assert len(samples) == 2
    assert samples[0]["pulse_ra"] == pytest.approx(20.0)
    assert samples[1]["pulse_ra"] == pytest.approx(-15.0)
