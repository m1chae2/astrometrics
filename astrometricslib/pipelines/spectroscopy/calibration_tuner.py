"""Automatically figure out the physics settings for our camera.

We use a known bright star (like Vega) as a reference. By finding the known
dark bands in its spectrum (hydrogen lines) and doing some math, we can
calculate exactly how far the grating is from the sensor and where the
rainbow starts. Then, it saves those numbers for next time.
"""

import itertools
import logging
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.signal import find_peaks

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.shared.quality.quality_metrics import DEFAULT_SATURATION_ADU_THRESHOLD
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.pipelines.spectroscopy.optics_physics import (
    BALMER_SERIES_NM,
    calculate_pixel_offset,
    calculate_wavelength,
)
from astrometricslib.pipelines.spectroscopy.pipeline import (
    SpectroscopyPipeline,
    _read_xy_source_position,
)
from astrometricslib.pipelines.spectroscopy.spectroscopy_instrument import SpectroscopyInstrument

logger = logging.getLogger(__name__)

# If more than this fraction of the pixels at the theoretical dispersion
# start are still saturated, the star's own flare/astigmatism is judged to
# be bleeding into the spectrum, and flare-masked extraction is turned on.
_FLARE_CONTAMINATION_FRACTION_THRESHOLD = 0.1


class SpectroscopyCalibrationTuner:
    """Figures out exactly how our spectrograph camera is set up.

    It looks at a known star's spectrum, finds the dark lines, and calculates
    the physical distance between the grating and the camera sensor.

    Attributes
    ----------
    config : `AppConfiguration`
        Where we load our settings from and save our new calibration to.
    """

    def __init__(self, config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the tuner service with the system configuration.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The settings object. If None, it will grab the default one.
        """
        if config is None:
            from astrometricslib.utilities.config_loader import get_configuration

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
        logger.info(f"Starting spectroscopy calibration tuning for {image_path}...")
        image = AstrometricsImage(image_path)

        if camera_name is None:
            camera_name = self.config.get_value("Observatory.Camera", "default_primary_camera", "Unknown")

        # Load the custom spectroscopy configuration for the target camera
        from astrometricslib.utilities import ConfigLoader

        spec_config = ConfigLoader.load_spectroscopy_config(app_config=self.config, camera_name=camera_name)
        spec_pipeline = SpectroscopyPipeline(config=spec_config)

        star_pos = self._resolve_calibration_star_position(image_path, star_pos)
        logger.info(f"Target star identified at position: {star_pos}")

        detected_angle, smoothed = self._extract_smoothed_spectrum(spec_pipeline, image, star_pos)

        dips = self._detect_absorption_dips(smoothed)
        logger.info(f"Detected {len(dips)} candidate absorption dips at indices: {dips}")

        # We know Vega (the target) should have dark lines at exactly the
        # Hydrogen Balmer series wavelengths.
        target_wls = np.array([
            BALMER_SERIES_NM["H-delta"],
            BALMER_SERIES_NM["H-gamma"],
            BALMER_SERIES_NM["H-beta"],
        ])
        current_start_px = float(spec_pipeline.instrument.zero_order_offset_px)

        best_rms, best_grating_distance_mm, best_combo = self._fit_grating_distance(
            dips, current_start_px, spec_pipeline, target_wls
        )

        # Now that we know the distance (L), calculate exactly where the
        # visible spectrum starts (380 nm) in pixels.
        best_x0 = calculate_pixel_offset(
            wavelength_nm=380.0,
            grating_distance_mm=best_grating_distance_mm,
            lines_per_mm=spec_pipeline.config.grating_lines_per_mm,
            pixel_size_um=spec_pipeline.config.camera.pixel_size_um,
        )

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
            current_start_px,
            best_combo,
            target_wls,
            spec_pipeline,
            use_flare_mask_extraction,
            max_extraction_length_px,
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
            image, extraction_start, int(tuned_config.extraction_radius)
        )
        max_extraction_length_px = SpectroscopyCalibrationTuner._detect_max_extraction_length_px(
            base_pos, vector, offset_px, length_px, image.data.shape
        )

        return use_flare_mask_extraction, max_extraction_length_px

    @staticmethod
    def _detect_flare_contamination(
        image: AstrometricsImage, extraction_start: tuple[float, float], extraction_radius: int
    ) -> bool:
        """Check for star-flare saturation at the theoretical dispersion start.

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
        fraction = compute_saturated_pixel_fraction(cutout, DEFAULT_SATURATION_ADU_THRESHOLD)
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
        ValueError
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
            raise ValueError("No stars detected in the calibration frame.")
        # The brightest source in a stacked calibration frame is our target
        star_x, star_y = _read_xy_source_position(context.stellar_objects[0].star_data)
        return (float(star_x), float(star_y))

    @staticmethod
    def _extract_smoothed_spectrum(
        spec_pipeline: SpectroscopyPipeline, image: AstrometricsImage, star_pos: tuple[float, float]
    ) -> tuple[float, np.ndarray]:
        """Extract the star's spectrum and smooth it to suppress pixel noise.

        Returns
        -------
        detected_angle : `float`
            The spectrum's detected rotation angle, in degrees.
        smoothed : `numpy.ndarray`
            The intensity profile, smoothed with a window of 5.
        """
        # auto_detect_angle is enabled to handle camera rotation tilts
        result = spec_pipeline._process_single_star(image, star_pos, auto_detect_angle=True)
        detected_angle = float(result["detected_angle"])

        intensities = np.array(result["intensities"])

        # Apply smoothing window of size 5 to suppress pixel noise and
        # highlight broad absorption bands
        smoothed = spec_pipeline.calibrator.apply_smoothing(intensities, window=5)
        return detected_angle, smoothed

    @staticmethod
    def _detect_absorption_dips(smoothed: np.ndarray) -> list[int]:
        """Find the dark absorption-line valleys in a smoothed spectrum.

        Look for the dark "valleys" (absorption lines) in the spectrum.
        We need to find at least 3 distinct valleys to figure out the
        math. We start by looking for very deep valleys (1% drop in
        brightness). If we can't find 3, we slowly lower our standards
        until we hit the noise floor (0.1%), making sure not to count
        the same valley twice.

        Fallback: If the background brightness is too uneven (maybe
        from heat), the simple valley search will fail. Here we try to
        flatten out the background first, then search again.

        Returns
        -------
        dips : `list` [`int`]
            The indices of at least 3 candidate absorption dips.

        Raises
        ------
        ValueError
            If fewer than 3 dips could be found even after the fallback.
        """
        min_depth = 0.01
        dips = []
        while len(dips) < 3 and min_depth >= 0.001:
            peak_indices, _ = find_peaks(-smoothed, prominence=min_depth, wlen=31, distance=6)
            dips = [int(i) for i in peak_indices if 5 <= i < len(smoothed) - 5]
            min_depth -= 0.002

        if len(dips) < 3:
            x_idx = np.arange(len(smoothed))
            try:
                poly = np.polyfit(x_idx, smoothed, 3)
                continuum = np.polyval(poly, x_idx)
                norm_spectrum = smoothed / np.maximum(continuum, 1e-6)
                min_depth = 0.01
                while len(dips) < 3 and min_depth >= 0.001:
                    peak_indices, _ = find_peaks(-norm_spectrum, prominence=min_depth, wlen=31, distance=6)
                    dips = [int(i) for i in peak_indices if 5 <= i < len(smoothed) - 5]
                    min_depth -= 0.002
            except Exception as norm_err:
                logger.debug(f"Continuum baseline normalization fallback failed: {norm_err}")

        if len(dips) < 3:
            raise ValueError(
                f"Could not identify at least 3 absorption features. Found dips at indices: {dips}"
            )

        return dips

    @staticmethod
    def _fit_grating_distance(
        dips: list[int],
        current_start_px: float,
        spec_pipeline: SpectroscopyPipeline,
        target_wls: np.ndarray,
    ) -> tuple[float, float, tuple[int, ...]]:
        """Search dip combinations for the grating distance that fits best.

        Tests every combination of 3 valleys against the known target
        wavelengths. For each combination, runs a math solver to
        figure out what grating distance (L) makes the lines fit best.

        Returns
        -------
        best_rms : `float`
            The best fit's RMS error, in nanometers.
        best_grating_distance_mm : `float`
            The best-fitting grating distance.
        best_combo : `tuple` [`int`, ...]
            The 3 dip indices that produced the best fit.

        Raises
        ------
        ValueError
            If no combination fit within an acceptable RMS error.
        """
        best_rms = float("inf")
        best_grating_distance_mm = None
        best_combo = None

        for combo in itertools.combinations(sorted(dips), 3):
            combo_indices = np.array(combo)
            absolute_offsets = current_start_px + combo_indices

            def loss(grating_distance_param):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
                grating_distance_mm = grating_distance_param[0]
                if grating_distance_mm <= 0:
                    return 1e10

                # Delegate to the stateless optics_physics library for
                # first-order grating calculations
                calculated_wavelengths = calculate_wavelength(
                    pixel_offset_px=absolute_offsets,  # ruff: ignore[function-uses-loop-variable] -- consumed synchronously, not deferred
                    grating_distance_mm=grating_distance_mm,
                    lines_per_mm=spec_pipeline.config.grating_lines_per_mm,
                    pixel_size_um=spec_pipeline.config.camera.pixel_size_um,
                )
                # Compute sum of squared errors between calibrated
                # model wavelengths and target reference bands
                return np.sum((calculated_wavelengths - target_wls) ** 2)

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
            raise ValueError(
                "Failed to fit physical parameters to dips with acceptable "
                f"accuracy (best RMS = {best_rms} nm)"
            )

        return best_rms, best_grating_distance_mm, best_combo

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
            f"Saving tuned parameters for {camera_name}: grating_distance = {tuned_grating_distance_mm} mm, "
            f"start = {tuned_x0} px, use_flare_mask_extraction = {use_flare_mask_extraction}, "
            f"max_extraction_length_px = {max_extraction_length_px}"
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
        current_start_px: float,
        best_combo: tuple[int, ...],
        target_wls: np.ndarray,
        spec_pipeline: SpectroscopyPipeline,
        use_flare_mask_extraction: bool,
        max_extraction_length_px: float | None,
    ) -> dict[str, Any]:
        """Build the final calibration report, including per-line deviations.

        Returns
        -------
        calibration_summary : `dict`
            A report of what settings were calculated and how accurate
            they are.
        """
        px_offsets = current_start_px + np.array(best_combo)
        calibrated_wls = calculate_wavelength(
            pixel_offset_px=px_offsets,
            grating_distance_mm=tuned_grating_distance_mm,
            lines_per_mm=spec_pipeline.config.grating_lines_per_mm,
            pixel_size_um=spec_pipeline.config.camera.pixel_size_um,
        )

        detailed_calibration = []
        features_map = ["H-delta", "H-gamma", "H-beta"]
        for name, idx, target, calc in zip(
            features_map, best_combo, target_wls, calibrated_wls, strict=False
        ):
            detailed_calibration.append({
                "feature": name,
                "extracted_index": int(idx),
                "pixel_offset": float(tuned_x0 + idx),
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
