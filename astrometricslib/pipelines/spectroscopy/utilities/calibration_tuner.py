"""Automatically figure out the physics settings for our camera.

We use a known bright star (like Vega) as a reference. By finding the known
dark bands in its spectrum (hydrogen lines) and doing some math, we can
calculate exactly how far the grating is from the sensor and where the
rainbow starts. Then, it saves those numbers for next time.

Finding the dark bands has three steps. The spectrum is divided by a running
continuum (its slowly changing brightness), so a dip is measured as a
fraction of the continuum and not in camera counts. A dip only counts if it
is several times deeper than the local noise. Only the deepest few dips are
kept, so the search over groups of three stays fast on a noisy spectrum. The
position of each kept dip is then refined to a fraction of a sample.
"""

import itertools
import logging
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from scipy.ndimage import median_filter
from scipy.optimize import minimize
from scipy.signal import find_peaks

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.pipelines.spectroscopy.pipeline import (
    SpectroscopyPipeline,
    _read_xy_source_position,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.optics_physics import (
    BALMER_SERIES_NM,
    calculate_pixel_offset,
    calculate_wavelength,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectroscopy_instrument import (
    SpectroscopyInstrument,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.spectroscopy_models import SpectroscopyConfig

logger = logging.getLogger(__name__)

# If more than this fraction of the pixels at the theoretical dispersion
# start are still saturated, the star's own flare/astigmatism is judged to
# be bleeding into the spectrum, and flare-masked extraction is turned on.
_FLARE_CONTAMINATION_FRACTION_THRESHOLD = 0.1

# Width, in samples, of the running median that estimates the continuum. It
# must be much wider than an absorption line so the median ignores the line.
# The Balmer lines are about 4 to 14 samples wide, so 101 samples (about
# 1100 A at this dispersion) leaves them well outside the median's reach.
_CONTINUUM_WINDOW_SAMPLES = 101

# A dip must be at least this many times deeper than the local noise. The
# smoothing makes neighbouring samples share their noise, so a pure-noise
# spectrum still shows some dips at 3 to 4 times the noise. 5 times rejects
# nearly all of them, while a real Balmer line is tens of times deeper.
_NOISE_PROMINENCE_MULTIPLE = 5.0

# Converts a median absolute deviation (MAD) to a standard deviation for
# Gaussian noise.
_MAD_TO_SIGMA = 1.4826

# The most dips passed on to the search over groups of three. The search
# fits C(N, 3) groups, which is 220 for 12 and tens of thousands for a
# noisy spectrum with dozens of dips.
_MAX_DIP_CANDIDATES = 12

# The smallest dip depth, as a fraction of the continuum, that is tried
# first, the last one tried, and the step between them.
_FIRST_FRACTIONAL_PROMINENCE = 0.01
_LAST_FRACTIONAL_PROMINENCE = 0.001
_FRACTIONAL_PROMINENCE_STEP = 0.002

# Dips closer than this many samples to either end of the spectrum are
# ignored, because the smoothing and the continuum are unreliable there.
_DIP_EDGE_MARGIN_SAMPLES = 5


class SpectroscopyCalibrationTuner:
    """Figures out exactly how our spectrograph camera is set up.

    It looks at a known star's spectrum, finds the dark lines, and calculates
    the physical distance between the grating and the camera sensor.

    Attributes
    ----------
    config : `AppConfiguration`
        Where we load our settings from and save our new calibration to.
    """

    def __init__(self, config: AppConfiguration | None = None) -> None:
        """Initialize the tuner service with the system configuration.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The settings object. If None, it will grab the default one.
        """
        if config is None:
            from astrometricslib.foundation.config import get_configuration

            config = get_configuration()
        self.config = config

    def tune_calibration(
        self,
        image_path: str,
        camera_name: str | None = None,
        star_pos: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        """Calculate the camera settings by looking at a known star.

        This looks for specific dark lines (absorption dips) in the star's
        light.
        Because we know exactly what color those lines should be, we can work
        backwards to figure out the physical camera settings, then save them.

        Parameters
        ----------
        image_path : `str`
            Where the picture of the star is saved.
        camera_name : `str`, optional
            Which camera we are tuning. If None, uses the default camera.
        star_pos : `Tuple[float, float]`, optional
            The `(x, y)` location of the star. If None, it will try to find it.

        Returns
        -------
        calibration_summary : `Dict[str, Any]`
            A report of what settings we calculated and how accurate they are.
        """
        logger.info("Starting spectroscopy calibration tuning for %s...", image_path)
        image = AstrometricsImage(image_path)

        if camera_name is None:
            camera_name = self.config.get_value("Observatory.Camera", "default_primary_camera", "Unknown")

        # Load the custom spectroscopy configuration for the target camera
        from astrometricslib.utilities import ConfigLoader

        spec_config = ConfigLoader.load_spectroscopy_config(app_config=self.config, camera_name=camera_name)
        spec_pipeline = SpectroscopyPipeline(config=spec_config)

        star_pos = self._resolve_calibration_star_position(image_path, star_pos)
        logger.info("Target star identified at position: %s", star_pos)

        detected_angle, smoothed, sample_distances_px = self._extract_smoothed_spectrum(
            spec_pipeline, image, star_pos
        )

        dips = self._detect_absorption_dips(smoothed)
        dips = self._refine_dip_centers(smoothed, dips)
        logger.info(
            "Detected %s candidate absorption dips at sample positions: %s",
            len(dips),
            [round(dip, 2) for dip in dips],
        )

        # We know Vega (the target) should have dark lines at exactly the
        # Hydrogen Balmer series wavelengths.
        target_wls = np.array([
            BALMER_SERIES_NM["H-delta"],
            BALMER_SERIES_NM["H-gamma"],
            BALMER_SERIES_NM["H-beta"],
        ])

        best_rms, best_grating_distance_mm, best_combo = self._fit_grating_distance(
            dips, sample_distances_px, spec_pipeline, target_wls
        )

        # Now that we know the distance (L), calculate exactly where the
        # spectrum starts in pixels.
        best_x0 = self._spectrum_start_offset_px(spec_pipeline.config, best_grating_distance_mm)

        use_flare_mask_extraction, max_extraction_length_px = self._detect_extraction_geometry_quirks(
            image, spec_pipeline, star_pos, best_grating_distance_mm, best_x0
        )

        tuned_grating_distance_mm, tuned_x0 = self._save_tuned_calibration(
            camera_name,
            best_grating_distance_mm,
            best_x0,
            use_flare_mask_extraction,
            max_extraction_length_px,
        )

        return self._build_calibration_summary(
            camera_name,
            tuned_grating_distance_mm,
            tuned_x0,
            best_rms,
            detected_angle,
            sample_distances_px,
            best_combo,
            target_wls,
            spec_pipeline,
            use_flare_mask_extraction,
            max_extraction_length_px,
        )

    @staticmethod
    def _spectrum_start_offset_px(config: SpectroscopyConfig, grating_distance_mm: float) -> float:
        """Work out how many pixels from the star the spectrum starts.

        Parameters
        ----------
        config : `SpectroscopyConfig`
            The camera and grating settings. The wavelength that counts as
            the start of the spectrum is its `extraction_start_wavelength_nm`
            (380 nm unless the config file says otherwise).
        grating_distance_mm : `float`
            The distance from the grating to the sensor, in millimeters.

        Returns
        -------
        offset_px : `float`
            The distance, in pixels, from the star to the start wavelength.
        """
        return calculate_pixel_offset(
            wavelength_nm=config.extraction_start_wavelength_nm,
            grating_distance_mm=grating_distance_mm,
            lines_per_mm=config.grating_lines_per_mm,
            pixel_size_um=config.camera.pixel_size_um,
        )

    @staticmethod
    def _detect_extraction_geometry_quirks(
        image: AstrometricsImage,
        spec_pipeline: SpectroscopyPipeline,
        star_pos: tuple[float, float],
        tuned_grating_distance_mm: float,
        tuned_dispersion_start_px: float,
    ) -> tuple[bool, float | None]:
        """Derive flare-masking and extraction-length-cap needs from the frame.

        Rebuilds the instrument model with the just-fitted grating
        distance and dispersion start, then checks two things against
        the actual calibration image: whether the star's own light
        still saturates the pixels where extraction would begin (flare
        contamination), and whether the physics-derived extraction
        length would run past the image edge (needing a hard cap).

        Returns
        -------
        use_flare_mask_extraction : `bool`
            Whether the star's flare/astigmatism saturates the pixels
            at the theoretical dispersion start.
        max_extraction_length_px : `float` or `None`
            The absolute pixel offset (from the star) at which
            extraction must stop to stay within the image, or `None`
            if the full physics-derived length already fits.
        """
        tuned_config = spec_pipeline.config.with_overrides(
            grating_distance_mm=tuned_grating_distance_mm, dispersion_start_px=tuned_dispersion_start_px
        )
        tuned_instrument = SpectroscopyInstrument(tuned_config)

        vector = tuned_instrument.get_dispersion_vector()
        offset_px = tuned_instrument.zero_order_offset_px
        length_px = tuned_instrument.expected_length_px
        base_pos = (
            star_pos[0] + tuned_config.dispersion_offset_x,
            star_pos[1] + tuned_config.dispersion_offset_y,
        )
        extraction_start = (base_pos[0] + offset_px * vector[0], base_pos[1] + offset_px * vector[1])

        use_flare_mask_extraction = SpectroscopyCalibrationTuner._detect_flare_contamination(
            image,
            extraction_start,
            int(tuned_config.extraction_radius),
            spec_pipeline.camera_profile.saturation_threshold_adu.value,
        )
        max_extraction_length_px = SpectroscopyCalibrationTuner._detect_max_extraction_length_px(
            base_pos, vector, offset_px, length_px, image.data.shape
        )

        return use_flare_mask_extraction, max_extraction_length_px

    @staticmethod
    def _detect_flare_contamination(
        image: AstrometricsImage,
        extraction_start: tuple[float, float],
        extraction_radius: int,
        saturation_threshold_adu: float,
    ) -> bool:
        """Check for star-flare saturation at the theoretical dispersion start.

        Parameters
        ----------
        image : `AstrometricsImage`
            The calibration image.
        extraction_start : `tuple` [`float`, `float`]
            Where the theoretical dispersion starts, in pixels.
        extraction_radius : `int`
            How far around that point to look, in pixels.
        saturation_threshold_adu : `float`
            A pixel at or above this value counts as saturated. Take it from
            the camera's profile.

        Returns
        -------
        contaminated : `bool`
            True if enough pixels at the extraction start are still
            saturated by the star's own light to warrant flare-masked
            extraction.
        """
        data = image.data
        height, width = data.shape
        x_center, y_center = round(extraction_start[0]), round(extraction_start[1])
        y_start, y_end = max(0, y_center - extraction_radius), min(height, y_center + extraction_radius + 1)
        x_start, x_end = max(0, x_center - extraction_radius), min(width, x_center + extraction_radius + 1)
        cutout = np.asarray(data[y_start:y_end, x_start:x_end], dtype=float)
        if cutout.size == 0:
            return False
        fraction = compute_saturated_pixel_fraction(cutout, saturation_threshold_adu)
        return fraction > _FLARE_CONTAMINATION_FRACTION_THRESHOLD

    @staticmethod
    def _detect_max_extraction_length_px(
        base_pos: tuple[float, float],
        vector: np.ndarray,
        offset_px: float,
        length_px: float,
        image_shape: tuple[int, int],
    ) -> float | None:
        """Cap the dispersion-axis extraction offset at the image edge.

        Returns
        -------
        max_extraction_length_px : `float` or `None`
            The absolute pixel offset (from the star) at which
            extraction must stop to stay within the image, or `None`
            if the full physics-derived length already fits within the
            image bounds.
        """
        height, width = image_shape
        theoretical_end_offset = offset_px + length_px

        candidates = []
        bx, by = base_pos
        vx, vy = vector
        if vx > 1e-9:
            candidates.append((width - 1 - bx) / vx)
        elif vx < -1e-9:
            candidates.append((0 - bx) / vx)
        if vy > 1e-9:
            candidates.append((height - 1 - by) / vy)
        elif vy < -1e-9:
            candidates.append((0 - by) / vy)

        if not candidates:
            return None

        available_end_offset = min(candidates)
        if available_end_offset >= theoretical_end_offset or available_end_offset <= offset_px:
            return None

        return round(float(available_end_offset), 1)

    def _resolve_calibration_star_position(
        self, image_path: str, star_pos: tuple[float, float] | None
    ) -> tuple[float, float]:
        """Find the calibration star's pixel position, if not already given.

        Returns
        -------
        star_pos : `tuple` [`float`, `float`]
            The `(x, y)` pixel position to extract the spectrum from.

        Raises
        ------
        ProcessingError
            If no star position was given and none could be detected.
        """
        if star_pos is not None:
            return star_pos

        logger.info("Star position not provided. Autodetecting brightest source using AstrometryPipeline...")
        from astrometricslib.pipelines.astrometry.pipeline import (
            AstrometryPipeline,
        )

        astrometry = AstrometryPipeline(app_config=self.config)
        context = astrometry.prepare_image(image_path, attempt_plate_solving=False)
        if not context.stellar_objects:
            raise ProcessingError("No stars detected in the calibration frame.")
        # The brightest source in a stacked calibration frame is our target
        star_x, star_y = _read_xy_source_position(context.stellar_objects[0].star_data)
        return (float(star_x), float(star_y))

    @staticmethod
    def _extract_smoothed_spectrum(
        spec_pipeline: SpectroscopyPipeline, image: AstrometricsImage, star_pos: tuple[float, float]
    ) -> tuple[float, np.ndarray, np.ndarray]:
        """Extract the star's spectrum and smooth it to suppress pixel noise.

        The pipeline drops samples that are off the image or outside the
        camera's wavelength range, so the first kept sample is often not
        the first sample requested. The distance array returned here says
        where every kept sample really lies.

        Returns
        -------
        detected_angle : `float`
            The spectrum's detected rotation angle, in degrees.
        smoothed : `numpy.ndarray`
            The intensity profile, smoothed with a window of 5.
        sample_distances_px : `numpy.ndarray`
            The distance of each sample from the zero order, in pixels,
            measured along the dispersion direction. It has one value per
            entry of `smoothed`.
        """
        # auto_detect_angle is enabled to handle camera rotation tilts
        result = spec_pipeline._process_single_star(image, star_pos, auto_detect_angle=True)
        detected_angle = float(result["detected_angle"])

        intensities = np.array(result["intensities"])
        sample_distances_px = np.asarray(result["sample_distances_px"], dtype=float)

        # Apply smoothing window of size 5 to suppress pixel noise and
        # highlight broad absorption bands
        smoothed = spec_pipeline.calibrator.apply_smoothing(intensities, window=5)
        return detected_angle, smoothed, sample_distances_px

    @staticmethod
    def _running_continuum(spectrum: np.ndarray) -> np.ndarray:
        """Estimate the slowly changing brightness under the absorption lines.

        Parameters
        ----------
        spectrum : `numpy.ndarray`
            The smoothed intensity profile.

        Returns
        -------
        continuum : `numpy.ndarray`
            A running median of the spectrum, with the same length. The
            window is `_CONTINUUM_WINDOW_SAMPLES` wide (shorter, and always
            odd, for a spectrum shorter than that). A line much narrower
            than the window does not move the median.
        """
        window = min(_CONTINUUM_WINDOW_SAMPLES, len(spectrum))
        window -= 1 - window % 2
        return median_filter(spectrum, size=max(window, 1), mode="nearest")

    @staticmethod
    def _normalize_by_continuum(spectrum: np.ndarray, continuum: np.ndarray) -> np.ndarray:
        """Divide a spectrum by its continuum.

        Parameters
        ----------
        spectrum : `numpy.ndarray`
            The smoothed intensity profile, in camera counts.
        continuum : `numpy.ndarray`
            The continuum, in the same units and with the same length.

        Returns
        -------
        normalized : `numpy.ndarray`
            The spectrum as a fraction of the continuum, so the continuum
            is 1.0 and a 10 percent deep line dips to 0.9. A sample whose
            continuum is not positive is set to 1.0, since it carries no
            usable signal.
        """
        positive = continuum > 0.0
        return np.where(positive, spectrum / np.where(positive, continuum, 1.0), 1.0)

    @staticmethod
    def _local_noise(normalized: np.ndarray) -> np.ndarray:
        """Estimate the noise around each sample of a normalized spectrum.

        Subtracts a running median from the spectrum and takes a running
        median of the absolute leftovers, the median absolute deviation
        (MAD), scaled to a standard deviation. A median ignores the few
        samples that sit in an absorption line, so a line does not inflate
        the noise estimate.

        Parameters
        ----------
        normalized : `numpy.ndarray`
            The spectrum as a fraction of its continuum.

        Returns
        -------
        noise : `numpy.ndarray`
            The noise at each sample, as a fraction of the continuum.
        """
        window = min(_CONTINUUM_WINDOW_SAMPLES, len(normalized))
        window -= 1 - window % 2
        size = max(window, 1)
        residual = normalized - median_filter(normalized, size=size, mode="nearest")
        return _MAD_TO_SIGMA * median_filter(np.abs(residual), size=size, mode="nearest")

    @staticmethod
    def _find_significant_dips(normalized: np.ndarray, minimum_prominence: float) -> list[tuple[int, float]]:
        """Find the dips deeper than a fraction and deeper than the noise.

        Parameters
        ----------
        normalized : `numpy.ndarray`
            The spectrum as a fraction of its continuum.
        minimum_prominence : `float`
            The least depth, as a fraction of the continuum, a dip must have
            compared with its surroundings.

        Returns
        -------
        dips : `list` [`tuple` [`int`, `float`]]
            The index and prominence of each dip that is at least
            `minimum_prominence` deep and at least
            `_NOISE_PROMINENCE_MULTIPLE` times the local noise, away from
            both ends of the spectrum.
        """
        noise = SpectroscopyCalibrationTuner._local_noise(normalized)
        peak_indices, properties = find_peaks(-normalized, prominence=minimum_prominence, wlen=31, distance=6)
        return [
            (int(index), float(prominence))
            for index, prominence in zip(peak_indices, properties["prominences"], strict=True)
            if _DIP_EDGE_MARGIN_SAMPLES <= index < len(normalized) - _DIP_EDGE_MARGIN_SAMPLES
            and prominence >= _NOISE_PROMINENCE_MULTIPLE * noise[index]
        ]

    @staticmethod
    def _sweep_dip_depths(normalized: np.ndarray) -> list[tuple[int, float]]:
        """Look for dips, lowering the depth requirement until 3 are found.

        Parameters
        ----------
        normalized : `numpy.ndarray`
            The spectrum as a fraction of its continuum.

        Returns
        -------
        dips : `list` [`tuple` [`int`, `float`]]
            The index and prominence of each dip found at the first depth
            that gave at least 3, or at the last depth tried when none did.
        """
        minimum_prominence = _FIRST_FRACTIONAL_PROMINENCE
        dips: list[tuple[int, float]] = []
        while len(dips) < 3 and minimum_prominence >= _LAST_FRACTIONAL_PROMINENCE:
            dips = SpectroscopyCalibrationTuner._find_significant_dips(normalized, minimum_prominence)
            minimum_prominence -= _FRACTIONAL_PROMINENCE_STEP
        return dips

    @staticmethod
    def _detect_absorption_dips(smoothed: np.ndarray) -> list[int]:
        """Find the dark absorption-line valleys in a smoothed spectrum.

        Look for the dark "valleys" (absorption lines) in the spectrum.
        We need to find at least 3 distinct valleys to figure out the
        math.

        The spectrum is first divided by a running median, its continuum,
        so a valley's depth is a fraction of the continuum and does not
        depend on how bright the star is in camera counts. A valley counts
        only if it is at least `_NOISE_PROMINENCE_MULTIPLE` times deeper
        than the local noise (the median absolute deviation of what is left
        after the continuum is removed). We start by asking for a 1 percent
        deep valley. If we can't find 3, we slowly lower that to 0.1
        percent, making sure not to count the same valley twice. The noise
        requirement stays, so a noisy spectrum cannot be searched down to
        its noise.

        Fallback: If the running median does not follow the background
        (maybe from heat), we fit a smooth cubic curve as the continuum
        instead and search again.

        Only the `_MAX_DIP_CANDIDATES` deepest valleys are kept, so the
        search over groups of three valleys stays short.

        Returns
        -------
        dips : `list` [`int`]
            The indices of at least 3 and at most `_MAX_DIP_CANDIDATES`
            candidate absorption dips, in increasing order.

        Raises
        ------
        ProcessingError
            If fewer than 3 dips could be found even after the fallback.
        """
        smoothed = np.asarray(smoothed, dtype=float)
        normalized = SpectroscopyCalibrationTuner._normalize_by_continuum(
            smoothed, SpectroscopyCalibrationTuner._running_continuum(smoothed)
        )
        dips = SpectroscopyCalibrationTuner._sweep_dip_depths(normalized)

        if len(dips) < 3:
            x_idx = np.arange(len(smoothed))
            try:
                poly = np.polyfit(x_idx, smoothed, 3)
                continuum = np.polyval(poly, x_idx)
                norm_spectrum = SpectroscopyCalibrationTuner._normalize_by_continuum(
                    smoothed, np.maximum(continuum, 1e-6)
                )
                dips = SpectroscopyCalibrationTuner._sweep_dip_depths(norm_spectrum)
            except DATA_ERRORS as norm_err:
                logger.debug("Continuum baseline normalization fallback failed: %s", norm_err)

        if len(dips) < 3:
            raise ProcessingError(
                "Could not identify at least 3 absorption features. "
                f"Found dips at indices: {[index for index, _ in dips]}"
            )

        deepest = sorted(dips, key=lambda dip: dip[1], reverse=True)[:_MAX_DIP_CANDIDATES]
        return sorted(index for index, _ in deepest)

    @staticmethod
    def _refine_dip_centers(smoothed: np.ndarray, dips: Sequence[int]) -> list[float]:
        """Place each dip to a fraction of a sample.

        The lowest sample of a dip can be up to half a sample from its true
        centre. A parabola through that sample and its two neighbours has
        its lowest point closer to the centre, so that point is used.

        The spectrum is divided by its continuum first. Otherwise a sloping
        continuum tilts the parabola and moves its lowest point.

        Parameters
        ----------
        smoothed : `numpy.ndarray`
            The smoothed intensity profile the dips were found in.
        dips : `Sequence` [`int`]
            The sample index of the lowest point of each dip.

        Returns
        -------
        centers : `list` [`float`]
            The refined position of each dip, in samples. It differs from
            the dip's index by at most 0.5. A dip whose three samples do not
            curve upward (a flat bottom) keeps its index.
        """
        smoothed = np.asarray(smoothed, dtype=float)
        normalized = SpectroscopyCalibrationTuner._normalize_by_continuum(
            smoothed, SpectroscopyCalibrationTuner._running_continuum(smoothed)
        )
        centers = []
        for index in dips:
            if not 0 < index < len(normalized) - 1:
                centers.append(float(index))
                continue
            left, middle, right = normalized[index - 1], normalized[index], normalized[index + 1]
            curvature = left - 2.0 * middle + right
            if curvature <= 0.0:
                centers.append(float(index))
                continue
            offset = 0.5 * (left - right) / curvature
            centers.append(float(index + min(max(offset, -0.5), 0.5)))
        return centers

    @staticmethod
    def _positions_to_distances_px(positions: Sequence[float], sample_distances_px: np.ndarray) -> np.ndarray:
        """Turn positions in samples into distances from the zero order.

        Parameters
        ----------
        positions : `Sequence` [`float`]
            Positions in the extracted spectrum, in samples. A whole number
            is a sample; a fraction is between two samples.
        sample_distances_px : `numpy.ndarray`
            The recorded distance of every sample from the zero order, in
            pixels, measured along the dispersion direction.

        Returns
        -------
        distances_px : `numpy.ndarray`
            The distance of each position, in pixels. Between two samples
            it is interpolated in a straight line.
        """
        return np.interp(
            np.asarray(positions, dtype=float), np.arange(len(sample_distances_px)), sample_distances_px
        )

    @staticmethod
    def _fit_grating_distance(
        dips: Sequence[float],
        sample_distances_px: np.ndarray,
        spec_pipeline: SpectroscopyPipeline,
        target_wls: np.ndarray,
    ) -> tuple[float, float, tuple[float, ...]]:
        """Search dip combinations for the grating distance that fits best.

        Tests every combination of 3 valleys against the known target
        wavelengths. For each combination, runs a math solver to
        figure out what grating distance (L) makes the lines fit best.

        A dip position counts samples in the extracted spectrum, and the
        pipeline may have dropped leading samples. So the distance of a
        dip comes from the recorded distance of its sample, never from
        the position plus a fixed start. A position between two samples
        (see `_refine_dip_centers`) gets a distance between the two
        recorded distances.

        Parameters
        ----------
        dips : `Sequence` [`float`]
            The positions of the candidate absorption dips in the extracted
            spectrum, in samples. Whole numbers or refined fractions.
        sample_distances_px : `numpy.ndarray`
            The distance of each sample from the zero order, in pixels,
            measured along the dispersion direction. It has one value per
            sample, so `sample_distances_px[i]` is the distance of sample
            `i`.
        spec_pipeline : `SpectroscopyPipeline`
            The pipeline that holds the grating and camera settings.
        target_wls : `numpy.ndarray`
            The three reference wavelengths, in nanometers, in order of
            increasing wavelength.

        Returns
        -------
        best_rms : `float`
            The best fit's RMS error, in nanometers.
        best_grating_distance_mm : `float`
            The best-fitting grating distance.
        best_combo : `tuple` [`float`, ...]
            The 3 dip positions that produced the best fit.

        Raises
        ------
        ProcessingError
            If no combination fit within an acceptable RMS error.
        """
        best_rms = float("inf")
        best_grating_distance_mm = None
        best_combo = None

        for combo in itertools.combinations(sorted(dips), 3):
            absolute_offsets = SpectroscopyCalibrationTuner._positions_to_distances_px(
                combo, sample_distances_px
            )
            loss = SpectroscopyCalibrationTuner._make_wavelength_loss(
                absolute_offsets, spec_pipeline, target_wls
            )

            # Run the math solver. We guess the grating is around 16.5 mm away
            # based on how the camera is physically built. We limit the solver
            # to between 10 and 30 mm so it doesn't give us an impossible
            # answer.
            res = minimize(loss, x0=[16.5], method="L-BFGS-B", bounds=[(10.0, 30.0)])

            if res.success:
                rms = np.sqrt(res.fun / 3)
                if rms < best_rms:
                    best_rms = rms
                    best_grating_distance_mm = res.x[0]
                    best_combo = combo

        if best_grating_distance_mm is None or best_rms > 10.0:
            raise ProcessingError(
                "Failed to fit physical parameters to dips with acceptable "
                f"accuracy (best RMS = {best_rms} nm)"
            )

        return best_rms, best_grating_distance_mm, best_combo

    @staticmethod
    def _make_wavelength_loss(
        pixel_offsets_px: np.ndarray, spec_pipeline: SpectroscopyPipeline, target_wls: np.ndarray
    ) -> Callable[[np.ndarray], float]:
        """Build the function the solver minimizes for one set of dips.

        Parameters
        ----------
        pixel_offsets_px : `numpy.ndarray`
            The distances of the three dips from the zero order, in pixels.
        spec_pipeline : `SpectroscopyPipeline`
            The pipeline that holds the grating and camera settings.
        target_wls : `numpy.ndarray`
            The three reference wavelengths, in nanometers.

        Returns
        -------
        loss : `Callable`
            A function of the trial grating distance (a one-element array,
            in millimeters). It returns the sum of squared differences, in
            square nanometers, between the wavelengths the physical model
            predicts at the dips and the reference wavelengths.
        """

        def loss(grating_distance_param: np.ndarray) -> float:
            """Score one trial grating distance.

            Parameters
            ----------
            grating_distance_param : `numpy.ndarray`
                A one-element array with the trial grating distance, in
                millimeters.

            Returns
            -------
            squared_error : `float`
                The sum of squared wavelength errors, in square
                nanometers. A very large number if the distance is not
                positive.
            """
            grating_distance_mm = grating_distance_param[0]
            if grating_distance_mm <= 0:
                return 1e10

            # Delegate to the stateless optics_physics library for
            # first-order grating calculations
            calculated_wavelengths = calculate_wavelength(
                pixel_offset_px=pixel_offsets_px,
                grating_distance_mm=grating_distance_mm,
                lines_per_mm=spec_pipeline.config.grating_lines_per_mm,
                pixel_size_um=spec_pipeline.config.camera.pixel_size_um,
            )
            # Compute sum of squared errors between calibrated
            # model wavelengths and target reference bands
            return float(np.sum((calculated_wavelengths - target_wls) ** 2))

        return loss

    def _save_tuned_calibration(
        self,
        camera_name: str,
        best_grating_distance_mm: float,
        best_x0: float,
        use_flare_mask_extraction: bool,
        max_extraction_length_px: float | None,
    ) -> tuple[float, float]:
        """Round the fitted parameters and save them to the camera's config.

        Returns
        -------
        tuned_grating_distance_mm, tuned_x0 : `float`
            The rounded values that were saved.
        """
        tuned_grating_distance_mm = round(float(best_grating_distance_mm), 2)
        tuned_x0 = round(float(best_x0), 1)

        section_name = f"Observatory.Camera.{camera_name}"
        section_params = {
            "grating_distance_mm": str(tuned_grating_distance_mm),
            "dispersion_start_px": str(tuned_x0),
            "use_flare_mask_extraction": str(use_flare_mask_extraction).lower(),
        }
        if max_extraction_length_px is not None:
            section_params["max_extraction_length_px"] = str(max_extraction_length_px)
        new_params = {section_name: section_params}

        logger.info(
            "Saving tuned parameters for %s: grating_distance = %s mm, start = %s px, "
            "use_flare_mask_extraction = %s, max_extraction_length_px = %s",
            camera_name,
            tuned_grating_distance_mm,
            tuned_x0,
            use_flare_mask_extraction,
            max_extraction_length_px,
        )
        self.config.update_config(new_params)

        return tuned_grating_distance_mm, tuned_x0

    @staticmethod
    def _build_calibration_summary(
        camera_name: str,
        tuned_grating_distance_mm: float,
        tuned_x0: float,
        best_rms: float,
        detected_angle: float,
        sample_distances_px: np.ndarray,
        best_combo: tuple[float, ...],
        target_wls: np.ndarray,
        spec_pipeline: SpectroscopyPipeline,
        use_flare_mask_extraction: bool,
        max_extraction_length_px: float | None,
    ) -> dict[str, Any]:
        """Build the final calibration report, including per-line deviations.

        The `pixel_offset` of each line is the recorded distance of its dip
        from the zero order, the same distance the fit used. A dip between
        two samples gets a distance between theirs. The `extracted_index` is
        the nearest whole sample.

        Returns
        -------
        calibration_summary : `dict`
            A report of what settings were calculated and how accurate
            they are.
        """
        px_offsets = SpectroscopyCalibrationTuner._positions_to_distances_px(best_combo, sample_distances_px)
        calibrated_wls = calculate_wavelength(
            pixel_offset_px=px_offsets,
            grating_distance_mm=tuned_grating_distance_mm,
            lines_per_mm=spec_pipeline.config.grating_lines_per_mm,
            pixel_size_um=spec_pipeline.config.camera.pixel_size_um,
        )

        detailed_calibration = []
        features_map = ["H-delta", "H-gamma", "H-beta"]
        for name, idx, offset_px, target, calc in zip(
            features_map, best_combo, px_offsets, target_wls, calibrated_wls, strict=False
        ):
            detailed_calibration.append({
                "feature": name,
                "extracted_index": round(idx),
                "pixel_offset": float(offset_px),
                "target_wavelength_nm": float(target),
                "calibrated_wavelength_nm": float(round(calc, 2)),
                "deviation_nm": float(round(calc - target, 2)),
            })

        return {
            "status": "success",
            "camera_name": camera_name,
            "fitted_grating_distance_mm": tuned_grating_distance_mm,
            "fitted_dispersion_start_px": tuned_x0,
            "rms_error_nm": round(float(best_rms), 3),
            "detected_angle_degrees": round(detected_angle, 4),
            "detailed_calibration": detailed_calibration,
            "use_flare_mask_extraction": use_flare_mask_extraction,
            "max_extraction_length_px": max_extraction_length_px,
        }
