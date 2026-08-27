"""Tools to find moving objects like asteroids.

This takes a list of possible star-like dots from our images and runs them
through a series of tests to find the real asteroids:
1. Does it show up in multiple pictures over time? (Persistence)
2. Is it actually moving across the sky, or just a bad pixel on the camera?
3. Is it moving in a straight line at a reasonable speed? (Linearity/Rate)

This helps us ignore random noise (like cosmic rays) and broken camera pixels.
"""

import logging
import math
import statistics
import uuid
from collections import defaultdict

import numpy as np

from astrometricslib.models.moving_object import (
    AsteroidRecoveryCandidate,
    CascadeStage,
    FrameDetection,
    MovingObjectTrack,
)
from astrometricslib.models.moving_object_config import MovingObjectConfig

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600.0

# A very tiny number used to prevent math errors. If an object hasn't moved
# at all, calculating its straight-line speed would involve dividing by zero.
# We use this to safely handle those cases.
_LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON = 1e-12


def _tangent_plane_offset_arcsec(
    right_ascension_deg: float,
    declination_deg: float,
    reference_right_ascension_deg: float,
    reference_declination_deg: float,
) -> tuple[float, float]:
    """Calculate the flat X/Y distance between two points on the sky.

    Because the sky is curved, measuring distance directly is hard. For
    small distances, we can pretend the sky is flat (a 'tangent plane')
    to make the math simpler.

    Parameters
    ----------
    right_ascension_deg : `float`
        The X-coordinate (RA) of the point we are checking.
    declination_deg : `float`
        The Y-coordinate (Dec) of the point we are checking.
    reference_right_ascension_deg : `float`
        The X-coordinate (RA) of our center or starting point.
    reference_declination_deg : `float`
        The Y-coordinate (Dec) of our center or starting point.

    Returns
    -------
    right_ascension_offset_arcsec : `float`
        How far left or right the point is from center, in arcseconds.
    declination_offset_arcsec : `float`
        How far up or down the point is from center, in arcseconds.
    """
    right_ascension_offset_arcsec = (
        (right_ascension_deg - reference_right_ascension_deg)
        * math.cos(math.radians(reference_declination_deg))
        * 3600.0
    )
    declination_offset_arcsec = (declination_deg - reference_declination_deg) * 3600.0
    return right_ascension_offset_arcsec, declination_offset_arcsec


def _fit_linear_rate_arcsec_per_hour(
    timestamps: np.ndarray, tangent_plane_offsets_arcsec: np.ndarray
) -> tuple[float, float]:
    """Calculate how fast an object moves in a straight line, on one axis.

    Parameters
    ----------
    timestamps : `numpy.ndarray`
        The times when the object was seen.
    tangent_plane_offsets_arcsec : `numpy.ndarray`
        Where the object was located at each of those times.

    Returns
    -------
    rate_arcsec_per_hour : `float`
        How fast the object is moving along this axis.
    r_squared : `float`
        How perfectly straight the movement is (1.0 is a perfect straight
        line).
    """
    rate_arcsec_per_second, intercept = np.polyfit(timestamps, tangent_plane_offsets_arcsec, 1)
    fitted_values = rate_arcsec_per_second * timestamps + intercept

    mean_offset_arcsec = np.mean(tangent_plane_offsets_arcsec)
    total_sum_of_squares = float(np.sum((tangent_plane_offsets_arcsec - mean_offset_arcsec) ** 2))
    residual_sum_of_squares = float(np.sum((tangent_plane_offsets_arcsec - fitted_values) ** 2))

    if total_sum_of_squares < _LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON:
        r_squared = 1.0 if residual_sum_of_squares < _LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON else 0.0
    else:
        r_squared = 1.0 - (residual_sum_of_squares / total_sum_of_squares)

    return rate_arcsec_per_second * _SECONDS_PER_HOUR, r_squared


class MovingObjectDetector:
    """Connects the dots between photos to find real moving objects.

    This runs the three main tests: checking if it shows up in multiple
    pictures, checking if it's actually moving on the sky, and checking
    if it moves in a straight line.

    Parameters
    ----------
    config : `MovingObjectConfig`
        The settings for how strict these tests should be.
    """

    def __init__(self, config: MovingObjectConfig):  # ruff: ignore[missing-return-type-special-method]
        self.config = config

    def detect_candidates(
        self, target_id: str, frame_detections: list[FrameDetection]
    ) -> list[AsteroidRecoveryCandidate]:
        """Connect the dots between frames and run all the tests.

        Parameters
        ----------
        target_id : `str`
            The ID of the target we are analyzing.
        frame_detections : `list` [`FrameDetection`]
            A list of all the possible dots found in all the pictures.

        Returns
        -------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            The list of possible moving objects we found. Even if a dot
            failed a test (like it didn't move fast enough), we still include
            it in this list with a note explaining why it failed.
        """
        # Step 2: Chain detections that appear consistently across
        # multiple frames (Persistence test)
        chains = self._chain_detections_by_persistence(frame_detections)
        candidates = []
        for chain in chains:
            # Steps 3-4: Run reference-frame (stationary test) and
            # rate/linearity checks
            candidates.append(self._evaluate_chain(target_id, chain))
        return candidates

    def _evaluate_chain(self, target_id: str, chain: list[FrameDetection]) -> AsteroidRecoveryCandidate:
        """Run one connected path of dots through our three main tests.

        Parameters
        ----------
        target_id : `str`
            The name of the target we are analyzing.
        chain : `list` [`FrameDetection`]
            A series of dots from different pictures that we think might
            be the same object.

        Returns
        -------
        candidate : `AsteroidRecoveryCandidate`
            The final result of the tests.
        """
        candidate_id = str(uuid.uuid4())

        if len(chain) < self.config.min_frames_for_persistence:
            return AsteroidRecoveryCandidate(
                id=candidate_id,
                target_id=target_id,
                frame_detections=chain,
                cascade_stage=CascadeStage.REJECTED_SINGLE_FRAME,
            )

        reference_frame_stage = self._evaluate_reference_frame_test(chain)
        if reference_frame_stage != CascadeStage.REFERENCE_FRAME_CONFIRMED:
            return AsteroidRecoveryCandidate(
                id=candidate_id,
                target_id=target_id,
                frame_detections=chain,
                cascade_stage=reference_frame_stage,
            )

        track, rate_linearity_stage = self._fit_rate_linearity(chain)
        return AsteroidRecoveryCandidate(
            id=candidate_id,
            target_id=target_id,
            frame_detections=chain,
            track=track,
            cascade_stage=rate_linearity_stage,
        )

    def _chain_detections_by_persistence(
        self, frame_detections: list[FrameDetection]
    ) -> list[list[FrameDetection]]:
        """Try to connect dots picture to picture by finding the closest match.

        It looks for a dot in the next picture that is close to where we'd
        expect it to be based on how much time has passed.

        Parameters
        ----------
        frame_detections : `list` [`FrameDetection`]
            All the dots found in all the pictures.

        Returns
        -------
        chains : `list` [`list` [`FrameDetection`]]
            The paths of dots we connected together.
        """
        detections_by_frame: dict[str, list[FrameDetection]] = defaultdict(list)
        for detection in frame_detections:
            detections_by_frame[detection.frame_path].append(detection)

        frame_paths_in_time_order = sorted(
            detections_by_frame.keys(), key=lambda path: detections_by_frame[path][0].timestamp
        )

        open_chains: list[list[FrameDetection]] = []
        for frame_path in frame_paths_in_time_order:
            unmatched_detections = list(detections_by_frame[frame_path])

            if not open_chains:
                for detection in unmatched_detections:
                    open_chains.append([detection])
                continue

            ra_arr = np.array([d.right_ascension_deg for d in unmatched_detections], dtype=float)
            dec_arr = np.array([d.declination_deg for d in unmatched_detections], dtype=float)
            matched_mask = np.zeros(len(unmatched_detections), dtype=bool)

            for chain in open_chains:
                available_indices = np.where(~matched_mask)[0]
                if available_indices.size == 0:
                    break

                last_detection = chain[-1]
                elapsed_seconds = abs(unmatched_detections[0].timestamp - last_detection.timestamp)
                elapsed_hours = elapsed_seconds / _SECONDS_PER_HOUR
                match_radius_arcsec = self.config.rate_max_arcsec_per_hour * elapsed_hours

                cos_dec = math.cos(math.radians(last_detection.declination_deg))
                max_deg = (match_radius_arcsec / 3600.0) * 1.2
                # When looking near the North or South Pole, the lines of
                # longitude (Right Ascension) get very close together. We use
                # a small math trick (max with 1e-6) to make sure we don't
                # accidentally divide by zero when calculating how far away
                # to look for the next dot.
                ra_bbox_half_width_deg = max_deg / max(abs(cos_dec), 1e-6)
                ra_min = last_detection.right_ascension_deg - ra_bbox_half_width_deg
                ra_max = last_detection.right_ascension_deg + ra_bbox_half_width_deg
                dec_min = last_detection.declination_deg - max_deg
                dec_max = last_detection.declination_deg + max_deg

                bbox_mask = (
                    (ra_arr[available_indices] >= ra_min)
                    & (ra_arr[available_indices] <= ra_max)
                    & (dec_arr[available_indices] >= dec_min)
                    & (dec_arr[available_indices] <= dec_max)
                )
                candidate_sub_indices = available_indices[bbox_mask]
                if candidate_sub_indices.size == 0:
                    continue

                ra_diff = ra_arr[candidate_sub_indices] - last_detection.right_ascension_deg
                ra_offsets = ra_diff * cos_dec * 3600.0
                dec_offsets = (dec_arr[candidate_sub_indices] - last_detection.declination_deg) * 3600.0
                distances = np.hypot(ra_offsets, dec_offsets)

                min_idx_in_cand = int(np.argmin(distances))
                if distances[min_idx_in_cand] <= match_radius_arcsec:
                    best_global_idx = candidate_sub_indices[min_idx_in_cand]
                    chain.append(unmatched_detections[best_global_idx])
                    matched_mask[best_global_idx] = True

            for idx in np.where(~matched_mask)[0]:
                open_chains.append([unmatched_detections[idx]])

        return open_chains

    def _evaluate_reference_frame_test(self, chain: list[FrameDetection]) -> CascadeStage:
        """Check if the dot is a real object or a camera artifact.

        Because the telescope shakes a tiny bit ('dithering'), a real star
        will move slightly on the camera sensor from picture to picture, but
        stay in the same place on the sky. A broken camera pixel will stay
        in the exact same place on the sensor, but look like it's moving
        across the sky.

        Parameters
        ----------
        chain : `list` [`FrameDetection`]
            The path of connected dots we want to check.

        Returns
        -------
        cascade_stage : `CascadeStage`
            The result: is it a bad pixel, a normal star, or a real moving
            object?
        """
        mean_right_ascension_deg = statistics.mean(detection.right_ascension_deg for detection in chain)
        mean_declination_deg = statistics.mean(detection.declination_deg for detection in chain)
        sky_spread_arcsec = max(
            math.hypot(
                *_tangent_plane_offset_arcsec(
                    detection.right_ascension_deg,
                    detection.declination_deg,
                    mean_right_ascension_deg,
                    mean_declination_deg,
                )
            )
            for detection in chain
        )

        mean_pixel_x = statistics.mean(detection.pixel_x for detection in chain)
        mean_pixel_y = statistics.mean(detection.pixel_y for detection in chain)
        pixel_spread_px = max(
            math.hypot(detection.pixel_x - mean_pixel_x, detection.pixel_y - mean_pixel_y)
            for detection in chain
        )

        # Stage 3 tests: First, we check if the dot stays on the exact same
        # camera pixel in every photo. If it does, it's just a broken "hot"
        # pixel,
        # not a real object in the sky. If it passes that, we then check if
        # it's
        # moving across the actual sky.
        if pixel_spread_px < self.config.pixel_match_tolerance_px:
            return CascadeStage.REJECTED_STATIONARY_PIXEL
        if sky_spread_arcsec < self.config.sky_match_tolerance_arcsec:
            return CascadeStage.REJECTED_STATIONARY_SKY
        return CascadeStage.REFERENCE_FRAME_CONFIRMED

    def _fit_rate_linearity(
        self, chain: list[FrameDetection]
    ) -> tuple[MovingObjectTrack | None, CascadeStage]:
        """Check if the object moves in a straight line at a reasonable speed.

        Asteroids don't usually zig-zag or move incredibly fast.

        Parameters
        ----------
        chain : `list` [`FrameDetection`]
            The path of connected dots we want to check.

        Returns
        -------
        track : `MovingObjectTrack` or `None`
            The calculated path details, or None if it failed the test.
        cascade_stage : `CascadeStage`
            Did it pass or fail?
        """
        # Calculate the mean position to serve as a local tangent-plane origin
        mean_right_ascension_deg = statistics.mean(detection.right_ascension_deg for detection in chain)
        mean_declination_deg = statistics.mean(detection.declination_deg for detection in chain)

        # Extract timestamps for the linear fit
        timestamps = np.array([detection.timestamp for detection in chain])

        # Project spherical RA/Dec onto a flat Cartesian plane (arcsec)
        # relative to the mean position
        right_ascension_offsets_arcsec = np.array([
            _tangent_plane_offset_arcsec(
                detection.right_ascension_deg,
                detection.declination_deg,
                mean_right_ascension_deg,
                mean_declination_deg,
            )[0]
            for detection in chain
        ])
        declination_offsets_arcsec = np.array([
            _tangent_plane_offset_arcsec(
                detection.right_ascension_deg,
                detection.declination_deg,
                mean_right_ascension_deg,
                mean_declination_deg,
            )[1]
            for detection in chain
        ])

        # Fit a straight line to the projected motion in both RA and
        # Dec axes against time
        right_ascension_rate, right_ascension_r_squared = _fit_linear_rate_arcsec_per_hour(
            timestamps, right_ascension_offsets_arcsec
        )
        declination_rate, declination_r_squared = _fit_linear_rate_arcsec_per_hour(
            timestamps, declination_offsets_arcsec
        )
        linear_fit_r_squared = min(right_ascension_r_squared, declination_r_squared)
        total_rate_arcsec_per_hour = math.hypot(right_ascension_rate, declination_rate)

        rate_in_range = (
            self.config.rate_min_arcsec_per_hour
            <= total_rate_arcsec_per_hour
            <= self.config.rate_max_arcsec_per_hour
        )

        # Reject the object if it's moving way too fast or too slow (like a
        # satellite instead of an asteroid), or if it's zig-zagging randomly
        # instead of moving in a straight line.
        if linear_fit_r_squared < self.config.rate_linearity_r_squared_min or not rate_in_range:
            return None, CascadeStage.REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE

        track = MovingObjectTrack(
            right_ascension_rate_arcsec_per_hour=right_ascension_rate,
            declination_rate_arcsec_per_hour=declination_rate,
            total_rate_arcsec_per_hour=total_rate_arcsec_per_hour,
            linear_fit_r_squared=linear_fit_r_squared,
            fit_start_timestamp=float(np.min(timestamps)),
            fit_end_timestamp=float(np.max(timestamps)),
        )
        return track, CascadeStage.RATE_LINEARITY_CONFIRMED
