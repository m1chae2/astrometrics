"""Automatic tuning of physical spectroscopy calibration parameters.

Processes a stacked stellar FITS spectrum (e.g. Vega), detects
absorption valleys, optimizes physical parameters (grating distance L
and zero-order offset dispersion_start_px) using a non-linear
least-squares fit, and automatically updates the high-level interface.config
file.

REQ: AST-1, AST-2
"""

import itertools
import logging
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.signal import find_peaks

from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.optics_physics import (
    calculate_pixel_offset,
    calculate_wavelength,
)
from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import SpectroscopyPipeline
from astrometricslib.utilities.image import AstrometricsImage

logger = logging.getLogger(__name__)


class SpectroscopyCalibrationTuner:
    """Autotune physical spectroscopy calibration parameters.

    Extracts the 1D spectrum, identifies hydrogen and telluric dips,
    and fits grating distance and zero-order offsets using a
    non-linear physical model solver.

    Attributes
    ----------
    config : `AppConfiguration`
        The application configuration object used to load spectroscopy
        settings and persist tuned calibration parameters.
    """

    def __init__(self, config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the tuner service with the system configuration.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application configuration object. If `None`, loads it
            via `get_configuration()`.
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
        """Fit physical spectrometer parameters from a calibration frame.

        Extracts the 1D spectrum of the calibration star, identifies
        absorption dips, fits the physical spectrometer parameters (L
        and zero-order start), and persists the tuned values directly
        to the application configuration file.

        Parameters
        ----------
        image_path : `str`
            Absolute path to the stacked FITS image of the calibration
            star (e.g. Vega).
        camera_name : `str`, optional
            Name of the camera section in config. If `None`, falls
            back to default_primary_camera.
        star_pos : `Tuple[float, float]`, optional
            The pixel coordinates (x, y) of the zero-order star. If
            `None`, autodetects.

        Returns
        -------
        calibration_summary : `Dict[str, Any]`
            Summary of the calibration results: fitted grating
            distance, fitted zero-order offset, RMS fit error,
            detected dispersion angle, and per-feature calibration
            detail.

        Raises
        ------
        ValueError
            Raised if no stars are detected in the calibration frame,
            if fewer than 3 absorption features can be identified, or
            if the physical parameter fit does not converge to an
            acceptable accuracy.
        """
        logger.info(f"Starting spectroscopy calibration tuning for {image_path}...")
        image = AstrometricsImage(image_path)

        if camera_name is None:
            camera_name = self.config.get_value("Observatory.Camera", "default_primary_camera", "Unknown")

        # Load the custom spectroscopy configuration for the target camera
        from astrometricslib.utilities import ConfigLoader

        spec_config = ConfigLoader.load_spectroscopy_config(app_config=self.config, camera_name=camera_name)
        spec_pipeline = SpectroscopyPipeline(config=spec_config)

        # 1. Detect calibration star position if not provided
        if star_pos is None:
            logger.info(
                "Star position not provided. Autodetecting brightest source using AstrometryPipeline..."
            )
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
                AstrometryPipeline,
            )

            astrometry = AstrometryPipeline(app_config=self.config)
            context = astrometry.prepare_image(image_path, attempt_plate_solving=False)
            if not context.stellar_objects:
                raise ValueError("No stars detected in the calibration frame.")
            # The brightest source in a stacked calibration frame is our target
            star = context.stellar_objects[0].star_data
            if hasattr(star, "xcentroid"):
                star_pos = (float(star.xcentroid), float(star.ycentroid))
            elif isinstance(star, dict):
                star_pos = (
                    float(star.get("xcentroid", star.get("x_centroid"))),
                    float(star.get("ycentroid", star.get("y_centroid"))),
                )
            else:
                star_pos = (float(star["xcentroid"]), float(star["ycentroid"]))

        logger.info(f"Target star identified at position: {star_pos}")

        # 2. Extract spectrum using standard pipeline logic
        # auto_detect_angle is enabled to handle camera rotation tilts
        result = spec_pipeline._process_single_star(image, star_pos, auto_detect_angle=True)
        detected_angle = float(result["detected_angle"])

        np.array(result["wavelengths"])
        intensities = np.array(result["intensities"])

        # 3. Detect absorption valleys in the extracted spectrum
        # Apply smoothing window of size 5 to suppress pixel noise and
        # highlight broad absorption bands
        smoothed = spec_pipeline.calibrator.apply_smoothing(intensities, window=5)

        # Dynamic valley depth thresholding:
        # We need at least 3 distinct absorption valleys to fit the
        # physical model (H-delta, H-gamma, H-beta). We start with a
        # strict minimum depth of 1% (0.01) relative intensity and
        # gradually relax it by 0.2% increments if not enough
        # features are found, down to a safe noise floor limit of
        # 0.1% (0.001).
        #
        # Detection delegates to scipy.signal.find_peaks on the
        # negated spectrum: prominence with wlen=31 reproduces the
        # historical depth measure min(left_max, right_max) - value
        # computed over a +/-15 px window, and distance=6 reproduces
        # the historical 11-px local-minimum uniqueness window
        # (equivalence covered by test_calibration_tuner_dip_detection.py
        # against a synthetic Balmer-line spectrum; the best-RMS grid
        # search below is unchanged and tolerant of small
        # candidate-set differences).
        min_depth = 0.01
        dips = []
        while len(dips) < 3 and min_depth >= 0.001:
            peak_indices, _ = find_peaks(-smoothed, prominence=min_depth, wlen=31, distance=6)
            dips = [int(i) for i in peak_indices if 5 <= i < len(smoothed) - 5]
            min_depth -= 0.002

        # Continuum-normalized fallback search if raw profile dips are
        # obscured by background slope
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

        logger.info(f"Detected {len(dips)} candidate absorption dips at indices: {dips}")

        # 4. Perform grid search and non-linear fit to physical model
        # Target reference features for Vega standard calibration star:
        # H-delta = 410.17 nm | H-gamma = 434.05 nm | H-beta = 486.13 nm
        target_wls = np.array([410.17, 434.05, 486.13])
        current_start_px = float(spec_pipeline.instrument.zero_order_offset_px)

        best_rms = float("inf")
        best_grating_distance_mm = None
        best_combo = None

        # Grid search over monotonically ordered combinations of 3 dips:
        # Since we do not know which detected dips correspond to
        # H-delta, H-gamma, and H-beta, we evaluate every combination
        # of 3 dips (n_dips choose 3). For each combination, we
        # optimize the physical grating-to-sensor distance (L).
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

            # Fit physical grating distance L (in mm) using L-BFGS-B
            # bound optimizer. Initial guess is 16.5 mm (standard
            # optical profile spacer thickness). Physical bounds are
            # set to [10 mm, 30 mm] to prevent unphysical optical
            # solutions.
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

        # Calculate the zero-order starting offset mathematically from
        # the fitted grating distance. Wavelength of 380 nm maps to
        # the starting edge of the visible spectrum. We solve the
        # inverse equation directly using our pure optics_physics
        # library.
        best_x0 = calculate_pixel_offset(
            wavelength_nm=380.0,
            grating_distance_mm=best_grating_distance_mm,
            lines_per_mm=spec_pipeline.config.grating_lines_per_mm,
            pixel_size_um=spec_pipeline.config.camera.pixel_size_um,
        )

        # 5. Persist calibrated parameters to configuration
        tuned_grating_distance_mm = round(float(best_grating_distance_mm), 2)
        tuned_x0 = round(float(best_x0), 1)

        section_name = f"Observatory.Camera.{camera_name}"
        new_params = {
            section_name: {
                "grating_distance_mm": str(tuned_grating_distance_mm),
                "dispersion_start_px": str(tuned_x0),
            }
        }

        logger.info(
            f"Saving tuned parameters for {camera_name}: "
            f"grating_distance = {tuned_grating_distance_mm} mm, start = {tuned_x0} px"
        )
        self.config.update_config(new_params)

        # 6. Calculate calibrated wavelengths under new parameters for
        # final response
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
        }
