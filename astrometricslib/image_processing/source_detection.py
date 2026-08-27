"""SourceDetector: The shared tool we use to find stars in our photos.

We use this for basic target alignment, looking for asteroids, and
finding the central star in spectroscopy.
"""

import logging
from typing import Any

import numpy as np
from astropy.stats import SigmaClip, sigma_clipped_stats
from photutils.background import Background2D, MedianBackground
from photutils.detection import DAOStarFinder

from astrometricslib.image_processing.fits_access import collapse_to_2d

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
    """Finds bright dots (stars) in an image using the DAOStarFinder math."""

    def __init__(self, fwhm: float = 4.0, threshold_sigma: float = 5.0):  # ruff: ignore[missing-return-type-special-method]
        """Set up the star finder.

        Parameters
        ----------
        fwhm : `float`, optional
            How wide we expect a typical star to be across the middle,
            measured in pixels (default is 4.0).
        threshold_sigma : `float`, optional
            How bright a dot needs to be before we believe it's a star.
            This is measured as a multiple of the background noise
            (default is 5.0x the noise).
        """
        self.fwhm = fwhm
        self.threshold_sigma = threshold_sigma

    def detect(self, data: np.ndarray, mask: np.ndarray | None = None) -> list[dict[str, Any]]:
        """Find the stars in the given picture.

        Parameters
        ----------
        data : `numpy.ndarray`
            The actual pixels of the picture. If it's a color image (3D),
            we squash it into black and white (2D) first.
        mask : `numpy.ndarray`, optional
            An optional list of bad pixels we should ignore.

        Returns
        -------
        sources : `list` [`dict`]
            A list of the stars we found, sorted from brightest to dimmest.
            Each star has coordinates (xcentroid, ycentroid) and a
            brightness value (flux).
        """
        # Ensure data is 2D for DAOStarFinder
        data = collapse_to_2d(data)

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
        """Combine overlapping dots into a single star.

        If the math accidentally splits a large star into two dots, this
        combines them back together. We take the average position and add
        their brightnesses together.

        Parameters
        ----------
        sources : `list` [`dict`]
            The list of stars we found in the previous step.
        separation_px : `float`, optional
            How close two dots need to be (in pixels) before we assume
            they are actually just one star. Default is 15.0 pixels.

        Returns
        -------
        unique_stars : `list` [`dict`]
            The cleaned up list of stars.
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
