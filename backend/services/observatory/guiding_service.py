"""Purpose: Start and stop the guide loop, and serve the guiding status.

Description: Guiding lives in the wayfinding library. The guiding driver
the active telescope's ``guiding_protocol`` names (PHD2, the pulses
KStars/Ekos sends the mount, or a simulator) reads the guide samples;
`control.guiding.status(include=["live"])` reports them with the
root-mean-square (RMS) error; and `control.guiding.run_loop` runs this
app's own guide loop. This service only runs that loop on a background
thread and stops it, and passes the RPC calls on.
"""

import logging
import threading
import time
from typing import Any

from astrometricslib import AstrometricsError
from wayfindinglib import ObservatoryControl

logger = logging.getLogger(__name__)

START_WAIT_SECONDS = 5.0
"""How long `start_guiding` waits for the loop to start or give up."""

START_POLL_SECONDS = 0.1
"""How often it checks."""


class GuidingService:
    """Run the library's guide loop on a thread and report guiding."""

    def __init__(self, observatory_api: ObservatoryControl) -> None:
        """Keep the observatory control.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which guides.
        """
        self._observatory = observatory_api
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start_guiding(self, exposure: float | None = None, gain: float | None = None) -> bool:
        """Start the guide loop on a background thread.

        Parameters
        ----------
        exposure : `float`, optional
            Guide exposure length in seconds. The last one when omitted.
        gain : `float`, optional
            Guide camera gain. The last one when omitted.

        Returns
        -------
        started : `bool`
            `True` once the loop runs; `False` if it was already running
            or could not start (for example, the mount is not tracking).
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Guiding already active")
            return False
        self._stop.clear()

        def run() -> None:
            """Run the guide loop; the top of a background thread."""
            try:
                self._observatory.guiding.run_loop(self._stop, exposure_seconds=exposure, gain=gain)
            except AstrometricsError as error:
                logger.warning("Guiding did not run: %s", error.message)
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("Guide loop failed")

        self._thread = threading.Thread(target=run, name="guiding", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline and self._thread.is_alive():
            if self._observatory.guiding.status(include=["live"]).live.is_guiding:
                logger.info("Started guiding loop: Exp=%ss, Gain=%s", exposure, gain)
                return True
            time.sleep(START_POLL_SECONDS)
        return self._thread.is_alive()

    def stop_guiding(self) -> bool:
        """Stop the guide loop and wait for its thread to end.

        Returns
        -------
        stopped : `bool`
            `True` once guiding has stopped (including if it was not
            running).
        """
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("Stopped guiding loop")
        return True

    def capture_frame(self, exposure: float = 1.0, gain: float | None = None) -> bool:
        """Take one exposure with the guide camera.

        This is a single picture, not the continuous loop `start_guiding`
        runs.

        Parameters
        ----------
        exposure : `float`, optional
            Exposure time in seconds. Defaults to 1.0.
        gain : `float`, optional
            Guide-camera gain. `None` (default) keeps the camera's current
            gain.

        Returns
        -------
        captured : `bool`
            `True` if the camera accepted the exposure command.
        """
        return bool(self._observatory.guiding.expose(exposure, gain=gain))

    def get_status(self) -> dict[str, Any]:
        """Report the guiding going on now.

        Reading it also takes in what PHD2 or KStars/Ekos measured since
        the last read.

        Returns
        -------
        status : `dict`
            ``is_guiding``, ``stats`` (RMS in arcseconds), ``history`` (the
            newest samples), ``exposure`` and ``gain``.
        """
        live = self._observatory.guiding.status(include=["live"]).live
        return live.model_dump(mode="json", by_alias=True)

    def analyze_guiding_spectrum(self, session_id: str | None = None) -> dict[str, Any]:
        """Refit the mount's periodic error model from recorded samples.

        Parameters
        ----------
        session_id : `str`, optional
            One night's samples, or every recorded sample when omitted.

        Returns
        -------
        spectrum : `dict` [`str`, `Any`]
            Dominant periods, peak-to-peak periodic error and the spectrum.
        """
        analysis = self._observatory.guiding.refit_spectrum(session_id=session_id, limit=2000)
        return analysis.model_dump(by_alias=True)
