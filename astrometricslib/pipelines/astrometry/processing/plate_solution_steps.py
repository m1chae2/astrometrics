"""Gives the plate solver its scale hints and checks its solution on Gaia.

This layer of `StarIdentifier` (see `star_identifier`) works out the image
scale from the header (focal length and pixel size, with the binning
checked), so the solver gets a start focal length (or a narrow range, online),
and after a solve measures the solution against the field's Gaia stars (see
`gaia_match`). It never changes the solution. It also keeps the peak over the
noise of the stars the measurement used.
"""

import logging
import math
import sqlite3
from typing import Any

import numpy as np
from astropy.wcs import WCS

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.parameters import resolve
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import load_field_gaia_stars
from astrometricslib.pipelines.astrometry.processing.gaia_match import GaiaMatch, measure_gaia_match
from astrometricslib.pipelines.astrometry.processing.gaia_tile_queries import GAIA_DEFAULT_MAGNITUDE_LIMIT
from astrometricslib.pipelines.astrometry.processing.simbad_matching import SimbadMatching
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)


FLAG_SCALE_HINT_BINNING_MISMATCH = "scale_hint_binning_mismatch"


FLAG_GAIA_MATCH_SKIPPED = "gaia_match_skipped"


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

    def _measure_wcs_against_gaia(self, wcs: WCS, sources: list[dict], height: int, width: int) -> None:
        """Measure the plate solution against the field's Gaia stars.

        This fills the Gaia tiles under the image (see
        `_prepare_field_gaia_stars`), reads the stars of the complete tiles to
        `GAIA_DEFAULT_MAGNITUDE_LIMIT` (or the query depth, if shallower),
        moves them to the date of the image, and measures the RMS distance of
        the matched stars (see `gaia_match`). The solution is not changed. The
        outcome is kept in `gaia_match`, and one flag says when no RMS could
        be measured. A failed download leaves `gaia_match` empty.

        Parameters
        ----------
        wcs : `astropy.wcs.WCS`
            The solver's solution.
        sources : `list` [`dict`]
            The detected stars, each with a centroid in pixels.
        height, width : `int`
            The image's size in pixels.
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
            tiles = self._prepare_field_gaia_stars(wcs, width, height).tiles
        except DATA_ERRORS:
            logger.exception("Could not place the solved image on the sky; the Gaia match is skipped.")
            self.gaia_match_note = "not measured: the solved image could not be placed on the sky"
            self._add_flag(FLAG_GAIA_MATCH_SKIPPED)
            return
        # The match keeps its own depth: bright stars place the field well,
        # and the faintest stars of a crowded field add wrong pairs.
        match_depth = min(float(resolve(GAIA_DEFAULT_MAGNITUDE_LIMIT).value), self._gaia_query_depth())
        try:
            match_stars = load_field_gaia_stars(self.config, tiles, match_depth, None)
        except sqlite3.Error, OSError:
            logger.exception("The Gaia cache could not be read for the Gaia match.")
            match_stars = None
        gaia_table = None if match_stars is None else match_stars.table
        gaia_coords = None if match_stars is None else match_stars.coords
        if gaia_table is None or gaia_coords is None:
            logger.warning("No Gaia stars for the Gaia match; no RMS distance to Gaia is measured.")
            self.gaia_match_note = (
                f"not measured: no Gaia stars cached for this field to depth G {match_depth:g}"
            )
            self._add_flag(FLAG_GAIA_MATCH_SKIPPED)
            return
        gaia_coords = self._move_gaia_coords_to_observation_epoch(gaia_table, gaia_coords)
        match = measure_gaia_match(wcs, np.array(centroids), gaia_coords)
        self.gaia_match = match
        self._record_match_star_signal(match, sources, centroid_source_indices)
        if match.rms_arcsec is None:
            logger.warning("No RMS distance to Gaia: %s.", match.reason)
            self.gaia_match_note = f"not measured: {match.reason}"
            self._add_flag(FLAG_GAIA_MATCH_SKIPPED)
            return
        logger.info(
            "The solution sits %.2f arcsec (RMS) from %s Gaia stars.", match.rms_arcsec, match.star_count
        )
        self.gaia_match_note = "measured"

    def _record_match_star_signal(
        self, match: GaiaMatch, sources: list[dict], centroid_source_indices: list[int]
    ) -> None:
        """Keep the peak over the noise of the stars the Gaia match used.

        The expected position error of a star follows from how far its peak
        stands above the background noise. The matched stars are the ones the
        measured residual is taken over, so the expectation is found over the
        same stars. A one-shot-color frame is searched on a binned copy whose
        peaks and noise are in other units, so it gets no value.

        Parameters
        ----------
        match : `GaiaMatch`
            What the Gaia match found.
        sources : `list` [`dict`]
            The detected stars the match was given, each with a ``peak``.
        centroid_source_indices : `list` [`int`]
            For each centroid given to the match, the index of its source in
            `sources`.
        """
        noise = self.detector.last_background_noise
        if not match.star_indices:
            self.gaia_match_signal_reason = "the Gaia match used no stars"
        elif self._detected_on_binned_copy:
            self.gaia_match_signal_reason = (
                "the stars were found on a binned copy of a one-shot-color frame, whose peaks and noise "
                "are not in full-pixel units"
            )
        elif noise is None or not math.isfinite(noise) or noise <= 0:
            self.gaia_match_signal_reason = "the detector measured no background noise"
        else:
            peaks = [
                float(sources[centroid_source_indices[index]].get("peak", math.nan))
                for index in match.star_indices
            ]
            if all(math.isfinite(peak) and peak > 0 for peak in peaks):
                self.gaia_match_peak_over_noise = tuple(peak / noise for peak in peaks)
                self.gaia_match_signal_reason = ""
            else:
                self.gaia_match_signal_reason = "a matched star had no positive peak from the star finder"
