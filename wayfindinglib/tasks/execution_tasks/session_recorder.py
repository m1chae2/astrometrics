"""Purpose: Builds an ObservationSession by Recording Observatory Telemetry.

Description: `ObservationSessionRecorder`'s guiding/weather telemetry
loop is carried forward unchanged from the deprecated
`observationlib.session_recorder` (`Wayfinding_Library_Architecture.md`
§2.4.7-§2.4.8) -- it listens to guiding telemetry via a
`PHD2GuidingService` and takes periodic INDI status/weather snapshots;
it never issues commands to PHD2 or the mount.

What does change: the deprecated recorder constructed a bare
`ObservationSession` from just an id. The new
`wayfindinglib.models.session.observation_session.ObservationSession` requires
`site_profile_id`/`telescope_id`/`camera_id` it has no way to invent,
because those belong to Observation Planning
(`Wayfinding_Library_Architecture.md` §2.2.3's session field-ownership
invariant: the queue and its placement context are written only by
Planning). `run()` therefore loads an existing, already-planned session
by id and attaches telemetry to it, rather than creating one from
scratch -- this follows directly from the field-ownership invariant,
not from a change in what the recorder itself does.

`PHD2GuidingService.drain_guiding_samples()` yields the new
`wayfindinglib.models.session.telemetry.GuidingSample` directly (the deprecated
`wayfindinglib.observatory.GuidingSample` it originally yielded carried
an identical field/alias shape, so repointing its import was a pure
relocation rather than a data transformation).
"""

import logging
import re
import time
from typing import Any

from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.drivers.phd2.phd2_guiding_service import PHD2GuidingService
from wayfindinglib.models.session.observation_session import ObservationSession, WeatherSample

logger = logging.getLogger(__name__)

_LEADING_FLOAT_PATTERN = re.compile(r"-?\d+(\.\d+)?")


def _parse_leading_float(value: Any) -> float | None:
    """Parse the leading numeric portion of an INDI status string.

    Returns
    -------
    parsed_value : `float` or `None`
        The leading numeric value, or `None` if `value` has no leading
        numeric portion (e.g. an unavailable-reading placeholder).
    """
    match = _LEADING_FLOAT_PATTERN.match(str(value))
    return float(match.group()) if match else None


class ObservationSessionRecorder:
    """Build an ObservationSession by recording observatory telemetry.

    Listens to guiding telemetry (via a `PHD2GuidingService`) and takes
    periodic INDI status/weather snapshots against an already-planned
    session; it never issues commands to PHD2 or the mount.

    Parameters
    ----------
    guiding_service : `PHD2GuidingService`
        Source of accumulated guiding telemetry for the session.
    indi_driver : `Any`
        Hardware driver (`IndiInterface` or `SimulatorIndiInterface`) to
        read status/weather snapshots from.
    butler : `DiskButler`, optional
        Persistence layer. If `None` (default), a new `DiskButler` is
        constructed from the process-wide configuration.
    snapshot_interval_seconds : `int`, optional
        How often to take a weather snapshot and persist a checkpoint
        (default 300).
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        guiding_service: PHD2GuidingService,
        indi_driver: Any,
        butler: DiskButler | None = None,
        snapshot_interval_seconds: int = 300,
    ):
        """Initialize the recorder without starting the recording loop yet."""
        self._guiding_service = guiding_service
        self._indi_driver = indi_driver
        self._butler = butler or DiskButler()
        self._snapshot_interval_seconds = snapshot_interval_seconds

    def capture_weather_snapshot(self) -> WeatherSample:
        """Return a best-effort weather sample from the INDI driver.

        Returns
        -------
        weather_sample : `WeatherSample`
            Reads whatever temperature/humidity properties are available
            from `indi_driver.get_status()` today (its existing raw
            property reads); unavailable fields stay `None`, which
            `WeatherSample` already tolerates.
        """
        status = self._indi_driver.get_status()
        return WeatherSample(
            time=time.time(),
            ambient_temperature_c=_parse_leading_float(getattr(status, "temperature", None)),
            humidity_percent=_parse_leading_float(getattr(status, "humidity", None)),
        )

    def save_checkpoint(self, session: ObservationSession) -> None:
        """Persist the session recorded so far.

        Parameters
        ----------
        session : `ObservationSession`
            The in-progress session to persist, called periodically
            (not just at the end) so a crash mid-night doesn't lose the
            whole recording.
        """
        self._butler.put(session, "observation_session", {"session_id": session.id})

    def run(self, session_id: str, stop_event: Any | None = None) -> ObservationSession:
        """Run the recording loop for one observing night.

        Parameters
        ----------
        session_id : `str`
            Identifier of an existing, already-planned `ObservationSession`.
        stop_event : `threading.Event`, optional
            When set, ends the loop after the current iteration. If `None`
            (default), the loop runs until `KeyboardInterrupt`.

        Returns
        -------
        session : `ObservationSession`
            The final recorded session, already persisted via
            `save_checkpoint`.

        Raises
        ------
        ValueError
            If `session_id` does not resolve to an existing session --
            this recorder attaches telemetry to a session Planning
            already created, it does not create one.
        """
        session = self._butler.get("observation_session", {"session_id": session_id})
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        self._guiding_service.poll_external_telemetry()

        try:
            while stop_event is None or not stop_event.is_set():
                time.sleep(self._snapshot_interval_seconds)
                self._guiding_service.poll_external_telemetry()
                session.guiding_samples.extend(self._guiding_service.drain_guiding_samples())
                session.weather_samples.append(self.capture_weather_snapshot())
                self.save_checkpoint(session)
        except KeyboardInterrupt:
            logger.info("Recording stopped for session '%s' (KeyboardInterrupt).", session_id)

        self.save_checkpoint(session)
        return session
