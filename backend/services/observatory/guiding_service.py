"""Autoguiding control loop, drift simulation, and RMS telemetry."""

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from wayfindinglib.api.control_registry import ObservatoryControl
from wayfindinglib.drivers.phd2.phd2_client import PHD2Client
from wayfindinglib.drivers.phd2.phd2_guiding_service import PHD2GuidingService
from wayfindinglib.models.session.telemetry import GuidingSampleSource

logger = logging.getLogger(__name__)


@dataclass
class GuidingStats:
    """Rolling RMS guiding error and guide-camera quality metrics."""

    rms_ra: float = 0.0
    rms_dec: float = 0.0
    rms_total: float = 0.0
    star_mass: float = 0.0
    snr: float = 0.0


class GuidingService:
    """Drive the autoguiding loop and track guiding performance history."""

    def __init__(
        self,
        observatory_api: ObservatoryControl,
        phd2_service: PHD2GuidingService | None = None,
        logger_interface: Any = None,
    ) -> None:
        self._observatory = observatory_api
        self._logger_interface = logger_interface

        self._phd2_service = phd2_service if phd2_service is not None else PHD2GuidingService(PHD2Client())
        self._is_guiding = False
        self._stop_event = threading.Event()
        self._guide_thread = None
        self._latest_stats = GuidingStats()
        self._history = []
        self._max_history = 100

        # Guiding Parameters
        self.exposure_time = 1.0  # seconds
        self.gain = 0

        # Persistent drift states across pulse cycles to avoid
        # orthogonal axes collapse
        self._residual_dra = 0.0
        self._residual_ddec = 0.0
        self._sim_drift_ra = 0.0
        self._sim_drift_dec = 0.0

    def start_guiding(self, exposure: float | None = None, gain: int | None = None) -> bool:
        """Start the background guiding loop once tracking is confirmed.

        Parameters
        ----------
        exposure : `float`, optional
            Guide exposure duration in seconds. If `None` (default),
            the current `exposure_time` is kept.
        gain : `int`, optional
            Guide camera gain. If `None` (default), the current `gain`
            is kept.

        Returns
        -------
        started : `bool`
            `True` if the guiding thread was started, `False` if
            guiding was already active or the telescope never reached
            a tracking state.
        """
        if self._is_guiding:
            logger.warning("Guiding already active")
            return False

        # Validate telescope is tracking (with a small retry loop for sync)
        max_retries = 5
        for _i in range(max_retries):
            tracking_status = self._observatory.get_telescope_status().get("trackingStatus", "Unknown")
            if tracking_status == "Tracking":
                break
            # Force a refresh in the driver if possible
            self._observatory.connect()
            time.sleep(0.5)
        else:
            logger.error(
                f"Cannot start guiding: Telescope is not tracking after {max_retries} attempts "
                f"(Status: {tracking_status})"
            )
            return False

        if exposure is not None:
            self.exposure_time = exposure
        if gain is not None:
            self.gain = gain

        self._stop_event.clear()
        self._is_guiding = True
        self.clear_history()

        self._guide_thread = threading.Thread(target=self._guiding_loop, daemon=True)
        self._guide_thread.start()
        logger.info(f"Started guiding loop: Exp={exposure}s, Gain={gain}")
        return True

    def clear_history(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Clear the guiding history (plots)."""
        self._history = []
        self._latest_stats = GuidingStats()  # Reset stats too
        self._residual_dra = 0.0
        self._residual_ddec = 0.0

    def stop_guiding(self) -> bool:
        """Stop the background guiding loop and wait for it to exit.

        Returns
        -------
        stopped : `bool`
            `True` once guiding has stopped (including if it was
            already inactive).
        """
        if not self._is_guiding:
            return True

        self._stop_event.set()
        if self._guide_thread and self._guide_thread.is_alive():
            self._guide_thread.join(timeout=5.0)

        self._is_guiding = False
        logger.info("Stopped guiding loop")
        return True

    def _current_target_name(self) -> str | None:
        """Look up the target name from the current telescope status.

        Returns
        -------
        target_name : `str` | `None`
            The active target name, or `None` if none is set.
        """
        return self._observatory.get_telescope_status().get("targetName")

    def poll_external_telemetry(self) -> None:
        """Drain queued real-time external timed guide pulses passively.

        Prioritizes live PHD2 GuideStep events if available, falling back
        to INDI mount guide pulse interception when external guiders like
        Ekos command the mount directly.
        """
        if self._is_guiding:
            return

        import math

        # 1. Check for ground-truth telemetry from local PHD2 event stream
        if self._phd2_service is not None:
            try:
                self._phd2_service.poll_external_telemetry()
                phd2_samples = self._phd2_service.drain_guiding_samples()
                if phd2_samples:
                    for s in phd2_samples:
                        history_entry = {
                            "time": s.time,
                            "dra": s.dra,
                            "ddec": s.ddec,
                            "pulse_ra": s.pulse_ra,
                            "pulse_dec": s.pulse_dec,
                            "pulseRa": s.pulse_ra,
                            "pulseDec": s.pulse_dec,
                            "snr": s.snr,
                            "rms_ra": s.rms_ra if s.rms_ra is not None else 0.0,
                            "rms_dec": s.rms_dec if s.rms_dec is not None else 0.0,
                            "rmsRa": s.rms_ra if s.rms_ra is not None else 0.0,
                            "rmsDec": s.rms_dec if s.rms_dec is not None else 0.0,
                        }
                        self._history.append(history_entry)
                        if len(self._history) > self._max_history:
                            self._history.pop(0)

                    latest = phd2_samples[-1]
                    n = len(self._history)
                    sq_sum_ra = sum(item["dra"] ** 2 for item in self._history)
                    sq_sum_dec = sum(item["ddec"] ** 2 for item in self._history)
                    self._latest_stats.rms_ra = latest.rms_ra or math.sqrt(sq_sum_ra / n)
                    self._latest_stats.rms_dec = latest.rms_dec or math.sqrt(sq_sum_dec / n)
                    self._latest_stats.rms_total = math.hypot(
                        self._latest_stats.rms_ra, self._latest_stats.rms_dec
                    )
                    self._latest_stats.snr = latest.snr if latest.snr is not None else 20.0

                    if self._logger_interface:
                        try:
                            target_name = self._current_target_name()
                            records = [
                                {
                                    "time": s.time,
                                    "dra": s.dra,
                                    "ddec": s.ddec,
                                    "pulse_ra": s.pulse_ra,
                                    "pulse_dec": s.pulse_dec,
                                    "snr": s.snr,
                                    "rms_ra": s.rms_ra,
                                    "rms_dec": s.rms_dec,
                                    "target_name": target_name,
                                    "source": GuidingSampleSource.PHD2_LIVE.value,
                                }
                                for s in phd2_samples
                            ]
                            self._logger_interface.record_guiding_samples(records)
                        except Exception as log_err:
                            logger.debug(f"Failed to record PHD2 guiding samples: {log_err}")

                    return
            except Exception as phd2_err:
                logger.debug(f"PHD2 telemetry poll skipped: {phd2_err}")

        # 2. Fall back to INDI timed guide pulse queue
        try:
            pulses = self._observatory.drain_external_pulses()

            if not pulses:
                return

            # Coalesce proximate WE and NS pulses (within 0.8s) into
            # joint samples
            coalesced: list[dict[str, Any]] = []
            for p in pulses:
                if not coalesced:
                    coalesced.append(dict(p))
                    continue
                last = coalesced[-1]
                if abs(p.get("time", 0.0) - last.get("time", 0.0)) < 0.8:
                    if p.get("pulse_n", 0) > 0:
                        last["pulse_n"] = p["pulse_n"]
                    if p.get("pulse_s", 0) > 0:
                        last["pulse_s"] = p["pulse_s"]
                    if p.get("pulse_w", 0) > 0:
                        last["pulse_w"] = p["pulse_w"]
                    if p.get("pulse_e", 0) > 0:
                        last["pulse_e"] = p["pulse_e"]
                else:
                    coalesced.append(dict(p))

            for p in coalesced:
                pulse_ns = 0.0
                pulse_we = 0.0

                if p.get("pulse_n", 0) > 0:
                    pulse_ns = p["pulse_n"]
                elif p.get("pulse_s", 0) > 0:
                    pulse_ns = -p["pulse_s"]

                if p.get("pulse_w", 0) > 0:
                    pulse_we = p["pulse_w"]
                elif p.get("pulse_e", 0) > 0:
                    pulse_we = -p["pulse_e"]

                if abs(pulse_ns) < 1e-6 and abs(pulse_we) < 1e-6:
                    continue

                # Timed guide pulse (ms) proportional drift estimation:
                # 0.5x sidereal guide speed = 7.52 arcsec/s.
                import random

                if abs(pulse_we) >= 1e-3:
                    raw_dra = (pulse_we / 1000.0) * 7.52
                    dra = raw_dra + random.gauss(0.0, 0.05)
                    self._residual_dra = raw_dra * 0.15
                else:
                    # Retain deadband residual and atmospheric seeing jitter
                    dra = self._residual_dra + random.gauss(0.0, 0.06)
                    self._residual_dra *= 0.85

                if abs(pulse_ns) >= 1e-3:
                    raw_ddec = (pulse_ns / 1000.0) * 7.52
                    ddec = raw_ddec + random.gauss(0.0, 0.05)
                    self._residual_ddec = raw_ddec * 0.15
                else:
                    # Retain deadband residual and atmospheric seeing jitter
                    ddec = self._residual_ddec + random.gauss(0.0, 0.06)
                    self._residual_ddec *= 0.85

                self._sim_drift_ra = dra
                self._sim_drift_dec = ddec

                n = len(self._history) + 1
                sq_sum_ra = sum(s["dra"] ** 2 for s in self._history) + dra**2
                sq_sum_dec = sum(s["ddec"] ** 2 for s in self._history) + ddec**2

                self._latest_stats.rms_ra = math.sqrt(sq_sum_ra / n)
                self._latest_stats.rms_dec = math.sqrt(sq_sum_dec / n)
                self._latest_stats.rms_total = math.hypot(
                    self._latest_stats.rms_ra, self._latest_stats.rms_dec
                )

                sample = {
                    "time": p.get("time", time.time()),
                    "dra": round(dra, 3),
                    "ddec": round(ddec, 3),
                    "pulse_ra": abs(pulse_we),
                    "pulse_dec": abs(pulse_ns),
                    "pulseRa": abs(pulse_we),
                    "pulseDec": abs(pulse_ns),
                    "snr": None,
                    "rms_ra": round(self._latest_stats.rms_ra, 3),
                    "rms_dec": round(self._latest_stats.rms_dec, 3),
                    "rmsRa": round(self._latest_stats.rms_ra, 3),
                    "rmsDec": round(self._latest_stats.rms_dec, 3),
                }

                self._history.append(sample)
                if len(self._history) > self._max_history:
                    self._history.pop(0)

            if self._logger_interface and coalesced:
                try:
                    target_name = self._current_target_name()
                    records = [
                        {
                            **s,
                            "target_name": target_name,
                            "source": GuidingSampleSource.INDI_PULSE_ESTIMATE.value,
                        }
                        for s in self._history[-len(coalesced) :]
                    ]
                    self._logger_interface.record_guiding_samples(records)
                except Exception as log_err:
                    logger.debug(f"Failed to record INDI guiding samples: {log_err}")
        except Exception as e:
            # Handle uninitialized C++ SWIG client in test simulators
            # gracefully
            logger.debug(f"Passive guiding telemetry polling skipped or failed: {e}")

    def get_status(self) -> dict:
        """Report the current guiding status.

        Reports guiding active if local loop is running, or if
        passive telemetry history is actively being generated.

        Returns
        -------
        status : `dict`
            Dict with ``"is_guiding"`` (`bool`), ``"stats"``, the
            latest guiding stats as a dict, ``"history"``, the
            recent sample history, ``"exposure"``, and ``"gain"``.
        """
        is_guiding_active = self._is_guiding or (
            len(self._history) > 0 and (time.time() - self._history[-1]["time"] < 5.0)
        )
        return {
            "is_guiding": is_guiding_active,
            "stats": asdict(self._latest_stats),
            "history": self._history,  # Last N samples
            "exposure": self.exposure_time,
            "gain": self.gain,
        }

    def _guiding_loop(self):  # ruff: ignore[missing-return-type-private-function]
        """Run the main guiding control loop.

        1. Request Exposure 2. Wait for Image 3. Calculate Offset
        (Simulated for now) 4. Send Pulse 5. Repeat
        """
        import random

        # Initial drift seed
        self._sim_drift_ra = 0.0
        self._sim_drift_dec = 0.0

        while not self._stop_event.is_set():
            try:
                time.time()

                # 0. Safety Check: Ensure Telescope is still Tracking
                # We log it but allow 3 consecutive failures before
                # breaking loop (to handle transient INDI states)
                current_tracking = self._observatory.get_telescope_status().get("trackingStatus")
                if current_tracking != "Tracking":
                    if not hasattr(self, "_tracking_fail_count"):
                        self._tracking_fail_count = 0
                    self._tracking_fail_count += 1
                    if self._tracking_fail_count >= 3:
                        logger.warning(
                            f"Guiding loop stopped: Telescope tracking status lost ({current_tracking})."
                        )
                        break
                    else:
                        logger.debug(
                            f"Tracking status transient: {current_tracking}. "
                            f"retry {self._tracking_fail_count}/3"
                        )
                        time.sleep(1)
                        continue
                else:
                    self._tracking_fail_count = 0

                # 1. Start Exposure
                if not self._observatory.guide_expose(self.exposure_time, self.gain):
                    logger.error("Failed to start guide exposure")
                    time.sleep(1)
                    continue

                # 2. Wait for Exposure
                # Simple sleep for now. Real implementation would wait
                # for BLOB event or property change. Add buffer for
                # readout
                time.sleep(self.exposure_time + 0.5)

                if self._stop_event.is_set():
                    break

                # 3. Process Image (Simulated Drift)
                # In real life: blob = self._observatory.get_guide_image() ->
                # processing -> dRA/dDEC

                # Simulate "Random Walk" drift
                drift_step = 0.5
                self._sim_drift_ra += random.uniform(-drift_step, drift_step)
                self._sim_drift_dec += random.uniform(-drift_step, drift_step)

                # Apply simulated "Correction" (proportional control)
                aggression = 0.7
                correction_ra = -self._sim_drift_ra * aggression
                correction_dec = -self._sim_drift_dec * aggression

                # 4. Send Pulse (if error significant)

                # ms per pixel error (dummy scale)
                pulse_duration_ra = abs(correction_ra) * 100
                pulse_duration_dec = abs(correction_dec) * 100

                # Cap pulse
                max_pulse = 2000
                pulse_duration_ra = min(pulse_duration_ra, max_pulse)
                pulse_duration_dec = min(pulse_duration_dec, max_pulse)

                if pulse_duration_ra > 50:
                    direction = "W" if correction_ra > 0 else "E"  # Direction logic depends on mount
                    self._observatory.pulse_guide(direction, int(pulse_duration_ra))
                    # "Physics" update: drift is reduced by correction
                    # Assume 90% efficiency
                    self._sim_drift_ra += correction_ra * 0.9

                if pulse_duration_dec > 50:
                    direction = "N" if correction_dec > 0 else "S"
                    self._observatory.pulse_guide(direction, int(pulse_duration_dec))
                    self._sim_drift_dec += correction_dec * 0.9

                # 5. Update Stats & RMS
                import math

                # Include the current sample in the RMS calculation
                n = len(self._history) + 1
                sq_sum_ra = sum(s["dra"] ** 2 for s in self._history) + self._sim_drift_ra**2
                sq_sum_dec = sum(s["ddec"] ** 2 for s in self._history) + self._sim_drift_dec**2

                self._latest_stats.rms_ra = math.sqrt(sq_sum_ra / n) if n > 0 else 0.0
                self._latest_stats.rms_dec = math.sqrt(sq_sum_dec / n) if n > 0 else 0.0
                self._latest_stats.rms_total = math.sqrt(
                    self._latest_stats.rms_ra**2 + self._latest_stats.rms_dec**2
                )

                # Simulate realistic guiding SNR and star mass fluctuations
                import random

                self._latest_stats.snr = random.uniform(15.0, 45.0)
                self._latest_stats.star_mass = random.uniform(10000.0, 20000.0)

                sample = {
                    "time": time.time(),
                    "dra": self._sim_drift_ra,
                    "ddec": self._sim_drift_dec,
                    "pulse_ra": pulse_duration_ra,
                    "pulse_dec": pulse_duration_dec,
                    "snr": self._latest_stats.snr,
                    "rms_ra": self._latest_stats.rms_ra,
                    "rms_dec": self._latest_stats.rms_dec,
                }

                self._history.append(sample)
                if len(self._history) > self._max_history:
                    self._history.pop(0)

            except Exception as e:
                logger.error(f"Guiding loop error: {e}")
                time.sleep(1)

        self._is_guiding = False
        logger.info("Guiding loop finished")

    def ingest_phd2_log_file(self, file_path: str, target_name: str | None = None) -> int:
        """Parse and persist a native PHD2 guide log text file into SQLite.

        Delegates to `ObservatoryControl.ingest_guiding_log_file`
        (`guiding_log_ingestion.py`, §6a) rather than parsing and
        analyzing directly -- this service no longer imports the
        parser/analytics modules itself.

        Parameters
        ----------
        file_path : `str`
            Path to the PHD2 guide log text file.
        target_name : `str` | `None`, optional
            Target name associated with the guiding run.

        Returns
        -------
        sample_count : `int`
            The refit spectrum's total recorded sample count (cumulative
            across all history, not just this file), `0` if the file
            contained no parseable samples.
        """
        analysis = self._observatory.ingest_guiding_log_file(file_path, target_name=target_name)
        return analysis.sample_count if analysis else 0

    def analyze_guiding_spectrum(self, session_id: str | None = None) -> dict[str, Any]:
        """Analyze periodic error, worm harmonics, and backlash.

        Delegates to `ObservatoryControl.refit_guiding_spectrum`
        (`guiding_log_ingestion.py`, §6a) rather than analyzing
        directly.

        Parameters
        ----------
        session_id : `str` | `None`, optional
            Target session to analyze, or `None` for all recorded
            samples.

        Returns
        -------
        spectrum : `dict` [`str`, `Any`]
            Dominant periods, peak-to-peak PE, and PSD curve points.
        """
        analysis = self._observatory.refit_guiding_spectrum(session_id=session_id, limit=2000)
        return analysis.model_dump(by_alias=True)
