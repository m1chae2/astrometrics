"""Purpose: Plan and stage the replay of one real night for the TPM run.

Description: The ``replay`` observatory profile plays the last minutes of a
real night through the app, so the numbers come from the kind of traffic a
real night makes (frames arriving, guide steps, dithers, lost stars, the log
pulls). The night is fixed (2026-10-03, the night the guide log fixture
was cut from), and the run is short, so this module does two jobs:

1. ``plan`` reads the night's Ekos analyze log, guide log and light frames
   (all read only, from the user's real folders) and decides what happens
   when. It picks the busiest few minutes late in the night (the window).
   Everything before the window is the end-of-night state: it is loaded
   before the timed run starts, as if the app had already downloaded it.
   Everything inside the window is replayed live at the original spacing
   (divided by the replay speed).
2. ``stage`` copies the files the plan needs into the throwaway folder:
   a spread of the earlier frames and the two logs cut at the window's
   start (into the stand-in telescope computer's folders), and the
   window's frames (into a holding folder the replay driver copies from).

Clocks. The analyze log counts seconds from a local start time that it
names (for example ``MDT``). The guide log writes local clock times without
a zone name. The frame headers use UTC. This module converts everything to
UTC with the zone the analyze log names. It reports how far the log's
"frame saved" times are from the headers' end-of-exposure times, so a wrong
zone shows up as an hour-sized number.

Safety: the user's real files are only read (opened for reading, never
renamed, touched or linked). Frames are copied, never hard-linked, so a
later write in the throwaway folder cannot reach a real file. All output
goes inside the throwaway folder, and the script refuses an output folder
that is inside the user's real library or state folders.

Usage::

    python -m simulator.night_replay stage <sandbox> \
        [--night 2026-10-03] [--speed 1] [--window-seconds 300] \
        [--preload-frames 16] [--window-start 2026-10-04T09:19:00]
    python -m simulator.night_replay choose --night 2026-10-03
    python -m simulator.night_replay report <sandbox> \
        --ingest <file> \
        --setup-seconds S --out <file>
"""

import argparse
import json
import re
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from simulator.guide_log_replay import timed_lines
from simulator.observatory_guard import real_protected_paths
from simulator.real_folders import real_folders
from wayfindinglib.logs import SavedCapture, ekos_utc_offset_hours, read_ekos_captures

#: The one fixed night (the date its guide log begins, local time).
DEFAULT_NIGHT = "2026-10-03"
#: Real seconds of live replay (a few minutes, about as long as a normal run).
DEFAULT_WINDOW_SECONDS = 300.0
#: How many earlier frames to load before the run (a spread over the night).
#: Each one is measured by the app's real ingest, which takes about 11 s per
#: frame, so this sets the setup time. Design estimate.
DEFAULT_PRELOAD_FRAMES = 16
#: How often (night seconds) the window search tries a new start.
WINDOW_STEP_SECONDS = 15.0
#: Scoring weights for a candidate window: a frame arriving, a settle after a
#: dither, and a lost-star row (at most `STAR_LOST_CAP` of them count).
FRAME_WEIGHT = 3.0
SETTLE_WEIGHT = 4.0
STAR_LOST_WEIGHT = 1.0
STAR_LOST_CAP = 4
#: The search looks only at the last part of the night (a fraction of its
#: span).
LATE_NIGHT_FRACTION = 0.4
#: Real seconds before a frame appears that the stand-in camera starts its
#: short exposure, so the app sees "Exposing" and then "Idle" (the signal that
#: starts its automatic download) at the moment the file arrives.
CAMERA_LEAD_SECONDS = 6.0
#: The folders under the user's home that hold real data. Output never goes
#: there, whatever the settings say (the real settings' own folders are
#: refused too, see `_refuse_real_folder`).
_REAL_FOLDERS = ("Astrometrics", ".local/state/astrometrics", ".local/share/astrometrics")
#: The header keyword and value that mark a frame copied for a replay, so a
#: copy that leaks out of the simulator cannot pass for a real frame.
SIMULATOR_STAMP_KEYWORD = "ORIGIN"
SIMULATOR_STAMP_VALUE = "ASTROMETRICS-SIMULATOR"
SIMULATOR_STAMP_COMMENT = "simulator copy, not real"
_CAPTURE_PATH_PARTS = re.compile(r"/Pictures/(.+)$")


@dataclass(frozen=True)
class CapturedFrame:
    """One light frame the night's analyze log says the camera saved.

    Attributes
    ----------
    name : `str`
        File name.
    remote_relpath : `str`
        Path under the telescope computer's ``Pictures`` folder, such as
        ``NGC_7331/Light/Luminance/NGC_7331_Light_Luminance_133.fits``.
    target_id : `str`
        The target's name (the ``OBJECT`` header).
    saved_utc : `datetime.datetime`
        When the log says the file was saved (naive UTC).
    exposure_seconds : `float`
        Exposure length.
    ra_hours, dec_degrees : `float`
        Where the mount pointed (from the header's ``RA`` in degrees and
        ``DEC``).
    source : `pathlib.Path`
        The real file in the user's library (read only).
    """

    name: str
    remote_relpath: str
    target_id: str
    saved_utc: datetime
    exposure_seconds: float
    ra_hours: float
    dec_degrees: float
    source: Path


def default_sources() -> tuple[Path, Path]:
    """Find the user's real log folder and frames folder.

    Returns
    -------
    log_directory, frames_directory : `pathlib.Path`
        ``ekos_logs`` inside the real wayfinding library, and the real
        image library's frames folder. The app's simulator world keeps the
        real folders in ``ASTROMETRICS_REAL_*`` variables, which
        `simulator.real_folders.real_folders` honours.
    """
    folders = real_folders()
    return folders.wayfinding / "ekos_logs", folders.frames


def list_nights(log_directory: Path | None = None, frames_directory: Path | None = None) -> list[dict]:
    """List the past nights that can be replayed, newest first.

    A night qualifies when it has a guide log, an Ekos analyze log and at
    least one saved light frame that is still in the library. The real files
    are only read.

    Parameters
    ----------
    log_directory, frames_directory : `pathlib.Path`, optional
        Where the real logs and library are. Default: the user's real ones.

    Returns
    -------
    nights : `list` [`dict`]
        ``{"night": "YYYY-MM-DD", "frames": int}``, newest first.
    """
    default_logs, default_frames = default_sources()
    logs = log_directory or default_logs
    frames_root = frames_directory or default_frames
    guide_nights = {
        path.name[len("guide_log-") : len("guide_log-") + 10] for path in logs.glob("guide_log-*T*.txt")
    }
    analyze_files: dict[str, list[Path]] = {}
    for path in logs.glob("ekos-*T*.analyze"):
        analyze_files.setdefault(path.name[len("ekos-") : len("ekos-") + 10], []).append(path)
    candidates = sorted(guide_nights & set(analyze_files), reverse=True)
    if not candidates:
        return []
    library = index_library_frames(frames_root)
    nights = []
    for night in candidates:
        names = set()
        for path in analyze_files[night]:
            captures = read_ekos_captures(path)
            if captures is not None:
                names.update(Path(capture.file_path).name for capture in captures)
        count = sum(1 for name in names if name in library)
        if count:
            nights.append({"night": night, "frames": count})
    return nights


def parse_analyze(path: Path) -> tuple[datetime, int, list[tuple[float | None, str]]]:
    """Read an Ekos analyze log.

    Parameters
    ----------
    path : `pathlib.Path`
        The ``ekos-*.analyze`` file.

    Returns
    -------
    start_local : `datetime.datetime`
        The local start time the log names.
    zone_hours : `int`
        The zone's offset from UTC in hours.
    rows : `list` [`tuple`]
        ``(seconds, line)`` in file order. `seconds` is the time column, or
        `None` for a line that has none (comments, the start line).

    Raises
    ------
    ValueError
        If the log has no ``AnalyzeStartTime`` line or names an unknown zone.
    """
    start_local: datetime | None = None
    zone_hours = 0
    rows: list[tuple[float | None, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split(",")
        if fields[0] == "AnalyzeStartTime":
            start_local = datetime.strptime(fields[1], "%Y-%m-%d %H:%M:%S.%f")
            zone = fields[2].strip().upper() if len(fields) > 2 else "UTC"
            offset_hours = ekos_utc_offset_hours(zone)
            if offset_hours is None:
                raise ValueError(f"Unknown time zone '{zone}' in {path.name}.")
            zone_hours = offset_hours
            rows.append((None, line))
            continue
        try:
            rows.append((float(fields[1]), line))
        except IndexError, ValueError:
            rows.append((None, line))
    if start_local is None:
        raise ValueError(f"{path.name} has no AnalyzeStartTime line.")
    return start_local, zone_hours, rows


def index_library_frames(frames_directory: Path) -> dict[str, list[Path]]:
    """List the light frames of a library by file name.

    Parameters
    ----------
    frames_directory : `pathlib.Path`
        The library's frames folder (the one with ``lights`` inside).

    Returns
    -------
    by_name : `dict` [`str`, `list` [`pathlib.Path`]]
        Every ``*.fits`` under ``lights``, keyed by file name.
    """
    by_name: dict[str, list[Path]] = {}
    for path in (frames_directory / "lights").rglob("*.fits"):
        by_name.setdefault(path.name, []).append(path)
    return by_name


def captured_frames(captures: list[SavedCapture], library: dict[str, list[Path]]) -> list[CapturedFrame]:
    """Match the analyze log's saved frames with files in the library.

    Parameters
    ----------
    captures : `list` [`SavedCapture`]
        The captures the library's Ekos log parser read from the analyze
        log (`read_ekos_captures`). Their times are already in UTC.
    library : `dict`
        Output of `index_library_frames`.

    Returns
    -------
    frames : `list` [`CapturedFrame`]
        Frames whose file is in the library, by time saved.
    """
    from astropy.io import fits

    frames: list[CapturedFrame] = []
    for capture in captures:
        name = Path(capture.file_path).name
        candidates = library.get(name, [])
        part = _CAPTURE_PATH_PARTS.search(capture.file_path)
        if not candidates or not part:
            continue
        header = fits.getheader(candidates[0], memmap=False)
        if header.get("RA") is None or header.get("DEC") is None:
            continue
        frames.append(
            CapturedFrame(
                name=name,
                remote_relpath=part.group(1),
                target_id=str(header.get("OBJECT", "")),
                saved_utc=datetime.fromtimestamp(capture.completed_at, UTC).replace(tzinfo=None),
                exposure_seconds=capture.exposure_seconds,
                ra_hours=float(header["RA"]) / 15.0,
                dec_degrees=float(header["DEC"]),
                source=candidates[0],
            )
        )
    return sorted(frames, key=lambda frame: frame.saved_utc)


def clock_check_seconds(frames: list[CapturedFrame]) -> float | None:
    """Compare the log's saved times with the headers' end-of-exposure times.

    Parameters
    ----------
    frames : `list` [`CapturedFrame`]
        The matched frames.

    Returns
    -------
    median_seconds : `float` or `None`
        Median of (saved time minus header ``DATE-OBS`` minus exposure). A few
        seconds is normal (download and write). About 3600 s means the zone
        is wrong.
    """
    from astropy.io import fits

    differences = []
    for frame in frames[:: max(1, len(frames) // 20)]:
        header = fits.getheader(frame.source, memmap=False)
        observed = datetime.fromisoformat(str(header["DATE-OBS"]).split("+")[0])
        differences.append((frame.saved_utc - observed).total_seconds() - frame.exposure_seconds)
    return statistics.median(differences) if differences else None


def choose_window(
    frames: list[CapturedFrame],
    settle_times: list[datetime],
    lost_times: list[datetime],
    span: tuple[datetime, datetime],
    length_seconds: float,
) -> datetime:
    """Find the busiest window late in the night.

    The score counts frames arriving, settles after dithers and lost-star
    rows. Ties go to the later start. The window must end before the last
    guide row, so the live guiding in it is real.

    Parameters
    ----------
    frames : `list` [`CapturedFrame`]
        The night's frames.
    settle_times, lost_times : `list` [`datetime.datetime`]
        UTC times of the settle starts and of the lost-star rows.
    span : `tuple` [`datetime.datetime`, `datetime.datetime`]
        The first and last guide row (UTC).
    length_seconds : `float`
        Night seconds the window covers.

    Returns
    -------
    start : `datetime.datetime`
        The window start (UTC).
    """
    first, last = span
    earliest = (first + (last - first) * (1.0 - LATE_NIGHT_FRACTION)).replace(microsecond=0)
    latest = last - timedelta(seconds=length_seconds)
    best_score, best_start = -1.0, earliest
    start = earliest
    while start <= latest:
        end = start + timedelta(seconds=length_seconds)
        arrivals = sum(start <= frame.saved_utc < end for frame in frames)
        settles = sum(start <= moment < end for moment in settle_times)
        lost = min(STAR_LOST_CAP, sum(start <= moment < end for moment in lost_times))
        score = FRAME_WEIGHT * arrivals + SETTLE_WEIGHT * settles + STAR_LOST_WEIGHT * lost
        if score >= best_score:
            best_score, best_start = score, start
        start += timedelta(seconds=WINDOW_STEP_SECONDS)
    return best_start


def _refuse_real_folder(path: Path) -> None:
    """Refuse an output folder inside, or around, the user's real folders.

    The fixed folders under the user's home are refused, and so are the
    library, frames, stacks and wayfinding folders the real settings name.

    Raises
    ------
    RuntimeError
        If `path` is inside one of the real folders, or holds one.
    """
    resolved = path.resolve()
    real_folders_to_refuse = [(Path.home() / name).resolve() for name in _REAL_FOLDERS]
    real_folders_to_refuse.extend(real_protected_paths())
    for real in real_folders_to_refuse:
        if resolved == real or real in resolved.parents or resolved in real.parents:
            raise RuntimeError(
                f"Refusing to write in {resolved}: it is inside, or holds, the real folder {real}."
            )


def build_plan(
    night: str = DEFAULT_NIGHT,
    speed: float = 1.0,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    preload_frames: int = DEFAULT_PRELOAD_FRAMES,
    window_start: datetime | None = None,
    log_directory: Path | None = None,
    frames_directory: Path | None = None,
) -> dict:
    """Read the night's real files and decide what is preloaded or replayed.

    Parameters
    ----------
    night : `str`, optional
        The date the night's logs begin (local), ``YYYY-MM-DD``.
    speed : `float`, optional
        Replay speed. At 2 the window covers twice as much night in the same
        real time.
    window_seconds : `float`, optional
        Real seconds of live replay.
    preload_frames : `int`, optional
        How many earlier frames to load before the run.
    window_start : `datetime.datetime`, optional
        Window start (naive UTC). Chosen automatically when omitted.
    log_directory, frames_directory : `pathlib.Path`, optional
        Where the real logs and library are. Default: the user's real ones.

    Returns
    -------
    plan : `dict`
        Everything the stager, the stand-in PHD2 and the driver need. Frames
        are
        `CapturedFrame` objects under ``_frames`` and ``_preload`` (dropped
        before the plan is written out).

    Raises
    ------
    FileNotFoundError
        If the night's logs are missing.
    ValueError
        If the night has no frames in the library or no guide rows in the
        window.
    """
    default_logs, default_frames = default_sources()
    logs = log_directory or default_logs
    frames_root = frames_directory or default_frames
    guide_candidates = sorted(logs.glob(f"guide_log-{night}T*.txt"), key=lambda path: path.stat().st_size)
    analyze_candidates = sorted(logs.glob(f"ekos-{night}T*.analyze"), key=lambda path: path.stat().st_size)
    if not guide_candidates or not analyze_candidates:
        raise FileNotFoundError(f"No guide log or Ekos analyze log for the night {night} in {logs}.")
    guide_path, analyze_path = guide_candidates[-1], analyze_candidates[-1]
    start_local, zone_hours, analyze_rows = parse_analyze(analyze_path)
    offset = timedelta(hours=zone_hours)
    captures = read_ekos_captures(analyze_path) or []
    all_frames = captured_frames(captures, index_library_frames(frames_root))
    if not all_frames:
        raise ValueError(f"No frame of the night {night} was found in the library under {frames_root}.")
    guide_lines = [(moment - offset, line, event) for moment, line, event in timed_lines(guide_path)]
    guide_times = [
        moment for moment, _, event in guide_lines if event and event["Event"] in ("GuideStep", "StarLost")
    ]
    span = (guide_times[0], guide_times[-1])
    length = window_seconds * speed
    if window_start is None:
        settles = [m for m, _, event in guide_lines if event and event["Event"] == "SettleBegin"]
        lost = [m for m, _, event in guide_lines if event and event["Event"] == "StarLost"]
        window_start = choose_window(all_frames, settles, lost, span, length)
    window_end = window_start + timedelta(seconds=length)

    def real_offset(moment: datetime) -> float:
        """Return real seconds from the window start to `moment`.

        Returns
        -------
        seconds : `float`
            The time since the window start, divided by the replay speed.
        """
        return (moment - window_start).total_seconds() / speed

    earlier = [frame for frame in all_frames if frame.saved_utc < window_start]
    in_window = [frame for frame in all_frames if window_start <= frame.saved_utc < window_end]
    if not in_window:
        raise ValueError(f"The window {window_start} to {window_end} holds no frame.")
    count = min(preload_frames, len(earlier))
    picks = (
        sorted({round(index * (len(earlier) - 1) / max(1, count - 1)) for index in range(count)})
        if count
        else []
    )
    preload = [earlier[index] for index in picks]
    window_guide = [(m, line, event) for m, line, event in guide_lines if window_start <= m < window_end]
    if not any(event for _, _, event in window_guide):
        raise ValueError("The window holds no guide step.")
    analyze_window = []
    analyze_before = []
    for seconds, line in analyze_rows:
        moment = None if seconds is None else start_local + timedelta(seconds=seconds) - offset
        if moment is None or moment < window_start:
            analyze_before.append(line)
        elif moment < window_end:
            analyze_window.append((moment, line))
    guide_before = [line for m, line, _ in guide_lines if m < window_start]
    return {
        "night": night,
        "speed": speed,
        "zone_hours": zone_hours,
        "window": {
            "start_utc": window_start.isoformat(),
            "end_utc": window_end.isoformat(),
            "night_seconds": length,
            "real_seconds": window_seconds,
        },
        "guide_log_name": guide_path.name,
        "analyze_log_name": analyze_path.name,
        "clock_check_seconds": clock_check_seconds(all_frames),
        "night_frames_total": len(all_frames),
        "frames_before_window": len(earlier),
        "guide": {
            "events": [[real_offset(m), event] for m, _, event in window_guide if event],
            "lines": [[real_offset(m), line] for m, line, _ in window_guide],
            "lines_before_window": len(guide_before),
        },
        "analyze": {
            "lines": [[real_offset(m), line] for m, line in analyze_window],
            "lines_before_window": len(analyze_before),
        },
        "_guide_before": guide_before,
        "_analyze_before": analyze_before,
        "_preload": preload,
        "_frames": in_window,
        "_real_offset": real_offset,
    }


def _frame_entry(frame: CapturedFrame, offset_seconds: float | None) -> dict:
    """Describe a frame for the plan file.

    Returns
    -------
    entry : `dict`
        The frame's name, target, path on the telescope computer, exposure,
        pointing and
        (for a window frame) the real offset at which it appears.
    """
    return {
        "name": frame.name,
        "remote_relpath": frame.remote_relpath,
        "target_id": frame.target_id,
        "exposure_seconds": frame.exposure_seconds,
        "ra_hours": frame.ra_hours,
        "dec_degrees": frame.dec_degrees,
        "saved_utc": frame.saved_utc.isoformat(),
        "offset": offset_seconds,
    }


def copy_frame(source: Path, destination: Path) -> None:
    """Copy one real frame and stamp the copy as a simulator frame.

    The real file is only read. The stamp is a header keyword
    (``ORIGIN = 'ASTROMETRICS-SIMULATOR'``) written in the copy, so a copy
    that leaks into a real library can be found and cannot be counted as a
    real frame.

    Parameters
    ----------
    source : `pathlib.Path`
        The real frame (read only).
    destination : `pathlib.Path`
        Where the stamped copy goes.
    """
    from astropy.io import fits

    shutil.copyfile(source, destination)
    with fits.open(destination, mode="update", memmap=False) as hdul:
        hdul[0].header[SIMULATOR_STAMP_KEYWORD] = (SIMULATOR_STAMP_VALUE, SIMULATOR_STAMP_COMMENT)
        hdul.flush()


def stage(sandbox: Path, plan: dict) -> dict:
    """Copy what the plan needs into the throwaway folder.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.
    plan : `dict`
        Output of `build_plan`.

    Returns
    -------
    written : `dict`
        The plan as written to ``<sandbox>/replay/plan.json`` (no private
        keys).
    """
    _refuse_real_folder(sandbox)
    home = sandbox / "standin" / "home"
    pictures = home / "Pictures"
    holding = sandbox / "replay" / "window_frames"
    holding.mkdir(parents=True, exist_ok=True)
    guide_directory = home / ".local" / "share" / "kstars" / "guidelogs"
    analyze_directory = home / ".local" / "share" / "kstars" / "analyze"
    guide_directory.mkdir(parents=True, exist_ok=True)
    analyze_directory.mkdir(parents=True, exist_ok=True)
    started = time.time()
    preload: list[CapturedFrame] = plan["_preload"]
    in_window: list[CapturedFrame] = plan["_frames"]
    real_offset = plan["_real_offset"]
    for frame in preload:
        destination = pictures / frame.remote_relpath
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_frame(frame.source, destination)
    for frame in in_window:
        copy_frame(frame.source, holding / frame.name)
    (guide_directory / plan["guide_log_name"]).write_text(
        "\n".join(plan["_guide_before"]) + "\n", encoding="utf-8"
    )
    (analyze_directory / plan["analyze_log_name"]).write_text(
        "\n".join(plan["_analyze_before"]) + "\n", encoding="utf-8"
    )
    written = {key: value for key, value in plan.items() if not key.startswith("_")}
    written["preload_frames"] = [_frame_entry(frame, None) for frame in preload]
    written["window_frames"] = [_frame_entry(frame, real_offset(frame.saved_utc)) for frame in in_window]
    written["paths"] = {
        "pictures": str(pictures),
        "guide_log": str(guide_directory / plan["guide_log_name"]),
        "analyze_log": str(analyze_directory / plan["analyze_log_name"]),
        "holding": str(holding),
    }
    written["stage_seconds"] = round(time.time() - started, 1)
    (sandbox / "replay" / "plan.json").write_text(json.dumps(written, indent=1), encoding="utf-8")
    return written


def summary(written: dict) -> dict:
    """Reduce a written plan to the facts that go into ``tpm.json``.

    Parameters
    ----------
    written : `dict`
        Output of `stage`.

    Returns
    -------
    facts : `dict`
        The night, the window, the frame and guide-event counts and the speed.
    """
    events = written["guide"]["events"]
    kinds: dict[str, int] = {}
    for _, event in events:
        kinds[event["Event"]] = kinds.get(event["Event"], 0) + 1
    return {
        "night": written["night"],
        "speed": written["speed"],
        "window": written["window"],
        "night_frames_total": written["night_frames_total"],
        "frames_before_window": written["frames_before_window"],
        "frames_preloaded": len(written["preload_frames"]),
        "frames_in_window": len(written["window_frames"]),
        "window_frame_names": [frame["name"] for frame in written["window_frames"]],
        "guide_events_in_window": kinds,
        "guide_lines_before_window": written["guide"]["lines_before_window"],
        "guide_lines_in_window": len(written["guide"]["lines"]),
        "analyze_lines_before_window": written["analyze"]["lines_before_window"],
        "analyze_lines_in_window": len(written["analyze"]["lines"]),
        "clock_check_seconds": written["clock_check_seconds"],
        "stage_seconds": written["stage_seconds"],
    }


def report(sandbox: Path, ingest_path: Path, setup_seconds: float) -> dict:
    """Make the replay facts of a finished run for ``tpm.json``.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.
    ingest_path : `pathlib.Path`
        The ``ingest.json`` that ``replay_preload.py ingest`` wrote.
    setup_seconds : `float`
        Seconds from the start of staging to the start of the timed run.

    Returns
    -------
    facts : `dict`
        `summary` of the plan, the ingest result and the setup time.
    """
    written = json.loads((sandbox / "replay" / "plan.json").read_text())
    ingest = json.loads(ingest_path.read_text()) if ingest_path.exists() else {}
    return {
        **summary(written),
        "setup_seconds": setup_seconds,
        "ingest_seconds": ingest.get("seconds"),
        "frames_in_library_after_ingest": ingest.get("frames_in_library"),
    }


def main() -> int:
    """Run one command from the command line.

    Returns
    -------
    status : `int`
        0 on success, 1 when the night's data cannot be found.
    """
    parser = argparse.ArgumentParser(description="Plan and stage the replay of a real night.")
    commands = parser.add_subparsers(dest="command", required=True)
    final = commands.add_parser("report")
    final.add_argument("sandbox", type=Path)
    final.add_argument("--ingest", type=Path, required=True)
    final.add_argument("--setup-seconds", type=float, required=True)
    final.add_argument("--out", type=Path, required=True)
    for name in ("stage", "choose"):
        command = commands.add_parser(name)
        if name == "stage":
            command.add_argument("sandbox", type=Path)
        command.add_argument("--night", default=DEFAULT_NIGHT)
        command.add_argument("--speed", type=float, default=1.0)
        command.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
        command.add_argument("--preload-frames", type=int, default=DEFAULT_PRELOAD_FRAMES)
        command.add_argument("--window-start", default=None, help="UTC, such as 2026-10-04T09:19:00")
    arguments = parser.parse_args()
    if arguments.command == "report":
        arguments.out.write_text(
            json.dumps(
                report(arguments.sandbox.resolve(), arguments.ingest, arguments.setup_seconds), indent=2
            ),
            encoding="utf-8",
        )
        return 0
    window_start = datetime.fromisoformat(arguments.window_start) if arguments.window_start else None
    try:
        plan = build_plan(
            arguments.night,
            arguments.speed,
            arguments.window_seconds,
            arguments.preload_frames,
            window_start,
        )
        if arguments.command == "choose":
            print(json.dumps({"window": plan["window"], "frames_in_window": len(plan["_frames"])}, indent=1))
            return 0
        written = stage(arguments.sandbox.resolve(), plan)
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary(written), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
