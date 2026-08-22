"""Iterative plate-solving alignment loop for the telescope mount."""

import logging
import threading
import time
from dataclasses import dataclass

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

    def __init__(self, indi_interface: IndiInterface, imaging_service=None, star_identifier=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.indi = indi_interface
        self._imaging_service = imaging_service
        self._star_identifier = star_identifier
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
            One dict per attempt with ``"status"``, ``"deltaRaArcsec"``,
            and ``"deltaDecArcsec"`` keys.
        """
        return [attempt.model_dump(by_alias=True) for attempt in self.alignment_attempts]

    def clear_attempts(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Clear alignment attempt history."""
        self.alignment_attempts = []

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
