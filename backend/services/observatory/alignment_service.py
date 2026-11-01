"""Purpose: Start and stop mount centering, and serve the alignment records.

Description: Centering the mount on a target by plate solving is
``control.mount.slew(destination, center=True)`` in the wayfinding
library, which records each round as an alignment attempt. This service
only runs that call on a background thread and stops it, lists the
recorded nights through ``review.query(kind="alignment")``, groups
attempts by target with ``review.query_alignment_sessions``, and
records the plate-solve syncs and polar alignment runs that another
program (such as Ekos) sends to the mount.
"""

import logging
import threading
import time
from typing import Any

from astrometricslib import TargetCatalog, parse_coordinate_string
from astrometricslib.exceptions import InvalidArgumentError, StorageError
from wayfindinglib import (
    ControlRecordStore,
    ObservatoryControl,
    SkyPosition,
)

logger = logging.getLogger(__name__)

CENTERING_MAX_ITERATIONS = 10
"""Most plate-solve rounds one centering run makes."""

RECENT_ATTEMPTS_SHOWN = 50
"""How many recorded attempts the live status lists when no run is active."""

ATTEMPT_STATUSES = ("solving", "failed", "warning", "aligned", "idle")
"""The statuses an alignment attempt can have."""

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
        records: ControlRecordStore | None = None,
        targets: TargetCatalog | None = None,
        review: Any = None,
    ) -> None:
        """Keep the control, the review, the records and the targets.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which runs the centering.
        review : `ObservationReview`, optional
            The Wayfinder's `review`, which lists the recorded nights and
            groups attempts by target.
        records : `ControlRecordStore`, optional
            The store that holds the alignment records.
        targets : `astrometricslib.TargetCatalog`, optional
            The target library, whose frames show when each target was
            tracked in a night's view.
        """
        self._observatory = observatory_api
        self._review = review
        self._records = records
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
        if not self._records:
            return {"alignmentAttempts": [], "alignmentTargets": []}
        rows = list(reversed(self._records.get_alignment_attempts(limit=RECENT_ATTEMPTS_SHOWN)))
        if self._run_started_at is not None:
            rows = [row for row in rows if (row.get("timestamp") or 0) >= self._run_started_at]
        attempts = [
            {
                "status": row.get("status") if row.get("status") in ATTEMPT_STATUSES else "aligned",
                "deltaRaArcsec": row.get("delta_ra_arcsec"),
                "deltaDecArcsec": row.get("delta_dec_arcsec"),
                "ra": row.get("mount_ra"),
                "dec": row.get("mount_dec"),
                "pointingErrorArcsec": row.get("pointing_error_arcsec"),
                "timestamp": row.get("timestamp"),
                "targetName": row.get("target_name"),
                "sessionId": None,
            }
            for row in rows
        ]
        targets = self._require_review().query_alignment_sessions(attempts).targets
        if self.is_active():
            attempts.append(dict(SOLVING_ATTEMPT))
        return {
            "alignmentAttempts": attempts,
            "alignmentTargets": [target.model_dump(mode="json", by_alias=True) for target in targets],
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
            ``review.query_alignment_sessions``) and ``polarAlignment``.
        """
        if not self._records:
            return {"alignmentAttempts": [], "alignmentTargets": [], "polarAlignment": None}

        try:
            raw_attempts = self._records.get_session_alignment_attempts(session_id)
            attempts = []
            for row in raw_attempts:
                attempts.append({
                    "status": row.get("status") if row.get("status") in ATTEMPT_STATUSES else "aligned",
                    "deltaRaArcsec": row.get("delta_ra_arcsec"),
                    "deltaDecArcsec": row.get("delta_dec_arcsec"),
                    "ra": row.get("mount_ra"),
                    "dec": row.get("mount_dec"),
                    "pointingErrorArcsec": row.get("pointing_error_arcsec"),
                    "timestamp": row.get("timestamp"),
                    "targetName": row.get("target_name"),
                    "sessionId": row.get("session_id"),
                })

            # Also add the tracking of the targets imaged that night.
            if self._targets is not None:
                attempts.extend(self._records.get_session_target_telemetry(session_id, self._targets.list()))

            if session_id in ("all", "*", None):
                polar_logs = self._records.get_polar_alignments(limit=1)
            else:
                polar_logs = self._records.get_polar_alignments(session_id=session_id, limit=1)

            polar_status = None
            if polar_logs:
                p = polar_logs[0]
                polar_status = {
                    "status": p.get("status", "aligned"),
                    "totalErrorArcsec": p.get("total_error_arcsec"),
                    "altErrorArcsec": p.get("alt_error_arcsec"),
                    "azErrorArcsec": p.get("az_error_arcsec"),
                    "poleRa": p.get("pole_ra"),
                    "poleDec": p.get("pole_dec"),
                    "paaPoints": p.get("paa_points", []),
                    "timestamp": p.get("timestamp"),
                }

            targets = self._require_review().query_alignment_sessions(attempts).targets
            return {
                "alignmentAttempts": attempts,
                "alignmentTargets": [target.model_dump(mode="json", by_alias=True) for target in targets],
                "polarAlignment": polar_status,
            }
        except (StorageError, ValueError, TypeError) as exc:
            logger.debug("Error loading session %s alignment data: %s", session_id, exc)
            return {"alignmentAttempts": [], "alignmentTargets": [], "polarAlignment": None}

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
        ra_deg = parse_coordinate_string(target_ra, is_ra=True)
        dec_deg = parse_coordinate_string(target_dec, is_ra=False)
        if self.is_active():
            logger.warning("Alignment already in progress")
            return False
        position = SkyPosition(ra_deg=ra_deg % 360.0, dec_deg=dec_deg)
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
