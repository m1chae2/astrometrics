"""SpectrumExtractor: extraction of 1D spectra from 2D images.

Includes straight-line box extraction and flare-masked extraction with
rotation/tilt tracking (rung 1, fixed-width/fixed-offset), plus traced
extraction (rung 3): a per-position Gaussian cross-section fit smoothed into
a trail-centerline polynomial, with an aperture adaptive to the locally
measured width. The trail-width profile this produces is a free
byproduct that also feeds SpectroscopyPipelineQualityMetrics
(targetlib/spectroscopy_quality.py).
"""

import logging
import warnings

import numpy as np
from astropy.modeling import fitting, models

from astrometricslib.utilities import AstrometricsImage

logger = logging.getLogger(__name__)

try:
    # pyrefly: ignore[missing-import] -- optional C accelerator built from
    # _extractor_c.c. Only the source is tracked, so the compiled module is
    # absent until it is built, and a static checker cannot introspect the .so
    # even once it is. The except branch below is the supported path.
    from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks._extractor_c import (
        fit_cross_section_gaussian_c as _fit_cross_section_c,
    )

    HAS_C_EXTENSION = True
except ImportError:
    _fit_cross_section_c = None
    HAS_C_EXTENSION = False

# Minimum valid samples in a cross-section for a Gaussian fit to be
# attempted at all -- below this the fit is underdetermined.
_MINIMUM_CROSS_SECTION_SAMPLES = 5

# Aperture half-width as a multiple of the locally fitted Gaussian sigma --
# ~2.5-3 sigma captures ~99% of a Gaussian's flux per side.
APERTURE_SIGMA_MULTIPLIER = 2.5


def fit_cross_section_gaussian(
    data: np.ndarray,
    center: tuple[float, float],
    perpendicular_vector: tuple[float, float],
    search_radius: float,
) -> tuple[float, float] | None:
    """Fit a 1D Gaussian to a cross-section perpendicular to dispersion.

    Samples pixel values at integer offsets from -search_radius to
    +search_radius along perpendicular_vector, centered at `center`, and
    fits a 1D Gaussian. Uses fast C extension when available, falling back
    to pure-Python/NumPy.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2D image array to sample from.
    center : `tuple[float, float]`
        (x, y) pixel coordinates of the nominal center.
    perpendicular_vector : `tuple[float, float]`
        Unit vector perpendicular to the dispersion direction.
    search_radius : `float`
        Half-width, in pixels, of the cross-section to sample.

    Returns
    -------
    fit_result : `tuple[float, float]` or `None`
        (center_offset_px, sigma_px) or `None` if fit fails.
    """
    if HAS_C_EXTENSION and _fit_cross_section_c is not None:
        try:
            c_res = _fit_cross_section_c(data, center, perpendicular_vector, float(search_radius))
            if c_res is not None:
                return c_res
        except Exception as exc:
            logger.debug("C extension cross-section fit failed, falling back to Python: %s", exc)
    height, width = data.shape
    perpendicular_x, perpendicular_y = perpendicular_vector
    center_x, center_y = center

    offsets = np.arange(-int(search_radius), int(search_radius) + 1)
    pixel_x = np.rint(center_x + offsets * perpendicular_x).astype(int)
    pixel_y = np.rint(center_y + offsets * perpendicular_y).astype(int)

    valid_mask = (pixel_x >= 0) & (pixel_x < width) & (pixel_y >= 0) & (pixel_y < height)
    if np.count_nonzero(valid_mask) < _MINIMUM_CROSS_SECTION_SAMPLES:
        return None

    offsets_array = offsets[valid_mask].astype(float)
    values_array = data[pixel_y[valid_mask], pixel_x[valid_mask]].astype(float)

    edge_sample_count = min(2, len(values_array) // 2)
    background = float(
        np.median(np.concatenate([values_array[:edge_sample_count], values_array[-edge_sample_count:]]))
    )
    background_subtracted = values_array - background

    amplitude_guess = float(np.max(background_subtracted))
    if amplitude_guess <= 0:
        return None
    mean_guess = float(offsets_array[np.argmax(background_subtracted)])

    gaussian_model = models.Gaussian1D(amplitude=amplitude_guess, mean=mean_guess, stddev=search_radius / 3.0)
    fitter = fitting.LevMarLSQFitter()

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted_model = fitter(gaussian_model, offsets_array, background_subtracted)
    except Exception:
        return None

    center_offset = float(fitted_model.mean.value)
    sigma = float(fitted_model.stddev.value)

    if not np.isfinite(center_offset) or not np.isfinite(sigma):
        return None
    if sigma <= 0 or sigma > search_radius * 2:
        return None
    if abs(center_offset) > search_radius:
        return None

    return center_offset, sigma


def fit_trail_centerline_polynomial(raw_centers: list[float | None], degree: int) -> list[float]:
    """Smooth per-step raw Gaussian-fit centers into a trail centerline.

    Fits a polynomial of the given degree through the steps that produced a
    valid fit, then evaluates it at every step -- including steps where the
    per-position fit failed. The same np.polyfit/np.polyval technique
    spectroscopy_pipeline.py's detect_dispersion_angle already uses for a
    single global angle, generalized here to a full per-position trace.

    Parameters
    ----------
    raw_centers : `List[Optional[float]]`
        One entry per step along the dispersion axis; `None` where the
        per-position Gaussian fit failed.
    degree : `int`
        Polynomial degree to fit.

    Returns
    -------
    smoothed_centers : `List[float]`
        One smoothed centerline value per step, same length as
        `raw_centers`. Falls back to all-zero (no correction) if too few
        steps produced a valid fit to constrain the requested degree.

    """
    valid_indices = [index for index, center in enumerate(raw_centers) if center is not None]
    if len(valid_indices) < degree + 1:
        return [0.0] * len(raw_centers)

    valid_values = [raw_centers[index] for index in valid_indices]
    coefficients = np.polyfit(valid_indices, valid_values, deg=degree)
    all_indices = np.arange(len(raw_centers))
    return [float(value) for value in np.polyval(coefficients, all_indices)]


class SpectrumExtractor:
    """Extract spectral data from a high-level interfaceImage.

    Attributes
    ----------
    radius : `int`
        Half-width of the summation window used across the dispersion
        direction when extracting a 1D profile (rung 1) or the initial
        cross-section search radius (rung 3).

    """

    def __init__(self, radius: int = 10):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the extractor with its default extraction radius.

        Parameters
        ----------
        radius : `int`, optional
            Half-width of the summation window across the dispersion
            direction (default 10 pixels).

        """
        self.radius = radius

    def extract_line(
        self, image: AstrometricsImage, start_pos: tuple[float, float], vector: np.ndarray, length: float
    ) -> np.ndarray:
        """Extract a line of pixels starting at start_pos following the vector.

        Sums over the extraction radius perpendicular to the
        dispersion direction at each step along the line.

        Parameters
        ----------
        image : `AstrometricsImage`
            The 2D image to extract the spectral line from.
        start_pos : `Tuple[float, float]`
            Starting pixel coordinates (x, y) of the extraction line.
        vector : `np.ndarray`
            2-element unit vector giving the direction of dispersion.
        length : `float`
            Number of steps to take along `vector`.

        Returns
        -------
        profile : `np.ndarray`
            1D array of summed intensities, one value per step along
            the line.

        """
        data = image.data
        h, w = data.shape
        x0, y0 = start_pos
        vx, vy = vector

        # Simple extraction for now:
        # If horizontal/vertical, use slice. If diagonal, use profiling
        # (future). Assuming horizontal/vertical for now as per config.

        pixels = []
        for i in range(int(length)):
            curr_x = int(x0 + i * vx)
            curr_y = int(y0 + i * vy)

            if 0 <= curr_x < w and 0 <= curr_y < h:
                # Sum over radius
                if abs(vx) > abs(vy):  # Horizontal-ish
                    y_start = max(0, curr_y - self.radius)
                    y_end = min(h, curr_y + self.radius + 1)
                    val = np.sum(data[y_start:y_end, curr_x])
                else:  # Vertical-ish
                    x_start = max(0, curr_x - self.radius)
                    x_end = min(w, curr_x + self.radius + 1)
                    val = np.sum(data[curr_y, x_start:x_end])
                pixels.append(val)
            else:
                pixels.append(0.0)

        return np.array(pixels)

    def extract_line_traced(
        self,
        image: AstrometricsImage,
        start_pos: tuple[float, float],
        vector: np.ndarray,
        length: float,
        centerline_polynomial_degree: int = 2,
    ) -> tuple[np.ndarray, list[float], list[float]]:
        """Traced (rung 3) variant of extract_line.

        At each step, fits a Gaussian cross-section perpendicular to
        `vector` instead of assuming the trail sits exactly on the nominal
        line; the raw per-step centers are then smoothed into a single
        trail-centerline polynomial, and flux is re-summed using an
        aperture adaptive to the locally measured width, centered on the
        smoothed centerline. A step whose Gaussian fit fails falls back to
        that step's plain fixed-radius sum around the nominal (uncorrected)
        position -- not fatal to the rest of the trace.

        Parameters
        ----------
        image : `AstrometricsImage`
            The 2D image to extract the spectral line from.
        start_pos : `Tuple[float, float]`
            Starting pixel coordinates (x, y) of the extraction line.
        vector : `np.ndarray`
            2-element unit vector giving the direction of dispersion.
        length : `float`
            Number of steps to take along `vector`.
        centerline_polynomial_degree : `int`, optional
            Degree of the polynomial fit to the per-step raw centers
            (default 2).

        Returns
        -------
        profile : `np.ndarray`
            1D array of summed intensities, one value per step.
        trail_centerline_px : `List[float]`
            Smoothed per-step centerline offset (pixels, perpendicular to
            `vector`) relative to the nominal line.
        trail_width_px : `List[float]`
            Per-step fitted Gaussian sigma (pixels); `0.0` for steps that
            fell back to the fixed-box method.

        """
        data = image.data
        height, width = data.shape
        x0, y0 = start_pos
        vx, vy = vector
        perpendicular_vector = (-vy, vx)

        raw_centers: list[float | None] = []
        raw_sigmas: list[float | None] = []
        for i in range(int(length)):
            curr_x = x0 + i * vx
            curr_y = y0 + i * vy
            fit_result = fit_cross_section_gaussian(data, (curr_x, curr_y), perpendicular_vector, self.radius)
            if fit_result is None:
                raw_centers.append(None)
                raw_sigmas.append(None)
            else:
                center_offset, sigma = fit_result
                raw_centers.append(center_offset)
                raw_sigmas.append(sigma)

        smoothed_centerline = fit_trail_centerline_polynomial(raw_centers, centerline_polynomial_degree)
        fitted_sigmas = [sigma for sigma in raw_sigmas if sigma is not None]
        fallback_sigma = float(np.median(fitted_sigmas)) if fitted_sigmas else float(self.radius) / 3.0

        pixels = []
        trail_width_px: list[float] = []
        for i in range(int(length)):
            curr_x = x0 + i * vx
            curr_y = y0 + i * vy
            int_x, int_y = int(curr_x), int(curr_y)

            if raw_centers[i] is None:
                # Per-position fallback: the original fixed-radius sum
                # around the nominal (uncorrected) position for this one
                # step.
                if 0 <= int_x < width and 0 <= int_y < height:
                    if abs(vx) > abs(vy):
                        y_start = max(0, int_y - self.radius)
                        y_end = min(height, int_y + self.radius + 1)
                        val = np.sum(data[y_start:y_end, int_x])
                    else:
                        x_start = max(0, int_x - self.radius)
                        x_end = min(width, int_x + self.radius + 1)
                        val = np.sum(data[int_y, x_start:x_end])
                    pixels.append(val)
                else:
                    pixels.append(0.0)
                trail_width_px.append(0.0)
                continue

            sigma = raw_sigmas[i] if raw_sigmas[i] is not None else fallback_sigma
            aperture_radius = max(1, round(sigma * APERTURE_SIGMA_MULTIPLIER))
            true_center_x = curr_x + perpendicular_vector[0] * smoothed_centerline[i]
            true_center_y = curr_y + perpendicular_vector[1] * smoothed_centerline[i]
            center_int_x, center_int_y = round(true_center_x), round(true_center_y)

            if abs(vx) > abs(vy):
                y_start = max(0, center_int_y - aperture_radius)
                y_end = min(height, center_int_y + aperture_radius + 1)
                val = np.sum(data[y_start:y_end, int_x]) if 0 <= int_x < width and y_start < y_end else 0.0
            else:
                x_start = max(0, center_int_x - aperture_radius)
                x_end = min(width, center_int_x + aperture_radius + 1)
                val = np.sum(data[int_y, x_start:x_end]) if 0 <= int_y < height and x_start < x_end else 0.0
            pixels.append(val)
            trail_width_px.append(sigma)

        return np.array(pixels), smoothed_centerline, trail_width_px

    def extract_with_flare_mask(
        self,
        image: AstrometricsImage,
        start_pos: tuple[float, float],
        flare_offset_pixels: float,
        max_offset_pixels: float,
        radius: int,
        orientation: str = "vertical",
        angle_degrees: float = 0.0,
    ) -> tuple[np.ndarray, float, float]:
        """Extract a 1D spectrum starting after a flare-masking offset.

        The extraction is relative to an accurately computed
        zero-order sub-pixel centroid anchor. Uses a 21x21 subgrid
        center-of-mass calculation to find the anchor point, defines a
        tight spectral bounding box (dynamically adjusted for rotation
        tilt), and sums intensities (column-wise for horizontal,
        row-wise for vertical).

        Parameters
        ----------
        image : `AstrometricsImage`
            The 2D image data.
        start_pos : `Tuple[float, float]`
            Rough coordinates of the zero-order star (x, y).
        flare_offset_pixels : `float`
            Starting pixel offset relative to the anchor to avoid the
            astigmatism flare.
        max_offset_pixels : `float`
            Ending pixel offset relative to the anchor to define the
            bounding box.
        radius : `int`
            Half-width of the extraction window.
        orientation : `str`, optional
            Orientation of the dispersion, "horizontal" or "vertical"
            (default "vertical").
        angle_degrees : `float`, optional
            Rotation/tilt angle of the dispersion streak in degrees
            (default 0.0).

        Returns
        -------
        profile : `np.ndarray`
            1D array of summed intensities.
        anchor_x : `float`
            Sub-pixel zero-order anchor X coordinate.
        anchor_y : `float`
            Sub-pixel zero-order anchor Y coordinate.

        """
        data = image.data
        h, w = data.shape
        x0, y0 = start_pos

        # 1. Centroid Anchor (21x21 subgrid around rough start position)
        ix0, iy0 = round(x0), round(y0)
        y_start = max(0, iy0 - 10)
        y_end = min(h, iy0 + 11)
        x_start = max(0, ix0 - 10)
        x_end = min(w, ix0 + 11)

        subgrid = data[y_start:y_end, x_start:x_end]
        total_mass = np.sum(subgrid)

        if total_mass > 0:
            y_indices, x_indices = np.indices(subgrid.shape)
            anchor_x = x_start + np.sum(subgrid * x_indices) / total_mass
            anchor_y = y_start + np.sum(subgrid * y_indices) / total_mass
        else:
            anchor_x, anchor_y = x0, y0

        # 2. Bounding Box & Profile Extraction with Dynamic Tilt Tracking
        profile = []
        slope = -np.tan(np.radians(angle_degrees))

        if orientation == "horizontal":
            start_x = round(anchor_x + flare_offset_pixels)
            end_x = round(anchor_x + max_offset_pixels)

            for x in range(start_x, end_x):
                # Calculate dynamically tilted y center
                y_center = anchor_y + slope * (x - anchor_x)
                iy_center = round(y_center)
                y_low = max(0, iy_center - radius)
                y_high = min(h, iy_center + radius + 1)

                if 0 <= x < w and y_low < y_high:
                    val = np.sum(data[y_low:y_high, x])
                    profile.append(val)
                else:
                    profile.append(0.0)
        else:  # vertical
            # Vertical dispersion: from anchor_y + flare_offset_pixels to
            # anchor_y + max_offset_pixels
            start_y = round(anchor_y + flare_offset_pixels)
            end_y = round(anchor_y + max_offset_pixels)

            for y in range(start_y, end_y):
                # Calculate dynamically tilted x center
                x_center = anchor_x + slope * (y - anchor_y)
                ix_center = round(x_center)
                x_low = max(0, ix_center - radius)
                x_high = min(w, ix_center + radius + 1)

                if 0 <= y < h and x_low < x_high:
                    # Sum rows horizontally in the bounding box centered
                    # around the tilted x center
                    val = np.sum(data[y, x_low:x_high])
                    profile.append(val)
                else:
                    profile.append(0.0)

        return np.array(profile), anchor_x, anchor_y

    def extract_with_flare_mask_traced(
        self,
        image: AstrometricsImage,
        start_pos: tuple[float, float],
        flare_offset_pixels: float,
        max_offset_pixels: float,
        radius: int,
        orientation: str = "vertical",
        angle_degrees: float = 0.0,
        centerline_polynomial_degree: int = 2,
    ) -> tuple[np.ndarray, float, float, list[float], list[float]]:
        """Traced (rung 3) variant of extract_with_flare_mask.

        Keeps the same sub-pixel zero-order anchor and global tilt slope as
        extract_with_flare_mask, but replaces the fixed-radius box at each
        step with a per-position Gaussian cross-section fit, smoothed into a
        trail-centerline polynomial and an adaptive aperture from the
        locally measured width -- same technique as extract_line_traced.

        Parameters
        ----------
        image : `AstrometricsImage`
            The 2D image data.
        start_pos : `Tuple[float, float]`
            Rough coordinates of the zero-order star (x, y).
        flare_offset_pixels : `float`
            Starting pixel offset relative to the anchor to avoid the
            astigmatism flare.
        max_offset_pixels : `float`
            Ending pixel offset relative to the anchor to define the
            bounding box.
        radius : `int`
            Initial cross-section search radius used for the per-step
            Gaussian fit.
        orientation : `str`, optional
            Orientation of the dispersion, "horizontal" or "vertical"
            (default "vertical").
        angle_degrees : `float`, optional
            Rotation/tilt angle of the dispersion streak in degrees
            (default 0.0).
        centerline_polynomial_degree : `int`, optional
            Degree of the polynomial fit to the per-step raw centers
            (default 2).

        Returns
        -------
        profile : `np.ndarray`
            1D array of summed intensities.
        anchor_x : `float`
            Sub-pixel zero-order anchor X coordinate.
        anchor_y : `float`
            Sub-pixel zero-order anchor Y coordinate.
        trail_centerline_px : `List[float]`
            Smoothed per-step centerline offset relative to the
            tilt-corrected nominal line.
        trail_width_px : `List[float]`
            Per-step fitted Gaussian sigma; `0.0` for steps that fell back
            to the fixed-box method.

        """
        data = image.data
        h, w = data.shape
        x0, y0 = start_pos

        ix0, iy0 = round(x0), round(y0)
        y_start = max(0, iy0 - 10)
        y_end = min(h, iy0 + 11)
        x_start = max(0, ix0 - 10)
        x_end = min(w, ix0 + 11)

        subgrid = data[y_start:y_end, x_start:x_end]
        total_mass = np.sum(subgrid)

        if total_mass > 0:
            y_indices, x_indices = np.indices(subgrid.shape)
            anchor_x = x_start + np.sum(subgrid * x_indices) / total_mass
            anchor_y = y_start + np.sum(subgrid * y_indices) / total_mass
        else:
            anchor_x, anchor_y = x0, y0

        slope = -np.tan(np.radians(angle_degrees))
        is_horizontal = orientation == "horizontal"

        if is_horizontal:
            steps = list(range(round(anchor_x + flare_offset_pixels), round(anchor_x + max_offset_pixels)))
            perpendicular_vector = (0.0, 1.0)
        else:
            steps = list(range(round(anchor_y + flare_offset_pixels), round(anchor_y + max_offset_pixels)))
            perpendicular_vector = (1.0, 0.0)

        nominal_centers = []
        for step in steps:
            if is_horizontal:
                nominal_centers.append((float(step), anchor_y + slope * (step - anchor_x)))
            else:
                nominal_centers.append((anchor_x + slope * (step - anchor_y), float(step)))

        raw_centers: list[float | None] = []
        raw_sigmas: list[float | None] = []
        for center in nominal_centers:
            fit_result = fit_cross_section_gaussian(data, center, perpendicular_vector, radius)
            if fit_result is None:
                raw_centers.append(None)
                raw_sigmas.append(None)
            else:
                center_offset, sigma = fit_result
                raw_centers.append(center_offset)
                raw_sigmas.append(sigma)

        smoothed_centerline = fit_trail_centerline_polynomial(raw_centers, centerline_polynomial_degree)
        fitted_sigmas = [sigma for sigma in raw_sigmas if sigma is not None]
        fallback_sigma = float(np.median(fitted_sigmas)) if fitted_sigmas else float(radius) / 3.0

        profile = []
        trail_width_px: list[float] = []
        for index, step in enumerate(steps):
            nominal_x, nominal_y = nominal_centers[index]
            int_step_x, int_step_y = (step, round(nominal_y)) if is_horizontal else (round(nominal_x), step)

            if raw_centers[index] is None:
                if is_horizontal:
                    y_low = max(0, int_step_y - radius)
                    y_high = min(h, int_step_y + radius + 1)
                    val = np.sum(data[y_low:y_high, step]) if 0 <= step < w and y_low < y_high else 0.0
                else:
                    x_low = max(0, int_step_x - radius)
                    x_high = min(w, int_step_x + radius + 1)
                    val = np.sum(data[step, x_low:x_high]) if 0 <= step < h and x_low < x_high else 0.0
                profile.append(val)
                trail_width_px.append(0.0)
                continue

            sigma = raw_sigmas[index] if raw_sigmas[index] is not None else fallback_sigma
            aperture_radius = max(1, round(sigma * APERTURE_SIGMA_MULTIPLIER))
            true_x = nominal_x + perpendicular_vector[0] * smoothed_centerline[index]
            true_y = nominal_y + perpendicular_vector[1] * smoothed_centerline[index]

            if is_horizontal:
                center_int_y = round(true_y)
                y_low = max(0, center_int_y - aperture_radius)
                y_high = min(h, center_int_y + aperture_radius + 1)
                val = np.sum(data[y_low:y_high, step]) if 0 <= step < w and y_low < y_high else 0.0
            else:
                center_int_x = round(true_x)
                x_low = max(0, center_int_x - aperture_radius)
                x_high = min(w, center_int_x + aperture_radius + 1)
                val = np.sum(data[step, x_low:x_high]) if 0 <= step < h and x_low < x_high else 0.0
            profile.append(val)
            trail_width_px.append(sigma)

        return np.array(profile), anchor_x, anchor_y, smoothed_centerline, trail_width_px
