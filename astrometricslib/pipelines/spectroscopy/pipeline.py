"""The master controller for processing spectra.

This ties all the other tools together. It takes a raw picture and a list
of stars, and returns the final, cleaned-up color data (spectra) for each star.
"""

import logging
from typing import Any

import numpy as np

from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.image_processing.quality_metrics import DEFAULT_SATURATION_ADU_THRESHOLD
from astrometricslib.image_processing.saturation import compute_saturated_pixel_fraction
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.analysis_context import AnalysisContext
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_correction import (
    apply_quantum_efficiency_correction,
)
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_curves import (
    get_quantum_efficiency_curve,
)
from astrometricslib.pipelines.spectroscopy.spectroscopy_instrument import (
    SpectroscopyInstrument,
)
from astrometricslib.pipelines.spectroscopy.spectrum_calibrator import SpectrumCalibrator
from astrometricslib.pipelines.spectroscopy.spectrum_extractor import SpectrumExtractor
from astrometricslib.utilities import SpectroscopyConfig

logger = logging.getLogger(__name__)


class SpectroscopyPipeline:
    """The master controller for processing spectra.

    Attributes
    ----------
    config : `SpectroscopyConfig`
        The camera settings.
    instrument : `SpectroscopyInstrument`
        The math model of the camera.
    extractor : `SpectrumExtractor`
        The tool that reads brightness from the image.
    calibrator : `SpectrumCalibrator`
        The tool that turns pixel numbers into colors.
    """

    def __init__(self, config: SpectroscopyConfig | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Set up the master controller.

        Parameters
        ----------
        config : `SpectroscopyConfig`, optional
            The camera settings to use. If you leave this blank, it will
            load the default settings automatically.
        """
        if config is None:
            from astrometricslib.utilities import ConfigLoader

            config = ConfigLoader.load_spectroscopy_config()
        self.config = config
        self.instrument = SpectroscopyInstrument(config)
        self.extractor = SpectrumExtractor(radius=config.extraction_radius)
        self.calibrator = SpectrumCalibrator(self.instrument)
        # Keeps track of how many stars were too bright (saturated) in the
        # center. We save this list so other parts of the program can check
        # the overall image quality later.
        self.last_run_zero_order_saturation_fractions: list[float] = []

    def process(
        self, context: AnalysisContext, limit: int = 10, auto_detect_angle: bool = True
    ) -> list[StellarObject]:
        """Process all the stars we found in an image.

        Parameters
        ----------
        context : `AnalysisContext`
            The picture and the list of stars we found in it.
        limit : `int`, optional
            The maximum number of stars to process (default is 10).
        auto_detect_angle : `bool`, optional
            Whether to try and figure out the exact tilt of the camera
            automatically (default is True).

        Returns
        -------
        processed_stellar_objects : `List[StellarObject]`
            The list of stars, now updated with their color data (spectra).
        """
        results = self.process_image(
            context.image, target_stars=context.stellar_objects[:limit], auto_detect_angle=auto_detect_angle
        )
        self.last_run_zero_order_saturation_fractions = [
            res["zero_order_saturated_pixel_fraction"]
            for res in results
            if "zero_order_saturated_pixel_fraction" in res
        ]
        valid_objects = [res["star_source"] for res in results if "error" not in res]

        # If astrometry noticed the target is a large object like a nebula
        # instead of a star, turn that plain fact into a StellarObject
        # sized for our own dispersion geometry -- astrometry knows where
        # the object is and how big it looks, but not how wide a
        # measurement box our spectrograph needs for it.
        if context.extended_target is None and context.extended_source_hint is not None:
            try:
                hint = context.extended_source_hint
                context.extended_target = self.create_extended_target_object(
                    extraction_center=hint.extraction_center,
                    object_name=hint.object_name,
                    otype=hint.otype,
                    extraction_radius=hint.extraction_radius_px,
                )
            except Exception as e:
                logger.warning(f"Failed to build extended target StellarObject from hint: {e}")

        # If the user is targeting a large object like a nebula instead of a
        # star,
        # we process it automatically using a wider measuring area.
        if context.extended_target:
            try:
                ext_radius = getattr(context.extended_target, "extraction_radius", 60)
                if ext_radius is None:
                    ext_radius = 60

                # Dynamic wide pipeline for the extended target's
                # large aperture
                from astrometricslib.pipelines.spectroscopy.pipeline import (
                    SpectroscopyPipeline,
                )

                wide_pipeline = SpectroscopyPipeline(
                    config=self.config.with_overrides(extraction_radius=ext_radius)
                )
                ext_results = wide_pipeline.process_image(
                    context.image, target_stars=[context.extended_target], auto_detect_angle=auto_detect_angle
                )
                if ext_results and "error" not in ext_results[0]:
                    # Insert the successfully extracted extended
                    # target at the beginning
                    valid_objects.insert(0, context.extended_target)
                    # Keep context.stellar_objects synced
                    if context.extended_target not in context.stellar_objects:
                        context.stellar_objects.insert(0, context.extended_target)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    f"Failed to automatically extract extended target spectrum: {e}"
                )

        return valid_objects

    def process_image(
        self,
        image: AstrometricsImage,
        target_stars: list[Any] | None = None,
        limit: int = 10,
        auto_detect_angle: bool = True,
    ) -> list[dict[str, Any]]:
        """Process stars in an image at a lower level than `process`.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to process.
        target_stars : `list`, optional
            A list of stars to process. These can be full data objects or
            just simple `(x, y)` coordinates.
        limit : `int`, optional
            The maximum number of stars to process.
        auto_detect_angle : `bool`, optional
            Whether to automatically find the camera tilt.

        Returns
        -------
        results : `list` [`dict`]
            A list of dictionaries containing the raw spectrum data for each
            star.
        """
        # 1. Detect stars if no targets provided
        if target_stars is None:
            return []

        # 2. Globally auto-detect dispersion angle once if requested
        if auto_detect_angle and target_stars:
            global_angle = self.config.dispersion_angle_degrees
            offset_px = self.instrument.zero_order_offset_px
            length_px = self.instrument.expected_length_px
            orient = self.config.dispersion_orientation
            direc = self.config.dispersion_direction
            h, w = image.data.shape

            best_star_pos = None
            for star in target_stars:
                is_stellar_obj = hasattr(star, "star_data")
                if isinstance(star, tuple):
                    pos = star
                elif is_stellar_obj:
                    pos = (
                        star.star_data.get("xcentroid", star.star_data.get("x_centroid")),
                        star.star_data.get("ycentroid", star.star_data.get("y_centroid")),
                    )
                else:
                    pos = (
                        star.get("xcentroid", star.get("x_centroid")),
                        star.get("ycentroid", star.get("y_centroid")),
                    )

                if pos[0] is None or pos[1] is None:
                    continue

                x_star, y_star = float(pos[0]), float(pos[1])

                if orient == "vertical":
                    if direc == "positive":
                        y_start = y_star + offset_px
                        y_end = y_start + length_px
                    else:
                        y_start = y_star - offset_px - length_px
                        y_end = y_start + length_px

                    if y_start >= 0 and y_end <= h and x_star - 20 >= 0 and x_star + 20 <= w:
                        best_star_pos = (x_star, y_star)
                        break
                else:
                    if direc == "positive":
                        x_start = x_star + offset_px
                        x_end = x_start + length_px
                    else:
                        x_start = x_star - offset_px - length_px
                        x_end = x_start + length_px

                    if x_start >= 0 and x_end <= w and y_star - 20 >= 0 and y_star + 20 <= h:
                        best_star_pos = (x_star, y_star)
                        break

            if best_star_pos is None:
                first_star = target_stars[0]
                is_stellar_obj = hasattr(first_star, "star_data")
                if isinstance(first_star, tuple):
                    best_star_pos = first_star
                elif is_stellar_obj:
                    best_star_pos = (
                        first_star.star_data.get("xcentroid", first_star.star_data.get("x_centroid")),
                        first_star.star_data.get("ycentroid", first_star.star_data.get("y_centroid")),
                    )
                else:
                    best_star_pos = (
                        first_star.get("xcentroid", first_star.get("x_centroid")),
                        first_star.get("ycentroid", first_star.get("y_centroid")),
                    )

            if best_star_pos is not None:
                global_angle = self.detect_dispersion_angle(image, best_star_pos)
                logger.info(f"Globally resolved grating dispersion angle: {global_angle:.2f} degrees")
                self.config.dispersion_angle_degrees = global_angle
                self.instrument.config.dispersion_angle_degrees = global_angle

            auto_detect_angle = False

        results = []
        for _i, star in enumerate(target_stars[:limit]):
            # Get position
            is_stellar_obj = hasattr(star, "star_data")
            if isinstance(star, tuple):
                pos = star
            elif is_stellar_obj:  # StellarObject
                pos = (
                    star.star_data.get("xcentroid", star.star_data.get("x_centroid")),
                    star.star_data.get("ycentroid", star.star_data.get("y_centroid")),
                )
            else:  # Row/Dict from photutils
                pos = (
                    star.get("xcentroid", star.get("x_centroid")),
                    star.get("ycentroid", star.get("y_centroid")),
                )

            result = self._process_single_star(image, pos, auto_detect_angle=auto_detect_angle)
            if "error" not in result:
                # Attach the original star object if possible for reference
                result["star_source"] = star

                # If it's a StellarObject, enrich it with results
                if is_stellar_obj:
                    star.detected_angle = result["detected_angle"]
                    star.spectrum_data_processed = {
                        "wavelengths_angstrom": [float(w) * 10.0 for w in result["wavelengths"]],
                        "intensities": result["intensities"],
                    }

                    # "Quantum Efficiency" (QE) corrects for the fact that
                    # camera
                    # sensors see some colors of light better than others.
                    # If we know the camera's exact QE curve, we fix the data
                    # here.
                    # If we don't know the camera, we just skip this step.
                    quantum_efficiency_curve = get_quantum_efficiency_curve(self.config.camera.name)
                    if quantum_efficiency_curve is not None:
                        star.spectrum_data_processed["quantum_efficiency_corrected_intensities"] = (
                            apply_quantum_efficiency_correction(
                                wavelength_nm=np.array(result["wavelengths"]),
                                intensity=np.array(result["intensities"]),
                                curve=quantum_efficiency_curve,
                            ).tolist()
                        )
                    star.trail_centerline_px = result.get("trail_centerline_px")
                    star.trail_width_px = result.get("trail_width_px")
                    if isinstance(star.star_data, dict):
                        star.star_data["xcentroid"] = result["target_pos"][0]
                        star.star_data["ycentroid"] = result["target_pos"][1]

                    # 1. Compute visual overlay rectangle geometry
                    offset_px = self.instrument.zero_order_offset_px
                    len_px = self.instrument.expected_length_px
                    mid_dist = offset_px + len_px / 2.0
                    aperture_px = 2 * self.config.extraction_radius + 1

                    old_angle = self.instrument.config.dispersion_angle_degrees
                    self.instrument.config.dispersion_angle_degrees = result["detected_angle"]
                    vec = self.instrument.get_dispersion_vector()
                    self.instrument.config.dispersion_angle_degrees = old_angle

                    # Compute total rotated dispersion angle
                    star.dispersion_angle = float(np.degrees(np.arctan2(vec[1], vec[0])))

                    cx = result["target_pos"][0] + self.config.dispersion_offset_x
                    cy = result["target_pos"][1] + self.config.dispersion_offset_y

                    mid_x = cx + mid_dist * vec[0]
                    mid_y = cy + mid_dist * vec[1]

                    star.rectangle = (float(mid_x), float(mid_y), float(len_px), int(aperture_px))

                results.append(result)

        return results

    def _process_single_star(
        self, image: AstrometricsImage, pos: tuple[float, float], auto_detect_angle: bool = True
    ) -> dict[str, Any]:
        """Process just one star.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        pos : `tuple` [`float`, `float`]
            Where the star is `(x, y)`.
        auto_detect_angle : `bool`, optional
            Whether to automatically find the camera tilt.

        Returns
        -------
        result : `dict`
            A dictionary containing the color data (wavelengths and
            intensities)
            and other math details about the extraction.
        """
        # 1. Auto-detect angle if requested
        detected_angle = self.config.dispersion_angle_degrees
        if auto_detect_angle:
            detected_angle = self.detect_dispersion_angle(image, pos)
            # Temporarily update instrument config for this extraction
            self.instrument.config.dispersion_angle_degrees = detected_angle

        # 2. Check if ZWO ASI533MM Pro camera is used
        is_asi533 = self.config.camera.name == "ZWO ASI533MM Pro"

        is_traced = self.config.extraction_method == "traced"
        trail_centerline_px: list | None = None
        trail_width_px: list | None = None

        if is_asi533:
            # For ZWO ASI533MM Pro with flare masking, we use the custom method
            flare_offset_pixels = (
                self.config.dispersion_start_px if self.config.dispersion_start_px is not None else 120.0
            )
            max_offset_pixels = 750.0

            # Apply fine-tuning offsets from config
            base_pos = (pos[0] + self.config.dispersion_offset_x, pos[1] + self.config.dispersion_offset_y)

            if is_traced:
                spectrum_1d, anchor_x, anchor_y, trail_centerline_px, trail_width_px = (
                    self.extractor.extract_with_flare_mask_traced(
                        image,
                        base_pos,
                        flare_offset_pixels,
                        max_offset_pixels,
                        self.config.extraction_radius,
                        self.config.dispersion_orientation,
                        angle_degrees=detected_angle,
                        centerline_polynomial_degree=self.config.centerline_polynomial_degree,
                    )
                )
            else:
                spectrum_1d, anchor_x, anchor_y = self.extractor.extract_with_flare_mask(
                    image,
                    base_pos,
                    flare_offset_pixels,
                    max_offset_pixels,
                    self.config.extraction_radius,
                    self.config.dispersion_orientation,
                    angle_degrees=detected_angle,
                )

            # Calibrate wavelengths relative to the zero-order anchor
            # starting from flare_offset_pixels
            wavelengths, intensities = self.calibrator.calibrate(spectrum_1d, flare_offset_pixels)

            target_pos = (anchor_x, anchor_y)
        else:
            # Default line extraction workflow
            vector = self.instrument.get_dispersion_vector()
            offset_px = self.instrument.zero_order_offset_px
            length = self.instrument.expected_length_px

            # Apply fine-tuning offsets from config
            base_pos = (pos[0] + self.config.dispersion_offset_x, pos[1] + self.config.dispersion_offset_y)

            # Calculate actual extraction start (at the edge of the
            # dispersion box)
            extraction_start = (base_pos[0] + offset_px * vector[0], base_pos[1] + offset_px * vector[1])

            # Extract only the dispersion region
            if is_traced:
                spectrum_1d, trail_centerline_px, trail_width_px = self.extractor.extract_line_traced(
                    image,
                    extraction_start,
                    vector,
                    length,
                    centerline_polynomial_degree=self.config.centerline_polynomial_degree,
                )
            else:
                spectrum_1d = self.extractor.extract_line(image, extraction_start, vector, length)

            # 3. Calibrate
            # We start from offset_px relative to zero order
            wavelengths, intensities = self.calibrator.calibrate(spectrum_1d, offset_px)
            target_pos = pos

        # We need to make sure the center of the star isn't completely maxed
        # out
        # (saturated). If the center is just a flat white blob, we won't know
        # exactly where the star is, which ruins the calibration for the
        # spectrum.
        # We check just the small box around the star for this problem.
        aperture_radius = int(self.config.extraction_radius)
        data = image.data
        height, width = data.shape
        x_center, y_center = round(target_pos[0]), round(target_pos[1])
        y_start, y_end = max(0, y_center - aperture_radius), min(height, y_center + aperture_radius + 1)
        x_start, x_end = max(0, x_center - aperture_radius), min(width, x_center + aperture_radius + 1)
        zero_order_cutout = np.asarray(data[y_start:y_end, x_start:x_end], dtype=float)
        zero_order_saturated_pixel_fraction = compute_saturated_pixel_fraction(
            zero_order_cutout, DEFAULT_SATURATION_ADU_THRESHOLD
        )

        return {
            "wavelengths": wavelengths.tolist(),
            "intensities": intensities.tolist(),
            "target_pos": target_pos,
            "detected_angle": detected_angle,
            "config_summary": self.instrument.config.model_dump(),
            "zero_order_saturated_pixel_fraction": zero_order_saturated_pixel_fraction,
            "trail_centerline_px": trail_centerline_px,
            "trail_width_px": trail_width_px,
        }

    def detect_dispersion_angle(self, image: AstrometricsImage, star_pos: tuple[float, float]) -> float:
        """Figure out exactly how much the camera is tilted.

        It looks at the bright streak of the spectrum and calculates its exact
        angle.

        Returns
        -------
        angle_degrees : `float`
            The tilt of the camera, in degrees.
        """
        data = image.data
        x_star, y_star = star_pos

        offset_px = self.instrument.zero_order_offset_px
        length_px = self.instrument.expected_length_px
        orient = self.config.dispersion_orientation
        direc = self.config.dispersion_direction

        # Define ROI for detection
        if orient == "vertical":
            y_start = int(y_star + (offset_px if direc == "positive" else -offset_px - length_px))
            y_end = int(y_start + length_px)
            x_start = int(x_star - 20)
            x_end = int(x_star + 20)
            fit_axis = "y"
        else:
            x_start = int(x_star + (offset_px if direc == "positive" else -offset_px - length_px))
            x_end = int(x_start + length_px)
            y_start = int(y_star - 20)
            y_end = int(y_star + 20)
            fit_axis = "x"

        # Clamp ROI
        y_start, y_end = max(0, y_start), min(data.shape[0], y_end)
        x_start, x_end = max(0, x_start), min(data.shape[1], x_end)

        if y_end <= y_start or x_end <= x_start:
            return 0.0

        roi = data[y_start:y_end, x_start:x_end]

        # Threshold top 5% to isolate streak
        threshold = np.percentile(roi, 95)
        y_indices, x_indices = np.where(roi > threshold)

        if len(x_indices) < 10:
            return 0.0

        try:
            if fit_axis == "y":
                # Vertical: slope is dx/dy
                slope, _ = np.polyfit(y_indices, x_indices, 1)
                angle = -np.degrees(np.arctan(slope))
                print(f"Auto-detected angle for star at {star_pos}: {angle:.2f} degrees")
                return angle
            else:
                # Horizontal: slope is dy/dx
                slope, _ = np.polyfit(x_indices, y_indices, 1)
                angle = -np.degrees(np.arctan(slope))
                print(f"Auto-detected angle for star at {star_pos}: {angle:.2f} degrees")
                return angle
        except Exception:
            return 0.0

    def create_extended_target_object(
        self, extraction_center: tuple[float, float], object_name: str, otype: str, extraction_radius: int
    ) -> StellarObject:
        """Create a data object for a large target like a nebula.

        Because a nebula is large, we have to calculate a much wider
        rectangular box to capture its light, rather than the thin line
        we use for a single star.

        Parameters
        ----------
        extraction_center : `tuple` [`float`, `float`]
            Centroid coordinate `(x, y)` of the target.
        object_name : `str`
            Name of the target (e.g. ``"M 13"``).
        otype : `str`
            Astronomical classification type (e.g. ``"GlC"``).
        extraction_radius : `int`
            Target extraction aperture radius, in pixels.

        Returns
        -------
        stellar_object : `StellarObject`
            Custom stellar object mapped to the physical dispersion
            bounding box.
        """
        # 1. Resolve physical dispersion vector from the instrument geometry
        vec = self.instrument.get_dispersion_vector()
        offset_px = self.instrument.zero_order_offset_px
        len_px = self.instrument.expected_length_px

        # 2. Midpoint of the spectral streak along the dispersion line:
        # mid_dist = offset_px + len_px / 2.0
        mid_dist = offset_px + len_px / 2.0

        # 3. Apply fine-tuning offsets from configuration
        cx = extraction_center[0] + self.config.dispersion_offset_x
        cy = extraction_center[1] + self.config.dispersion_offset_y

        # 4. Map the center coordinate of the dispersion rectangle
        mid_x = cx + mid_dist * vec[0]
        mid_y = cy + mid_dist * vec[1]

        # 5. Bounding box aperture width matches full wide extraction
        # area: (2 * radius + 1)
        aperture_px = 2 * extraction_radius + 1

        # 6. Instantiate and return a clean StellarObject model
        return StellarObject(
            id=f"{object_name.replace(' ', '_')}_Cluster",
            name=f"{object_name} ({otype})",
            flux=100000.0,
            magnitude=5.8,
            spectral_type=otype,
            stellar_spectral_type="Cluster",
            star_data={
                "xcentroid": float(extraction_center[0]),
                "ycentroid": float(extraction_center[1]),
                "flux": 100000.0,
            },
            rectangle=(float(mid_x), float(mid_y), float(len_px), int(aperture_px)),
            dispersion_angle=float(np.degrees(np.arctan2(vec[1], vec[0]))),
            extraction_radius=int(extraction_radius),
        )
