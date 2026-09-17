"""Main controller for finding asteroids in our photos.

This ties all the steps together: figuring out where the photos are pointing,
finding all the dots (stars/asteroids) in them, running the tests to filter
out the noise, and finally checking if any survivors match known asteroids
in our databases.
"""

import logging
import math
import os
import statistics
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Literal

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.spatial import cKDTree

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import (
    MovingObjectConfig,
    MovingObjectConfigLoader,
)
from astrometricslib.pipelines.asteroid_detection.detection import MovingObjectDetector
from astrometricslib.pipelines.asteroid_detection.ephemeris import EphemerisCrossMatcher
from astrometricslib.pipelines.asteroid_detection.frame_wcs_composer import (
    estimate_frame_wcs_from_mount_pointing,
)
from astrometricslib.pipelines.astrometry.source_detection import SourceDetector

logger = logging.getLogger(__name__)

# Matches the FOV-radius calculation already used for SIMBAD cone
# searches in star_identifier.py: half the larger field dimension,
# padded 10% for safety, capped at SkyBoT's practical query-size sweet
# spot (well under its own 10-degree maximum).
_FIELD_QUERY_RADIUS_BUFFER_FACTOR = 1.1
_FIELD_QUERY_RADIUS_CAP_DEG = 1.0

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


def _estimate_bulk_pointing_correction_deg(
    detection_positions_deg: list[tuple[float, float]],
    reference_tree: cKDTree,
    reference_ra_deg: np.ndarray,
    reference_dec_deg: np.ndarray,
    reference_cos_declination: float,
) -> tuple[float, float]:
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
    """
    if not detection_positions_deg or reference_ra_deg.size == 0:
        return 0.0, 0.0

    detection_array = np.array(detection_positions_deg)
    query_points = np.column_stack((
        detection_array[:, 0] * reference_cos_declination * 3600.0,
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
            (reference_ra_deg[candidate_reference_indices] - detection_array[detection_index, 0])
            * reference_cos_declination
            * 3600.0
        )
        dec_offset_arcsec_per_pair.append(
            (reference_dec_deg[candidate_reference_indices] - detection_array[detection_index, 1]) * 3600.0
        )
    if not ra_offset_arcsec_per_pair:
        return 0.0, 0.0

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
        return 0.0, 0.0

    return (
        float(np.median(ra_offsets_arcsec[near_peak])) / reference_cos_declination / 3600.0,
        float(np.median(dec_offsets_arcsec[near_peak])) / 3600.0,
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
        (RA scaled by `cos_declination` so an arcsecond means the same
        thing on both axes), or `None` if there were none.
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
    flattened_points = np.column_stack((
        reference_ra_deg * cos_declination * 3600.0,
        reference_dec_deg * 3600.0,
    ))
    return cKDTree(flattened_points), reference_ra_deg, reference_dec_deg, cos_declination


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
        logger.warning(f"Failed to read frame '{frame_path}' for asteroid detection: {read_error}")
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
    if reference_tree is not None:
        ra_offset_deg, dec_offset_deg = _estimate_bulk_pointing_correction_deg(
            raw_positions, reference_tree, reference_ra_deg, reference_dec_deg, reference_cos_declination
        )

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

    def __init__(self, config: MovingObjectConfig | None = None):  # ruff: ignore[missing-return-type-special-method]
        self.config = config or MovingObjectConfigLoader.load_moving_object_config()
        # A simple dictionary to store the results of the last run. We save
        # things like "how many asteroids did we find?" so the main program
        # can show a summary to the user later.
        self.last_run_metrics: dict[str, int] = {}

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
        ValueError
            If `stacked_image_path` is empty.
        """
        if not stacked_image_path:
            raise ValueError(
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

        print(
            f"  [Asteroid Detection] Detected {len(frame_detections)} total point sources "
            f"across {frames_with_wcs_estimate} frames."
        )
        print("  [Asteroid Detection] Running spatial-temporal track persistence chaining...")
        detector = MovingObjectDetector(self.config)
        candidates = detector.detect_candidates(target_id, frame_detections)
        print(f"  [Asteroid Detection] Chaining completed: {len(candidates)} track candidates generated.")

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
        stack_data = fits.getdata(stacked_image_path)
        source_detector = SourceDetector(
            fwhm=self.config.detection_fwhm_px, threshold_sigma=self.config.detection_threshold_sigma
        )
        sources = source_detector.detect(np.asarray(stack_data, dtype=float))
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
                )
                for frame_path, frame_timestamp in frames
            ]
            for future in as_completed(futures):
                completed += 1
                if completed % 5 == 0 or completed == total_light:
                    print(
                        f"  [Asteroid Detection] Scanned {completed}/{total_light} "
                        "frames for point sources..."
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
        candidates: list[AsteroidDetectionCandidate],
        frame_detections: list[FrameDetection],
        stack_wcs: WCS,
        stack_header: fits.Header,
    ) -> list[AsteroidDetectionCandidate]:
        """Check our final list of moving objects against the database.

        We only do this if we actually found something that looks like a real
        asteroid.

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
