"""Purpose: Moving-object discrimination cascade for asteroid recovery.

Description: Chains per-frame point-source detections (already
morphology-filtered by SourceDetector -- cascade step 1) across frames,
then applies the persistence, reference-frame, and rate/linearity tests
(cascade steps 2-4) to separate real slow movers from cosmic rays,
missed stars, and hot pixels. Ephemeris cross-matching (cascade step 5)
is a separate, later stage (EphemerisCrossMatcher).
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

# Below this total sum-of-squares (in arcsec^2), a chain's sky positions
# are treated as having no meaningful spread at all for R^2 purposes,
# avoiding a division by (near-)zero. Not a tunable quality threshold --
# a numerical-stability guard only.
_LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON = 1e-12


def _tangent_plane_offset_arcsec(
    right_ascension_deg: float,
    declination_deg: float,
    reference_right_ascension_deg: float,
    reference_declination_deg: float,
) -> tuple[float, float]:
    """Project a sky position onto a small tangent-plane offset, in arcsec.

    A small-angle approximation (`right_ascension` scaled by
    `cos(declination)`, no great-circle correction) -- adequate at the
    sub-degree field-of-view scales this pipeline operates at.

    Parameters
    ----------
    right_ascension_deg : `float`
        The position's right ascension, in degrees.
    declination_deg : `float`
        The position's declination, in degrees.
    reference_right_ascension_deg : `float`
        The reference position's right ascension, in degrees.
    reference_declination_deg : `float`
        The reference position's declination, in degrees.

    Returns
    -------
    right_ascension_offset_arcsec : `float`
        East-west offset from the reference position, in arcsec.
    declination_offset_arcsec : `float`
        North-south offset from the reference position, in arcsec.
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
    """Fits a single tangent-plane axis's offsets linearly against time.

    Parameters
    ----------
    timestamps : `numpy.ndarray`
        Unix-epoch-seconds timestamps, one per frame detection in the chain.
    tangent_plane_offsets_arcsec : `numpy.ndarray`
        This axis's tangent-plane offset (arcsec) at each timestamp.

    Returns
    -------
    rate_arcsec_per_hour : `float`
        The fitted linear rate of change, in arcsec/hour.
    r_squared : `float`
        The fit's coefficient of determination, in [0, 1].
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
    """Chain per-frame detections into candidate moving-object tracks.

    Applies the persistence, reference-frame, and rate/linearity
    discrimination cascade stages.

    Parameters
    ----------
    config : `MovingObjectConfig`
        Configuration supplying the cascade's match radii, thresholds,
        and rate bounds.
    """

    def __init__(self, config: MovingObjectConfig):  # ruff: ignore[missing-return-type-special-method]
        self.config = config

    def detect_candidates(
        self, target_id: str, frame_detections: list[FrameDetection]
    ) -> list[AsteroidRecoveryCandidate]:
        """Chains detections across frames and runs the discrimination cascade.

        Parameters
        ----------
        target_id : `str`
            The `Target` these detections were made on.
        frame_detections : `list` [`FrameDetection`]
            All morphology-filtered point-source detections across the
            target's individual frames (cascade step 1 already applied
            upstream, by whichever caller ran `SourceDetector` on each
            frame).

        Returns
        -------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            One candidate per chained track, including rejected ones
            (labeled with the cascade stage they were rejected at) --
            rejections are labeled, not dropped, so the quality summary
            can report on them.
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
        """Run the persistence/reference-frame/rate-linearity cascade.

        Parameters
        ----------
        target_id : `str`
            The target identifier.
        chain : `list` [`FrameDetection`]
            Chained detections across frames.

        Returns
        -------
        candidate : `AsteroidRecoveryCandidate`
            The evaluated candidate object.
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
        """Greedily chain detections via nearest-neighbor sky matching.

        The match radius is scaled by elapsed time (cascade step 2).

        Parameters
        ----------
        frame_detections : `list` [`FrameDetection`]
            All detections across every frame, unordered.

        Returns
        -------
        chains : `list` [`list` [`FrameDetection`]]
            Every resulting chain, including single-detection chains
            (persistence hasn't been evaluated yet at this point).
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
                # The RA bbox half-width must use the true cos(dec), floored
                # only against literal division-by-zero at the pole -- not
                # against some larger value, which would narrow the box
                # below what a real near-pole match needs (RA degrees
                # genuinely blow up near the pole for a fixed arcsec
                # separation) and silently drop valid candidates.
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
        """Distinguish a missed star from a hot pixel (cascade step 3).

        A missed star has a fixed sky position but a moving native
        pixel position under dithering; a hot pixel has a fixed native
        pixel position but an apparently moving sky position.

        Parameters
        ----------
        chain : `list` [`FrameDetection`]
            A chain that has already passed the persistence check.

        Returns
        -------
        cascade_stage : `CascadeStage`
            `REJECTED_STATIONARY_PIXEL`, `REJECTED_STATIONARY_SKY`, or
            `REFERENCE_FRAME_CONFIRMED`.
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

        # Checked first: a fixed native-pixel signature is the hot-pixel
        # discriminator regardless of how much its derived sky position
        # appears to drift under per-frame WCS re-centering (see
        # frame_wcs_composer's mount-pointing caveat).
        if pixel_spread_px < self.config.pixel_match_tolerance_px:
            return CascadeStage.REJECTED_STATIONARY_PIXEL
        if sky_spread_arcsec < self.config.sky_match_tolerance_arcsec:
            return CascadeStage.REJECTED_STATIONARY_SKY
        return CascadeStage.REFERENCE_FRAME_CONFIRMED

    def _fit_rate_linearity(
        self, chain: list[FrameDetection]
    ) -> tuple[MovingObjectTrack | None, CascadeStage]:
        """Fit the chain's sky motion linearly against time.

        Checks the fitted rate against the configured rate bounds
        (cascade step 4).

        Parameters
        ----------
        chain : `list` [`FrameDetection`]
            A chain that has already passed the reference-frame test.

        Returns
        -------
        track : `MovingObjectTrack` or `None`
            The fitted track, or `None` if the chain was rejected.
        cascade_stage : `CascadeStage`
            `RATE_LINEARITY_CONFIRMED` or
            `REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE`.
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

        # Reject the candidate if it is moving too fast/slow (e.g.
        # noise/satellite)
        # or if the motion is not a straight line (low R-squared).
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
