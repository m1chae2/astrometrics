"""Purpose: Orchestrates the asteroid-recovery pipeline end to end.

Description: Wires per-frame WCS estimation, point-source detection, the
moving-object discrimination cascade, and ephemeris cross-matching into
one pipeline that takes a `Target` directly (not an `AnalysisContext`,
since this pipeline works from a set of individual per-frame images
plus the stack header, not one image).
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
from astrometricslib.tasks.shared.source_detection_shared import SourceDetector

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
    """Read one frame, estimate its WCS, and detect its point sources.

    Module-level so it can run in a worker process (see
    `AsteroidRecoveryPipeline._detect_frame_sources`) -- each frame's
    read/WCS-estimate/detect is fully independent of every other
    frame, so the per-frame loop parallelizes across processes with
    no change to what gets detected.

    Returns
    -------
    status : `"ok"`, `"no_wcs"`, or `"read_failed"`
        `"read_failed"` if the frame couldn't be opened or had no
        pixel data; `"no_wcs"` if its WCS couldn't be estimated from
        the mount-pointing headers; `"ok"` otherwise.
    detections : `list` [`FrameDetection`]
        This frame's point-source detections, or `[]` if `status` is
        not `"ok"`.
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
    """Recover known bright asteroids from a target's plate-solved frames.

    Parameters
    ----------
    config : `MovingObjectConfig`, optional
        Pipeline configuration. If `None` (default), loaded via
        `MovingObjectConfigLoader.load_moving_object_config`.
    """

    def __init__(self, config: MovingObjectConfig | None = None):  # ruff: ignore[missing-return-type-special-method]
        self.config = config or MovingObjectConfigLoader.load_moving_object_config()
        # Populated by process(); read by Target.analyze_target to build
        # the quality summary, matching the
        # last_run_diagnostics/last_run_zero_order_saturation_fractions
        # convention already used by the Siril driver and
        # SpectroscopyPipeline.
        self.last_run_metrics: dict[str, int] = {}

    def process(self, target: Any) -> list[AsteroidRecoveryCandidate]:
        """Run the full asteroid-recovery pipeline against one target.

        Parameters
        ----------
        target : `astrometricslib.models.target.Target`
            The target to process. Must already have a `stacked_image`
            (i.e. `analyze_target(type="astrometry")` must have already
            run).

        Returns
        -------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            Every candidate the discrimination cascade produced,
            including rejected ones (labeled with the cascade stage
            they were rejected at).

        Raises
        ------
        ValueError
            If the target has no `stacked_image`.
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
        """Estimate each light frame's WCS and run point-source detection.

        Frames are fully independent of each other, so this fans the
        per-frame work (read, WCS estimate, DAOStarFinder detection)
        out across worker processes -- real parallelism, not threads,
        since this is CPU-bound and threads would stay serialized
        behind the GIL. Detection results are identical to running
        the same work serially; only wall-clock changes.

        Returns
        -------
        frame_detections : `list` [`FrameDetection`]
        frames_with_wcs_estimate : `int`
        frames_excluded_missing_pointing_metadata : `int`
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
        """Run the ephemeris cross-match (cascade step 5).

        Only runs if any candidate reached rate-linearity confirmation.

        Returns
        -------
        candidates : `list[AsteroidRecoveryCandidate]`
            The input candidates, augmented with ephemeris cross-match
            results, or returned unchanged if no candidate reached
            rate-linearity confirmation or no frame detections exist.
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
