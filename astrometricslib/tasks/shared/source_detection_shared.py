"""SourceDetector: Standardized point-source detection wrapping photutils.

Used for star identification and spectroscopy zero-order detection.
"""

import logging
from typing import Any

import numpy as np
from astropy.stats import SigmaClip, sigma_clipped_stats
from photutils.background import Background2D, MedianBackground
from photutils.detection import DAOStarFinder

logger = logging.getLogger(__name__)

# Below this pixel count, background stats are computed on the full
# array -- subsampling only pays off once the full-array cost is
# large enough to matter, and small arrays don't have enough pixels
# for a subsample to stay statistically representative.
_BACKGROUND_STATS_SUBSAMPLE_MIN_PIXELS = 512 * 512
# Verified against real 4000x6000 M 81 subframes: a true-random 1/16
# sample matches the full-array sigma_clipped_stats mean/median/std
# within 0.05%, at ~20x lower cost. A *strided* subsample (data[::4,
# ::4]) was tried first and rejected -- it aliased with real spatial
# structure in the frame and came out 77% off on std.
_BACKGROUND_STATS_SAMPLE_FRACTION = 1.0 / 16.0

# Background2D box size, in pixels, as a fraction of the image's
# shorter axis. Verified against a NGC 6992 (Veil Nebula) frame: a
# single global median/std (the previous approach) left smoothly-
# varying nebular glow un-subtracted, so DAOStarFinder's flat
# threshold treated bright filament structure as point sources --
# 194/196 detections came back with no SIMBAD/Gaia match (i.e. not
# real stars). A locally-varying background map absorbs that glow
# into "background" instead of candidate source flux. 1/20 keeps the
# box comfortably larger than a stellar FWHM while still tracking
# nebula-scale gradients.
_BACKGROUND_2D_BOX_FRACTION = 1.0 / 20.0
_BACKGROUND_2D_BOX_MIN_PX = 20
_BACKGROUND_2D_BOX_MAX_PX = 200
# Background2D needs several boxes per axis to interpolate a map at
# all; below this, per-box statistics are too noisy to be meaningful
# and we fall back to a single global background estimate.
_BACKGROUND_2D_MIN_BOXES_PER_AXIS = 3


class SourceDetector:
    """Finds point sources in an image using DAOStarFinder."""

    def __init__(self, fwhm: float = 4.0, threshold_sigma: float = 5.0):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the source detector.

        Parameters
        ----------
        fwhm : `float`, optional
            Full width at half maximum of the Gaussian kernel used by
            the star finder, in pixels (default 4.0).
        threshold_sigma : `float`, optional
            Detection threshold expressed as a multiple of the
            background noise standard deviation (default 5.0).
        """
        self.fwhm = fwhm
        self.threshold_sigma = threshold_sigma

    def detect(self, data: np.ndarray, mask: np.ndarray | None = None) -> list[dict[str, Any]]:
        """Detect stars in the provided image data.

        Parameters
        ----------
        data : `numpy.ndarray`
            2D (or 3D, e.g. multi-plane) image data to search for point
            sources. 3D data is collapsed to 2D by averaging.
        mask : `numpy.ndarray`, optional
            Boolean mask of pixels to exclude from background
            statistics and detection (default `None`).

        Returns
        -------
        sources : `list` [`dict`]
            List of dictionaries containing star properties
            (``xcentroid``, ``ycentroid``, ``flux``, etc.), sorted by
            descending flux.
        """
        # Ensure data is 2D for DAOStarFinder
        if data.ndim == 3:
            if data.shape[0] in [3, 4]:
                data = np.mean(data, axis=0)
            else:
                data = np.mean(data, axis=-1)

        # 1. Estimate background. Prefer a locally-varying 2D map over a
        # single global scalar: a flat global median/std leaves smoothly
        # extended structure (nebulosity, gradients) sitting above the
        # detection threshold as if it were source flux. Falls back to
        # the previous global-scalar approach on images too small for a
        # meaningful box grid (see _BACKGROUND_2D_MIN_BOXES_PER_AXIS).
        box_size = int(
            np.clip(
                min(data.shape) * _BACKGROUND_2D_BOX_FRACTION,
                _BACKGROUND_2D_BOX_MIN_PX,
                _BACKGROUND_2D_BOX_MAX_PX,
            )
        )
        background_map: np.ndarray | float
        if min(data.shape) >= box_size * _BACKGROUND_2D_MIN_BOXES_PER_AXIS:
            try:
                bkg = Background2D(
                    data,
                    box_size=box_size,
                    mask=mask,
                    sigma_clip=SigmaClip(sigma=3.0),
                    bkg_estimator=MedianBackground(),
                )
                background_map = bkg.background
                std = bkg.background_rms_median
                logger.debug(f"Source detection: 2D background (box={box_size}px), median_rms={std:.2f}")
            except Exception as e:
                logger.warning(f"Background2D failed, falling back to global scalar: {e}")
                background_map = None
        else:
            background_map = None

        if background_map is None:
            # 1b. Global-scalar fallback, from a random subsample on large
            # images (see _BACKGROUND_STATS_SUBSAMPLE_MIN_PIXELS).
            stats_data, stats_mask = data, mask
            if data.size >= _BACKGROUND_STATS_SUBSAMPLE_MIN_PIXELS:
                rng = np.random.default_rng()
                sample_size = int(data.size * _BACKGROUND_STATS_SAMPLE_FRACTION)
                sample_indices = rng.choice(data.size, size=sample_size, replace=False)
                stats_data = data.ravel()[sample_indices]
                stats_mask = mask.ravel()[sample_indices] if mask is not None else None
            mean, median, std = sigma_clipped_stats(stats_data, sigma=3.0, mask=stats_mask)
            background_map = median
            logger.debug(
                f"Source detection: global background, mean={mean:.2f}, median={median:.2f}, std={std:.2f}"
            )

        # 2. Initialize finder. roundness_range is tightened from
        # DAOStarFinder's full (-1.0, 1.0) default to reject elongated
        # nebular filament structure that the background subtraction
        # alone doesn't catch -- real stars are round, filaments aren't.
        star_finder = DAOStarFinder(
            fwhm=self.fwhm,
            threshold=self.threshold_sigma * std,
            exclude_border=True,
            sharpness_range=(0.2, 1.0),
            roundness_range=(-0.6, 0.6),
        )

        # 3. Detect sources (subtract the background estimate for SNR)
        sources = star_finder(data - background_map, mask=mask)

        if sources is None:
            return []

        # Convert QTable to list of dicts and sort by flux descending
        sources.sort("flux")
        sources.reverse()

        return [dict(zip(sources.colnames, row, strict=False)) for row in sources]

    def deduplicate(self, sources: list[dict[str, Any]], separation_px: float = 15.0) -> list[dict[str, Any]]:
        """Merge nearby detections by averaging coordinates.

        Detections closer than `separation_px` are merged into a
        single entry: their centroid coordinates are averaged and
        their flux values are summed.

        Parameters
        ----------
        sources : `list` [`dict`]
            List of source dictionaries, as returned by `detect`.
        separation_px : `float`, optional
            Maximum center-to-center distance, in pixels, for two
            detections to be considered the same source
            (default 15.0).

        Returns
        -------
        unique_stars : `list` [`dict`]
            List of source dictionaries with nearby duplicates merged.
        """
        if not sources:
            return []

        from scipy.spatial import cKDTree

        # Extract coordinates into numpy array
        coords = np.array(
            [
                (
                    s.get("x_centroid", s.get("xcentroid")),
                    s.get("y_centroid", s.get("ycentroid")),
                )
                for s in sources
            ],
            dtype=float,
        )

        tree = cKDTree(coords)
        visited = np.zeros(len(sources), dtype=bool)
        unique_stars = []

        for idx in range(len(sources)):
            if visited[idx]:
                continue

            # Query all neighbors within separation_px
            neighbor_indices = tree.query_ball_point(coords[idx], r=separation_px)
            visited[neighbor_indices] = True
            group = [sources[i] for i in neighbor_indices]

            if len(group) == 1:
                unique_stars.append(group[0])
            else:
                leader = group[0]
                merged = leader.copy()
                all_x = [coords[i][0] for i in range(len(group))]
                all_y = [coords[i][1] for i in range(len(group))]
                all_flux = [s.get("flux", 0.0) for s in group]

                if "x_centroid" in merged:
                    merged["x_centroid"] = float(np.mean(all_x))
                    merged["y_centroid"] = float(np.mean(all_y))
                else:
                    merged["xcentroid"] = float(np.mean(all_x))
                    merged["ycentroid"] = float(np.mean(all_y))

                merged["flux"] = float(np.sum(all_flux))
                unique_stars.append(merged)

        return unique_stars
