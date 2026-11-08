"""Purpose: A stand-in PHD2 event server for the simulated TPM run.

Description: PHD2 (the guiding program) offers an event server: a program
connects to a TCP port and receives one JSON object per line. This script
plays that part. It listens on one local port, and when the first client
connects it replays the guide steps (and the settle events) of a real guide
log at the pace the log was recorded (see `guide_log_replay.py`). The replay
is the same every run. It writes a small statistics file (clients, events
sent), which the run turns into TPMs.

In the replay profile the events are not looped from the start of a log.
They come from a plan (``--plan``): a list of events with the number of
seconds after a shared start time at which each is sent. The server waits for
a start file (``--start-file``) that holds that start time as seconds since
the epoch, so the guide steps line up with the replayed frames.

Safety: the server binds to 127.0.0.1 only and refuses port 4400 (the port a
real PHD2 uses) and port 5000 (the real backend). It never connects out.

Usage::

    python -m simulator.mock_phd2 --port 45123 \
        --log simulator/guide_log_sample.txt --stats <out.json>
    python -m simulator.mock_phd2 --port 45123 \
        --plan <sandbox>/replay/plan.json \
        --start-file <sandbox>/replay/start --stats <out.json>

The script stops on SIGTERM or SIGINT.
"""

import argparse
import json
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from simulator.guide_log_replay import ReplayItem, event_json_line, load_replay_items, replay

#: Ports the stand-in server refuses: a real PHD2 and the real backend.
FORBIDDEN_PORTS = (4400, 5000)
#: Seconds between two writes of the statistics file.
STATS_INTERVAL_SECONDS = 2.0


class MockPhd2Server:
    """A PHD2 event server that replays a guide log to every client.

    Parameters
    ----------
    port : `int`
        The local port to listen on.
    items : `list` [`ReplayItem`]
        The replay items.
    stats_path : `pathlib.Path` or `None`
        Where to write the statistics, or `None` for no file.
    scheduled : `list` [`tuple` [`float`, `dict`]], optional
        Replay mode: events with their offset in seconds after the start
        time. When given, `items` is not used.
    start_file : `pathlib.Path`, optional
        Replay mode: the file that holds the start time.
    """

    def __init__(
        self,
        port: int,
        items: list[ReplayItem],
        stats_path: Path | None,
        scheduled: list[tuple[float, dict]] | None = None,
        start_file: Path | None = None,
    ) -> None:
        """Keep the settings; nothing is opened yet.

        Raises
        ------
        ValueError
            If `port` is a real PHD2's or the real backend's port.
        """
        if port in FORBIDDEN_PORTS:
            raise ValueError(f"Refusing port {port}: it is a real PHD2 or the real backend.")
        self.port = port
        self._items = items
        self._scheduled = scheduled
        self._start_file = start_file
        self._stats_path = stats_path
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._replay_started = False
        self.clients_connected_total = 0
        self.guide_steps_sent = 0
        self.events_sent = 0
        self.first_client_at: float | None = None
        self.first_guide_step_at: float | None = None
        self._listener: socket.socket | None = None

    def stop(self) -> None:
        """Ask every thread to end."""
        self._stop.set()

    def _send_to_all(self, payload: bytes, is_guide_step: bool) -> None:
        """Send one line to every client and count it.

        A client that fails is dropped.

        Parameters
        ----------
        payload : `bytes`
            The line to send.
        is_guide_step : `bool`
            Whether the line is a GuideStep event (counted separately).
        """
        with self._lock:
            for client in list(self._clients):
                try:
                    client.sendall(payload)
                except OSError:
                    self._clients.remove(client)
                    client.close()
                    continue
                self.events_sent += 1
                if is_guide_step:
                    self.guide_steps_sent += 1
                    if self.first_guide_step_at is None:
                        self.first_guide_step_at = time.time()

    def _handle_item(self, item: ReplayItem) -> None:
        """Send the event of one replay item, if it has one.

        Parameters
        ----------
        item : `ReplayItem`
            The item that is due.
        """
        if item.event is not None:
            self._send_to_all(event_json_line(item.event, time.time()), item.event["Event"] == "GuideStep")

    def _replay_loop(self) -> None:
        """Replay the log until stopped (runs on its own thread)."""
        if self._scheduled is not None:
            self._scheduled_loop()
            return
        replay(self._items, self._handle_item, self._stop.is_set, self._stop.wait)

    def _scheduled_loop(self) -> None:
        """Send each planned event at its offset after the start (replay mode).

        The loop waits for the start file, then sends every event once, at
        its own time (not at the sum of the gaps, so a slow send cannot make
        the rest drift).
        """
        if self._scheduled is None:
            return
        while not self._stop.is_set():
            try:
                start_time = float(self._start_file.read_text().strip()) if self._start_file else time.time()
                break
            except OSError, ValueError:
                self._stop.wait(0.2)
        else:
            return
        for offset, event in self._scheduled:
            wait = start_time + offset - time.time()
            if wait > 0 and self._stop.wait(wait):
                return
            self._send_to_all(event_json_line(event, time.time()), event["Event"] == "GuideStep")

    def _greet(self, client: socket.socket) -> None:
        """Send the first two events a real PHD2 sends on a new connection.

        Parameters
        ----------
        client : `socket.socket`
            The new client.
        """
        now = time.time()
        client.sendall(
            event_json_line(
                {
                    "Event": "Version",
                    "PHDVersion": "2.6.14",
                    "PHDSubver": "tpm",
                    "OverlapSupport": True,
                    "MsgVersion": 1,
                },
                now,
            )
        )
        client.sendall(event_json_line({"Event": "AppState", "State": "Guiding"}, now))

    def write_stats(self) -> None:
        """Write the statistics file, if one was asked for."""
        if self._stats_path is None:
            return
        with self._lock:
            stats = {
                "port": self.port,
                "clients_connected_total": self.clients_connected_total,
                "clients_now": len(self._clients),
                "guide_steps_sent": self.guide_steps_sent,
                "events_sent": self.events_sent,
                "first_client_at": self.first_client_at,
                "first_guide_step_at": self.first_guide_step_at,
            }
        temporary = self._stats_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        temporary.replace(self._stats_path)

    def serve(self) -> None:
        """Listen, accept clients and replay, until `stop` is called."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen(4)
        listener.settimeout(0.5)
        self._listener = listener
        last_stats = 0.0
        while not self._stop.is_set():
            try:
                client, _ = listener.accept()
            except TimeoutError:
                client = None
            if client is not None:
                self._accept(client)
            if time.time() - last_stats >= STATS_INTERVAL_SECONDS:
                self.write_stats()
                last_stats = time.time()
        self.write_stats()
        listener.close()
        with self._lock:
            for client in self._clients:
                client.close()

    def _accept(self, client: socket.socket) -> None:
        """Register a new client and start the replay on the first one.

        Parameters
        ----------
        client : `socket.socket`
            The connection just accepted.
        """
        try:
            self._greet(client)
        except OSError:
            client.close()
            return
        with self._lock:
            self._clients.append(client)
            self.clients_connected_total += 1
            if self.first_client_at is None:
                self.first_client_at = time.time()
            start_replay = not self._replay_started
            self._replay_started = True
        if start_replay:
            threading.Thread(target=self._replay_loop, daemon=True, name="Phd2Replay").start()


def main() -> int:
    """Run the server from the command line.

    Returns
    -------
    status : `int`
        The process exit status.
    """
    parser = argparse.ArgumentParser(description="Stand-in PHD2 event server that replays a guide log.")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--plan", type=Path, default=None, help="replay plan.json (replay profile)")
    parser.add_argument("--start-file", type=Path, default=None)
    parser.add_argument("--stats", type=Path, default=None)
    arguments = parser.parse_args()
    if arguments.plan is not None:
        scheduled = [
            (float(offset), event)
            for offset, event in json.loads(arguments.plan.read_text())["guide"]["events"]
        ]
        if not scheduled:
            print(f"ERROR: no guide events in {arguments.plan}", file=sys.stderr)
            return 1
        server = MockPhd2Server(arguments.port, [], arguments.stats, scheduled, arguments.start_file)
    else:
        if arguments.log is None:
            parser.error("give --log or --plan")
        items = load_replay_items(arguments.log)
        if not any(item.event for item in items):
            print(f"ERROR: no guide steps in {arguments.log}", file=sys.stderr)
            return 1
        server = MockPhd2Server(arguments.port, items, arguments.stats)
    signal.signal(signal.SIGTERM, lambda *_: server.stop())
    signal.signal(signal.SIGINT, lambda *_: server.stop())
    server.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
