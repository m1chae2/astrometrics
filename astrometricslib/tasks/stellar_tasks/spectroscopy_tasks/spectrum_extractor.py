"""Tools to read the brightness of a spectral streak in an image.

This file provides two ways to measure a spectrum:
1. Basic: Draws a straight rectangular box over the spectrum and adds up
   all the light inside it.
2. Advanced (Traced): Carefully follows the exact center of the spectrum as
   it bends or widens. It adjusts the size of the box on the fly to get
   the best possible reading while ignoring background noise.
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

# The minimum number of pixels we need to confidently find the center of
# the spectrum line. If the line is too thin, the math will fail, so we
# fall back to a simpler method.
_MINIMUM_CROSS_SECTION_SAMPLES = 5

# How wide to make the reading box, compared to the measured width of the
# spectrum. A value of 2.5 is wide enough to capture almost all (99%) of
# the star's light without accidentally including too much empty black sky.
APERTURE_SIGMA_MULTIPLIER = 2.5


def fit_cross_section_gaussian(
    data: np.ndarray,
    center: tuple[float, float],
    perpendicular_vector: tuple[float, float],
    search_radius: float,
) -> tuple[float, float] | None:
    """Look sideways across the spectrum to find its exact center and width.

    Parameters
    ----------
    data : `numpy.ndarray`
        The picture data.
    center : `tuple[float, float]`
        Where we think the center is `(x, y)`.
    perpendicular_vector : `tuple[float, float]`
        An arrow pointing exactly sideways across the spectrum.
    search_radius : `float`
        How many pixels sideways to look.

    Returns
    -------
    fit_result : `tuple[float, float]` or `None`
        How far off our guess was (offset) and how wide the line is (sigma).
        Returns None if we couldn't find a clear line.
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
    """Draw a smooth curve through all the center points we found.

    Sometimes we can't find the center perfectly because a pixel is too dark
    or completely blown out (white). This fits a smooth curve through the
    points we *did* find, so we can guess where the center should have been
    in the bad spots.

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
    """Reads the brightness of a spectrum from an image.

    Attributes
    ----------
    radius : `int`
        How wide of a box to draw around the spectrum (in pixels).
    """

    def __init__(self, radius: int = 10):  # ruff: ignore[missing-return-type-special-method]
        """Set up the extractor.

        Parameters
        ----------
        radius : `int`, optional
            How wide of a box to use (default is 10 pixels).
        """
        self.radius = radius

    def extract_line(
        self, image: AstrometricsImage, start_pos: tuple[float, float], vector: np.ndarray, length: float
    ) -> np.ndarray:
        """Read a straight line across the image.

        This draws a straight box and adds up all the light inside it.
        It assumes the spectrum is perfectly straight.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        start_pos : `Tuple[float, float]`
            Where the line starts `(x, y)`.
        vector : `np.ndarray`
            Which direction the line goes.
        length : `float`
            How long the line is (in pixels).

        Returns
        -------
        profile : `np.ndarray`
            The total brightness at each step along the line.
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
        """Advanced version of extract_line that follows curves.

        Instead of assuming the spectrum is perfectly straight, this checks the
        true center at every step along the line. It draws a smooth curve
        through
        those centers, and widens or narrows its reading box depending on how
        fat the spectrum is at that spot. If it loses the trail for a moment,
        it just guesses using a straight line until it finds it again.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        start_pos : `Tuple[float, float]`
            Where to start looking.
        vector : `np.ndarray`
            Which direction to go.
        length : `float`
            How long the spectrum is.
        centerline_polynomial_degree : `int`, optional
            How flexible the curve should be (default 2 means a simple curve).

        Returns
        -------
        profile : `np.ndarray`
            The brightness at each step.
        trail_centerline_px : `List[float]`
            How far the actual center was from the straight line we guessed.
        trail_width_px : `List[float]`
            How wide the spectrum was at each step.
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
                # If we couldn't find the exact center here, just draw a
                # standard fixed-size box exactly where we expected the
                # line to be.
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
        """Measure a spectrum while ignoring the bright star flare.

        First, this finds the exact center of the star using a 21x21 pixel box.
        Then, it starts measuring the spectrum a few pixels away to avoid the
        bright flare from the star itself. It tracks any tilt in the image and
        adds up the light inside a tight box.

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
        """Smart version of extract_with_flare_mask that follows curves.

        Like extract_with_flare_mask, this avoids the bright star flare.
        Like extract_line_traced, it also tracks the exact center of the
        spectrum as it bends and changes width.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        start_pos : `Tuple[float, float]`
            Where we think the star is `(x, y)`.
        flare_offset_pixels : `float`
            How far away to start reading, so we don't blind ourselves.
        max_offset_pixels : `float`
            Where to stop reading.
        radius : `int`
            How wide to look for the center.
        orientation : `str`, optional
            "horizontal" or "vertical".
        angle_degrees : `float`, optional
            Any overall tilt to the picture.
        centerline_polynomial_degree : `int`, optional
            How flexible the curve tracking should be.

        Returns
        -------
        profile : `np.ndarray`
            The brightness at each step.
        anchor_x : `float`
            The exact `x` center of the star.
        anchor_y : `float`
            The exact `y` center of the star.
        trail_centerline_px : `List[float]`
            How far the spectrum drifted from straight.
        trail_width_px : `List[float]`
            How fat the spectrum was at each step.
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
