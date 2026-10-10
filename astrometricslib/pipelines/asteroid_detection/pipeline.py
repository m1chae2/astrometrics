"""Main controller for finding asteroids in our photos.

This ties all the steps together: figuring out where the photos are pointing,
finding all the dots (stars/asteroids) in them, running the tests to filter
out the noise, and finally checking if any survivors match known asteroids
in our databases.
"""

import logging
import math
import os
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Literal

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.spatial import cKDTree

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import ConflictError
from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import (
    MovingObjectConfig,
    MovingObjectConfigLoader,
)
from astrometricslib.pipelines.asteroid_detection.detection import (
    MovingObjectDetector,
    wrapped_ra_difference_deg,
)
from astrometricslib.pipelines.asteroid_detection.ephemeris import EphemerisCrossMatcher
from astrometricslib.pipelines.asteroid_detection.frame_wcs_composer import (
    estimate_frame_wcs_from_mount_pointing,
)
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector

logger = logging.getLogger(__name__)

# `estimate_frame_wcs_from_mount_pointing` centers each frame's WCS on
# the mount's own RA/DEC header readback, which for an amateur GOTO
# mount without a plate-solve sync is typically only accurate to tens of
# arcseconds to a few arcminutes -- much coarser than
# `MovingObjectConfig`'s few-arcsecond stationary-object thresholds. Left
# uncorrected, every real (stationary) star in the field appears to
# "move" by the mount's own pointing error, which is exactly what the
# reference-frame and rate-linearity tests are supposed to be screening
# out; confirmed empirically on real NGC 2403 (36 frames) and M 81 (257
# frames) data, where this produced 11,990/19,068 track candidates and
# 187/97 "confirmed" linear movers with zero SkyBoT matches -- far more
# than a handful of real frames could plausibly contain, and directly
# measured per-frame mount offsets of 10-40 arcsec against NGC 2403's own
# reference stars. This is how far a detection may sit from a candidate
# reference star and still be considered for the frame's bulk pointing
# offset vote below: wide enough to comfortably contain a several
# arcminute mount error. It does not by itself need to be smaller than
# the field's own star spacing (unlike a plain nearest-neighbor match),
# because the vote below is robust to the many wrong candidate pairs a
# wide radius admits in a dense field -- see `_estimate_bulk_pointing_
# correction_deg`. Not yet validated against a range of real mount
# pointing errors beyond this one target.
_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC = 300.0
# Bin width for the offset-voting histogram below. A plain "nearest
# reference star" match was tried first and rejected: on real NGC 2403
# data (median 48 arcsec star spacing, comparable to the mount's own
# 10-40 arcsec pointing error) it matched many detections to the wrong
# neighboring star, leaving a residual scatter of ~13 arcsec even after
# subtracting the fitted median -- barely better than no correction at
# all. Voting instead on every candidate pair's offset picks out the one
# true shared displacement even when most individual candidate pairs are
# wrong, since only the real matches all land in the same bin. 5 arcsec
# is small enough to separate the true peak from this field's spacing,
# comfortably larger than DAOStarFinder's sub-arcsec centroiding noise.
_POINTING_CORRECTION_VOTE_BIN_ARCSEC = 5.0
# Below this many candidate pairs landing in the winning bin (plus its
# immediate neighbors), the vote would be too noisy to trust as a real
# peak rather than a chance cluster -- the frame is left with its raw
# mount-pointing WCS rather than applying an unreliable correction.
_POINTING_CORRECTION_MIN_VOTES = 5
# After the bulk shift is applied, each detection is compared with its
# nearest reference star. Pairs farther apart than this are treated as a
# wrong star and left out of the position-error estimate. Three vote bins is
# wide enough to keep the spread of the right pairs, narrow enough that most
# wrong pairs fall outside. If a frame's true error is near or above this
# radius, the estimate reads low (the far tail is cut off).
_POSITION_ERROR_MATCH_RADIUS_ARCSEC = 3.0 * _POINTING_CORRECTION_VOTE_BIN_ARCSEC
# Scale factor that turns a median absolute deviation into the standard
# deviation of a normal distribution.
_MAD_TO_SIGMA = 1.4826


def _reference_centre_ra_deg(reference_ra_deg: np.ndarray) -> float:
    """Find one central Right Ascension (RA) for a set of reference stars.

    RA wraps from 360 deg back to 0 deg, so a plain average of
    359.98 deg and 0.02 deg gives 180 deg, which is the wrong side of
    the sky. This function averages the RA values as points on a circle
    instead, so a field that straddles RA = 0 h gets a centre inside
    the field. The tree builder and the offset estimator both call it on
    the same reference array, so they always agree on the centre.

    Parameters
    ----------
    reference_ra_deg : `numpy.ndarray`
        The reference stars' RA values, in degrees.

    Returns
    -------
    centre_ra_deg : `float`
        The circular mean RA, in degrees in [0, 360). It is `0.0` for an
        empty array.
    """
    if reference_ra_deg.size == 0:
        return 0.0
    ra_radians = np.radians(reference_ra_deg)
    centre_radians = math.atan2(float(np.mean(np.sin(ra_radians))), float(np.mean(np.cos(ra_radians))))
    return math.degrees(centre_radians) % 360.0


def _estimate_bulk_pointing_correction_deg(
    detection_positions_deg: list[tuple[float, float]],
    reference_tree: cKDTree,
    reference_ra_deg: np.ndarray,
    reference_dec_deg: np.ndarray,
    reference_cos_declination: float,
) -> tuple[float, float, float | None]:
    """Estimate one frame's systematic RA/Dec pointing offset from the stack.

    Pairs every one of this frame's raw detections with every stack
    reference star within `_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC` and
    votes on the resulting `(ra_offset, dec_offset)` via a coarse 2D
    histogram. Real, stationary stars all share the mount's one common
    pointing offset for this frame, so their pairs cluster into a single
    dominant bin; noise, moving objects, and detections paired with the
    wrong nearby star in a crowded field scatter across many different
    offsets instead. Refining the winning bin's own member offsets with a
    median (rather than trusting the bin's coarse center) keeps the
    result from being quantized to the bin width.

    Parameters
    ----------
    detection_positions_deg : `list` [`tuple` [`float`, `float`]]
        This frame's raw `(ra_deg, dec_deg)` detections, computed from
        the mount-pointing-based WCS estimate.
    reference_tree : `scipy.spatial.cKDTree`
        Tree over the stack's reference stars, in the same flattened
        tangent-plane-ish arcsecond space `_build_reference_star_tree`
        builds.
    reference_ra_deg, reference_dec_deg : `numpy.ndarray`
        The reference stars' true sky positions, indexed the same way
        as `reference_tree`.
    reference_cos_declination : `float`
        The same `cos(dec)` flattening factor `reference_tree` was built
        with (`_build_reference_star_tree`'s return) -- reusing it here,
        rather than recomputing one from this frame's own declination,
        keeps the query points in the same flattened coordinate space
        the tree was indexed in.

    Returns
    -------
    ra_offset_deg, dec_offset_deg : `float`
        The offset to *add* to this frame's raw RA/Dec so its stars line
        up with the reference catalog. Both are `0.0` if no vote reached
        `_POINTING_CORRECTION_MIN_VOTES`.
    position_error_arcsec : `float` or `None`
        How far the frame's star positions still scatter from the reference
        stars after the shift, in arcseconds on the sky: the standard
        deviation of the nearest-star residuals, on each axis, averaged
        over the two axes. `None` when there was no correction, or fewer
        than `_POINTING_CORRECTION_MIN_VOTES` detections had a reference
        star within `_POSITION_ERROR_MATCH_RADIUS_ARCSEC`.
    """
    if not detection_positions_deg or reference_ra_deg.size == 0:
        return 0.0, 0.0, None

    detection_array = np.array(detection_positions_deg)
    # Measure every RA from one centre, wrapped, so a field across
    # RA = 0 h lands in one connected patch of the tree's flat space.
    centre_ra_deg = _reference_centre_ra_deg(reference_ra_deg)
    query_points = np.column_stack((
        wrapped_ra_difference_deg(detection_array[:, 0], centre_ra_deg) * reference_cos_declination * 3600.0,
        detection_array[:, 1] * 3600.0,
    ))
    candidate_reference_indices_per_detection = reference_tree.query_ball_point(
        query_points, r=_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC
    )

    ra_offset_arcsec_per_pair = []
    dec_offset_arcsec_per_pair = []
    for detection_index, candidate_reference_indices in enumerate(candidate_reference_indices_per_detection):
        if not candidate_reference_indices:
            continue
        candidate_reference_indices = np.asarray(candidate_reference_indices)
        ra_offset_arcsec_per_pair.append(
            wrapped_ra_difference_deg(
                reference_ra_deg[candidate_reference_indices], detection_array[detection_index, 0]
            )
            * reference_cos_declination
            * 3600.0
        )
        dec_offset_arcsec_per_pair.append(
            (reference_dec_deg[candidate_reference_indices] - detection_array[detection_index, 1]) * 3600.0
        )
    if not ra_offset_arcsec_per_pair:
        return 0.0, 0.0, None

    ra_offsets_arcsec = np.concatenate(ra_offset_arcsec_per_pair)
    dec_offsets_arcsec = np.concatenate(dec_offset_arcsec_per_pair)

    bin_count = int(2 * _POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC / _POINTING_CORRECTION_VOTE_BIN_ARCSEC)
    vote_histogram, ra_bin_edges, dec_bin_edges = np.histogram2d(
        ra_offsets_arcsec,
        dec_offsets_arcsec,
        bins=bin_count,
        range=[
            [-_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC, _POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC],
            [-_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC, _POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC],
        ],
    )
    peak_ra_bin, peak_dec_bin = np.unravel_index(np.argmax(vote_histogram), vote_histogram.shape)

    # Widen by one bin on each side so real matches that straddled a bin
    # edge aren't dropped out of the refinement.
    bin_width = _POINTING_CORRECTION_VOTE_BIN_ARCSEC
    near_peak = (
        (ra_offsets_arcsec >= ra_bin_edges[peak_ra_bin] - bin_width)
        & (ra_offsets_arcsec <= ra_bin_edges[peak_ra_bin + 1] + bin_width)
        & (dec_offsets_arcsec >= dec_bin_edges[peak_dec_bin] - bin_width)
        & (dec_offsets_arcsec <= dec_bin_edges[peak_dec_bin + 1] + bin_width)
    )
    if int(np.count_nonzero(near_peak)) < _POINTING_CORRECTION_MIN_VOTES:
        return 0.0, 0.0, None

    ra_shift_arcsec = float(np.median(ra_offsets_arcsec[near_peak]))
    dec_shift_arcsec = float(np.median(dec_offsets_arcsec[near_peak]))

    # Apply the shift, then see how well each detection lands on its nearest
    # reference star. What is left is the position error of this frame. The
    # tree returns an index equal to its size when nothing is in range.
    shifted_query_points = query_points + np.array([ra_shift_arcsec, dec_shift_arcsec])
    _distances, nearest_indices = reference_tree.query(
        shifted_query_points, distance_upper_bound=_POSITION_ERROR_MATCH_RADIUS_ARCSEC
    )
    matched = nearest_indices < reference_tree.n
    position_error_arcsec = None
    if int(np.count_nonzero(matched)) >= _POINTING_CORRECTION_MIN_VOTES:
        residuals = reference_tree.data[nearest_indices[matched]] - shifted_query_points[matched]
        per_axis_sigma_arcsec = [
            _MAD_TO_SIGMA * float(np.median(np.abs(axis_residuals - np.median(axis_residuals))))
            for axis_residuals in residuals.T
        ]
        position_error_arcsec = math.sqrt(sum(sigma**2 for sigma in per_axis_sigma_arcsec) / 2.0)

    return (
        ra_shift_arcsec / reference_cos_declination / 3600.0,
        dec_shift_arcsec / 3600.0,
        position_error_arcsec,
    )


def _build_reference_star_tree(
    reference_positions_deg: list[tuple[float, float]],
) -> tuple[cKDTree | None, np.ndarray, np.ndarray, float]:
    """Build a fast nearest-neighbor index over the stack's reference stars.

    Parameters
    ----------
    reference_positions_deg : `list` [`tuple` [`float`, `float`]]
        The stack's own `(ra_deg, dec_deg)` source positions.

    Returns
    -------
    tree : `scipy.spatial.cKDTree` or `None`
        Index over the reference stars' flattened arcsecond positions
        (RA measured as a wrapped offset from the stars' circular-mean
        RA, then scaled by `cos_declination` so an arcsecond means the
        same thing on both axes), or `None` if there were none.
    reference_ra_deg, reference_dec_deg : `numpy.ndarray`
        The reference stars' true sky positions, indexed the same way
        as `tree`.
    cos_declination : `float`
        The field's mean `cos(dec)`, used to flatten RA into arcseconds.
        Callers must reuse this exact value (not recompute their own)
        when querying `tree`, so query points land in the same flattened
        space it was built in.
    """
    if not reference_positions_deg:
        return None, np.array([]), np.array([]), 1.0
    reference_ra_deg = np.array([ra for ra, _ in reference_positions_deg])
    reference_dec_deg = np.array([dec for _, dec in reference_positions_deg])
    cos_declination = math.cos(math.radians(float(np.mean(reference_dec_deg))))
    centre_ra_deg = _reference_centre_ra_deg(reference_ra_deg)
    flattened_points = np.column_stack((
        wrapped_ra_difference_deg(reference_ra_deg, centre_ra_deg) * cos_declination * 3600.0,
        reference_dec_deg * 3600.0,
    ))
    return cKDTree(flattened_points), reference_ra_deg, reference_dec_deg, cos_declination


def _read_exposure_seconds(frame_header: fits.Header) -> float | None:
    """Read how long a frame was exposed, in seconds, from its header.

    Parameters
    ----------
    frame_header : `astropy.io.fits.Header`
        The frame's primary header.

    Returns
    -------
    exposure_seconds : `float` or `None`
        The ``EXPTIME`` value, or `None` if it is missing, is not a number,
        or is not positive.
    """
    try:
        exposure_seconds = float(frame_header.get("EXPTIME"))
    except TypeError, ValueError:
        return None
    if not math.isfinite(exposure_seconds) or exposure_seconds <= 0.0:
        return None
    return exposure_seconds


def _detect_sources_in_one_frame(
    frame_path: str,
    frame_timestamp: float,
    stack_wcs: WCS,
    fwhm: float,
    threshold_sigma: float,
    reference_tree: cKDTree | None,
    reference_ra_deg: np.ndarray,
    reference_dec_deg: np.ndarray,
    reference_cos_declination: float,
    centroid_error_px: float,
) -> tuple[Literal["ok", "no_wcs", "read_failed"], list[FrameDetection]]:
    """Read one photo, figure out where it's pointing, and find all the dots.

    We put this function out here on its own so that we can run it on
    multiple photos at the same time using different CPU cores.

    Parameters
    ----------
    reference_tree, reference_ra_deg, reference_dec_deg,
    reference_cos_declination
        The stack's reference-star index from `_build_reference_star_tree`,
        used to correct this frame's mount-pointing-based WCS estimate
        (see `_estimate_bulk_pointing_correction_deg`). `reference_tree`
        is `None` when the stack itself had no detectable reference
        stars, in which case this frame's raw, uncorrected positions are
        used.
    centroid_error_px : `float`
        The error, in pixels, of finding a faint dot's centre. It is added
        in quadrature to the frame's measured pointing scatter to give each
        detection's `astrometric_error_arcsec`.

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
            exposure_seconds = _read_exposure_seconds(frame_header)
    except FITS_READ_ERRORS as read_error:
        logger.warning("Failed to read frame '%s' for asteroid detection: %s", frame_path, read_error)
        return "read_failed", []
    if frame_data is None:
        return "read_failed", []

    frame_wcs = estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header)
    if frame_wcs is None:
        return "no_wcs", []

    source_detector = SourceDetector(fwhm=fwhm, threshold_sigma=threshold_sigma)
    sources = source_detector.detect(np.asarray(frame_data, dtype=float))

    # A typical brightness level for this one picture, from every dot
    # found in it. Stamped onto every detection below so a later step
    # can divide a dot's own brightness by this number and cancel out
    # this picture's own sky conditions (see FrameDetection).
    brightness_values = [float(source["flux"]) for source in sources if source.get("flux") is not None]
    picture_brightness_level = float(np.median(brightness_values)) if brightness_values else None

    raw_positions = []
    pixel_positions = []
    fluxes = []
    for source in sources:
        pixel_x = source.get("xcentroid", source.get("x_centroid"))
        pixel_y = source.get("ycentroid", source.get("y_centroid"))
        if pixel_x is None or pixel_y is None:
            continue
        sky_position = frame_wcs.wcs_pix2world([[float(pixel_x), float(pixel_y)]], 1)[0]
        raw_positions.append((float(sky_position[0]), float(sky_position[1])))
        pixel_positions.append((float(pixel_x), float(pixel_y)))
        fluxes.append(source.get("flux"))

    ra_offset_deg, dec_offset_deg = 0.0, 0.0
    pointing_scatter_arcsec = None
    if reference_tree is not None:
        ra_offset_deg, dec_offset_deg, pointing_scatter_arcsec = _estimate_bulk_pointing_correction_deg(
            raw_positions, reference_tree, reference_ra_deg, reference_dec_deg, reference_cos_declination
        )
    # One position's error: the measured scatter plus the error of finding a
    # faint dot's centre. Left as `None` when the scatter could not be
    # measured, so the straight-line test uses its assumed value instead.
    astrometric_error_arcsec = None
    if pointing_scatter_arcsec is not None:
        pixel_scale_arcsec = float(np.mean(proj_plane_pixel_scales(frame_wcs))) * 3600.0
        astrometric_error_arcsec = math.hypot(pointing_scatter_arcsec, centroid_error_px * pixel_scale_arcsec)

    detections = []
    for (raw_ra_deg, raw_dec_deg), (pixel_x, pixel_y), source_flux in zip(
        raw_positions, pixel_positions, fluxes, strict=True
    ):
        detections.append(
            FrameDetection(
                frame_path=frame_path,
                timestamp=frame_timestamp,
                pixel_x=pixel_x,
                pixel_y=pixel_y,
                right_ascension_deg=raw_ra_deg + ra_offset_deg,
                declination_deg=raw_dec_deg + dec_offset_deg,
                brightness=float(source_flux) if source_flux is not None else None,
                picture_brightness_level=picture_brightness_level,
                astrometric_error_arcsec=astrometric_error_arcsec,
                exposure_seconds=exposure_seconds,
            )
        )
    return "ok", detections


class AsteroidDetectionPipeline:
    """The main factory line for finding asteroids in our photos.

    Parameters
    ----------
    config : `MovingObjectConfig`, optional
        The settings to use. If None, it loads the default settings.
    """

    def __init__(self, config: MovingObjectConfig | None = None) -> None:
        self.config = config or MovingObjectConfigLoader.load_moving_object_config()
        # A simple dictionary to store the results of the last run. We save
        # things like "how many asteroids did we find?" so the main program
        # can show a summary to the user later.
        self.last_run_metrics: dict[str, float] = {}
        # How many SkyBoT questions the last run asked and how many failed.
        self._ephemeris_queries_attempted = 0
        self._ephemeris_queries_failed = 0

    def process(
        self, target_id: str, stacked_image_path: str, frames: list[tuple[str, float]]
    ) -> list[AsteroidDetectionCandidate]:
        """Run the whole asteroid-finding process on one set of photos.

        Takes plain data rather than a `Target` -- this class has no
        dependency on `astrometricslib.models.target.Target`, `FrameRecord`,
        or any other orchestration-layer concern, so it can be driven
        directly by a caller that wants to assemble its own pipeline
        instead of going through `AsteroidDetectionPipelineAdapter`.

        Parameters
        ----------
        target_id : `str`
            The id to stamp onto detected candidates.
        stacked_image_path : `str`
            Path to the target's finished stacked image, so we know
            exactly where it is in the sky. Must already exist.
        frames : `list` [`tuple` [`str`, `float`]]
            Each frame's path and capture timestamp to search for moving
            objects across. The caller is responsible for filtering to
            `LIGHT`-role frames with a usable capture timestamp.

        Returns
        -------
        candidates : `list` [`AsteroidDetectionCandidate`]
            The list of moving objects we found.

        Raises
        ------
        ConflictError
            If `stacked_image_path` is empty.
        """
        if not stacked_image_path:
            raise ConflictError(
                f"Target '{target_id}' has no stacked_image; run analyze_target(type='astrometry') first."
            )

        stack_header = fits.getheader(stacked_image_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            stack_wcs = WCS(stack_header, naxis=2)

        reference_star_index = self._build_reference_stars(stacked_image_path, stack_wcs)

        frame_detections, frames_with_wcs_estimate, frames_excluded_missing_pointing_metadata = (
            self._detect_frame_sources(frames, stack_wcs, reference_star_index)
        )

        logger.info(
            "  [Asteroid Detection] Detected %s total point sources across %s frames.",
            len(frame_detections),
            frames_with_wcs_estimate,
        )
        logger.info("  [Asteroid Detection] Running spatial-temporal track persistence chaining...")
        detector = MovingObjectDetector(self.config)
        candidates = detector.detect_candidates(target_id, frame_detections)
        logger.info(
            "  [Asteroid Detection] Chaining completed: %s track candidates generated.", len(candidates)
        )

        self._ephemeris_queries_attempted = 0
        self._ephemeris_queries_failed = 0
        candidates = self._cross_match_ephemeris(candidates)
        residual_summary = detector.last_residual_summary

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
            "ephemeris_queries_attempted": self._ephemeris_queries_attempted,
            "ephemeris_queries_failed": self._ephemeris_queries_failed,
            # How the straight-line (residual) test went. The ratio is the
            # largest RMS-to-error ratio among accepted tracks; it is 0.0
            # when none was accepted.
            "residual_chains_tested": residual_summary.chains_tested,
            "residual_chains_rejected": residual_summary.chains_rejected,
            "residual_chains_non_monotonic": residual_summary.chains_non_monotonic,
            "residual_assumed_error_detections": residual_summary.assumed_error_detections,
            "residual_worst_accepted_ratio": residual_summary.worst_accepted_ratio or 0.0,
        }
        return candidates

    def _build_reference_stars(
        self, stacked_image_path: str, stack_wcs: WCS
    ) -> tuple[cKDTree | None, np.ndarray, np.ndarray, float]:
        """Detect the stack's own stars, to correct each frame's pointing by.

        Run once (not per-frame): finds sources in the already-solved
        stacked image itself, using the same detector settings each frame
        uses, and projects them to sky coordinates via the stack's own
        accurate WCS. See `_estimate_bulk_pointing_correction_deg` for why
        this reference set exists.

        Parameters
        ----------
        stacked_image_path : `str`
            Path to the target's finished stacked image.
        stack_wcs : `astropy.wcs.WCS`
            The stack's own solved WCS.

        Returns
        -------
        reference_star_index : `tuple`
            `_build_reference_star_tree`'s return -- `(tree,
            reference_ra_deg, reference_dec_deg, cos_declination)`.
        """
        with fits.open(stacked_image_path, memmap=False) as hdul:
            stack_data = np.asarray(hdul[0].data, dtype=float)
        source_detector = SourceDetector(
            fwhm=self.config.detection_fwhm_px, threshold_sigma=self.config.detection_threshold_sigma
        )
        sources = source_detector.detect(stack_data)
        reference_positions_deg = []
        for source in sources:
            pixel_x = source.get("xcentroid", source.get("x_centroid"))
            pixel_y = source.get("ycentroid", source.get("y_centroid"))
            if pixel_x is None or pixel_y is None:
                continue
            sky_position = stack_wcs.wcs_pix2world([[float(pixel_x), float(pixel_y)]], 1)[0]
            reference_positions_deg.append((float(sky_position[0]), float(sky_position[1])))
        return _build_reference_star_tree(reference_positions_deg)

    def _detect_frame_sources(
        self,
        frames: list[tuple[str, float]],
        stack_wcs: WCS,
        reference_star_index: tuple[cKDTree | None, np.ndarray, np.ndarray, float],
    ) -> tuple[list[FrameDetection], int, int]:
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

        total_light = len(frames)
        if total_light == 0:
            return frame_detections, frames_with_wcs_estimate, frames_excluded_missing_pointing_metadata

        reference_tree, reference_ra_deg, reference_dec_deg, reference_cos_declination = reference_star_index

        max_workers = min(total_light, os.cpu_count() or 1)
        completed = 0
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _detect_sources_in_one_frame,
                    frame_path,
                    frame_timestamp,
                    stack_wcs,
                    self.config.detection_fwhm_px,
                    self.config.detection_threshold_sigma,
                    reference_tree,
                    reference_ra_deg,
                    reference_dec_deg,
                    reference_cos_declination,
                    self.config.centroid_error_px,
                )
                for frame_path, frame_timestamp in frames
            ]
            for future in as_completed(futures):
                completed += 1
                if completed % 5 == 0 or completed == total_light:
                    logger.info(
                        "  [Asteroid Detection] Scanned %s/%s frames for point sources...",
                        completed,
                        total_light,
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
        self, candidates: list[AsteroidDetectionCandidate]
    ) -> list[AsteroidDetectionCandidate]:
        """Check our final list of moving objects against the database.

        We only do this if we actually found something that looks like a real
        asteroid. The database is asked about each such object at the time
        and place of its first and of its last detection.

        Parameters
        ----------
        candidates : `list` [`AsteroidDetectionCandidate`]
            Every candidate the detector produced.

        Returns
        -------
        candidates : `list[AsteroidDetectionCandidate]`
            The same list of objects, but with database match info added if we
            found any.
        """
        has_rate_confirmed_candidate = any(
            candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED for candidate in candidates
        )
        if not has_rate_confirmed_candidate:
            return candidates

        cross_matcher = EphemerisCrossMatcher(self.config)
        matched_candidates = cross_matcher.cross_match_candidates(candidates)
        self._ephemeris_queries_attempted = cross_matcher.queries_attempted
        self._ephemeris_queries_failed = cross_matcher.queries_failed
        return matched_candidates
