"""Purpose: Start and stop the guide loop, and serve the guiding status.

Description: Guiding lives in the wayfinding library. The guiding driver
the active telescope's ``guiding_protocol`` names (PHD2, the pulses
KStars/Ekos sends the mount, or a simulator) reads the guide samples;
`control.guiding.get_status(include=["live"])` reports them with the
root-mean-square (RMS) error; and `control.guiding.start` and `stop` run
this app's own guide loop on a thread the library keeps. This service
passes the RPC calls on and keeps their replies.
"""

import logging
from typing import Any

from astrometricslib.exceptions import ConflictError
from backend.services.error_logging import log_failure
from wayfindinglib import ObservatoryControl

logger = logging.getLogger(__name__)


class GuidingService:
    """Start and stop the library's guide loop and report guiding."""

    def __init__(self, observatory_api: ObservatoryControl) -> None:
        """Keep the observatory control.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which guides.
        """
        self._observatory = observatory_api

    def start_guiding(self, exposure: float | None = None, gain: float | None = None) -> bool:
        """Start the guide loop.

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

        Notes
        -----
        In monitor mode the library raises `PermissionDeniedError`; this
        service lets the error through.
        """
        try:
            self._observatory.guiding.start(exposure_s=exposure, gain=gain)
        except ConflictError as error:
            log_failure(logger, error, "Guiding did not start")
            return False
        logger.info("Started guiding loop: Exp=%ss, Gain=%s", exposure, gain)
        return True

    def stop_guiding(self) -> bool:
        """Stop the guide loop and wait for it to end.

        Returns
        -------
        stopped : `bool`
            `True` once guiding has stopped (including if it was not
            running).

        Notes
        -----
        In monitor mode the library raises `PermissionDeniedError` (issue
        #95, decision U4); this service lets the error through.
        """
        self._observatory.guiding.stop()
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
            `True` once the camera accepted the exposure command. A refusal
            raises instead.
        """
        self._observatory.guiding.capture(exposure, gain=gain)
        return True

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
        live = self._observatory.guiding.get_status(include=["live"]).live
        return live.model_dump(mode="json", by_alias=True)
