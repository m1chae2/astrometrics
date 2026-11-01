"""Tools to find moving objects like asteroids.

This takes a list of possible star-like dots from our images and runs them
through a series of tests to find the real asteroids:
1. Does it show up in multiple pictures over time? (Recording)
2. Is it actually moving across the sky, or just a bad pixel on the camera?
3. Is it moving in a straight line at a reasonable speed? (Linearity/Rate)

This helps us ignore random noise (like cosmic rays) and broken camera pixels.

Dots are only linked within one observing session (one observing night, as
named by `observing_night_id`). Pictures from different nights are never
chained together: with gaps of days, a search radius large enough to follow
an asteroid would also link two unrelated stars. Joining a mover seen on
different nights is a separate step that this module does not do.

A straight-line track is judged by how far its dots miss the fitted line, in
arcseconds, compared with the position error of one picture. R-squared is
still reported, but it does not decide anything: it is close to 1 for any
chain with a large displacement, however badly the dots scatter.
"""

import logging
import math
import statistics
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from astrometricslib.models.moving_object import (
    AsteroidDetectionCandidate,
    CascadeStage,
    FrameDetection,
    MovingObjectTrack,
)
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.shared.angles import wrapped_ra_difference_deg
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600.0

# A very tiny number used to prevent math errors. If an object hasn't moved
# at all, calculating its straight-line speed would involve dividing by zero.
# We use this to safely handle those cases.
_LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON = 1e-12


@dataclass
class ResidualCriterionSummary:
    """Counts of how the straight-line (residual) test went in one run.

    Attributes
    ----------
    chains_tested : `int`
        Chains that reached the straight-line test.
    chains_rejected : `int`
        Chains whose dots missed the fitted line by more than the allowed
        multiple of the position error.
    chains_non_monotonic : `int`
        Chains that passed the other straight-line conditions but moved back
        along their fitted direction by more than the position error.
    assumed_error_detections : `int`
        Dots, in the tested chains, whose picture had no measured position
        error, so the assumed value from the settings was used.
    worst_accepted_ratio : `float` or `None`
        The largest ratio of RMS residual to position error among the
        chains that were accepted, or `None` if none were.
    """

    chains_tested: int = 0
    chains_rejected: int = 0
    chains_non_monotonic: int = 0
    assumed_error_detections: int = 0
    worst_accepted_ratio: float | None = None


def _circular_mean_degrees(values_deg: Iterable[float]) -> float:
    """Average a set of angles (e.g. Right Ascension) that wrap at 360 deg.

    A plain arithmetic mean of angles close to the 0/360 deg boundary (e.g.
    359.95 and 0.05) gives a nonsense result near the opposite side of the
    circle (180.0 instead of ~0.0). Averaging the unit vectors instead
    handles the wraparound correctly.

    Parameters
    ----------
    values_deg : `Iterable` [`float`]
        Angles in degrees to average.

    Returns
    -------
    mean_deg : `float`
        The circular mean, in degrees, normalized to [0, 360).
    """
    radians = [math.radians(v) for v in values_deg]
    mean_sin = statistics.mean(math.sin(r) for r in radians)
    mean_cos = statistics.mean(math.cos(r) for r in radians)
    return math.degrees(math.atan2(mean_sin, mean_cos)) % 360.0


def _tangent_plane_offset_arcsec(
    right_ascension_deg: float,
    declination_deg: float,
    reference_right_ascension_deg: float,
    reference_declination_deg: float,
) -> tuple[float, float]:
    """Calculate the flat X/Y distance between two points on the sky.

    Because the sky is curved, measuring distance directly is hard. For
    small distances, we can pretend the sky is flat (a 'tangent plane')
    to make the math simpler. The RA difference goes through
    `wrapped_ra_difference_deg`, so two points on opposite sides of
    RA = 0 deg are still close together.

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
        wrapped_ra_difference_deg(right_ascension_deg, reference_right_ascension_deg)
        * math.cos(math.radians(reference_declination_deg))
        * 3600.0
    )
    declination_offset_arcsec = (declination_deg - reference_declination_deg) * 3600.0
    return right_ascension_offset_arcsec, declination_offset_arcsec


def _combined_axis_error(axis_errors: list[float | None], scalar_errors: np.ndarray) -> float:
    """Combine the position error of one sky axis over the pictures of a chain.

    Parameters
    ----------
    axis_errors : `list` [`float` or `None`]
        Each picture's error on this axis, in arcseconds, or `None` when the
        picture did not measure one.
    scalar_errors : `numpy.ndarray`
        Each picture's one error (measured, or the assumed value from the
        settings), used for a picture with no error on this axis.

    Returns
    -------
    error : `float`
        The square root of the mean of the squares, in arcseconds.
    """
    errors = np.array([
        axis_error if axis_error is not None else scalar_error
        for axis_error, scalar_error in zip(axis_errors, scalar_errors, strict=True)
    ])
    return float(np.sqrt(np.mean(errors**2)))


def _fit_linear_rate_arcsec_per_hour(
    timestamps: np.ndarray, tangent_plane_offsets_arcsec: np.ndarray
) -> tuple[float, float, float]:
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
        line). It is near 1 for any chain with a large displacement, so it
        is a diagnostic only.
    residual_rms_arcsec : `float`
        The root-mean-square (RMS) distance, in arcseconds, between the
        positions and the fitted line. It is the square root of the mean of
        the squared misses.
    """
    # Time is measured from its mean so the fit works with small numbers.
    centred_timestamps = timestamps - np.mean(timestamps)
    rate_arcsec_per_second, intercept = np.polyfit(centred_timestamps, tangent_plane_offsets_arcsec, 1)
    fitted_values = rate_arcsec_per_second * centred_timestamps + intercept

    mean_offset_arcsec = np.mean(tangent_plane_offsets_arcsec)
    total_sum_of_squares = float(np.sum((tangent_plane_offsets_arcsec - mean_offset_arcsec) ** 2))
    residual_sum_of_squares = float(np.sum((tangent_plane_offsets_arcsec - fitted_values) ** 2))

    if total_sum_of_squares < _LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON:
        r_squared = 1.0 if residual_sum_of_squares < _LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON else 0.0
    else:
        r_squared = 1.0 - (residual_sum_of_squares / total_sum_of_squares)

    residual_rms_arcsec = math.sqrt(residual_sum_of_squares / len(tangent_plane_offsets_arcsec))
    return float(rate_arcsec_per_second) * _SECONDS_PER_HOUR, r_squared, residual_rms_arcsec


def _reverses_direction(
    timestamps: np.ndarray,
    right_ascension_offsets_arcsec: np.ndarray,
    declination_offsets_arcsec: np.ndarray,
    right_ascension_rate: float,
    declination_rate: float,
    errors_arcsec: np.ndarray,
) -> bool:
    """Say whether a chain moves back along its own fitted direction.

    Each dot is projected onto the direction of the fitted motion (the unit
    vector along the two fitted rates). A real mover only ever goes forward
    along that direction, so its projections, taken in time order, never
    decrease. A star whose measured centre flips between two points
    alternates instead. A step back counts only when it is larger than the
    combined position error of the two dots compared (the square root of the
    sum of their squared errors), so noise on a slow mover does not trip it.
    Every earlier dot is compared with every later one, not only neighbours,
    so a slow drift of small steps cannot hide a larger reversal.

    Parameters
    ----------
    timestamps : `numpy.ndarray`
        The times of the dots.
    right_ascension_offsets_arcsec : `numpy.ndarray`
        Tangent-plane RA positions of the dots, in arcseconds.
    declination_offsets_arcsec : `numpy.ndarray`
        Tangent-plane Dec positions of the dots, in arcseconds.
    right_ascension_rate, declination_rate : `float`
        The fitted rates on the two axes. Only their ratio is used.
    errors_arcsec : `numpy.ndarray`
        The position error of each dot, in arcseconds.

    Returns
    -------
    reverses : `bool`
        True if any earlier dot lies ahead of a later dot, along the fitted
        direction, by more than their combined position error.
    """
    total_rate = math.hypot(right_ascension_rate, declination_rate)
    if total_rate < _LINEAR_FIT_TOTAL_SUM_OF_SQUARES_EPSILON:
        return False
    order = np.argsort(timestamps, kind="stable")
    projections = (
        right_ascension_offsets_arcsec[order] * right_ascension_rate
        + declination_offsets_arcsec[order] * declination_rate
    ) / total_rate
    sorted_errors = errors_arcsec[order]
    # step_back[i, j] is how far dot i sits ahead of the later dot j.
    step_back = projections[:, None] - projections[None, :]
    tolerance = np.hypot(sorted_errors[:, None], sorted_errors[None, :])
    later_than = np.triu(np.ones_like(step_back, dtype=bool), k=1)
    return bool(np.any(later_than & (step_back > tolerance)))


# Chains whose last dot has a cosine of declination below this are within
# about 78 degrees of a pole. There the flat distance test is not close to
# the true angle, so the tree search is not trusted and every dot is tried.
_POLAR_COS_DEC_LIMIT = 0.2


def _unit_vectors(ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    """Turn sky positions into points on the unit sphere.

    Parameters
    ----------
    ra_deg, dec_deg : `numpy.ndarray`
        Right Ascension and Declination in degrees.

    Returns
    -------
    points : `numpy.ndarray`
        Array of shape (N, 3) with the x, y, z of each position.
    """
    ra_rad = np.radians(ra_deg)
    dec_rad = np.radians(dec_deg)
    cos_dec = np.cos(dec_rad)
    return np.column_stack((cos_dec * np.cos(ra_rad), cos_dec * np.sin(ra_rad), np.sin(dec_rad)))


class _ChainEnds:
    """Position and time of the last dot of every open chain, as arrays.

    Chaining compares every open chain with every dot of a new picture.
    Keeping the last positions in arrays lets that comparison run in
    NumPy instead of in a Python loop over the chains.
    """

    def __init__(self) -> None:
        self._count = 0
        self.ra_deg = np.zeros(64)
        self.dec_deg = np.zeros(64)
        self.cos_dec = np.zeros(64)
        self.timestamp = np.zeros(64)

    def add(self, detection: FrameDetection) -> None:
        """Start tracking a new chain whose last dot is `detection`."""
        if self._count == self.ra_deg.size:
            for name in ("ra_deg", "dec_deg", "cos_dec", "timestamp"):
                setattr(self, name, np.concatenate((getattr(self, name), np.zeros(self._count))))
        self._count += 1
        self.replace(self._count - 1, detection)

    def replace(self, chain_index: int, detection: FrameDetection) -> None:
        """Record `detection` as the new last dot of chain `chain_index`."""
        self.ra_deg[chain_index] = detection.right_ascension_deg
        self.dec_deg[chain_index] = detection.declination_deg
        self.cos_dec[chain_index] = math.cos(math.radians(detection.declination_deg))
        self.timestamp[chain_index] = detection.timestamp


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

    def __init__(self, config: MovingObjectConfig) -> None:
        self.config = config
        # How the straight-line test went in the most recent run.
        self.last_residual_summary = ResidualCriterionSummary()

    def detect_candidates(
        self, target_id: str, frame_detections: list[FrameDetection]
    ) -> list[AsteroidDetectionCandidate]:
        """Connect the dots between frames and run all the tests.

        The dots are first split by observing night, and each night is
        chained on its own. A mover seen on two nights comes out as two
        separate candidates.

        Parameters
        ----------
        target_id : `str`
            The ID of the target we are analyzing.
        frame_detections : `list` [`FrameDetection`]
            A list of all the possible dots found in all the pictures.

        Returns
        -------
        candidates : `list` [`AsteroidDetectionCandidate`]
            The list of possible moving objects we found. Even if a dot
            failed a test (like it didn't move fast enough), we still include
            it in this list with a note explaining why it failed.
        """
        self.last_residual_summary = ResidualCriterionSummary()
        detections_by_night: dict[str, list[FrameDetection]] = defaultdict(list)
        for detection in frame_detections:
            detections_by_night[observing_night_id(detection.timestamp)].append(detection)

        candidates = []
        # Night names are ISO dates, so sorting them puts the nights in order.
        for night in sorted(detections_by_night):
            # Step 2: Chain detections that appear consistently across
            # multiple frames of this night (Recording test)
            for chain in self._chain_detections_by_persistence(detections_by_night[night]):
                # Steps 3-4: Run reference-frame (stationary test) and
                # rate/linearity checks
                candidates.append(self._evaluate_chain(target_id, chain))
        return candidates

    def _evaluate_chain(self, target_id: str, chain: list[FrameDetection]) -> AsteroidDetectionCandidate:
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
        candidate : `AsteroidDetectionCandidate`
            The final result of the tests.
        """
        candidate_id = str(uuid.uuid4())

        if len(chain) < self.config.min_frames_for_persistence:
            return AsteroidDetectionCandidate(
                id=candidate_id,
                target_id=target_id,
                frame_detections=chain,
                cascade_stage=CascadeStage.REJECTED_SINGLE_FRAME,
            )

        reference_frame_stage = self._evaluate_reference_frame_test(chain)
        if reference_frame_stage != CascadeStage.REFERENCE_FRAME_CONFIRMED:
            return AsteroidDetectionCandidate(
                id=candidate_id,
                target_id=target_id,
                frame_detections=chain,
                cascade_stage=reference_frame_stage,
            )

        track, rate_linearity_stage = self._fit_rate_linearity(chain)
        return AsteroidDetectionCandidate(
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
        expect it to be based on how much time has passed. The search
        radius grows with that time but never beyond
        `MovingObjectConfig.chain_match_radius_max_arcsec`.

        Parameters
        ----------
        frame_detections : `list` [`FrameDetection`]
            The dots found in the pictures of one observing session.

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
        chain_ends = _ChainEnds()
        for frame_path in frame_paths_in_time_order:
            unmatched_detections = list(detections_by_frame[frame_path])

            if not open_chains:
                for detection in unmatched_detections:
                    open_chains.append([detection])
                    chain_ends.add(detection)
                continue

            ra_arr = np.array([d.right_ascension_deg for d in unmatched_detections], dtype=float)
            dec_arr = np.array([d.declination_deg for d in unmatched_detections], dtype=float)
            chain_count = len(open_chains)

            # Gather every (chain, detection) pair that falls within that
            # chain's own match radius, without claiming anything yet. Each
            # chain gets an independent, uncapped view of the frame's
            # detections here -- claiming happens in one global pass below so
            # that a detection is always matched to whichever open chain is
            # truly nearest, not just to whichever chain happens to be
            # listed first (which can silently swap track identity in a
            # crowded field).
            elapsed_seconds = np.abs(unmatched_detections[0].timestamp - chain_ends.timestamp[:chain_count])
            elapsed_hours = elapsed_seconds / _SECONDS_PER_HOUR
            # Capped at a few arcminutes so that a gap of hours inside
            # one night cannot grow the radius to a whole field of view,
            # where two unrelated stars would look like a mover.
            match_radius_arcsec = np.minimum(
                self.config.rate_max_arcsec_per_hour * elapsed_hours,
                self.config.chain_match_radius_max_arcsec,
            )
            pair_chains, pair_detections = self._find_nearby_pairs(
                ra_arr,
                dec_arr,
                chain_ends.ra_deg[:chain_count],
                chain_ends.dec_deg[:chain_count],
                chain_ends.cos_dec[:chain_count],
                match_radius_arcsec,
            )

            # Same distance formula as always, now on the nearby pairs only.
            # The box test is kept so the answer is exactly the old one.
            pair_last_dec = chain_ends.dec_deg[pair_chains]
            pair_cos_dec = chain_ends.cos_dec[pair_chains]
            pair_radius = match_radius_arcsec[pair_chains]
            max_deg = (pair_radius / 3600.0) * 1.2
            # When looking near the North or South Pole, the lines of
            # longitude (Right Ascension) get very close together. We use
            # a small math trick (max with 1e-6) to make sure we don't
            # accidentally divide by zero when calculating how far away
            # to look for the next dot.
            ra_bbox_half_width_deg = max_deg / np.maximum(np.abs(pair_cos_dec), 1e-6)
            # The RA difference wraps at 0/360 deg, so a chain near
            # RA = 0 still finds detections on the other side of it.
            ra_diff = wrapped_ra_difference_deg(ra_arr[pair_detections], chain_ends.ra_deg[pair_chains])
            pair_dec = dec_arr[pair_detections]
            in_box = (
                (np.abs(ra_diff) <= ra_bbox_half_width_deg)
                & (pair_dec >= pair_last_dec - max_deg)
                & (pair_dec <= pair_last_dec + max_deg)
            )
            ra_offsets = ra_diff * pair_cos_dec * 3600.0
            dec_offsets = (pair_dec - pair_last_dec) * 3600.0
            distances = np.hypot(ra_offsets, dec_offsets)
            keep = in_box & (distances <= pair_radius)
            pair_chains = pair_chains[keep]
            pair_detections = pair_detections[keep]
            distances = distances[keep]

            # Claim nearest-first so each detection goes to its globally
            # closest open chain, and each chain claims at most one
            # detection per frame. Ties keep chain order, then dot order.
            order = np.lexsort((pair_detections, pair_chains))
            order = order[np.argsort(distances[order], kind="stable")]
            matched_mask = np.zeros(len(unmatched_detections), dtype=bool)
            claimed_chains = np.zeros(chain_count, dtype=bool)
            for chain_index, detection_index in zip(
                pair_chains[order].tolist(), pair_detections[order].tolist(), strict=True
            ):
                if matched_mask[detection_index] or claimed_chains[chain_index]:
                    continue
                open_chains[chain_index].append(unmatched_detections[detection_index])
                chain_ends.replace(chain_index, unmatched_detections[detection_index])
                matched_mask[detection_index] = True
                claimed_chains[chain_index] = True

            for idx in np.where(~matched_mask)[0]:
                open_chains.append([unmatched_detections[idx]])
                chain_ends.add(unmatched_detections[idx])

        return open_chains

    def _find_nearby_pairs(
        self,
        ra_deg: np.ndarray,
        dec_deg: np.ndarray,
        chain_ra_deg: np.ndarray,
        chain_dec_deg: np.ndarray,
        chain_cos_dec: np.ndarray,
        chain_radius_arcsec: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """List the (chain, dot) pairs that might be within a chain's radius.

        This only narrows the field. The caller still applies the exact
        distance test, so a few extra pairs here cost nothing. A tree
        search over the dots of the new picture finds the pairs without
        comparing every chain with every dot.

        The tree measures true angles on the sphere. The distance test
        uses a flat approximation, which can read a little shorter than
        the true angle. Away from the poles the gap is under 1 percent of
        the radius, so a 10 percent margin keeps every pair the exact test
        would accept. Chains within `_POLAR_COS_DEC_LIMIT` of a pole (where the
        approximation breaks down) are paired with every dot instead.

        Parameters
        ----------
        ra_deg, dec_deg : `numpy.ndarray`
            Positions of the dots in the new picture.
        chain_ra_deg, chain_dec_deg, chain_cos_dec : `numpy.ndarray`
            Position of the last dot of each open chain, and the cosine of
            its declination.
        chain_radius_arcsec : `numpy.ndarray`
            Match radius of each open chain.

        Returns
        -------
        chain_indices, dot_indices : `numpy.ndarray`
            Equal-length integer arrays, one entry per candidate pair.
        """
        dot_count = ra_deg.size
        polar = np.abs(chain_cos_dec) < _POLAR_COS_DEC_LIMIT
        chain_indices_parts: list[np.ndarray] = []
        dot_indices_parts: list[np.ndarray] = []

        polar_chains = np.where(polar)[0]
        if polar_chains.size:
            chain_indices_parts.append(np.repeat(polar_chains, dot_count))
            dot_indices_parts.append(np.tile(np.arange(dot_count), polar_chains.size))

        regular_chains = np.where(~polar)[0]
        if regular_chains.size and dot_count:
            tree = cKDTree(_unit_vectors(ra_deg, dec_deg))
            chord_radius = np.radians(chain_radius_arcsec[regular_chains] / 3600.0) * 1.1 + 1e-12
            hits = tree.query_ball_point(
                _unit_vectors(chain_ra_deg[regular_chains], chain_dec_deg[regular_chains]), chord_radius
            )
            hit_counts = np.fromiter((len(h) for h in hits), dtype=np.intp, count=len(hits))
            if hit_counts.sum():
                chain_indices_parts.append(np.repeat(regular_chains, hit_counts))
                dot_indices_parts.append(
                    np.fromiter((i for h in hits for i in h), dtype=np.intp, count=int(hit_counts.sum()))
                )

        if not chain_indices_parts:
            empty = np.zeros(0, dtype=np.intp)
            return empty, empty
        return np.concatenate(chain_indices_parts), np.concatenate(dot_indices_parts)

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
        mean_right_ascension_deg = _circular_mean_degrees(
            detection.right_ascension_deg for detection in chain
        )
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

        A straight line is fitted to the sky positions on each axis. The
        chain passes when all of these hold:

        * The total rate is inside the allowed range.
        * On each axis, the RMS distance of the dots from the line is at
          most `residual_rms_max_multiple` times the position error of one
          picture. The error comes from each dot's
          `astrometric_error_arcsec`, or the assumed value in the settings
          when that is missing.
        * The fitted line moves the object at least
          `min_displacement_error_multiple` times that error between the
          first and the last picture. Without this, a star that only
          jitters by its position error would pass.
        * The dots never move back along the fitted direction by more than
          the position error (see `_reverses_direction`). A chain that
          meets every other condition but fails this one is rejected as
          `REJECTED_NON_MONOTONIC`.

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
        mean_right_ascension_deg = _circular_mean_degrees(
            detection.right_ascension_deg for detection in chain
        )
        mean_declination_deg = statistics.mean(detection.declination_deg for detection in chain)

        # Extract timestamps for the linear fit
        timestamps = np.array([detection.timestamp for detection in chain])

        # Project spherical RA/Dec onto a flat Cartesian plane (arcsec)
        # relative to the mean position
        offsets = [
            _tangent_plane_offset_arcsec(
                detection.right_ascension_deg,
                detection.declination_deg,
                mean_right_ascension_deg,
                mean_declination_deg,
            )
            for detection in chain
        ]
        right_ascension_offsets_arcsec = np.array([offset[0] for offset in offsets])
        declination_offsets_arcsec = np.array([offset[1] for offset in offsets])

        # Fit a straight line to the projected motion in both RA and
        # Dec axes against time
        right_ascension_rate, right_ascension_r_squared, right_ascension_rms_arcsec = (
            _fit_linear_rate_arcsec_per_hour(timestamps, right_ascension_offsets_arcsec)
        )
        declination_rate, declination_r_squared, declination_rms_arcsec = _fit_linear_rate_arcsec_per_hour(
            timestamps, declination_offsets_arcsec
        )
        linear_fit_r_squared = min(right_ascension_r_squared, declination_r_squared)
        total_rate_arcsec_per_hour = math.hypot(right_ascension_rate, declination_rate)

        rate_in_range = (
            self.config.rate_min_arcsec_per_hour
            <= total_rate_arcsec_per_hour
            <= self.config.rate_max_arcsec_per_hour
        )

        # The error of one position, combined over the dots of this chain as
        # the square root of the mean of the squares.
        assumed_error_detections = sum(1 for d in chain if d.astrometric_error_arcsec is None)
        errors_arcsec = np.array([
            d.astrometric_error_arcsec
            if d.astrometric_error_arcsec is not None
            else self.config.astrometric_error_default_arcsec
            for d in chain
        ])
        astrometric_error_arcsec = float(np.sqrt(np.mean(errors_arcsec**2)))
        # The error of each sky axis, where the pictures measured them. A
        # picture that did not has its one error used for both axes.
        right_ascension_error_arcsec = _combined_axis_error(
            [d.astrometric_error_ra_arcsec for d in chain], errors_arcsec
        )
        declination_error_arcsec = _combined_axis_error(
            [d.astrometric_error_dec_arcsec for d in chain], errors_arcsec
        )
        residual_limit_arcsec = self.config.residual_rms_max_multiple * astrometric_error_arcsec
        # Each axis's residual is judged against its own error, so an axis
        # that is measured more precisely is held to a tighter limit.
        residual_ok = (
            right_ascension_rms_arcsec <= self.config.residual_rms_max_multiple * right_ascension_error_arcsec
            and declination_rms_arcsec <= self.config.residual_rms_max_multiple * declination_error_arcsec
        )

        displacement_arcsec = total_rate_arcsec_per_hour * float(np.ptp(timestamps)) / _SECONDS_PER_HOUR
        moves_enough = (
            displacement_arcsec >= self.config.min_displacement_error_multiple * astrometric_error_arcsec
        )

        summary = self.last_residual_summary
        summary.chains_tested += 1
        summary.assumed_error_detections += assumed_error_detections
        if not residual_ok:
            summary.chains_rejected += 1

        # Reject the object if it's moving way too fast or too slow (like a
        # satellite instead of an asteroid), if its dots miss a straight
        # line by more than the position error allows, or if it hardly
        # moves compared with that error.
        if not (rate_in_range and residual_ok and moves_enough):
            return None, CascadeStage.REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE
        if _reverses_direction(
            timestamps,
            right_ascension_offsets_arcsec,
            declination_offsets_arcsec,
            right_ascension_rate,
            declination_rate,
            errors_arcsec,
        ):
            summary.chains_non_monotonic += 1
            return None, CascadeStage.REJECTED_NON_MONOTONIC

        if right_ascension_error_arcsec > 0.0 and declination_error_arcsec > 0.0:
            ratio = max(
                right_ascension_rms_arcsec / right_ascension_error_arcsec,
                declination_rms_arcsec / declination_error_arcsec,
            )
            if summary.worst_accepted_ratio is None or ratio > summary.worst_accepted_ratio:
                summary.worst_accepted_ratio = ratio

        track = MovingObjectTrack(
            right_ascension_rate_arcsec_per_hour=right_ascension_rate,
            declination_rate_arcsec_per_hour=declination_rate,
            total_rate_arcsec_per_hour=total_rate_arcsec_per_hour,
            linear_fit_r_squared=linear_fit_r_squared,
            residual_rms_right_ascension_arcsec=right_ascension_rms_arcsec,
            residual_rms_declination_arcsec=declination_rms_arcsec,
            astrometric_error_arcsec=astrometric_error_arcsec,
            astrometric_error_ra_arcsec=right_ascension_error_arcsec,
            astrometric_error_dec_arcsec=declination_error_arcsec,
            residual_limit_arcsec=residual_limit_arcsec,
            astrometric_error_assumed=assumed_error_detections > 0,
            fit_start_timestamp=float(np.min(timestamps)),
            fit_end_timestamp=float(np.max(timestamps)),
        )
        return track, CascadeStage.RATE_LINEARITY_CONFIRMED
