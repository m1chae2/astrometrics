"""Purpose: Start and stop mount centering, and serve the alignment records.

Description: Centering the mount on a target by plate solving is
``control.mount.slew(destination, center=True)`` in the wayfinding
library, which records each round as an alignment attempt. This service
only runs that call on a background thread and stops it, lists the
recorded nights through ``review.query(kind="alignment")``, reads the
stored attempts, grouped by target, with
``review.query_alignment_records``, and
records the plate-solve syncs and polar alignment runs that another
program (such as Ekos) sends to the mount.
"""

import logging
import threading
import time
from typing import Any

from astrometricslib import TargetCatalog
from astrometricslib.exceptions import InvalidArgumentError, StorageError
from backend.services.coordinate_text import parse_coordinate_text
from wayfindinglib import ObservatoryControl

logger = logging.getLogger(__name__)

CENTERING_MAX_ITERATIONS = 10
"""Most plate-solve rounds one centering run makes."""

RECENT_ATTEMPTS_SHOWN = 50
"""How many recorded attempts the live status lists when no run is active."""

SOLVING_ATTEMPT = {
    "status": "solving",
    "deltaRaArcsec": None,
    "deltaDecArcsec": None,
    "ra": None,
    "dec": None,
    "pointingErrorArcsec": None,
    "timestamp": None,
    "targetName": None,
    "sessionId": None,
}
"""The placeholder row shown while a centering run waits for a solve."""


class AlignmentService:
    """Run mount centering in the background and report alignment records."""

    def __init__(
        self,
        observatory_api: ObservatoryControl,
        targets: TargetCatalog | None = None,
        review: Any = None,
    ) -> None:
        """Keep the control, the review and the targets.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which runs the centering.
        review : `ObservationReview`, optional
            The Wayfinder's `review`, which lists the recorded nights and
            groups attempts by target.
        targets : `astrometricslib.TargetCatalog`, optional
            The target library, whose frames show when each target was
            tracked in a night's view.
        """
        self._observatory = observatory_api
        self._review = review
        self._targets = targets
        self._alignment_thread: threading.Thread | None = None
        self._run_started_at: float | None = None

    def get_attempts(self) -> dict[str, list[dict]]:
        """List the live alignment attempts, and the same grouped by target.

        Since a centering run was started, these are the attempts recorded
        from then on, with a ``solving`` entry while it runs. Before any
        run, they are the most recent recorded attempts.

        Returns
        -------
        live : `dict` [`str`, `list` [`dict`]]
            ``alignmentAttempts``: one dict per attempt, oldest first.
            ``alignmentTargets``: the same attempts grouped into one
            session per target, with jitter and drift rates, worked out by
            the library.
        """
        records = self._require_review().query_alignment_records(
            limit=RECENT_ATTEMPTS_SHOWN, since=self._run_started_at
        )
        attempts = [attempt.model_dump(mode="json", by_alias=True) for attempt in records.attempts]
        if self.is_active():
            attempts.append(dict(SOLVING_ATTEMPT))
        return {
            "alignmentAttempts": attempts,
            "alignmentTargets": [target.model_dump(mode="json", by_alias=True) for target in records.targets],
        }

    def _require_review(self) -> Any:
        """Return the review, or fail clearly if none was given.

        Returns
        -------
        review : `ObservationReview`
            The Wayfinder's review.

        Raises
        ------
        InvalidArgumentError
            If the service was built without a review.
        """
        if self._review is None:
            raise InvalidArgumentError("The alignment service was built without a review.")
        return self._review

    def get_polar_alignment(self) -> dict[str, Any]:
        """Get current or latest polar alignment assistant status.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            Polar alignment metrics and coordinates, camelCase keys. The
            library reads a run in progress first, then the last stored
            run.
        """
        return self._observatory.mount.get_polar_alignment().model_dump(by_alias=True)

    def list_sessions(self) -> list[dict[str, Any]]:
        """List the nights that recorded alignment data, newest first.

        Returns
        -------
        sessions : `list` [`dict` [`str`, `Any`]]
            One summary per night, from
            ``review.query(kind="alignment")``.
        """
        reply = self._require_review().query(kind="alignment", limit=50, register_job=False)
        return reply.sessions

    def get_session_data(self, session_id: str = "") -> dict[str, Any]:
        """Fetch alignment attempts and polar alignment data for a session.

        Parameters
        ----------
        session_id : `str`, optional
            Session identifier or date string. Empty by default.

        Returns
        -------
        data : `dict` [`str`, `Any`]
            ``alignmentAttempts`` (a status the model does not know reads
            as ``aligned``), ``alignmentTargets`` (the attempts grouped by
            target, with jitter and drift rates, from
            ``review.query_alignment_records``) and ``polarAlignment``.
        """
        try:
            records = self._require_review().query_alignment_records(
                session_id, targets=self._targets.list() if self._targets is not None else None
            )
        except (StorageError, ValueError, TypeError) as exc:
            logger.debug("Error loading session %s alignment data: %s", session_id, exc)
            return {"alignmentAttempts": [], "alignmentTargets": [], "polarAlignment": None}
        polar = records.polar_alignment
        return {
            "alignmentAttempts": [
                attempt.model_dump(mode="json", by_alias=True) for attempt in records.attempts
            ],
            "alignmentTargets": [target.model_dump(mode="json", by_alias=True) for target in records.targets],
            "polarAlignment": polar.model_dump(mode="json", by_alias=True) if polar is not None else None,
        }

    def get_cumulative_tracking_data(self, limit: int = 10000) -> dict[str, Any]:
        """Fetch cumulative tracking attempts across all recorded sessions.

        Parameters
        ----------
        limit : `int`, optional
            Maximum number of alignment attempts to return (default 10000).

        Returns
        -------
        data : `dict` [`str`, `Any`]
            Dictionary containing cumulative attempts and polar alignment.
        """
        return self.get_session_data("all")

    def compute_pointing_model(self, session_id: str | None = None) -> dict[str, Any]:
        """Compute decomposed geometric mount pointing terms.

        Delegates to `review.query(kind="pointing_model")`
        rather than fitting directly.

        Parameters
        ----------
        session_id : `str` | `None`, optional
            The observing night to model. A model that mixes nights is
            not meaningful, so the library refuses `None`.

        Returns
        -------
        model : `dict` [`str`, `Any`]
            Decomposed model terms (ME, MA, CH, TF) and RMS improvements,
            with camelCase keys. The library refuses with an
            `InvalidArgumentError` when it cannot fit a model: no night was
            given, or no observer location is known.
        """
        reply = self._require_review().query(kind="pointing_model", session_id=session_id, register_job=False)
        return reply.model.model_dump(by_alias=True)

    def poll_external_syncs(self) -> None:
        """Store the plate-solve syncs and polar alignment of other programs.

        The library drains what the INDI session queued and stores it as
        alignment records (``control.mount.poll_external_alignment``).
        """
        self._observatory.mount.poll_external_alignment()

    def start_alignment(self, target_ra: str, target_dec: str) -> bool:
        """Start centering the mount on a sky position, in the background.

        Parameters
        ----------
        target_ra : `str`
            Target right ascension as text, such as ``"12h 00m 00s"``.
        target_dec : `str`
            Target declination as text, such as ``"+45d 00m 00s"``.

        Returns
        -------
        started : `bool`
            `True` if the centering thread was started, `False` if a run
            is already active. A coordinate that cannot be read raises
            `InvalidArgumentError` instead.
        """
        ra_deg = parse_coordinate_text(target_ra, is_right_ascension=True)
        dec_deg = parse_coordinate_text(target_dec, is_right_ascension=False)
        if self.is_active():
            logger.warning("Alignment already in progress")
            return False
        position = {"ra_deg": ra_deg % 360.0, "dec_deg": dec_deg}
        self._run_started_at = time.time()

        def run() -> None:
            """Center the mount; the top of a background thread."""
            try:
                centered = self._observatory.mount.slew(
                    position, center=True, max_iterations=CENTERING_MAX_ITERATIONS
                )
                logger.info(
                    "Centering on RA=%s, DEC=%s finished; centered: %s", target_ra, target_dec, centered
                )
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("Centering on RA=%s, DEC=%s failed", target_ra, target_dec)

        self._alignment_thread = threading.Thread(target=run, name="centering", daemon=True)
        self._alignment_thread.start()
        logger.info("Started alignment for RA=%s, DEC=%s", target_ra, target_dec)
        return True

    def cancel_alignment(self) -> bool:
        """Stop the current centering run.

        Stops the mount through `control.mount.cancel_centering`, which also
        ends the centering loop before its next step.

        Returns
        -------
        cancelled : `bool`
            `True` once the run has been stopped and its thread joined;
            `False` if no run was active.

        Notes
        -----
        In monitor mode the library raises `PermissionDeniedError` (issue
        #95, decision U4); this service lets the error through.
        """
        active = self.is_active()
        if not self._observatory.mount.cancel_centering(active):
            logger.warning("No alignment in progress")
            return False
        logger.info("Cancelled alignment")
        if self._alignment_thread is not None:
            self._alignment_thread.join(timeout=5.0)
        return True

    def is_active(self) -> bool:
        """Check whether a centering run is active.

        Returns
        -------
        active : `bool`
            `True` if a centering thread is running.
        """
        return self._alignment_thread is not None and self._alignment_thread.is_alive()
