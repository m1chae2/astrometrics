"""Purpose: PHD2-backed implementation of the guiding_service contract.

Description: ObservatoryManager.guiding_service is a settable property that
hardware_operations.get_telescope_status() already calls
(poll_external_telemetry() then get_status()) with nothing implementing it
today. PHD2GuidingService fills that seam: it runs PHD2Client's event
stream on a background thread, accumulates GuideStep events as
GuidingSample records, and exposes them both in the pre-existing legacy
history shape and as real GuidingSample objects for ObservationSession
persistence.
"""

import logging
import threading
from collections import deque
from typing import Any

from wayfindinglib.drivers.phd2.phd2_client import PHD2Client
from wayfindinglib.drivers.phd2.phd2_events import parse_guide_step_event, to_legacy_history_entry
from wayfindinglib.models.session.telemetry import GuidingSample

logger = logging.getLogger(__name__)


class PHD2GuidingService:
    """Guiding-telemetry source backed by a live PHD2 connection.

    Implements ObservatoryManager's guiding_service contract.

    Parameters
    ----------
    client : `PHD2Client`
        The PHD2 event-stream connection to read from.
    history_limit : `int`, optional
        Maximum number of GuidingSample entries retained in memory
        (default 500); oldest entries are dropped once exceeded.
    """

    def __init__(self, client: PHD2Client, history_limit: int = 500):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the service without starting the background thread."""
        self._client = client
        self._samples: deque[GuidingSample] = deque(maxlen=history_limit)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _run_event_loop(self) -> None:
        """Parse GuideStep events into the sample deque (background thread)."""
        for event in self._client.events(stop_event=self._stop_event):
            guiding_sample = parse_guide_step_event(event)
            if guiding_sample is not None:
                with self._lock:
                    self._samples.append(guiding_sample)

    def poll_external_telemetry(self) -> None:
        """Ensure the background PHD2 event-reading thread is running.

        Matches the guiding_service contract's expected method name
        and signature exactly. Lazily starts (or restarts, if the
        thread died) the connection thread rather than requiring a
        separate explicit start() call -- constructing and assigning
        this service is enough.
        """
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
            self._thread.start()

    def get_status(self) -> dict[str, Any]:
        """Return the accumulated guiding history in the legacy consumer shape.

        Returns
        -------
        status : `dict`
            `{"history": [to_legacy_history_entry(s) for s in samples]}`.
        """
        with self._lock:
            samples = list(self._samples)
        return {"history": [to_legacy_history_entry(sample) for sample in samples]}

    def drain_guiding_samples(self) -> list[GuidingSample]:
        """Return and clear the accumulated GuidingSample list.

        Returns
        -------
        guiding_samples : `list` [`GuidingSample`]
            The session recorder's actual read path -- distinct from
            `get_status()`'s legacy-shaped view, which exists only for the
            pre-existing script consumer.
        """
        with self._lock:
            samples = list(self._samples)
            self._samples.clear()
        return samples

    def stop(self) -> None:
        """Stop the background PHD2 event-reading thread."""
        self._stop_event.set()
