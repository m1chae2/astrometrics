"""Autoguiding control loop, drift simulation, and RMS telemetry."""

import logging
import threading
import time
from dataclasses import asdict, dataclass

from wayfindinglib import IndiInterface

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

    def __init__(self, indi_interface: IndiInterface | None = None, observatory_api=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._observatory_api = observatory_api
        if observatory_api:
            # Observatory exposes the hardware driver via _driver/driver
            # properties
            manager = getattr(observatory_api, "_manager", None)
            self.indi = (
                getattr(manager, "_driver", None)
                or getattr(observatory_api, "_driver", None)
                or getattr(observatory_api, "driver", None)
            )
        else:
            self.indi = indi_interface
        self._is_guiding = False
        self._stop_event = threading.Event()
        self._guide_thread = None
        self._latest_stats = GuidingStats()
        self._history = []
        self._max_history = 100

        # Guiding Parameters
        self.exposure_time = 1.0  # seconds
        self.gain = 0

        # Simulated Drift (since we don't have real star detection yet)
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
            tracking_status = self.indi.status.get("TRACKING_STATUS", "Unknown")
            if tracking_status == "Tracking":
                break
            # Force a refresh in the driver if possible
            if hasattr(self.indi, "connect_to_telescope"):
                self.indi.connect_to_telescope()
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

    def poll_external_telemetry(self) -> None:
        """Drain queued real-time external timed guide pulses passively.

        Applies a decay model to simulate visual drift between pulses,
        updates rolling RMS errors, and logs guiding samples when
        KStars/Ekos/PHD2 is guiding.
        """
        if self._is_guiding:
            return

        try:
            # Drain the thread-safe external pulse queue from the INDI
            # interface
            pulses = []
            if hasattr(self.indi, "_external_pulses"):
                with self.indi._external_pulses_lock:
                    pulses = list(self.indi._external_pulses)
                    self.indi._external_pulses.clear()

            pulse_ns = 0.0
            pulse_we = 0.0

            if pulses:
                # Find the maximum pulse values received in the last
                # polling interval
                for p in pulses:
                    # Treat North as positive correction, South as negative
                    if p["pulse_n"] > 0:
                        pulse_ns = p["pulse_n"]
                    elif p["pulse_s"] > 0:
                        pulse_ns = -p["pulse_s"]

                    # Treat West as positive correction, East as negative
                    if p["pulse_w"] > 0:
                        pulse_we = p["pulse_w"]
                    elif p["pulse_e"] > 0:
                        pulse_we = -p["pulse_e"]

            is_externally_guiding = abs(pulse_ns) > 0 or abs(pulse_we) > 0

            is_connected = False
            try:
                is_connected = self.indi.isServerConnected()
            except Exception as exc:
                logger.debug("Failed to query INDI server connection status: %s", exc)

            # Append guiding sample if active pulse exists, history
            # already has data, or the INDI server is connected (to
            # begin baseline tracking immediately)
            if is_externally_guiding or len(self._history) > 0 or is_connected:
                import math
                import random

                # If a new pulse correction was detected, update our
                # tracking error. Timed guide pulse (ms) is
                # proportional to the drift error saw by the guider.
                # Proportional drift estimation: 1000ms pulse duration
                # ≈ 1.5 arcsec drift error
                if abs(pulse_we) > 0:
                    self._sim_drift_ra = (pulse_we / 1000.0) * 1.5
                else:
                    # Decay active drift error towards baseline
                    # tracking noise (±0.03 arcsec)
                    self._sim_drift_ra = self._sim_drift_ra * 0.5 + random.uniform(-0.03, 0.03)

                if abs(pulse_ns) > 0:
                    self._sim_drift_dec = (pulse_ns / 1000.0) * 1.5
                else:
                    self._sim_drift_dec = self._sim_drift_dec * 0.5 + random.uniform(-0.03, 0.03)

                dra = self._sim_drift_ra
                ddec = self._sim_drift_dec

                n = len(self._history) + 1
                sq_sum_ra = sum(s["dra"] ** 2 for s in self._history) + dra**2
                sq_sum_dec = sum(s["ddec"] ** 2 for s in self._history) + ddec**2

                self._latest_stats.rms_ra = math.sqrt(sq_sum_ra / n)
                self._latest_stats.rms_dec = math.sqrt(sq_sum_dec / n)
                self._latest_stats.rms_total = math.sqrt(
                    self._latest_stats.rms_ra**2 + self._latest_stats.rms_dec**2
                )

                # Standard guide camera SNR & star mass simulation for
                # passive plots
                if is_externally_guiding:
                    self._latest_stats.snr = random.uniform(18.0, 32.0)
                    self._latest_stats.star_mass = random.uniform(8000.0, 15000.0)
                else:
                    self._latest_stats.snr = random.uniform(22.0, 26.0)
                    self._latest_stats.star_mass = random.uniform(10000.0, 11000.0)

                sample = {
                    "time": time.time(),
                    "dra": dra,
                    "ddec": ddec,
                    "pulse_ra": abs(pulse_we),
                    "pulse_dec": abs(pulse_ns),
                    "snr": self._latest_stats.snr,
                    "rms_ra": self._latest_stats.rms_ra,
                    "rms_dec": self._latest_stats.rms_dec,
                }

                self._history.append(sample)
                if len(self._history) > self._max_history:
                    self._history.pop(0)
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
                current_tracking = self.indi.status.get("TRACKING_STATUS")
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
                if not self.indi.guide_expose(self.exposure_time, self.gain):
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
                # In real life: blob = self.indi.get_guide_image() ->
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
                    self.indi.pulse_guide(direction, int(pulse_duration_ra))
                    # "Physics" update: drift is reduced by correction
                    # Assume 90% efficiency
                    self._sim_drift_ra += correction_ra * 0.9

                if pulse_duration_dec > 50:
                    direction = "N" if correction_dec > 0 else "S"
                    self.indi.pulse_guide(direction, int(pulse_duration_dec))
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
