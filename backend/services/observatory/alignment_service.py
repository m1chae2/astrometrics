"""Iterative plate-solving alignment loop for the telescope mount."""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from wayfindinglib import IndiInterface
from wayfindinglib.models.session.telemetry import AlignmentAttempt

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    """Result from plate solving an alignment image."""

    success: bool
    ra: float = 0.0
    dec: float = 0.0
    error: str = ""


class AlignmentService:
    """Service for telescope alignment using iterative plate solving.

        Performs alignment by capturing images, plate solving with
    astrometry.net,
        syncing the telescope mount, and re-slewing until within accuracy
    threshold.
    """

    def __init__(
        self,
        indi_interface: IndiInterface,
        imaging_service: Any = None,
        star_identifier: Any = None,
        logger_interface: Any = None,
    ) -> None:
        self.indi = indi_interface
        self._imaging_service = imaging_service
        self._star_identifier = star_identifier
        self._logger_interface = logger_interface
        self.alignment_attempts: list[AlignmentAttempt] = []
        self._alignment_active = False
        self._alignment_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

        # Alignment parameters (can be set via setters)
        self.accuracy_threshold = 30.0  # arcseconds
        self.settle_time = 1.5  # seconds
        self.alignment_exposure = 1.0  # seconds

    def get_attempts(self) -> list[dict]:
        """Get current alignment attempts list for frontend consumption.

        Returns
        -------
        attempts : `list` [`dict`]
            One dict per attempt with coordinate and delta keys.
        """
        if not self.alignment_attempts and self._logger_interface:
            try:
                logs = self._logger_interface.get_alignment_logs(limit=50)
                for log in reversed(logs):
                    self.alignment_attempts.append(
                        AlignmentAttempt(
                            status=log.get("status", "aligned"),
                            delta_ra_arcsec=log.get("delta_ra_arcsec"),
                            delta_dec_arcsec=log.get("delta_dec_arcsec"),
                            ra=log.get("mount_ra"),
                            dec=log.get("mount_dec"),
                            pointing_error_arcsec=log.get("pointing_error_arcsec"),
                            timestamp=log.get("timestamp"),
                            target_name=log.get("target_name"),
                        )
                    )
            except Exception as exc:
                logger.debug(f"Failed loading alignment attempts from SQLite: {exc}")

        return [attempt.model_dump(by_alias=True) for attempt in self.alignment_attempts]

    def clear_attempts(self) -> None:
        """Clear alignment attempt history."""
        self.alignment_attempts = []

    def get_polar_alignment(self) -> dict[str, Any]:
        """Get current or latest polar alignment assistant status.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            Polar alignment metrics and coordinates.
        """
        driver = self.indi
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
            except Exception as exc:
                logger.debug(f"Error fetching polar alignment fallback: {exc}")

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
        """List distinct past observing sessions that recorded alignment data.

        Returns
        -------
        sessions : `list` [`dict` [`str`, `Any`]]
            List of session summaries.
        """
        if self._logger_interface:
            try:
                raw_sessions = self._logger_interface.get_alignment_sessions()

                # Count targets per session date from the target library,
                # when it is available
                session_target_counts: dict[str, int] = {}
                try:
                    import json
                    import os
                    import sqlite3
                    from datetime import datetime

                    target_db_path = os.path.join(
                        os.path.dirname(self._logger_interface.db_path), "astrometrics.db"
                    )
                    if os.path.exists(target_db_path):
                        conn_t = sqlite3.connect(target_db_path)
                        c_t = conn_t.cursor()
                        c_t.execute("SELECT id, data_json FROM targets")
                        for _tid, djson in c_t.fetchall():
                            data = json.loads(djson) if djson else {}
                            dates: set[str] = set()
                            for f in data.get("frames", []):
                                ts = f.get("timestamp")
                                if ts:
                                    d = datetime.fromtimestamp(ts - 43200).strftime("%Y-%m-%d")
                                    dates.add(d)
                            for d in dates:
                                session_target_counts[d] = session_target_counts.get(d, 0) + 1
                        conn_t.close()
                except Exception as target_count_err:
                    logger.debug(f"Error computing session target counts: {target_count_err}")

                sessions = []
                for s in raw_sessions:
                    sdate = s.get("session_date")
                    t_count = session_target_counts.get(sdate, 0)
                    sessions.append({
                        "sessionId": s.get("session_id"),
                        "sessionDate": sdate,
                        "syncCount": s.get("sync_count", 0),
                        "targetCount": t_count,
                        "startTime": s.get("start_time"),
                        "endTime": s.get("end_time"),
                        "avgErrorArcsec": s.get("avg_error_arcsec"),
                        "polarErrorArcsec": s.get("polar_error_arcsec"),
                        "polarAltErrorArcsec": s.get("polar_alt_error_arcsec"),
                        "polarAzErrorArcsec": s.get("polar_az_error_arcsec"),
                    })
                return sessions
            except Exception as exc:
                logger.debug(f"Error listing alignment sessions: {exc}")
        return []

    def get_session_data(self, session_id: str) -> dict[str, Any]:
        """Fetch alignment attempts and polar alignment data for a session.

        Parameters
        ----------
        session_id : `str`
            Session identifier or date string.

        Returns
        -------
        data : `dict` [`str`, `Any`]
            Dictionary containing attempts and polarAlignment.
        """
        if not self._logger_interface:
            return {"alignmentAttempts": [], "polarAlignment": None}

        try:
            raw_attempts = self._logger_interface.get_session_alignment_attempts(session_id)
            attempts = []
            for row in raw_attempts:
                attempts.append({
                    "status": row.get("status", "aligned"),
                    "deltaRaArcsec": row.get("delta_ra_arcsec"),
                    "deltaDecArcsec": row.get("delta_dec_arcsec"),
                    "ra": row.get("mount_ra"),
                    "dec": row.get("mount_dec"),
                    "pointingErrorArcsec": row.get("pointing_error_arcsec"),
                    "timestamp": row.get("timestamp"),
                    "targetName": row.get("target_name"),
                })

            # Also fetch synthesized target tracking telemetry for this session
            if hasattr(self._logger_interface, "get_session_target_telemetry"):
                try:
                    target_attempts = self._logger_interface.get_session_target_telemetry(session_id)
                    attempts.extend(target_attempts)
                except Exception as target_telemetry_err:
                    logger.debug(
                        f"Error loading target telemetry for session {session_id}: {target_telemetry_err}"
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

            return {
                "alignmentAttempts": attempts,
                "polarAlignment": polar_status,
            }
        except Exception as exc:
            logger.debug(f"Error loading session {session_id} alignment data: {exc}")
            return {"alignmentAttempts": [], "polarAlignment": None}

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

        Parameters
        ----------
        session_id : `str` | `None`, optional
            Target session to model, or `None` to fit all recorded
            historical solves.

        Returns
        -------
        model : `dict` [`str`, `Any`]
            Decomposed model terms (ME, MA, CH, TF) and RMS improvements.
        """
        from wayfindinglib.analytics.pointing_model import fit_pointing_model

        attempts: list[dict[str, Any]] = []
        if self._logger_interface:
            try:
                if session_id:
                    attempts = self._logger_interface.get_session_alignment_attempts(session_id)
                else:
                    attempts = self._logger_interface.get_alignment_logs(limit=5000)
            except Exception as exc:
                logger.error(f"Error fetching attempts for pointing model: {exc}")

        # Fall back to in-memory attempts if DB has none
        if not attempts and self.alignment_attempts:
            for a in self.alignment_attempts:
                attempts.append({
                    "ra": a.ra,
                    "dec": a.dec,
                    "delta_ra_arcsec": a.delta_ra_arcsec,
                    "delta_dec_arcsec": a.delta_dec_arcsec,
                    "timestamp": a.timestamp,
                })

        model = fit_pointing_model(attempts)
        return model.model_dump(by_alias=True)

    def poll_external_syncs(self, indi_interface: Any = None) -> None:
        """Poll and drain external plate-solve syncs and polar alignment.

        Parameters
        ----------
        indi_interface : `Any`, optional
            INDI driver instance to drain sync records from. If `None`,
            falls back to `self.indi`.
        """
        driver = indi_interface or self.indi
        if not driver:
            return

        # Poll external syncs
        if hasattr(driver, "drain_external_syncs"):
            try:
                sync_records = driver.drain_external_syncs()
                for record in sync_records:
                    status = record.get("status", "aligned")
                    delta_ra = record.get("delta_ra_arcsec")
                    delta_dec = record.get("delta_dec_arcsec")
                    ra_val = record.get("ra")
                    dec_val = record.get("dec")
                    pointing_err = record.get("pointing_error_arcsec")
                    timestamp = record.get("time", time.time())
                    driver_status = getattr(driver, "status", None)
                    target_name = (
                        driver_status.get("TARGET_NAME") if isinstance(driver_status, dict) else None
                    )
                    if not isinstance(target_name, str):
                        target_name = None

                    attempt = AlignmentAttempt(
                        status=status,
                        delta_ra_arcsec=delta_ra,
                        delta_dec_arcsec=delta_dec,
                        ra=ra_val,
                        dec=dec_val,
                        pointing_error_arcsec=pointing_err,
                        timestamp=timestamp,
                        target_name=target_name,
                    )
                    self.alignment_attempts.append(attempt)
                    if self._logger_interface:
                        try:
                            record_payload = {
                                "status": status,
                                "delta_ra_arcsec": delta_ra,
                                "delta_dec_arcsec": delta_dec,
                                "pointing_error_arcsec": pointing_err,
                                "timestamp": timestamp,
                                "ra": ra_val,
                                "dec": dec_val,
                                "target_name": target_name,
                            }
                            self._logger_interface.record_alignment_attempt(record_payload)
                        except Exception as log_err:
                            logger.debug(f"Failed to record alignment attempt in SQLite: {log_err}")
            except Exception as exc:
                logger.debug(f"Error polling external syncs: {exc}")

        # Poll polar alignment updates
        if hasattr(driver, "drain_polar_alignment"):
            try:
                polar_record = driver.drain_polar_alignment()
                if polar_record and self._logger_interface:
                    self._logger_interface.record_polar_alignment(polar_record)
            except Exception as p_err:
                logger.debug(f"Error polling polar alignment: {p_err}")

    def solve_image(self, image_path: str) -> AlignmentResult:
        """Solves the given image using identifyStars (Astrometry.

        net). Returns the center RA/DEC coordinates.

        Returns
        -------
        result : `AlignmentResult`
            The solved center RA/DEC on success, or a result with
            ``success=False`` and an ``error`` message on failure.
        """
        try:
            if not self._star_identifier:
                return AlignmentResult(success=False, error="StarIdentifier not configured")

            # Only need WCS, skip SIMBAD for speed during alignment
            _, wcs = self._star_identifier.process_image(image_path, attempt_plate_solving=True)

            if not wcs:
                return AlignmentResult(success=False, error="Solver failed to find solution")

            # Use WCS reference point (CRVAL) as image center coordinates
            ra = wcs.wcs.crval[0]
            dec = wcs.wcs.crval[1]

            return AlignmentResult(success=True, ra=ra, dec=dec)

        except Exception as e:
            logger.error(f"Plate solving error: {e}")
            return AlignmentResult(success=False, error=str(e))

    def _alignment_loop(self, target_ra: float, target_dec: float):  # ruff: ignore[missing-return-type-private-function]
        """Run the alignment loop in a background thread.

        Iteratively captures, solves, syncs, and re-slews until within
        accuracy threshold.
        """
        max_attempts = 10
        attempt_count = 0

        while not self._stop_flag.is_set() and attempt_count < max_attempts:
            attempt_count += 1

            # Add a "solving" status attempt
            solving_attempt = AlignmentAttempt(status="solving")
            self.alignment_attempts.append(solving_attempt)

            try:
                # Wait for settle time
                time.sleep(self.settle_time)

                # Capture alignment image
                logger.info(f"Capturing alignment image (attempt {attempt_count})")

                if not self._imaging_service:
                    logger.error("ImagingService not initialized in AlignmentService")
                    solving_attempt.status = "failed"
                    break

                image_result = self._imaging_service.capture_light_frame(
                    exposure=self.alignment_exposure,
                    iso=800,
                    gain=None,  # Default ISO
                )

                if not image_result or "path" not in image_result:
                    logger.error("Failed to capture alignment image")
                    solving_attempt.status = "failed"
                    continue

                # Plate solve
                logger.info("Plate solving alignment image")
                solve_result = self.solve_image(image_result["path"])

                if not solve_result.success:
                    logger.error(f"Plate solve failed: {solve_result.error}")
                    solving_attempt.status = "failed"
                    continue

                # Calculate coordinate delta (arcseconds). solve_result.ra
                # (from the WCS solution's CRVAL) and target_ra are both
                # decimal degrees, so no hours-to-degrees factor applies here
                # -- that factor is only needed when RA is expressed in time
                # units, which it isn't at this point in the pipeline.
                ra_error_arcsec = (solve_result.ra - target_ra) * 3600.0
                dec_error_arcsec = (solve_result.dec - target_dec) * 3600.0

                solving_attempt.delta_ra_arcsec = ra_error_arcsec
                solving_attempt.delta_dec_arcsec = dec_error_arcsec

                # Check accuracy
                error_magnitude = (ra_error_arcsec**2 + dec_error_arcsec**2) ** 0.5

                if error_magnitude < self.accuracy_threshold:
                    # Success!
                    logger.info(f"Alignment successful! Error: {error_magnitude:.2f} arcsec")
                    solving_attempt.status = "aligned"
                    self._alignment_active = False
                    break
                elif error_magnitude < self.accuracy_threshold * 2:
                    # Close but not quite there
                    solving_attempt.status = "warning"
                else:
                    # Still far off, but we'll sync and retry
                    solving_attempt.status = "warning"

                # Sync telescope to solved coordinates. IndiInterface's mount
                # commands take RA in hours; solve_result.ra is decimal
                # degrees (from the WCS solution), so convert at this
                # boundary rather than upstream, where degrees is correct.
                solved_ra_hours = solve_result.ra / 15.0
                logger.info(f"Syncing to solved coordinates: RA={solved_ra_hours}h, DEC={solve_result.dec}")
                self.indi.sync_coordinates(solved_ra_hours, solve_result.dec)

                # Re-slew to target
                target_ra_hours = target_ra / 15.0
                logger.info(f"Re-slewing to target: RA={target_ra_hours}h, DEC={target_dec}")
                self.indi.slew(target_ra_hours, target_dec)

            except Exception as e:
                logger.error(f"Alignment attempt {attempt_count} failed: {e}")
                solving_attempt.status = "failed"

        if attempt_count >= max_attempts:
            logger.warning("Alignment max attempts reached")

        self._alignment_active = False

    def start_alignment(self, target_ra: float, target_dec: float) -> bool:
        """Start the alignment process for the given target coordinates.

        Runs in a background thread.

        Parameters
        ----------
        target_ra : `float`
            Target right ascension, in decimal degrees.
        target_dec : `float`
            Target declination, in decimal degrees.

        Returns
        -------
        started : `bool`
            `True` if the alignment thread was started, `False` if
            alignment was already active.
        """
        if self._alignment_active:
            logger.warning("Alignment already in progress")
            return False

        # Clear previous attempts
        self.clear_attempts()

        # Reset stop flag
        self._stop_flag.clear()

        # Start alignment thread
        self._alignment_active = True
        self._alignment_thread = threading.Thread(
            target=self._alignment_loop, args=(target_ra, target_dec), daemon=True
        )
        self._alignment_thread.start()

        logger.info(f"Started alignment for RA={target_ra}, DEC={target_dec}")
        return True

    def cancel_alignment(self) -> bool:
        """Cancel the current alignment process.

        Returns
        -------
        cancelled : `bool`
            `True` once the alignment thread has been signalled to
            stop and joined; `False` if no alignment was active.
        """
        if not self._alignment_active:
            logger.warning("No alignment in progress")
            return False

        logger.info("Cancelling alignment")
        self._stop_flag.set()
        self._alignment_active = False

        # Wait for thread to finish
        if self._alignment_thread and self._alignment_thread.is_alive():
            self._alignment_thread.join(timeout=5.0)

        return True

    def is_active(self) -> bool:
        """Check if alignment is currently active.

        Returns
        -------
        active : `bool`
            `True` if an alignment loop is currently running.
        """
        return self._alignment_active
