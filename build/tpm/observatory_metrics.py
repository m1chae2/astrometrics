"""Purpose: Measure what the simulated observatory did during a TPM run.

Description: In the simulated profile the exposure driver
(`exposure_driver.py`)
and the stand-in PHD2 (`mock_phd2.py`) each write a statistics file. This
script compares them with what ended up in the throwaway library and reduces
the result to a few numbers, written to ``observatory-metrics.json``:

- ``frames_exposed``: frames the simulated camera saved in the stand-in
  telescope computer's picture folder.
- ``frames_auto_downloaded``: how many of those frames the app copied into the
  library by itself.
- ``frame_to_library_seconds``: for each copied frame, the time from the file
  appearing in the stand-in folder until its copy was made in the library
  (the copy's inode change time minus the source's write time).
- ``guide_events_received``: guide steps the stand-in PHD2 sent to the app.
- ``guide_log_lines_pulled``: lines of guide log the app pulled with its
  log sync.
- ``ekos_log_lines_pulled``: lines of the longest Ekos analyze log the app
  pulled (only the replay profile writes one; `None` otherwise).

The ``wait`` command pauses the run until the expected frames have arrived (or
a time limit passes), so a short run still shows the download working. The
simulated profile expects one frame. The replay profile expects every frame
of its window and waits for its driver to finish (``--frames N
--require-finished``).

Usage::

    .venv/bin/python build/tpm/observatory_metrics.py wait \
        --sandbox <folder> --sim-stats <file> [--timeout 240] \
        [--frames 1] [--require-finished]
    .venv/bin/python build/tpm/observatory_metrics.py summarize \
        --sandbox <folder> --sim-stats <file> \
        --phd2-stats <file> --out <file>
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path


def _read_json(path: Path) -> dict:
    """Read a JSON file.

    A missing or half-written file gives an empty dict.

    Parameters
    ----------
    path : `pathlib.Path`
        The file.

    Returns
    -------
    data : `dict`
        The contents, or `{}`.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}


def library_copies(sandbox: Path, names: list[str]) -> dict[str, Path]:
    """Find the library copy of each named frame.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.
    names : `list` [`str`]
        File names the camera saved.

    Returns
    -------
    copies : `dict` [`str`, `pathlib.Path`]
        The library path of each name that has a copy. With a library copy
        (the simulation mode) it is the path in the overlay's upper folder,
        so a real file of the same name is not counted.
    """
    wanted = set(names)
    found: dict[str, Path] = {}
    # A library copy shows the real files too, and one can share the new
    # frame's name (M_13_Light_001.fits). Only the overlay's upper folder
    # holds what the run wrote.
    upper = sandbox / "overlay" / "upper"
    for path in (upper if upper.is_dir() else sandbox / "library" / "frames").rglob("*.fits"):
        if path.name in wanted and path.name not in found:
            found[path.name] = path
    return found


def summarize(sandbox: Path, sim_stats: Path, phd2_stats: Path) -> dict:
    """Reduce the statistics files and the library to the metrics.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.
    sim_stats : `pathlib.Path`
        The exposure driver's statistics file.
    phd2_stats : `pathlib.Path`
        The stand-in PHD2's statistics file.

    Returns
    -------
    metrics : `dict`
        The numbers described in the module description.
    """
    simulation = _read_json(sim_stats)
    phd2 = _read_json(phd2_stats)
    frames = simulation.get("frames", [])
    copies = library_copies(sandbox, [frame["name"] for frame in frames])
    latencies = [
        copies[frame["name"]].stat().st_ctime - frame["appeared_at"]
        for frame in frames
        if frame["name"] in copies
    ]
    pulled_lines = 0
    for log in (sandbox / "wayfinding").rglob("guide_log*.txt"):
        pulled_lines = max(pulled_lines, len(log.read_text(encoding="utf-8", errors="replace").splitlines()))
    analyze_lines = None
    for log in (sandbox / "wayfinding").rglob("ekos-*.analyze"):
        count = len(log.read_text(encoding="utf-8", errors="replace").splitlines())
        analyze_lines = max(analyze_lines or 0, count)
    return {
        "exposure_seconds": simulation.get("exposure_seconds"),
        "frames_exposed": len(frames),
        "frames_auto_downloaded": len(copies),
        "frame_to_library_seconds": statistics.median(latencies) if latencies else None,
        "frame_to_library_seconds_all": latencies,
        "guide_events_received": phd2.get("guide_steps_sent"),
        "phd2_clients_connected_total": phd2.get("clients_connected_total"),
        "guide_log_lines_written": simulation.get("guide_log_lines_written"),
        "guide_log_lines_pulled": pulled_lines,
        "ekos_log_lines_pulled": analyze_lines,
        "analyze_lines_written": simulation.get("analyze_lines_written"),
        "driver_finished": simulation.get("finished"),
    }


def wait_for_frame(
    sandbox: Path,
    sim_stats: Path,
    timeout_seconds: float,
    minimum_frames: int = 1,
    require_finished: bool = False,
) -> bool:
    """Wait until the app has copied the expected frames into the library.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.
    sim_stats : `pathlib.Path`
        The exposure (or replay) driver's statistics file.
    timeout_seconds : `float`
        The longest time to wait.
    minimum_frames : `int`, optional
        How many frames must have a library copy (default 1).
    require_finished : `bool`, optional
        Also wait for the driver to report that it played everything (replay).

    Returns
    -------
    arrived : `bool`
        `True` once the expected copies exist.
    """
    deadline = time.time() + timeout_seconds
    last_report = 0.0
    while time.time() < deadline:
        stats = _read_json(sim_stats)
        frames = stats.get("frames", [])
        copies = library_copies(sandbox, [frame["name"] for frame in frames])
        finished = bool(stats.get("finished")) or not require_finished
        if len(copies) >= minimum_frames and finished:
            return True
        if time.time() - last_report > 15:
            print(
                f"  waiting for the frames to reach the library ({len(copies)} of {minimum_frames} copied, "
                f"{len(frames)} appeared so far)"
            )
            last_report = time.time()
        time.sleep(2)
    return False


def main() -> int:
    """Run one command from the command line.

    Returns
    -------
    status : `int`
        0 on success, 1 if the wait timed out.
    """
    parser = argparse.ArgumentParser(description="Metrics of the simulated observatory.")
    commands = parser.add_subparsers(dest="command", required=True)
    wait = commands.add_parser("wait")
    wait.add_argument("--sandbox", type=Path, required=True)
    wait.add_argument("--sim-stats", type=Path, required=True)
    wait.add_argument("--timeout", type=float, default=240.0)
    wait.add_argument("--frames", type=int, default=1)
    wait.add_argument("--require-finished", action="store_true")
    summary = commands.add_parser("summarize")
    summary.add_argument("--sandbox", type=Path, required=True)
    summary.add_argument("--sim-stats", type=Path, required=True)
    summary.add_argument("--phd2-stats", type=Path, required=True)
    summary.add_argument("--out", type=Path, required=True)
    summary.add_argument(
        "--replay-summary", type=Path, default=None, help="replay facts to add under 'replay'"
    )
    arguments = parser.parse_args()
    if arguments.command == "wait":
        if wait_for_frame(
            arguments.sandbox,
            arguments.sim_stats,
            arguments.timeout,
            arguments.frames,
            arguments.require_finished,
        ):
            print("The expected frames reached the library.")
            return 0
        print("ERROR: the expected frames did not reach the library in time.", file=sys.stderr)
        return 1
    metrics = summarize(arguments.sandbox, arguments.sim_stats, arguments.phd2_stats)
    if arguments.replay_summary is not None:
        metrics["replay"] = _read_json(arguments.replay_summary)
    arguments.out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(
        json.dumps({
            key: value
            for key, value in metrics.items()
            if key not in ("frame_to_library_seconds_all", "replay")
        })
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
