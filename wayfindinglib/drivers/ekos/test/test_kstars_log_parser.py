"""Purpose: Unit tests for reading dither events from a KStars log.

Description: The sample lines are cut from a real KStars log (session of
2026-10-02), keeping each message the guider writes around a dither.
"""

from pathlib import Path

import pytest

from wayfindinglib.drivers.ekos.kstars_log_parser import parse_dither_events


def _line(time: str, message: str, module: str = "guide") -> str:
    """Build one KStars log line in the real format.

    Returns
    -------
    line : `str`
        The line, with a local time, a zone and a module name.
    """
    return f'[2026-10-02T{time} MDT INFO ][     org.kde.kstars.ekos.{module}] - "{message}"'


_LOG = "\n".join([
    _line("21:09:22.464", "Dithering by 2.5 pixels. Target: 76.4,602.5"),
    _line("21:09:23.000", "Dithering in progress. Current: 75.1,603.4"),
    _line("21:10:05.949", "Warning: Dithering failed. Autoguiding shall continue."),
    _line("21:10:05.950", "Post-dither settling for 15 seconds..."),
    _line("21:10:21.100", "Dithering completed successfully."),
    _line("21:19:39.275", "Dithering by 1.5 pixels. Target: 109.1,715.3"),
    _line("21:19:58.912", "Post-dither settling for 30 seconds..."),
    _line("21:20:28.900", "Dithering completed successfully."),
    _line("21:24:23.762", "Dithering by 2.5 pixels. Target: 108.6,714.4"),
    _line("21:24:25.000", "Dithering succeeded.", module="capture"),
])
"""Two finished dithers (one failed) and one the log ends in the middle of."""


def _write_log(tmp_path: Path, text: str) -> str:
    """Write `text` to a temporary KStars log and return its path.

    Returns
    -------
    path : `str`
        The file's path.
    """
    path = tmp_path / "log_20-23-44.txt"
    path.write_text(text)
    return str(path)


def test_each_dither_is_read_with_its_outcome_and_settle_time(tmp_path: Path) -> None:
    """A failed dither and a successful one are told apart."""
    events = parse_dither_events(_write_log(tmp_path, _LOG))

    assert [e.succeeded for e in events] == [False, True, None]
    assert [e.amplitude_px for e in events] == [2.5, 1.5, 2.5]
    assert [e.settle_seconds for e in events] == [15.0, 30.0, None]


def test_dither_duration_runs_from_the_start_to_the_completion_message(tmp_path: Path) -> None:
    """The first dither lasted 43 s of moving plus 15 s of settling."""
    events = parse_dither_events(_write_log(tmp_path, _LOG))

    assert events[0].duration_seconds == pytest.approx(58.636, abs=0.01)
    assert events[2].duration_seconds is None


def test_times_are_converted_from_local_time_to_utc(tmp_path: Path) -> None:
    """21:09:22 MDT is 03:09:22 UTC the next day."""
    events = parse_dither_events(_write_log(tmp_path, _LOG))

    assert events[0].timestamp == pytest.approx(1790996962.464)


def test_a_missing_file_or_a_log_without_dithers_gives_no_events(tmp_path: Path) -> None:
    """Neither case raises."""
    assert parse_dither_events(str(tmp_path / "absent.txt")) == []
    assert (
        parse_dither_events(_write_log(tmp_path, "[2026-10-02T21:09:22.464 MDT INFO ][ x] - hello\n")) == []
    )
