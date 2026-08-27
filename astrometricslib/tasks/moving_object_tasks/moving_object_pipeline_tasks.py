"""Main controller for finding asteroids in our photos.

This ties all the steps together: figuring out where the photos are pointing,
finding all the dots (stars/asteroids) in them, running the tests to filter
out the noise, and finally checking if any survivors match known asteroids
in our databases.
"""

import logging
import os
import statistics
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Literal

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_scales

from astrometricslib.image_processing.source_detection import SourceDetector
from astrometricslib.models.moving_object import AsteroidRecoveryCandidate, CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import (
    MovingObjectConfig,
    MovingObjectConfigLoader,
)
from astrometricslib.tasks.moving_object_tasks.frame_wcs_composer import (
    estimate_frame_wcs_from_mount_pointing,
)
from astrometricslib.tasks.moving_object_tasks.moving_object_detection_tasks import MovingObjectDetector
from astrometricslib.tasks.moving_object_tasks.moving_object_ephemeris_tasks import EphemerisCrossMatcher

logger = logging.getLogger(__name__)

# Matches the FOV-radius calculation already used for SIMBAD cone
# searches in star_identifier.py: half the larger field dimension,
# padded 10% for safety, capped at SkyBoT's practical query-size sweet
# spot (well under its own 10-degree maximum).
_FIELD_QUERY_RADIUS_BUFFER_FACTOR = 1.1
_FIELD_QUERY_RADIUS_CAP_DEG = 1.0


def _detect_sources_in_one_frame(
    frame_path: str,
    frame_timestamp: float,
    stack_wcs: WCS,
    fwhm: float,
    threshold_sigma: float,
) -> tuple[Literal["ok", "no_wcs", "read_failed"], list[FrameDetection]]:
    """Read one photo, figure out where it's pointing, and find all the dots.

    We put this function out here on its own so that we can run it on
    multiple photos at the same time using different CPU cores.

    Returns
    -------
    status : `"ok"`, `"no_wcs"`, or `"read_failed"`
        Did it work? "ok" means yes, "read_failed" means we couldn't open
        the file, and "no_wcs" means we couldn't figure out where it was
        pointing.
    detections : `list` [`FrameDetection`]
        A list of all the possible stars/asteroids we found in this photo.
    """
    try:
        with fits.open(frame_path, memmap=False) as hdul:
            frame_header = hdul[0].header
            frame_data = hdul[0].data
    except Exception as read_error:
        logger.warning(f"Failed to read frame '{frame_path}' for asteroid recovery: {read_error}")
        return "read_failed", []
    if frame_data is None:
        return "read_failed", []

    frame_wcs = estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header)
    if frame_wcs is None:
        return "no_wcs", []

    source_detector = SourceDetector(fwhm=fwhm, threshold_sigma=threshold_sigma)
    sources = source_detector.detect(np.asarray(frame_data, dtype=float))

    detections = []
    for source in sources:
        pixel_x = source.get("xcentroid", source.get("x_centroid"))
        pixel_y = source.get("ycentroid", source.get("y_centroid"))
        if pixel_x is None or pixel_y is None:
            continue
        sky_position = frame_wcs.wcs_pix2world([[float(pixel_x), float(pixel_y)]], 1)[0]
        detections.append(
            FrameDetection(
                frame_path=frame_path,
                timestamp=frame_timestamp,
                pixel_x=float(pixel_x),
                pixel_y=float(pixel_y),
                right_ascension_deg=float(sky_position[0]),
                declination_deg=float(sky_position[1]),
                flux=float(source.get("flux", 0.0) or 0.0),
                sharpness=float(source.get("sharpness", 0.0) or 0.0),
                photutils_roundness1=float(source.get("roundness1", 0.0) or 0.0),
            )
        )
    return "ok", detections


class AsteroidRecoveryPipeline:
    """The main factory line for finding asteroids in our photos.

    Parameters
    ----------
    config : `MovingObjectConfig`, optional
        The settings to use. If None, it loads the default settings.
    """

    def __init__(self, config: MovingObjectConfig | None = None):  # ruff: ignore[missing-return-type-special-method]
        self.config = config or MovingObjectConfigLoader.load_moving_object_config()
        # A simple dictionary to store the results of the last run. We save
        # things like "how many asteroids did we find?" so the main program
        # can show a summary to the user later.
        self.last_run_metrics: dict[str, int] = {}

    def process(self, target: Any) -> list[AsteroidRecoveryCandidate]:
        """Run the whole asteroid-finding process on one set of photos.

        Parameters
        ----------
        target : `astrometricslib.models.target.Target`
            The target we are analyzing. It must already have a finished
            stacked image so we know exactly where it is in the sky.

        Returns
        -------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            The list of moving objects we found.

        Raises
        ------
        ValueError
            If the target hasn't been stacked yet.
        """
        if not target.stacked_image:
            raise ValueError(
                f"Target '{target.id}' has no stacked_image; run analyze_target(type='astrometry') first."
            )

        stack_header = fits.getheader(target.stacked_image)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            stack_wcs = WCS(stack_header, naxis=2)

        frame_detections, frames_with_wcs_estimate, frames_excluded_missing_pointing_metadata = (
            self._detect_frame_sources(target, stack_wcs)
        )

        print(
            f"  [Asteroid Recovery] Detected {len(frame_detections)} total point sources "
            f"across {frames_with_wcs_estimate} frames."
        )
        print("  [Asteroid Recovery] Running spatial-temporal track persistence chaining...")
        detector = MovingObjectDetector(self.config)
        candidates = detector.detect_candidates(target.id, frame_detections)
        print(f"  [Asteroid Recovery] Chaining completed: {len(candidates)} track candidates generated.")

        candidates = self._cross_match_ephemeris(candidates, frame_detections, stack_wcs, stack_header)

        self.last_run_metrics = {
            "frames_with_wcs_estimate": frames_with_wcs_estimate,
            "frames_excluded_missing_pointing_metadata": frames_excluded_missing_pointing_metadata,
            "candidates_detected": len(candidates),
            "candidates_persistence_confirmed": sum(
                1 for candidate in candidates if candidate.cascade_stage != CascadeStage.REJECTED_SINGLE_FRAME
            ),
            "candidates_rate_linearity_confirmed": sum(
                1
                for candidate in candidates
                if candidate.cascade_stage
                in (CascadeStage.RATE_LINEARITY_CONFIRMED, CascadeStage.EPHEMERIS_MATCHED)
            ),
            "candidates_ephemeris_matched": sum(
                1 for candidate in candidates if candidate.cascade_stage == CascadeStage.EPHEMERIS_MATCHED
            ),
        }
        return candidates

    def _detect_frame_sources(self, target: Any, stack_wcs: WCS) -> tuple[list[FrameDetection], int, int]:
        """Find all the dots in all the photos.

        We have a lot of photos to check, so we divide the work. This splits
        the photos across all the available CPU cores so they can be processed
        at the same time, making it much faster.

        Returns
        -------
        frame_detections : `list` [`FrameDetection`]
            All the dots found in all the photos.
        frames_with_wcs_estimate : `int`
            How many photos we successfully checked.
        frames_excluded_missing_pointing_metadata : `int`
            How many photos we had to skip because they were missing data.
        """
        frame_detections: list[FrameDetection] = []
        frames_with_wcs_estimate = 0
        frames_excluded_missing_pointing_metadata = 0

        light_frames = [f for f in target.frames if f.role == "LIGHT" and f.timestamp is not None]
        total_light = len(light_frames)
        if total_light == 0:
            return frame_detections, frames_with_wcs_estimate, frames_excluded_missing_pointing_metadata

        max_workers = min(total_light, os.cpu_count() or 1)
        completed = 0
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _detect_sources_in_one_frame,
                    frame.path,
                    frame.timestamp,
                    stack_wcs,
                    self.config.detection_fwhm_px,
                    self.config.detection_threshold_sigma,
                )
                for frame in light_frames
            ]
            for future in as_completed(futures):
                completed += 1
                if completed % 5 == 0 or completed == total_light:
                    print(
                        f"  [Asteroid Recovery] Scanned {completed}/{total_light} frames for point sources..."
                    )
                status, detections = future.result()
                if status == "read_failed":
                    continue
                if status == "no_wcs":
                    frames_excluded_missing_pointing_metadata += 1
                    continue
                frames_with_wcs_estimate += 1
                frame_detections.extend(detections)

        return frame_detections, frames_with_wcs_estimate, frames_excluded_missing_pointing_metadata

    def _cross_match_ephemeris(
        self,
        candidates: list[AsteroidRecoveryCandidate],
        frame_detections: list[FrameDetection],
        stack_wcs: WCS,
        stack_header: fits.Header,
    ) -> list[AsteroidRecoveryCandidate]:
        """Check our final list of moving objects against the database.

        We only do this if we actually found something that looks like a real
        asteroid.

        Returns
        -------
        candidates : `list[AsteroidRecoveryCandidate]`
            The same list of objects, but with database match info added if we
            found any.
        """
        has_rate_confirmed_candidate = any(
            candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED for candidate in candidates
        )
        if not has_rate_confirmed_candidate:
            return candidates
        if not frame_detections:
            return candidates

        frame_width_px = stack_header.get("NAXIS1", 0)
        frame_height_px = stack_header.get("NAXIS2", 0)
        pixel_scales_deg = proj_plane_pixel_scales(stack_wcs)
        field_width_deg = pixel_scales_deg[0] * frame_width_px
        field_height_deg = pixel_scales_deg[1] * frame_height_px
        radius_deg = min(
            max(field_width_deg, field_height_deg) / 2.0 * _FIELD_QUERY_RADIUS_BUFFER_FACTOR,
            _FIELD_QUERY_RADIUS_CAP_DEG,
        )

        center_right_ascension_deg = float(stack_wcs.wcs.crval[0])
        center_declination_deg = float(stack_wcs.wcs.crval[1])
        epoch_unix = statistics.mean(detection.timestamp for detection in frame_detections)

        cross_matcher = EphemerisCrossMatcher(self.config)
        return cross_matcher.cross_match_candidates(
            candidates, center_right_ascension_deg, center_declination_deg, epoch_unix, radius_deg
        )
