"""Purpose: Unit tests for gathering the live session status.

Description: Checks the file handling around the status analysis: which
log is read when several exist, that the telescope computer is asked for
fresh logs only when requested, and that a missing log gives no status
instead of an error.
"""

import os
import types
from pathlib import Path

import pytest

from astrometricslib import InvalidArgumentError
from wayfindinglib.tasks.control_tasks import live_session_status_task as task

_ANALYZE = """\
#KStars version 3.8.3. Analyze log version 1.0.

AnalyzeStartTime,2026-10-02 20:00:00.000,MDT
MountCoords,1.0,350.0,61.7,34.0,67.0,0,-387.0
GuideState,5.0,Guiding
GuideStats,10.0,0.8,0.5,0,0,400.0,300.0,100
GuideStats,14.0,-0.8,-0.5,0,0,400.0,300.0,100
CaptureComplete,130.0,120.000,Luminance,1.6,/home/stellarmate/Pictures/T/T_001.fits,240,1288,0.65
"""
_GUIDE_PREFIX = "[2026-10-02T{time} MDT INFO ][     org.kde.kstars.ekos.guide] - "
_DITHER_LOG = (
    _GUIDE_PREFIX.format(time="20:01:00.000")
    + '"Dithering by 2.5 pixels."\n'
    + _GUIDE_PREFIX.format(time="20:01:30.000")
    + '"Dithering completed successfully."\n'
)


class _FakeDriver:
    """A remote-transfer driver that records what it was asked to download."""

    def __init__(self) -> None:
        """Start with no downloads recorded."""
        self.downloads: list[str] = []

    def download_ekos_analyze_logs(self, destination_dir: str) -> list[str]:
        """Record an analyze-log download.

        Returns
        -------
        paths : `list` [`str`]
            Nothing: the test pre-populates the folder.
        """
        self.downloads.append("analyze")
        return []

    def download_kstars_logs(self, destination_dir: str) -> list[str]:
        """Record a KStars-log download.

        Returns
        -------
        paths : `list` [`str`]
            Nothing: the test pre-populates the folder.
        """
        self.downloads.append("kstars")
        return []


def _populate(folder: Path) -> None:
    """Write an old and a new analyze log and a KStars log into `folder`."""
    (folder / "kstars_logs").mkdir()
    (folder / "ekos-2026-09-30T18-00-00.analyze").write_text(_ANALYZE.replace("2026-10-02", "2026-09-30"))
    (folder / "ekos-2026-10-02T20-00-00.analyze").write_text(_ANALYZE)
    (folder / "kstars_logs" / "log_20-00-00.txt").write_text(_DITHER_LOG)


def test_the_newest_analyze_log_is_read_with_its_dithers(tmp_path: Path) -> None:
    """The newest log is read, together with the KStars dither."""
    _populate(tmp_path)

    status = task.get_live_session_status(None, str(tmp_path), refresh=False)

    assert status is not None
    assert status.analyze_file == "ekos-2026-10-02T20-00-00.analyze"
    assert status.kstars_log_file == "log_20-00-00.txt"
    assert [d.succeeded for d in status.dithers] == [True]
    assert status.guider_state == "Guiding"
    assert status.exposures[0].frame_name == "T_001.fits"


def test_logs_are_downloaded_only_when_a_refresh_is_asked_for(tmp_path: Path) -> None:
    """Only a refresh asks the telescope computer for logs."""
    _populate(tmp_path)
    driver = _FakeDriver()
    observatory = types.SimpleNamespace(remote_transfer_driver=driver)

    task.get_live_session_status(observatory, str(tmp_path), refresh=False)
    assert driver.downloads == []

    task.get_live_session_status(observatory, str(tmp_path), refresh=True)
    assert driver.downloads == ["analyze", "kstars"]


def test_a_driver_without_log_downloads_still_reads_local_files(tmp_path: Path) -> None:
    """A driver that cannot fetch logs still lets local files be read."""
    _populate(tmp_path)
    observatory = types.SimpleNamespace(remote_transfer_driver=object())

    assert task.get_live_session_status(observatory, str(tmp_path), refresh=True) is not None


def test_no_analyze_log_gives_no_status(tmp_path: Path) -> None:
    """An empty or missing folder returns `None`, not an error."""
    assert task.get_live_session_status(None, str(tmp_path), refresh=False) is None
    assert task.get_live_session_status(None, os.path.join(str(tmp_path), "absent"), refresh=False) is None


def test_asked_sections_of_the_record_come_back_under_details(tmp_path: Path) -> None:
    """The mount positions are attached, and nothing else is."""
    _populate(tmp_path)

    status = task.get_live_session_status(None, str(tmp_path), refresh=False, include=["mount_positions"])

    assert status is not None
    assert set(status.details) == {"mount_positions"}
    assert status.details["mount_positions"]["total"] == 1


def test_an_unknown_section_is_refused_before_any_file_is_read(tmp_path: Path) -> None:
    """A made-up section name raises an error that lists the real ones."""
    with pytest.raises(InvalidArgumentError, match="autofocus_runs"):
        task.get_live_session_status(None, str(tmp_path), refresh=False, include=["bogus"])
