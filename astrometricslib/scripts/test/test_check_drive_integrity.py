"""Tests for the script that checks raw frames on Google Drive are unchanged.

A fake ``rclone`` (a small Python script that prints a JSON file) stands in
for the real program, so nothing touches the network or Google Drive.
"""

import json
import stat
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from astrometricslib.scripts import check_drive_integrity as integrity


def entry(path: str, md5: str = "aaa", size: int = 10) -> dict:
    """Build one fake ``rclone lsjson`` entry.

    Parameters
    ----------
    path : `str`
        File path relative to the library folder.
    md5 : `str`
        The fake checksum.
    size : `int`
        The fake size in bytes.

    Returns
    -------
    entry : `dict`
        An entry shaped like real rclone output.
    """
    return {"Path": path, "Size": size, "ModTime": "2026-01-01T00:00:00Z", "Hashes": {"md5": md5}}


@pytest.fixture
def fake_rclone(tmp_path: Path) -> Callable:
    """Create a fake rclone program and a function that sets its listing.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Pytest temporary folder.

    Returns
    -------
    set_listing : `callable`
        ``set_listing(entries, exit_code=0)`` sets what the fake prints.
        It has an ``executable`` attribute holding the program path.
    """
    program = tmp_path / "fake_rclone"
    listing = tmp_path / "listing.json"
    code_file = tmp_path / "exit_code.txt"
    program.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"sys.stdout.write(open({str(listing)!r}).read())\n"
        f"sys.exit(int(open({str(code_file)!r}).read()))\n"
    )
    program.chmod(program.stat().st_mode | stat.S_IEXEC)

    def set_listing(entries: list[dict], exit_code: int = 0) -> None:
        """Set what the fake rclone prints and returns."""
        listing.write_text(json.dumps(entries))
        code_file.write_text(str(exit_code))

    set_listing.executable = str(program)
    return set_listing


def run_check(fake_rclone: Callable, tmp_path: Path, *extra: str) -> int:
    """Run the script's main function against the fake rclone.

    Parameters
    ----------
    fake_rclone : `callable`
        The fixture above.
    tmp_path : `pathlib.Path`
        Pytest temporary folder.
    *extra : `str`
        Extra command-line flags.

    Returns
    -------
    exit_code : `int`
        The script's exit code.
    """
    return integrity.main([
        "--rclone",
        fake_rclone.executable,
        "--manifest-directory",
        str(tmp_path / "m"),
        "--no-notify",
        *extra,
    ])


def test_baseline_then_unchanged(
    fake_rclone: Callable, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first run records a baseline; a second identical run is clean."""
    fake_rclone([entry("lights/M 1/a.fits"), entry("_backup/x.fits")])
    assert run_check(fake_rclone, tmp_path) == 0
    assert "Baseline recorded: 1 files" in capsys.readouterr().out
    manifest_files = integrity.list_manifest_files(tmp_path / "m")
    assert len(manifest_files) == 1
    assert list(integrity.load_manifest(manifest_files[0])) == ["lights/M 1/a.fits"]
    assert run_check(fake_rclone, tmp_path) == 0
    assert "No serious problems" in capsys.readouterr().out


def test_missing_raw_frame_summary(
    fake_rclone: Callable, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The JSON summary lists the missing frame and counts added ones."""
    fake_rclone([entry("flats/f.fits", md5="f"), entry("flats/g.fits", md5="g")])
    run_check(fake_rclone, tmp_path)
    capsys.readouterr()
    fake_rclone([entry("flats/g.fits", md5="g"), entry("flats/h.fits", md5="h")])
    assert run_check(fake_rclone, tmp_path, "--json") == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["serious_missing"] == ["flats/f.fits"]
    assert summary["added_count"] == 1


def test_changed_raw_frame_exits_two(
    fake_rclone: Callable, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A raw frame whose checksum changed is serious."""
    fake_rclone([entry("darks/d.fits", md5="one")])
    run_check(fake_rclone, tmp_path)
    fake_rclone([entry("darks/d.fits", md5="two")])
    assert run_check(fake_rclone, tmp_path) == 2
    assert "CHANGED raw frame: darks/d.fits" in capsys.readouterr().out


def test_missing_derived_file_is_informational(
    fake_rclone: Callable, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing stacks, JPEGs and other derived files do not fail the check."""
    derived = [
        "lights/M 1/M_1_Stacked_x.fits",
        "lights/M 1/m.jpg",
        "lights/M 1/stacks/s.fits",
        "lights/M 1/a_RejMap.fits",
    ]
    fake_rclone([entry(path) for path in derived] + [entry("lights/M 1/a.fits")])
    run_check(fake_rclone, tmp_path)
    fake_rclone([entry("lights/M 1/a.fits")])
    assert run_check(fake_rclone, tmp_path) == 0
    output = capsys.readouterr().out
    assert "missing derived file (info): lights/M 1/m.jpg" in output
    assert "MISSING raw frame" not in output


def test_rclone_failure_exits_one(fake_rclone: Callable, tmp_path: Path) -> None:
    """A failing rclone gives exit code 1 and stores nothing."""
    fake_rclone([], exit_code=3)
    assert run_check(fake_rclone, tmp_path) == 1
    assert not (tmp_path / "m").exists()


def test_missing_rclone_program_exits_one(tmp_path: Path) -> None:
    """An rclone program that does not exist gives exit code 1."""
    exit_code = integrity.main([
        "--rclone",
        str(tmp_path / "nope"),
        "--manifest-directory",
        str(tmp_path / "m"),
        "--no-notify",
    ])
    assert exit_code == 1


def test_manifest_pruning_keeps_newest_ten(tmp_path: Path) -> None:
    """Only the newest ten manifests are kept."""
    start = datetime(2026, 1, 1)
    for index in range(13):
        integrity.save_manifest({}, tmp_path, start + timedelta(seconds=index))
    names = [path.name for path in integrity.list_manifest_files(tmp_path)]
    assert len(names) == 10
    assert names[0] == "manifest_20260101_000003.json.gz"


def test_raw_frame_moved_to_excluded_is_informational(
    fake_rclone: Callable, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A raw frame moved into an ``_excluded`` folder is MOVED, not missing."""
    fake_rclone([entry("lights/M 1/a.fits", md5="m1"), entry("lights/M 1/b.fits", md5="m2")])
    run_check(fake_rclone, tmp_path)
    capsys.readouterr()
    fake_rclone([entry("lights/M 1/a.fits", md5="m1"), entry("lights/M 1/_excluded/b.fits", md5="m2")])
    assert run_check(fake_rclone, tmp_path, "--json") == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["moved"] == [["lights/M 1/b.fits", "lights/M 1/_excluded/b.fits"]]
    assert summary["serious_missing"] == []
    assert summary["added_count"] == 0


def test_moved_frame_with_no_matching_checksum_stays_serious(fake_rclone: Callable, tmp_path: Path) -> None:
    """A vanished raw frame whose checksum appears nowhere is still serious."""
    fake_rclone([entry("lights/M 1/b.fits", md5="m2")])
    run_check(fake_rclone, tmp_path)
    fake_rclone([entry("lights/M 1/_excluded/b.fits", md5="other")])
    assert run_check(fake_rclone, tmp_path) == 2


def test_renamed_derived_file_is_moved(
    fake_rclone: Callable, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A renamed derived file is reported as MOVED and exits 0."""
    fake_rclone([entry("lights/M 1/old.jpg", md5="j")])
    run_check(fake_rclone, tmp_path)
    fake_rclone([entry("lights/M 1/new.jpg", md5="j")])
    assert run_check(fake_rclone, tmp_path) == 0
    assert "MOVED (info): lights/M 1/old.jpg -> lights/M 1/new.jpg" in capsys.readouterr().out
