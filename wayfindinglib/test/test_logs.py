"""Purpose: Unit tests for the public Ekos log readers in `wayfindinglib.logs`.

Description: Checks that the frames an analyze log saved come back as plain
records with UTC times, that a missing file gives `None`, and that the zone
names Ekos writes map to their offsets.
"""

from pathlib import Path

import pytest

from wayfindinglib.logs import SavedCapture, ekos_utc_offset_hours, read_ekos_captures

_ANALYZE_LOG = """\
AnalyzeStartTime,2026-09-23 20:31:48.442,MDT
CaptureComplete,597.265,30.000,Luminance,1.262,/home/stellarmate/Pictures/M_27/Light/Luminance/M_27_Light_Luminance_001.fits,238,460,0.452
"""


def test_the_saved_frames_come_back_with_utc_times(tmp_path: Path) -> None:
    """One finished exposure becomes one `SavedCapture`."""
    path = tmp_path / "ekos-2026-09-23T20-31-48.analyze"
    path.write_text(_ANALYZE_LOG, encoding="utf-8")

    captures = read_ekos_captures(path)

    assert captures == [
        SavedCapture(
            file_path="/home/stellarmate/Pictures/M_27/Light/Luminance/M_27_Light_Luminance_001.fits",
            completed_at=pytest.approx(1790217108.442 + 597.265),
            exposure_seconds=30.0,
        )
    ]


def test_a_missing_file_gives_none(tmp_path: Path) -> None:
    """No file, no captures."""
    assert read_ekos_captures(tmp_path / "missing.analyze") is None


def test_zone_names_map_to_offsets() -> None:
    """Known names give hours, in any letter case; others give `None`."""
    assert ekos_utc_offset_hours("MDT") == -6
    assert ekos_utc_offset_hours(" utc ") == 0
    assert ekos_utc_offset_hours("CET") is None
