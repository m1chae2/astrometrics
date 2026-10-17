"""Purpose: A guiding-telemetry source backed by a live PHD2 connection.

Description: The guiding-telemetry interface is two calls: a caller calls
`poll_external_telemetry()` and then `get_status()`. The backend's
`GuidingService` and the execution session recorder use it to read the guiding
history. `PHD2GuidingService` implements this interface. It runs PHD2Client's
event stream on a background thread and stores each GuideStep event as a
GuidingSample record. It returns those records in two forms: as history entries
for status reports, and as GuidingSample objects for ObservationSession
recording.
"""

import logging
import threading
from collections import deque
from typing import Any

from wayfindinglib.drivers.phd2.phd2_client import PHD2Client
from wayfindinglib.drivers.phd2.phd2_events import parse_guide_step_event, to_history_entry
from wayfindinglib.models.session.telemetry import GuidingSample

logger = logging.getLogger(__name__)


class PHD2GuidingService:
    """Guiding-telemetry source backed by a live PHD2 connection.

    Implements the guiding-telemetry interface (`poll_external_telemetry`
    and `get_status`) that the session recorder and the backend use.

    Parameters
    ----------
    client : `PHD2Client`
        The PHD2 event-stream connection to read from.
    history_limit : `int`, optional
        Maximum number of GuidingSample entries retained in memory
        (default 500); oldest entries are dropped once exceeded.
    """

    def __init__(self, client: PHD2Client, history_limit: int = 500) -> None:
        """Initialize the service without starting the background thread."""
        self._client = client
        self._samples: deque[GuidingSample] = deque(maxlen=history_limit)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Held while deciding whether to start the thread, so callers polling
        # at the same moment cannot each start one.
        self._start_lock = threading.Lock()
        self._closed = False

    def _run_event_loop(self) -> None:
        """Parse GuideStep events into the sample deque (background thread)."""
        for event in self._client.events(stop_event=self._stop_event):
            guiding_sample = parse_guide_step_event(event)
            if guiding_sample is not None:
                with self._lock:
                    self._samples.append(guiding_sample)

    def poll_external_telemetry(self) -> None:
        """Ensure the background PHD2 event-reading thread is running.

        Matches the guiding-telemetry interface's expected method name
        and signature exactly. Lazily starts (or restarts, if the
        thread died) the connection thread rather than requiring a
        separate explicit start() call -- constructing and assigning
        this service is enough. Does nothing after `close()`.
        """
        with self._start_lock:
            if self._closed:
                return
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_event_loop, daemon=True, name="PHD2Events")
                self._thread.start()

    def get_status(self) -> dict[str, Any]:
        """Return the accumulated guiding history as history entries.

        Returns
        -------
        status : `dict`
            `{"history": [to_history_entry(s) for s in samples]}`.
        """
        with self._lock:
            samples = list(self._samples)
        return {"history": [to_history_entry(sample) for sample in samples]}

    def drain_guiding_samples(self) -> list[GuidingSample]:
        """Return and clear the accumulated GuidingSample list.

        Returns
        -------
        guiding_samples : `list` [`GuidingSample`]
            The session recorder's actual read path -- distinct from
            `get_status()`'s history view, which holds dictionaries for the
            status display.
        """
        with self._lock:
            samples = list(self._samples)
            self._samples.clear()
        return samples

    def is_running(self) -> bool:
        """Report whether the background PHD2 event-reading thread is alive.

        Returns
        -------
        running : `bool`
            `True` while the reader thread runs.
        """
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout_seconds: float = 2.0) -> bool:
        """Stop the background PHD2 event-reading thread and wait for it.

        The next `poll_external_telemetry()` starts it again; use `close()`
        to stop it for good.

        Parameters
        ----------
        timeout_seconds : `float`, optional
            Longest to wait for the thread to end. The client notices a stop
            within about a second, so 2 seconds leaves room to spare.

        Returns
        -------
        stopped : `bool`
            `True` if no reader thread is still running.
        """
        with self._start_lock:
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_seconds)
        return not self.is_running()

    def close(self, timeout_seconds: float = 2.0) -> bool:
        """Stop the reader thread for good, as at shutdown.

        Unlike `stop()`, later polls do not start the thread again.

        Parameters
        ----------
        timeout_seconds : `float`, optional
            Longest to wait for the thread to end.

        Returns
        -------
        stopped : `bool`
            `True` if no reader thread is still running.
        """
        with self._start_lock:
            self._closed = True
        return self.stop(timeout_seconds)
