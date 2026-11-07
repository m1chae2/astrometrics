"""Purpose: Play the live window of a real night for the replay TPM run.

Description: ``night_replay.py`` has already put the end-of-night state into
the stand-in telescope computer's folders and written ``plan.json``. This
script plays the window that follows, in real time, so the app sees what it
would see during those minutes of a real night:

- The light frames of the window appear in the stand-in picture folder at the
  moment the real camera saved them (divided by the replay speed). They are
  the real files from the user's library, copied in whole, so the app's
  download, ingest and quality measurement work on real pixels.
- A few seconds before each frame appears, the INDI simulator mount is pointed
  at the frame's coordinates (from its header) and the simulated camera runs a
  short exposure. That only drives the camera status the app watches
  ("Exposing" and then "Idle" starts its automatic download). The simulated
  camera's own picture goes to a scratch folder and is never used.
- The window's lines of the guide log and of the Ekos analyze log are appended
  to the stand-in log files, one line at a time at their original times, so
  the app's log pulls see the logs grow.

The stand-in PHD2 (``mock_phd2.py``) plays the same window's guide events from
the same plan against the same start time.

The script connects the simulators, writes ``ready`` in the replay folder, then
waits for the ``start`` file (the start time as seconds since the epoch).

Safety: it talks only to 127.0.0.1 on the INDI port given on the command line,
reads only the sandbox's plan and holding folder, and writes only inside the
stand-in folder and the replay folder it is given.

Usage::

    python -m wayfindinglib.simulation.replay_driver --indi-port N \
        --standin <sandbox>/standin/home --replay <sandbox>/replay \
        --stats <out.json>

The script stops on SIGTERM or SIGINT.
"""

import argparse
import json
import shutil
import signal
import statistics
import sys
import threading
import time
from pathlib import Path

from wayfindinglib.simulation.exposure_driver import ExposureDriver
from wayfindinglib.simulation.night_replay import CAMERA_LEAD_SECONDS


class ReplayDriver:
    """Plays the window of a staged night against the stand-ins.

    Parameters
    ----------
    indi_port : `int`
        Port of the local INDI server.
    standin : `pathlib.Path`
        Home folder of the stand-in telescope computer.
    replay : `pathlib.Path`
        The replay folder with ``plan.json`` and the holding folder.
    stats_path : `pathlib.Path`
        Where to write the statistics.
    """

    def __init__(self, indi_port: int, standin: Path, replay: Path, stats_path: Path) -> None:
        """Read the plan; nothing runs yet."""
        self.replay = replay
        self.plan = json.loads((replay / "plan.json").read_text())
        self.standin = standin
        self.stats_path = stats_path
        self.stop_event = threading.Event()
        self.frames: list[dict] = []
        self.guide_lines = 0
        self.analyze_lines = 0
        self.finished = False
        self.start_time: float | None = None
        self._lock = threading.Lock()
        first = self.plan["window_frames"][0]
        self.indi = ExposureDriver(
            indi_port,
            standin,
            replay / "unused_guide_log",
            stats_path,
            CAMERA_LEAD_SECONDS,
            first["target_id"],
            first["ra_hours"],
            first["dec_degrees"],
        )

    def write_stats(self) -> None:
        """Write the statistics file (the simulated profile's shape)."""
        exposures = [frame["exposure_seconds"] for frame in self.plan["window_frames"]]
        with self._lock:
            stats = {
                "started_at": self.start_time,
                "target": self.plan["window_frames"][0]["target_id"],
                "exposure_seconds": statistics.median(exposures),
                "frames": list(self.frames),
                "frames_planned": len(self.plan["window_frames"]),
                "guide_log_lines_written": self.guide_lines,
                "analyze_lines_written": self.analyze_lines,
                "finished": self.finished,
            }
        temporary = self.stats_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        temporary.replace(self.stats_path)

    def _wait_until(self, moment: float) -> bool:
        """Wait until a wall-clock time.

        Parameters
        ----------
        moment : `float`
            Seconds since the epoch.

        Returns
        -------
        reached : `bool`
            `False` if the driver was stopped first.
        """
        delay = moment - time.time()
        return not (delay > 0 and self.stop_event.wait(delay)) and not self.stop_event.is_set()

    def _camera_loop(self) -> None:
        """Point the mount and start a short exposure before each frame."""
        if self.start_time is None:
            return
        for frame in self.plan["window_frames"]:
            if not self._wait_until(self.start_time + frame["offset"] - CAMERA_LEAD_SECONDS):
                return
            self.indi.point_mount(frame["ra_hours"], frame["dec_degrees"])
            self.indi._set(f"CCD Simulator.CCD_EXPOSURE.CCD_EXPOSURE_VALUE={CAMERA_LEAD_SECONDS}")

    def _file_loop(self) -> None:
        """Copy each frame in and append each log line at its own time."""
        if self.start_time is None:
            return
        pictures = Path(self.plan["paths"]["pictures"])
        holding = Path(self.plan["paths"]["holding"])
        events = [(frame["offset"], "frame", frame) for frame in self.plan["window_frames"]]
        events += [(offset, "guide", line) for offset, line in self.plan["guide"]["lines"]]
        events += [(offset, "analyze", line) for offset, line in self.plan["analyze"]["lines"]]
        events.sort(key=lambda event: event[0])
        guide_handle = Path(self.plan["paths"]["guide_log"]).open("a", encoding="utf-8")
        analyze_handle = Path(self.plan["paths"]["analyze_log"]).open("a", encoding="utf-8")
        try:
            for offset, kind, payload in events:
                if not self._wait_until(self.start_time + offset):
                    return
                if kind == "guide":
                    guide_handle.write(payload + "\n")
                    guide_handle.flush()
                    with self._lock:
                        self.guide_lines += 1
                elif kind == "analyze":
                    analyze_handle.write(payload + "\n")
                    analyze_handle.flush()
                    with self._lock:
                        self.analyze_lines += 1
                else:
                    self._place_frame(pictures, holding, payload)
        finally:
            guide_handle.close()
            analyze_handle.close()

    def _place_frame(self, pictures: Path, holding: Path, frame: dict) -> None:
        """Copy one frame into the stand-in picture folder in one step.

        The file is written under a hidden name and renamed, so the app's
        copy never sees a half-written file.

        Parameters
        ----------
        pictures : `pathlib.Path`
            The stand-in ``Pictures`` folder.
        holding : `pathlib.Path`
            The folder with the staged window frames.
        frame : `dict`
            The frame's plan entry.
        """
        destination = pictures / frame["remote_relpath"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.part")
        shutil.copyfile(holding / frame["name"], partial)
        partial.replace(destination)
        with self._lock:
            self.frames.append({
                "name": frame["name"],
                "appeared_at": destination.stat().st_mtime,
                "exposure_started_at": destination.stat().st_mtime - frame["exposure_seconds"],
                "size_bytes": destination.stat().st_size,
            })
        self.write_stats()

    def run(self) -> int:
        """Connect, wait for the start file, then play the window.

        Returns
        -------
        status : `int`
            1 if the simulators could not be reached, else 0.
        """
        if not self.indi.connect_devices(upload_directory=self.replay / "camera_scratch"):
            print("ERROR: the INDI simulators did not appear.", file=sys.stderr)
            return 1
        (self.replay / "ready").write_text("ready\n")
        start_file = self.replay / "start"
        while not self.stop_event.is_set():
            try:
                self.start_time = float(start_file.read_text().strip())
                break
            except OSError, ValueError:
                self.stop_event.wait(0.2)
        if self.start_time is None:
            return 0
        threads = [
            threading.Thread(target=self._camera_loop, daemon=True, name="ReplayCamera"),
            threading.Thread(target=self._file_loop, daemon=True, name="ReplayFiles"),
        ]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads) and not self.stop_event.is_set():
            self.stop_event.wait(1.0)
            self.write_stats()
        self.finished = not self.stop_event.is_set()
        self.write_stats()
        # Stay up (the run script ends this process) so the stand-in folders
        # stay as they are.
        self.stop_event.wait()
        return 0


def main() -> int:
    """Run the driver from the command line.

    Returns
    -------
    status : `int`
        The process exit status.
    """
    parser = argparse.ArgumentParser(description="Live window driver for the replay TPM run.")
    parser.add_argument("--indi-port", type=int, required=True)
    parser.add_argument("--standin", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.indi_port in (5000, 7624):
        print(f"Refusing INDI port {arguments.indi_port}: a real service's port.", file=sys.stderr)
        return 1
    driver = ReplayDriver(arguments.indi_port, arguments.standin, arguments.replay, arguments.stats)
    signal.signal(signal.SIGTERM, lambda *_: driver.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: driver.stop_event.set())
    return driver.run()


if __name__ == "__main__":
    sys.exit(main())
