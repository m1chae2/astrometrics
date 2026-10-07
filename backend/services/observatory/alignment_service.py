"""Purpose: Start and stop mount centering, and serve the alignment records.

Description: Centering the mount on a target by plate solving is
``control.mount.slew(destination, center=True)`` in the wayfinding
library, which records each round as an alignment attempt. This service
only runs that call on a background thread and stops it, lists the
recorded nights through ``control.history.query(kind="alignment")``, and
records the plate-solve syncs and polar alignment runs that another
program (such as Ekos) sends to the mount.
"""

import logging
import sqlite3
import threading
import time
from typing import Any

from astrometricslib import InvalidArgumentError
from wayfindinglib import (
    AlignmentAttempt,
    AlignmentTargetSession,
    MountPointingModel,
    ObservatoryControl,
    SkyPosition,
)

logger = logging.getLogger(__name__)

CENTERING_MAX_ITERATIONS = 10
"""Most plate-solve rounds one centering run makes."""

RECENT_ATTEMPTS_SHOWN = 50
"""How many recorded attempts the live status lists when no run is active."""

ATTEMPT_STATUSES = ("solving", "failed", "warning", "aligned", "idle")
"""The statuses an `AlignmentAttempt` can have."""


class AlignmentService:
    """Run mount centering in the background and report alignment records."""

    def __init__(self, observatory_api: ObservatoryControl, logger_interface: Any = None) -> None:
        """Keep the observatory control and the log database.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which runs the centering.
        logger_interface : `LoggerInterface`, optional
            The log database that holds the alignment records.
        """
        self._observatory = observatory_api
        self._logger_interface = logger_interface
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
            `AlignmentTargetSession` per target, with jitter and drift
            rates, worked out by the library.
        """
        if not self._logger_interface:
            return {"alignmentAttempts": [], "alignmentTargets": []}
        rows = list(reversed(self._logger_interface.get_alignment_logs(limit=RECENT_ATTEMPTS_SHOWN)))
        if self._run_started_at is not None:
            rows = [row for row in rows if (row.get("timestamp") or 0) >= self._run_started_at]
        attempts = [
            AlignmentAttempt(
                status=row.get("status") if row.get("status") in ATTEMPT_STATUSES else "aligned",
                delta_ra_arcsec=row.get("delta_ra_arcsec"),
                delta_dec_arcsec=row.get("delta_dec_arcsec"),
                ra=row.get("mount_ra"),
                dec=row.get("mount_dec"),
                pointing_error_arcsec=row.get("pointing_error_arcsec"),
                timestamp=row.get("timestamp"),
                target_name=row.get("target_name"),
            )
            for row in rows
        ]
        targets = AlignmentTargetSession.from_attempts(attempts)
        if self.is_active():
            attempts.append(AlignmentAttempt(status="solving"))
        return {
            "alignmentAttempts": [attempt.model_dump(by_alias=True) for attempt in attempts],
            "alignmentTargets": [target.model_dump(mode="json", by_alias=True) for target in targets],
        }

    def get_polar_alignment(self) -> dict[str, Any]:
        """Get current or latest polar alignment assistant status.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            Polar alignment metrics and coordinates.
        """
        driver = self._observatory.driver
        if driver and hasattr(driver, "get_polar_alignment_status"):
            st = driver.get_polar_alignment_status()
            if st.get("status") != "idle":
                return st

        # Fallback to latest persisted polar alignment in SQLite
        if self._logger_interface:
            try:
                logs = self._logger_interface.get_polar_alignment_logs(limit=1)
                if logs:
                    row = logs[0]
                    return {
                        "status": row.get("status", "aligned"),
                        "totalErrorArcsec": row.get("total_error_arcsec"),
                        "altErrorArcsec": row.get("alt_error_arcsec"),
                        "azErrorArcsec": row.get("az_error_arcsec"),
                        "poleRa": row.get("pole_ra"),
                        "poleDec": row.get("pole_dec"),
                        "paaPoints": row.get("paa_points", []),
                        "timestamp": row.get("timestamp"),
                    }
            except sqlite3.Error as exc:
                logger.debug("Error fetching polar alignment fallback: %s", exc)

        return {
            "status": "idle",
            "totalErrorArcsec": None,
            "altErrorArcsec": None,
            "azErrorArcsec": None,
            "poleRa": None,
            "poleDec": None,
            "paaPoints": [],
            "timestamp": None,
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        """List the nights that recorded alignment data, newest first.

        Returns
        -------
        sessions : `list` [`dict` [`str`, `Any`]]
            One summary per night, from
            ``control.history.query(kind="alignment")``.
        """
        reply = self._observatory.history.query(kind="alignment", limit=50, register_job=False)
        return reply["sessions"]

    def get_session_data(self, session_id: str) -> dict[str, Any]:
        """Fetch alignment attempts and polar alignment data for a session.

        Parameters
        ----------
        session_id : `str`
            Session identifier or date string.

        Returns
        -------
        data : `dict` [`str`, `Any`]
            ``alignmentAttempts`` (a status the model does not know reads
            as ``aligned``), ``alignmentTargets`` (the attempts grouped by
            target, with jitter and drift rates, from
            `AlignmentTargetSession.from_attempts`) and ``polarAlignment``.
        """
        if not self._logger_interface:
            return {"alignmentAttempts": [], "alignmentTargets": [], "polarAlignment": None}

        try:
            raw_attempts = self._logger_interface.get_session_alignment_attempts(session_id)
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

            # Also fetch synthesized target tracking telemetry for this session
            if hasattr(self._logger_interface, "get_session_target_telemetry"):
                try:
                    target_attempts = self._logger_interface.get_session_target_telemetry(session_id)
                    attempts.extend(target_attempts)
                except sqlite3.Error as target_telemetry_err:
                    logger.debug(
                        "Error loading target telemetry for session %s: %s", session_id, target_telemetry_err
                    )

            if session_id in ("all", "*", None):
                polar_logs = self._logger_interface.get_polar_alignment_logs(limit=1)
            else:
                polar_logs = self._logger_interface.get_polar_alignment_logs(session_id=session_id, limit=1)

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

            targets = AlignmentTargetSession.from_attempts([
                AlignmentAttempt.model_validate(attempt) for attempt in attempts
            ])
            return {
                "alignmentAttempts": attempts,
                "alignmentTargets": [target.model_dump(mode="json", by_alias=True) for target in targets],
                "polarAlignment": polar_status,
            }
        except (sqlite3.Error, ValueError, TypeError) as exc:
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

        Delegates to `control.history.query(kind="pointing_model")`
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
            with camelCase keys.

        Raises
        ------
        InvalidArgumentError
            If the library cannot fit a model: no night was given, or no
            observer location is known.
        """
        reply = self._observatory.history.query(
            kind="pointing_model", session_id=session_id, register_job=False
        )
        if "error" in reply:
            raise InvalidArgumentError(reply["error"])
        return MountPointingModel.model_validate(reply["model"]).model_dump(by_alias=True)

    def poll_external_syncs(self, indi_interface: Any = None) -> None:
        """Poll and drain external plate-solve syncs and polar alignment.

        Parameters
        ----------
        indi_interface : `Any`, optional
            INDI driver instance to drain sync records from. If `None`,
            falls back to `self._observatory.driver`.
        """
        driver = indi_interface or self._observatory.driver
        if not driver:
            return

        # Poll external syncs
        if hasattr(driver, "drain_external_syncs"):
            try:
                sync_records = driver.drain_external_syncs()
                driver_status = getattr(driver, "status", None)
                target_name = driver_status.get("TARGET_NAME") if isinstance(driver_status, dict) else None
                if not isinstance(target_name, str):
                    target_name = None
                for record in sync_records:
                    if not self._logger_interface:
                        continue
                    self._logger_interface.record_alignment_attempt({
                        "status": record.get("status", "aligned"),
                        "delta_ra_arcsec": record.get("delta_ra_arcsec"),
                        "delta_dec_arcsec": record.get("delta_dec_arcsec"),
                        "pointing_error_arcsec": record.get("pointing_error_arcsec"),
                        "timestamp": record.get("time", time.time()),
                        "ra": record.get("ra"),
                        "dec": record.get("dec"),
                        "target_name": target_name,
                    })
            except (sqlite3.Error, ValueError) as exc:
                logger.debug("Error polling external syncs: %s", exc)

        # Poll polar alignment updates
        if hasattr(driver, "drain_polar_alignment"):
            try:
                polar_record = driver.drain_polar_alignment()
                if polar_record and self._logger_interface:
                    self._logger_interface.record_polar_alignment(polar_record)
            except sqlite3.Error as p_err:
                logger.debug("Error polling polar alignment: %s", p_err)

    def start_alignment(self, target_ra: float, target_dec: float) -> bool:
        """Start centering the mount on a sky position, in the background.

        Parameters
        ----------
        target_ra : `float`
            Target right ascension, in decimal degrees.
        target_dec : `float`
            Target declination, in decimal degrees.

        Returns
        -------
        started : `bool`
            `True` if the centering thread was started, `False` if a run
            is already active.
        """
        if self.is_active():
            logger.warning("Alignment already in progress")
            return False
        position = SkyPosition(ra_deg=target_ra % 360.0, dec_deg=target_dec)
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

        Stops the mount through `control.mount.abort_motion`, which also
        ends the centering loop before its next step.

        Returns
        -------
        cancelled : `bool`
            `True` once the run has been stopped and its thread joined;
            `False` if no run was active.
        """
        if not self.is_active():
            logger.warning("No alignment in progress")
            return False
        logger.info("Cancelling alignment")
        self._observatory.mount.abort_motion()
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
