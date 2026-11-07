"""Purpose: Tests for the replay profile and the network seal of the TPM run.

Description: Builds a small made-up night (an Ekos analyze log, a guide log
and a few FITS files) in a temporary folder and checks that the replay
planner picks a window, splits the night into the end-of-night state and the
live window with the right times, stages the files, and that the stand-in
PHD2 plays the planned events after the start file appears. It also checks
the loopback-only network seal with a real ``bwrap --unshare-net`` call (the
test is skipped where bubblewrap does not work).

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from astropy.io import fits

from simulator import guide_log_replay, mock_phd2, night_replay

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import replay_preload  # ruff: ignore[module-import-not-at-top-of-file]
import write_sandbox_config  # ruff: ignore[module-import-not-at-top-of-file]

#: The made-up night: the logs are in local time (MDT, six hours behind UTC).
LOCAL_START = datetime(2026, 10, 3, 20, 0, 0)
ZONE_HOURS = -6
FRAME_COUNT = 12
FRAME_SPACING_SECONDS = 120.0
EXPOSURE_SECONDS = 100.0


def _make_night(root: Path) -> tuple[Path, Path]:
    """Write a made-up night: logs, and FITS files in a library.

    Parameters
    ----------
    root : `pathlib.Path`
        An empty temporary folder.

    Returns
    -------
    log_directory, frames_directory : `pathlib.Path`
        The folder with the two logs and the library's frames folder.
    """
    logs = root / "logs"
    logs.mkdir(parents=True)
    frames = root / "frames"
    (frames / "lights" / "Test Target" / "scope" / "camera").mkdir(parents=True)
    analyze = [
        "#KStars version 3.8.3. Analyze log version 1.0.",
        "",
        f"AnalyzeStartTime,{LOCAL_START:%Y-%m-%d %H:%M:%S}.000,MDT",
        "MountState,1.000,Idle",
    ]
    for index in range(FRAME_COUNT):
        saved = 600.0 + index * FRAME_SPACING_SECONDS
        name = f"Test_Target_Light_Luminance_{index + 1:03d}.fits"
        analyze.append(f"GuideStats,{saved - 10:.3f},0.1,0.2")
        analyze.append(
            f"CaptureComplete,{saved:.3f},{EXPOSURE_SECONDS:.3f},Luminance,1.5,"
            f"/home/stellarmate/Pictures/Test_Target/Light/Luminance/{name},200,800,0.3"
        )
        observed = LOCAL_START - timedelta(hours=ZONE_HOURS) + timedelta(seconds=saved - EXPOSURE_SECONDS)
        header = fits.Header()
        header["OBJECT"] = "Test Target"
        header["RA"] = 150.0
        header["DEC"] = 30.0
        header["DATE-OBS"] = observed.strftime("%Y-%m-%dT%H:%M:%S.000")
        fits.PrimaryHDU(data=None, header=header).writeto(
            frames / "lights" / "Test Target" / "scope" / "camera" / name
        )
    (logs / "ekos-2026-10-03T20-00-00.analyze").write_text("\n".join(analyze) + "\n")
    guide = [
        "KStars version 3.8.3. PHD2 log version 2.5. Log enabled at 2026-10-03 20:00:00",
        "",
        "Guiding Begins at 2026-10-03 20:00:00",
        "Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,"
        "RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode",
    ]
    for index in range(1, 1500):
        seconds = index * 1.0
        if index == 1000:
            guide.append("INFO: SETTLING STATE CHANGE, Settling started")
        if index == 1004:
            guide.append("INFO: SETTLING STATE CHANGE, Settling complete")
        if index in (900, 901):
            guide.append(f'{index},{seconds:.3f},"DROP",0.0,0.0,0.0,0.0,0.0,0.0,0,,0,,,,0,0.00,7')
        else:
            guide.append(f'{index},{seconds:.3f},"Mount",0.1,0.2,0.1,0.2,0.1,0.2,10,E,20,N,,,80000,200.0,0')
    guide.append("Guiding Ends at 2026-10-03 20:25:00")
    (logs / "guide_log-2026-10-03T20-00-00.txt").write_text("\n".join(guide) + "\n")
    return logs, frames


@pytest.fixture
def made_up_night(tmp_path: Path) -> tuple[Path, Path]:
    """Make the night once per test.

    Returns
    -------
    folders : `tuple` [`pathlib.Path`, `pathlib.Path`]
        The log folder and the frames folder.
    """
    return _make_night(tmp_path / "real")


def test_row_event_maps_guide_rows_settles_and_dropped_stars() -> None:
    """A dropped row is a StarLost event; other rows map as before."""
    step = '5,14.4,"Mount",0.2,0.5,0.0,0.5,0.0,0.1,0,W,145,N,,,74771,193.35,0'
    drop = '6,17.4,"DROP",0.0,0.0,0.0,0.0,0.0,0.0,0,,0,,,,0,0.00,7'
    assert guide_log_replay.row_event(step)["Event"] == "GuideStep"
    lost = guide_log_replay.row_event(drop)
    assert lost["Event"] == "StarLost"
    assert lost["ErrorCode"] == 7
    assert guide_log_replay.row_event("INFO: SETTLING STATE CHANGE, Settling started") == {
        "Event": "SettleBegin"
    }
    assert guide_log_replay.row_event("Pixel scale = 6.39 arc-sec/px") is None


def test_timed_lines_give_every_line_a_clock_time(made_up_night: tuple[Path, Path]) -> None:
    """Rows are section start plus time column; notes copy the row before."""
    logs, _ = made_up_night
    lines = guide_log_replay.timed_lines(logs / "guide_log-2026-10-03T20-00-00.txt")
    by_text = {line: moment for moment, line, _ in lines}
    assert by_text["INFO: SETTLING STATE CHANGE, Settling started"] == LOCAL_START + timedelta(seconds=999)
    row = next(line for line in by_text if line.startswith("1000,"))
    assert by_text[row] == LOCAL_START + timedelta(seconds=1000)


def test_window_is_late_busy_and_split_at_the_right_time(
    made_up_night: tuple[Path, Path],
) -> None:
    """Earlier frames are preload; window frames are live, with offsets."""
    logs, frames = made_up_night
    plan = night_replay.build_plan(
        window_seconds=360.0,
        preload_frames=4,
        log_directory=logs,
        frames_directory=frames,
    )
    start = datetime.fromisoformat(plan["window"]["start_utc"])
    end = datetime.fromisoformat(plan["window"]["end_utc"])
    assert (end - start).total_seconds() == pytest.approx(360.0)
    assert plan["clock_check_seconds"] == pytest.approx(0.0, abs=1.0)
    window_frames = plan["_frames"]
    assert window_frames
    assert all(start <= frame.saved_utc < end for frame in window_frames)
    assert all(frame.saved_utc < start for frame in plan["_preload"])
    assert 0 < len(plan["_preload"]) <= 4
    offsets = [offset for offset, _ in plan["guide"]["events"]]
    assert offsets == sorted(offsets)
    assert 0 <= offsets[0] < 5
    assert offsets[-1] < 360.0


def test_speed_packs_more_night_into_the_same_real_time(made_up_night: tuple[Path, Path]) -> None:
    """At speed 2 the window covers twice the night; offsets are halved."""
    logs, frames = made_up_night
    start = datetime(2026, 10, 4, 2, 8, 0)
    slow = night_replay.build_plan(
        speed=1.0, window_seconds=300.0, window_start=start, log_directory=logs, frames_directory=frames
    )
    fast = night_replay.build_plan(
        speed=2.0, window_seconds=300.0, window_start=start, log_directory=logs, frames_directory=frames
    )
    assert fast["window"]["night_seconds"] == pytest.approx(600.0)
    assert len(fast["guide"]["events"]) > len(slow["guide"]["events"])
    slow_second = slow["guide"]["events"][1][0]
    fast_second = fast["guide"]["events"][1][0]
    assert fast_second == pytest.approx(slow_second / 2.0, abs=0.01)


def test_stage_cuts_the_logs_at_the_window_and_copies_frames(
    made_up_night: tuple[Path, Path], tmp_path: Path
) -> None:
    """The stand-in logs end at the window start; frames are copies."""
    logs, frames = made_up_night
    plan = night_replay.build_plan(
        window_seconds=360.0, preload_frames=3, log_directory=logs, frames_directory=frames
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    written = night_replay.stage(sandbox, plan)
    guide_text = Path(written["paths"]["guide_log"]).read_text()
    analyze_text = Path(written["paths"]["analyze_log"]).read_text()
    assert len(guide_text.splitlines()) == written["guide"]["lines_before_window"]
    assert "AnalyzeStartTime" in analyze_text
    assert len(written["preload_frames"]) == 3
    for entry in written["preload_frames"]:
        copy = Path(written["paths"]["pictures"]) / entry["remote_relpath"]
        assert copy.exists()
        assert copy.stat().st_nlink == 1
        assert fits.getheader(copy)["ORIGIN"] == "ASTROMETRICS-SIMULATOR"
    for entry in written["window_frames"]:
        assert (Path(written["paths"]["holding"]) / entry["name"]).exists()
    for source in frames.rglob("*.fits"):
        assert fits.getheader(source).get("ORIGIN") != "ASTROMETRICS-SIMULATOR"
    facts = night_replay.summary(written)
    assert facts["frames_in_window"] == len(written["window_frames"])
    assert facts["guide_events_in_window"]["GuideStep"] > 0
    assert json.loads((sandbox / "replay" / "plan.json").read_text())["night"] == "2026-10-03"


def test_stage_refuses_the_real_library_folder() -> None:
    """A staging folder inside the user's real library is refused."""
    with pytest.raises(RuntimeError, match="Refusing"):
        night_replay._refuse_real_folder(Path.home() / "Astrometrics" / "Library" / "x")


def test_stage_refuses_the_folders_the_real_settings_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real settings' four library folders are refused."""
    config = tmp_path / "real.toml"
    config.write_text(
        f'["Image Library"]\npath = "{tmp_path / "lib"}"\nframes_path = "{tmp_path / "frames"}"\n'
        f'stacks_path = "{tmp_path / "stacks"}"\n["Wayfinding Library"]\npath = "{tmp_path / "wf"}"\n'
    )
    monkeypatch.setenv("ASTROMETRICS_REAL_CONFIG_PATH", str(config))
    for name in ("lib", "frames", "stacks", "wf"):
        with pytest.raises(RuntimeError, match="Refusing"):
            night_replay._refuse_real_folder(tmp_path / name / "inside")
    night_replay._refuse_real_folder(tmp_path / "somewhere_else")


def test_the_zone_table_and_the_log_parser_are_the_librarys(tmp_path: Path) -> None:
    """The replay uses the library's own Ekos zone table and parser."""
    from wayfindinglib.drivers.ekos import analyze_log_parser  # ruff: ignore[banned-api]

    assert not hasattr(night_replay, "ZONE_HOURS")
    table = analyze_log_parser.UTC_OFFSET_HOURS_BY_ABBREVIATION
    assert night_replay.UTC_OFFSET_HOURS_BY_ABBREVIATION is table
    path = tmp_path / "ekos-x.analyze"
    path.write_text("AnalyzeStartTime,2026-10-03 20:00:00.000,GMT\n")
    assert night_replay.parse_analyze(path)[1] == 0


def test_unknown_zone_in_the_analyze_log_is_refused(tmp_path: Path) -> None:
    """A zone name the planner does not know stops the plan."""
    path = tmp_path / "ekos-x.analyze"
    path.write_text("AnalyzeStartTime,2026-10-03 20:00:00.000,XYZ\n")
    with pytest.raises(ValueError, match="XYZ"):
        night_replay.parse_analyze(path)


def test_preload_helpers() -> None:
    """Coordinates match how targets hold them; targets come from the plan."""
    assert replay_preload._sexagesimal(22.61776, True) == "22:37:04"
    assert replay_preload._sexagesimal(-5.5, False) == "-05:30:00"
    assert replay_preload._sexagesimal(34.4, False) == "+34:24:00"
    plan = {
        "preload_frames": [{"target_id": "A", "ra_hours": 1.0, "dec_degrees": 2.0}],
        "window_frames": [{"target_id": "B", "ra_hours": 3.0, "dec_degrees": 4.0}],
    }
    assert replay_preload.night_targets(plan) == {"A": (1.0, 2.0), "B": (3.0, 4.0)}


def test_replay_profile_writes_the_same_settings_as_simulated(tmp_path: Path) -> None:
    """The replay profile points at stand-ins like the simulated one."""
    simulated = write_sandbox_config.write_sandbox_config(tmp_path / "a", "simulated", 45001, 45002)
    replay = write_sandbox_config.write_sandbox_config(tmp_path / "b", "replay", 45001, 45002)
    assert simulated.read_text().replace("/a/", "/x/") == replay.read_text().replace("/b/", "/x/")


def test_mock_phd2_plays_planned_events_after_the_start_file(tmp_path: Path) -> None:
    """In replay mode the server sends each event at its offset."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    start_file = tmp_path / "start"
    scheduled = [(0.2, {"Event": "GuideStep", "Frame": 1}), (0.6, {"Event": "StarLost", "Frame": 2})]
    server = mock_phd2.MockPhd2Server(port, [], tmp_path / "stats.json", scheduled, start_file)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    time.sleep(0.5)
    received = b""
    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.settimeout(0.5)
        time.sleep(0.5)
        assert b"GuideStep" not in client.recv(4096)
        start_file.write_text(str(time.time()))
        deadline = time.time() + 4
        while received.count(b"StarLost") < 1 and time.time() < deadline:
            try:
                received += client.recv(4096)
            except TimeoutError:
                continue
    server.stop()
    thread.join(timeout=3)
    names = [json.loads(line)["Event"] for line in received.decode().splitlines()]
    assert names.index("GuideStep") < names.index("StarLost")


def _bwrap_works() -> bool:
    """Say whether an unprivileged network namespace can be made here.

    Returns
    -------
    works : `bool`
        `True` if ``bwrap --unshare-net true`` succeeds.
    """
    if shutil.which("bwrap") is None:
        return False
    result = subprocess.run(
        ["bwrap", "--bind", "/", "/", "--dev-bind", "/dev", "/dev", "--unshare-net", "true"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.skipif(not _bwrap_works(), reason="bwrap --unshare-net does not work here")
def test_seal_leaves_only_loopback_and_loopback_still_works() -> None:
    """Inside the seal only lo exists, and a local socket pair works."""
    guard = TPM_DIR / "seal_guard.py"
    code = (
        "import socket, subprocess, sys\n"
        f"r = subprocess.run([sys.executable, {str(guard)!r}, 'sealed'])\n"
        "server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(1)\n"
        "client = socket.create_connection(server.getsockname(), timeout=2)\n"
        "sys.exit(r.returncode)\n"
    )
    command = [
        "bwrap", "--bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
        "--unshare-net", "--unshare-pid", "--die-with-parent", sys.executable, "-c", code,
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not _bwrap_works(), reason="bwrap --unshare-net does not work here")
def test_seal_guard_fails_outside_the_seal_when_a_second_interface_exists() -> None:
    """The guard's interface list is read from the namespace, not from /sys."""
    names = [
        line.split(":", 1)[0].strip()
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]
        if ":" in line
    ]
    guard = TPM_DIR / "seal_guard.py"
    result = subprocess.run(
        [sys.executable, str(guard), "sealed"], capture_output=True, text=True, check=False
    )
    if names == ["lo"]:
        assert result.returncode == 0
    else:
        assert result.returncode == 1
        assert "not sealed" in result.stderr
