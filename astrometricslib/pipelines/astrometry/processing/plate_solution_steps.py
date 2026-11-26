"""Gives the plate solver its scale hints and refits its solution to Gaia.

This layer of `StarIdentifier` (see `star_identifier`) works out the image
scale from the header (focal length and pixel size, with the binning
checked), so the solver searches a narrow range, and after a solve refits
the solution to the field's Gaia stars (see `wcs_refinement`). It also keeps
the peak over the noise of the stars that fit used.
"""

import logging
import math
import sqlite3
from typing import Any

import numpy as np
from astropy.wcs import WCS

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.parameters import resolve
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import (
    load_field_gaia_stars,
    tiles_under_image,
)
from astrometricslib.pipelines.astrometry.processing.gaia_tile_queries import GAIA_DEFAULT_MAGNITUDE_LIMIT
from astrometricslib.pipelines.astrometry.processing.simbad_matching import SimbadMatching
from astrometricslib.pipelines.astrometry.processing.wcs_refinement import (
    WcsRefinement,
    refine_wcs_with_gaia,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)


FLAG_SCALE_HINT_BINNING_MISMATCH = "scale_hint_binning_mismatch"


FLAG_WCS_REFINED = "wcs_refined_against_gaia"


FLAG_WCS_REFINEMENT_SKIPPED = "wcs_refinement_skipped"


class PlateSolutionSteps(SimbadMatching):
    """The plate-solving layer of `StarIdentifier`."""

    def _read_axis_mean(self, header: Any, x_keyword: str, y_keyword: str) -> float | None:
        """Read a per-axis header value, averaging the X and Y cards.

        If both cards hold usable values and they differ, the mean is used
        and the run is flagged, because the scale hint then fits neither
        axis exactly.

        Parameters
        ----------
        header : `astropy.io.fits.Header`
            The image header.
        x_keyword, y_keyword : `str`
            The cards for the X and Y axes, such as ``XPIXSZ`` and ``YPIXSZ``.

        Returns
        -------
        value : `float` or `None`
            The common value when the axes agree, their mean when they
            differ, the one usable value when only one card is usable, or
            `None` when neither is a positive, finite number.
        """
        values: list[float] = []
        for keyword in (x_keyword, y_keyword):
            try:
                value = float(header.get(keyword))
            except TypeError, ValueError:
                continue
            if math.isfinite(value) and value > 0:
                values.append(value)
        if not values:
            return None
        if len(values) == 2 and not math.isclose(values[0], values[1]):
            logger.warning(
                "%s (%s) and %s (%s) differ; using their mean for the scale hint.",
                x_keyword,
                values[0],
                y_keyword,
                values[1],
            )
            self._add_flag(FLAG_SCALE_HINT_BINNING_MISMATCH)
        return sum(values) / len(values)

    def _read_binned_pixel_size(self, header: Any) -> float | None:
        """Read the size of one image pixel, in micrometers, from the header.

        Capture programs that write through INDI/Ekos (libindi), NINA and
        Siril store ``XPIXSZ`` and ``YPIXSZ`` as the binned pixel size (the
        camera's pixel size times the binning factor), so those cards are
        used as they are. Only when they are absent does the function use
        ``PIXSIZE1`` and ``PIXSIZE2``, which hold the camera's own
        (unbinned) pixel size, and multiply by the binning factor from
        ``XBINNING`` and ``YBINNING`` (1 when absent).

        Parameters
        ----------
        header : `astropy.io.fits.Header`
            The image header.

        Returns
        -------
        pixel_size : `float` or `None`
            The binned pixel size in micrometers, or `None` if the header
            names none.
        """
        binned_size = self._read_axis_mean(header, "XPIXSZ", "YPIXSZ")
        if binned_size is not None:
            return binned_size
        unbinned_size = self._read_axis_mean(header, "PIXSIZE1", "PIXSIZE2")
        if unbinned_size is None:
            return None
        binning = self._read_axis_mean(header, "XBINNING", "YBINNING")
        return unbinned_size * (1.0 if binning is None else binning)

    def _calculate_scale_hints(self, image_data_or_path: Any) -> tuple[float | None, float | None]:
        """Guess how zoomed in the image is based on the telescope settings.

        This helps the math solver run much faster because it doesn't have
        to guess the zoom level. The guess is the pixel scale in arcseconds
        per pixel: ``206.265 * pixel size (micrometers) / focal length
        (millimeters)``. The pixel size is the header's ``XPIXSZ``, which
        already includes binning. Without ``XPIXSZ`` it is ``PIXSIZE1``
        times ``XBINNING`` (see `_read_binned_pixel_size`). When the header
        has neither but has ``PIXSCAL`` (the scale in arcseconds per
        pixel), that value is the scale and binning is not applied to it.

        Returns
        -------
        scale_lower, scale_upper : `float` or `None`
            A rough guess of the zoom scale, or None if the settings are
            missing.
        """
        focal_len = None
        pixel_size = None
        header_scale = None

        if isinstance(image_data_or_path, AstrometricsImage):
            hdr = image_data_or_path.header
            focal_len = hdr.get("FOCALLEN")
            pixel_size = self._read_binned_pixel_size(hdr)
            if pixel_size is None:
                header_scale = hdr.get("PIXSCAL")

        try:
            if header_scale and float(header_scale) > 0:
                logger.info("Using the header's pixel scale: %.3f arcsec/pixel", float(header_scale))
                return float(header_scale) * 0.95, float(header_scale) * 1.05
        except TypeError, ValueError:
            logger.warning("Could not read PIXSCAL from the image header.")

        if not focal_len or focal_len <= 0:
            try:
                focal_len = self.config.get_focal_length_mm()
            except ValueError:
                focal_len = None

        if focal_len and focal_len > 0 and pixel_size and pixel_size > 0:
            try:
                calculated_scale = 206.265 * float(pixel_size) / float(focal_len)
                logger.info("Calculated expected pixel scale: %.3f arcsec/pixel", calculated_scale)
                return calculated_scale * 0.95, calculated_scale * 1.05
            except ValueError, TypeError:
                logger.warning("Could not calculate pixel scale from metadata.")
                return None, None

        logger.info("Equipment metadata missing; proceeding with blind scale search.")
        return None, None

    def _refine_wcs_against_gaia(
        self, wcs: WCS, sources: list[dict], height: int, width: int, *, cache_only: bool = False
    ) -> WCS:
        """Refit the plate solution to the field's Gaia stars.

        The solver fits only the few stars of its own index files, and can sit
        more than a pixel from the truth with no check noticing (see
        `wcs_refinement`). This fills the Gaia tiles under the image (see
        `_prepare_field_gaia_stars`), reads the stars of the complete tiles
        to `GAIA_DEFAULT_MAGNITUDE_LIMIT` (or the query depth, if shallower),
        moves them to the date of the image, and refits. Stars of a tile that
        is not complete are not used. The outcome is kept in
        `wcs_refinement`, and one flag says whether the new solution was used.
        A failed download leaves the solver's solution as it was.

        Parameters
        ----------
        wcs : `astropy.wcs.WCS`
            The solver's solution.
        sources : `list` [`dict`]
            The detected stars, each with a centroid in pixels.
        height, width : `int`
            The image's size in pixels.
        cache_only : `bool`, optional
            If True, use only tiles already complete in the cache and never
            ask a remote service. With none, the solver's solution is kept.

        Returns
        -------
        wcs : `astropy.wcs.WCS`
            The refined solution, or `wcs` unchanged.
        """
        centroids = []
        centroid_source_indices = []
        for source_index, source in enumerate(sources):
            x = source.get("x_centroid", source.get("xcentroid"))
            y = source.get("y_centroid", source.get("ycentroid"))
            if x is not None and y is not None:
                centroids.append((float(x), float(y)))
                centroid_source_indices.append(source_index)
        try:
            if cache_only:
                tiles = tiles_under_image(wcs, width, height)
            else:
                tiles = self._prepare_field_gaia_stars(wcs, width, height).tiles
        except DATA_ERRORS:
            logger.exception("Could not place the solved image on the sky; the Gaia refit is skipped.")
            self.wcs_refit_note = "not refit: the solved image could not be placed on the sky"
            self._add_flag(FLAG_WCS_REFINEMENT_SKIPPED)
            return wcs
        # The refit keeps its own depth: bright stars place the field well,
        # and the faintest stars of a crowded field add wrong pairs.
        refit_depth = min(float(resolve(GAIA_DEFAULT_MAGNITUDE_LIMIT).value), self._gaia_query_depth())
        try:
            refit_stars = load_field_gaia_stars(self.config, tiles, refit_depth, None)
        except sqlite3.Error, OSError:
            logger.exception("The Gaia cache could not be read for the refit.")
            refit_stars = None
        gaia_table = None if refit_stars is None else refit_stars.table
        gaia_coords = None if refit_stars is None else refit_stars.coords
        if gaia_table is None or gaia_coords is None:
            # In cache-only mode (stacking) a new field has no cached stars
            # yet: the analysis downloads them later, so this is expected.
            logger.log(
                logging.INFO if cache_only else logging.WARNING,
                "No Gaia stars for the refit of the solution; the solver's solution is kept.",
            )
            self.wcs_refit_note = f"not refit: no Gaia stars cached for this field to depth G {refit_depth:g}"
            self._add_flag(FLAG_WCS_REFINEMENT_SKIPPED)
            return wcs
        gaia_coords = self._move_gaia_coords_to_observation_epoch(gaia_table, gaia_coords)
        refinement = refine_wcs_with_gaia(wcs, np.array(centroids), gaia_coords, (height, width))
        self.wcs_refinement = refinement
        self._record_fit_star_signal(refinement, sources, centroid_source_indices)
        if refinement.applied:
            logger.info(
                "Refit the solution to %s Gaia stars: RMS %.2f -> %.2f arcsec, moved up to %.2f arcsec.",
                refinement.star_count,
                refinement.rms_before_arcsec,
                refinement.rms_after_arcsec,
                refinement.shift_arcsec,
            )
            self.wcs_refit_note = "refit to Gaia stars"
            self._add_flag(FLAG_WCS_REFINED)
            return refinement.wcs
        logger.warning("The solver's solution was kept: %s.", refinement.reason)
        self.wcs_refit_note = f"refit rejected: {refinement.reason}"
        self._add_flag(FLAG_WCS_REFINEMENT_SKIPPED)
        return wcs

    def _record_fit_star_signal(
        self, refinement: WcsRefinement, sources: list[dict], centroid_source_indices: list[int]
    ) -> None:
        """Keep the peak over the noise of the stars the Gaia fit used.

        The expected position error of a star follows from how far its peak
        stands above the background noise. The fit's stars are the ones the
        measured residual is taken over, so the expectation is found over the
        same stars. A one-shot-color frame is searched on a binned copy whose
        peaks and noise are in other units, so it gets no value.

        Parameters
        ----------
        refinement : `WcsRefinement`
            What the Gaia fit found.
        sources : `list` [`dict`]
            The detected stars the fit was given, each with a ``peak``.
        centroid_source_indices : `list` [`int`]
            For each centroid given to the fit, the index of its source in
            `sources`.
        """
        noise = self.detector.last_background_noise
        if not refinement.fit_star_indices:
            self.gaia_fit_signal_reason = "the Gaia fit used no stars"
        elif self._detected_on_binned_copy:
            self.gaia_fit_signal_reason = (
                "the stars were found on a binned copy of a one-shot-color frame, whose peaks and noise "
                "are not in full-pixel units"
            )
        elif noise is None or not math.isfinite(noise) or noise <= 0:
            self.gaia_fit_signal_reason = "the detector measured no background noise"
        else:
            peaks = [
                float(sources[centroid_source_indices[index]].get("peak", math.nan))
                for index in refinement.fit_star_indices
            ]
            if all(math.isfinite(peak) and peak > 0 for peak in peaks):
                self.gaia_fit_peak_over_noise = tuple(peak / noise for peak in peaks)
                self.gaia_fit_signal_reason = ""
            else:
                self.gaia_fit_signal_reason = "a fitted star had no positive peak from the star finder"
