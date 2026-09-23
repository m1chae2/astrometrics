"""The master controller for processing spectra.

This ties all the other tools together. It takes a raw picture and a list
of stars, and returns the final, cleaned-up color data (spectra) for each star.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.models.stellar_source import SpectralObservation, SpectroscopyResult, StellarObject
from astrometricslib.pipelines.shared.analysis_context import AnalysisContext
from astrometricslib.pipelines.shared.quality.quality_metrics import DEFAULT_SATURATION_ADU_THRESHOLD
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_correction import (
    apply_quantum_efficiency_correction,
)
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_curves import (
    get_quantum_efficiency_curve,
)
from astrometricslib.pipelines.spectroscopy.spectroscopy_instrument import (
    SpectroscopyInstrument,
)
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum
from astrometricslib.pipelines.spectroscopy.spectrum_calibrator import SpectrumCalibrator
from astrometricslib.pipelines.spectroscopy.spectrum_extractor import SpectrumExtractor
from astrometricslib.utilities import SpectroscopyConfig

logger = logging.getLogger(__name__)

# How far `detect_dispersion_angle` looks to each side of a star, across the
# dispersion axis, when it fits a line through the brightest pixels to
# measure the grating's tilt. Unvalidated beyond visual inspection of the
# streaks it was written for.
DISPERSION_ANGLE_ROI_HALF_WIDTH_PX = 20

# The least `DISPERSION_ANGLE_ROI_HALF_WIDTH_PX` is ever shrunk to, so a very
# close neighbour still leaves enough width to catch a few pixels per row.
# Below this, `detect_dispersion_angle`'s own "fewer than 10 bright pixels
# found" check already refuses cleanly instead of fitting noise.
DISPERSION_ANGLE_ROI_MINIMUM_HALF_WIDTH_PX = 3.0

# Below this DAOStarFinder "sharpness" value, a detection is not compact
# enough to trust as a real point source. Found on real data: a bright
# star's own dispersed trail can contain a locally bright pixel (a strong
# spectral feature) that DAOStarFinder reports as a separate "star" sitting
# right on the trail. On Vega's master stack, two such trail-only
# detections measured sharpness 0.268 and 0.262, while every genuine field
# star detected in the same frame measured between 0.88 and 0.99, and
# Albireo's own real companion star -- only ~17px from its primary --
# measured 0.72. 0.5 sits roughly in the middle of that gap: comfortably
# above the trail artifacts seen so far, comfortably below every real star
# seen so far.
TRAIL_CONTAMINATION_MAXIMUM_SHARPNESS = 0.5


def _safe_dispersion_angle_roi_half_width_px(
    star_pos: tuple[float, float],
    neighbor_positions: list[tuple[float, float]],
    orientation: str,
    trail_length_px: float,
    default_half_width_px: float = DISPERSION_ANGLE_ROI_HALF_WIDTH_PX,
) -> float:
    """Shrink the angle-detection ROI so it can never reach a close neighbour.

    `detect_dispersion_angle` fits one straight line through the brightest
    pixels in a strip beside a star. If another star's own dispersed trail
    falls inside that strip, its pixels get mixed into the same fit and bias
    the angle. This was found on real data: Albireo's two components are
    only about 17 px apart on the sensor, well inside the previous fixed
    20 px half-width, and the shared angle it produced swung by several
    degrees between exposures instead of converging as more frames were
    stacked -- a sign the fit was measuring two overlapping streaks, not
    noise on one. Stopping the ROI at or before the midpoint to the nearest
    neighbour means it can never include that neighbour's own trail centre.

    Only a neighbour whose own dispersed trail could actually reach into
    this star's trail is a real contamination risk. Every star shares the
    same offset and length, so two trails can only overlap along the
    dispersion axis when the stars themselves are within one trail length
    of each other there -- the shared offset cancels out of that
    comparison entirely. An earlier version of this compared only the
    perpendicular-axis distance, treating any star at a similar x (for a
    vertical grating) as a contamination risk regardless of how far away
    it sat along y, which does not require its trail to go anywhere near
    this one. That unnecessarily narrowed the ROI (degrading the angle
    measurement) for a target with an unrelated field star nearby in
    projection but nowhere near its dispersed trail: a real reprocessing
    run measured a standard star's own angle at 2.11 degrees in isolation
    but only 0.92 degrees as part of a real multi-star batch, degrading
    its later spectral classification for no real contamination reason.

    Parameters
    ----------
    star_pos : `tuple` [`float`, `float`]
        The star the angle is about to be measured for, `(x, y)`.
    neighbor_positions : `list` [`tuple` [`float`, `float`]]
        Every other star being processed alongside it.
    orientation : `str`
        `config.dispersion_orientation`: "vertical" means the fixed
        half-width applies across the x axis, "horizontal" across y.
    trail_length_px : `float`
        How long a dispersed trail is, along the dispersion axis --
        `instrument.expected_length_px`. Two same-length trails can only
        overlap when the stars themselves are closer than this along
        that axis.
    default_half_width_px : `float`, optional
        The half-width to use when there is no close, relevant neighbour.

    Returns
    -------
    half_width_px : `float`
        `default_half_width_px`, or less when a relevant neighbour is
        closer than twice that, never below
        `DISPERSION_ANGLE_ROI_MINIMUM_HALF_WIDTH_PX`.
    """
    axis_index = 0 if orientation == "vertical" else 1
    dispersion_axis_index = 1 - axis_index
    relevant_neighbor_positions = [
        neighbor
        for neighbor in neighbor_positions
        if abs(neighbor[dispersion_axis_index] - star_pos[dispersion_axis_index]) < trail_length_px
    ]
    nearest_distance = min(
        (abs(star_pos[axis_index] - neighbor[axis_index]) for neighbor in relevant_neighbor_positions),
        default=float("inf"),
    )
    return max(DISPERSION_ANGLE_ROI_MINIMUM_HALF_WIDTH_PX, min(default_half_width_px, nearest_distance / 2.0))


def _read_xy_source_position(source: Any) -> tuple[Any, Any]:
    """Read an `(x, y)` pixel position off a source, whatever form it's in.

    A source can be a photutils `SourceCatalog` row (attribute access), a
    plain dict or a `StellarObject.star_data` dict (`.get` access), or a
    bare subscriptable record -- falling back between the
    `xcentroid`/`x_centroid` and `ycentroid`/`y_centroid` spellings
    throughout. Either coordinate can come back `None` if the source
    lacked one; callers decide whether that's fatal.

    Returns
    -------
    pos : `tuple`
        The `(x, y)` position, either coordinate possibly `None`.
    """
    if hasattr(source, "xcentroid"):
        return source.xcentroid, source.ycentroid
    if hasattr(source, "get"):
        return (
            source.get("xcentroid", source.get("x_centroid")),
            source.get("ycentroid", source.get("y_centroid")),
        )
    return source["xcentroid"], source["ycentroid"]


def _star_pixel_position(star: Any) -> tuple[bool, tuple[Any, Any]]:
    """Read a star's raw `(x, y)` pixel position, whatever form it's in.

    `target_stars` mixes three forms depending on the caller: a plain
    `(x, y)` tuple, a `StellarObject` (position under `.star_data`), or a
    photutils source-detection row/dict.

    Returns
    -------
    is_stellar_obj : `bool`
        Whether `star` is a `StellarObject` (has `.star_data`) -- callers
        use this afterward to decide whether to enrich the object in place.
    pos : `tuple`
        The `(x, y)` position, either coordinate possibly `None`.
    """
    if isinstance(star, tuple):
        return False, star
    is_stellar_obj = hasattr(star, "star_data")
    source = star.star_data if is_stellar_obj else star
    return is_stellar_obj, _read_xy_source_position(source)


def _star_sharpness(star: Any) -> float | None:
    """Read a detected source's DAOStarFinder "sharpness" value, if it has one.

    A plain `(x, y)` tuple (a caller-supplied position with no detection
    metadata behind it) has no sharpness at all, so this returns `None`
    for it rather than guessing.

    Returns
    -------
    sharpness : `float` or `None`
        How compact and point-like the detection looked, or `None` if
        `star` carries no such measurement.
    """
    source = star.star_data if hasattr(star, "star_data") else star
    sharpness = source.get("sharpness") if hasattr(source, "get") else None
    return float(sharpness) if sharpness is not None else None


def _is_inside_dispersion_trail(
    candidate_pos: tuple[float, float],
    trail_owner_pos: tuple[float, float],
    dispersion_vector: np.ndarray,
    offset_px: float,
    length_px: float,
    perpendicular_tolerance_px: float,
) -> bool:
    """Check whether a position falls inside another star's own trail.

    A trail runs from `trail_owner_pos + offset_px * dispersion_vector` to
    `trail_owner_pos + (offset_px + length_px) * dispersion_vector` -- a
    thin rectangle `perpendicular_tolerance_px` wide on each side of that
    line. `candidate_pos` is projected onto the dispersion axis to get its
    position along the trail and its distance off to the side of it.

    Returns
    -------
    is_inside : `bool`
        Whether `candidate_pos` falls within that rectangle.
    """
    delta = np.array([candidate_pos[0] - trail_owner_pos[0], candidate_pos[1] - trail_owner_pos[1]])
    along_trail = float(np.dot(delta, dispersion_vector))
    off_trail = float(np.linalg.norm(delta - along_trail * dispersion_vector))
    return offset_px <= along_trail <= offset_px + length_px and off_trail <= perpendicular_tolerance_px


def _drop_spurious_trail_detections(
    stars: list[Any],
    dispersion_vector: np.ndarray,
    offset_px: float,
    length_px: float,
    perpendicular_tolerance_px: float,
    maximum_sharpness: float = TRAIL_CONTAMINATION_MAXIMUM_SHARPNESS,
) -> list[Any]:
    """Drop candidates that are just a bright point in a brighter star's trail.

    `stars` is checked brightest-first (its natural detection order), so a
    candidate is only ever compared against stars already accepted as
    real -- never against another candidate still waiting to be judged
    itself. A candidate is dropped only when both things are true: it
    isn't compact enough to trust as a real point source (see
    `TRAIL_CONTAMINATION_MAXIMUM_SHARPNESS`), and it sits inside an
    already-accepted star's own dispersed trail. Either fact alone is not
    enough -- a real, faint star can legitimately be less sharp than a
    bright one, and a real close binary companion (Albireo's, for
    instance) can legitimately sit inside its primary's trail region.

    Parameters
    ----------
    stars : `list`
        Candidate stars, brightest first.
    dispersion_vector : `numpy.ndarray`
        Unit vector `(x, y)` pointing along the grating's dispersion axis.
    offset_px : `float`
        How far a star's own trail starts from its position --
        `instrument.zero_order_offset_px`.
    length_px : `float`
        How long a trail runs from that start --
        `instrument.expected_length_px`.
    perpendicular_tolerance_px : `float`
        How far to each side of the trail's centre line still counts as
        part of it -- `config.extraction_radius`.
    maximum_sharpness : `float`, optional
        The sharpness floor below which a detection is treated as
        possibly spurious.

    Returns
    -------
    kept_stars : `list`
        `stars`, with spurious trail detections removed.
    """
    kept: list[Any] = []
    kept_positions: list[tuple[float, float]] = []
    for star in stars:
        _, pos = _star_pixel_position(star)
        sharpness = _star_sharpness(star)
        if (
            pos[0] is not None
            and pos[1] is not None
            and sharpness is not None
            and sharpness < maximum_sharpness
            and any(
                _is_inside_dispersion_trail(
                    (float(pos[0]), float(pos[1])),
                    owner_pos,
                    dispersion_vector,
                    offset_px,
                    length_px,
                    perpendicular_tolerance_px,
                )
                for owner_pos in kept_positions
            )
        ):
            logger.info(
                "Dropping candidate star at (%.1f, %.1f): sharpness %.3f is below the real-star floor "
                "(%.2f) and it sits inside an already-accepted star's own dispersed trail -- likely a "
                "bright point on that trail, not a separate star.",
                pos[0],
                pos[1],
                sharpness,
                maximum_sharpness,
            )
            continue
        kept.append(star)
        if pos[0] is not None and pos[1] is not None:
            kept_positions.append((float(pos[0]), float(pos[1])))
    return kept


def keep_usable_samples(
    wavelengths_nm: np.ndarray,
    intensities: np.ndarray,
    minimum_wavelength_nm: float,
    maximum_wavelength_nm: float,
) -> np.ndarray:
    """Find which samples of an extracted spectrum hold real measurements.

    A sample is not usable when the spectrum trail ran off the edge of the
    picture there (the extractor marks those samples as NaN, "not a
    number"), or when its wavelength is outside the range the camera can
    actually see. Treating such samples as zero brightness would make the
    star look like it goes dark, which is not what was measured.

    Parameters
    ----------
    wavelengths_nm : `np.ndarray`
        The wavelength of each sample, in nanometers.
    intensities : `np.ndarray`
        The brightness of each sample, NaN where nothing was measured.
    minimum_wavelength_nm : `float`
        The shortest wavelength the camera can see, in nanometers.
    maximum_wavelength_nm : `float`
        The longest wavelength the camera can see, in nanometers.

    Returns
    -------
    usable : `np.ndarray`
        A boolean array, `True` for each sample worth keeping.
    """
    return (
        np.isfinite(wavelengths_nm)
        & np.isfinite(intensities)
        & (wavelengths_nm >= minimum_wavelength_nm)
        & (wavelengths_nm <= maximum_wavelength_nm)
    )


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
        # The extractor also does the sky background subtraction stage:
        # each brightness reading has the night-sky glow (measured in strips
        # beside the streak) taken out of it, so everything after this point
        # in the pipeline sees the star's light only.
        self.extractor = SpectrumExtractor(
            radius=config.extraction_radius, subtract_sky_background=config.subtract_sky_background
        )
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
        # Filter before slicing to `limit`: a spurious trail detection
        # sitting near the top of the brightness-sorted list would
        # otherwise take a slot a real, fainter star should have had.
        candidate_stars = _drop_spurious_trail_detections(
            context.stellar_objects,
            self.instrument.get_dispersion_vector(),
            self.instrument.zero_order_offset_px,
            self.instrument.expected_length_px,
            self.config.extraction_radius,
        )
        results = self.process_image(
            context.image, target_stars=candidate_stars[:limit], auto_detect_angle=auto_detect_angle
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
                ext_radius = getattr(context.extended_target.spectroscopy, "extraction_radius", 60)
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
            self._resolve_global_dispersion_angle(image, target_stars)
            auto_detect_angle = False

        return self._process_target_stars(image, target_stars, limit, auto_detect_angle)

    def _resolve_global_dispersion_angle(self, image: AstrometricsImage, target_stars: list[Any]) -> None:
        """Auto-detect the grating dispersion angle once for a whole batch.

        Picks the first target star whose full dispersion box fits
        inside the image (falling back to the first star if none do),
        measures the angle from it, and updates `self.config` /
        `self.instrument.config` in place so every star in this batch
        uses the same angle.
        """
        global_angle = self.config.dispersion_angle_degrees
        offset_px = self.instrument.zero_order_offset_px
        length_px = self.instrument.expected_length_px
        orient = self.config.dispersion_orientation
        direc = self.config.dispersion_direction
        h, w = image.data.shape

        all_positions: list[tuple[float, float]] = []
        for star in target_stars:
            _, pos = _star_pixel_position(star)
            if pos[0] is not None and pos[1] is not None:
                all_positions.append((float(pos[0]), float(pos[1])))

        best_star_pos = None
        for x_star, y_star in all_positions:
            if orient == "vertical":
                if direc == "positive":
                    y_start = y_star + offset_px
                    y_end = y_start + length_px
                else:
                    y_start = y_star - offset_px - length_px
                    y_end = y_start + length_px

                if (
                    y_start >= 0
                    and y_end <= h
                    and x_star - DISPERSION_ANGLE_ROI_HALF_WIDTH_PX >= 0
                    and x_star + DISPERSION_ANGLE_ROI_HALF_WIDTH_PX <= w
                ):
                    best_star_pos = (x_star, y_star)
                    break
            else:
                if direc == "positive":
                    x_start = x_star + offset_px
                    x_end = x_start + length_px
                else:
                    x_start = x_star - offset_px - length_px
                    x_end = x_start + length_px

                if (
                    x_start >= 0
                    and x_end <= w
                    and y_star - DISPERSION_ANGLE_ROI_HALF_WIDTH_PX >= 0
                    and y_star + DISPERSION_ANGLE_ROI_HALF_WIDTH_PX <= h
                ):
                    best_star_pos = (x_star, y_star)
                    break

        if best_star_pos is None:
            _, fallback_pos = _star_pixel_position(target_stars[0])
            if fallback_pos[0] is not None and fallback_pos[1] is not None:
                best_star_pos = (float(fallback_pos[0]), float(fallback_pos[1]))

        if best_star_pos is not None:
            neighbor_positions = [pos for pos in all_positions if pos != best_star_pos]
            roi_half_width_px = _safe_dispersion_angle_roi_half_width_px(
                best_star_pos, neighbor_positions, orient, length_px
            )
            global_angle = self.detect_dispersion_angle(image, best_star_pos, roi_half_width_px)
            logger.info(
                f"Globally resolved grating dispersion angle: {global_angle:.2f} degrees "
                f"(angle-detection half-width {roi_half_width_px:.1f}px)"
            )
            self.config.dispersion_angle_degrees = global_angle
            self.instrument.config.dispersion_angle_degrees = global_angle

    def _process_target_stars(
        self,
        image: AstrometricsImage,
        target_stars: list[Any],
        limit: int,
        auto_detect_angle: bool,
    ) -> list[dict[str, Any]]:
        """Run single-star extraction over a batch of target stars.

        Returns
        -------
        results : `list` [`dict`]
            One result dict per successfully processed star, each also
            carrying its original `star_source` object.
        """
        results = []
        for star in target_stars[:limit]:
            is_stellar_obj, pos = _star_pixel_position(star)

            result = self._process_single_star(image, pos, auto_detect_angle=auto_detect_angle)
            if "error" not in result:
                # Attach the original star object if possible for reference
                result["star_source"] = star

                # If it's a StellarObject, enrich it with results
                if is_stellar_obj:
                    self._apply_result_to_stellar_object(star, result, image)

                results.append(result)

        return results

    def _apply_result_to_stellar_object(
        self, star: StellarObject, result: dict[str, Any], image: AstrometricsImage
    ) -> None:
        """Copy a single star's extraction result onto its `StellarObject`.

        "Quantum Efficiency" (QE) corrects for the fact that camera
        sensors see some colors of light better than others. If we
        know the camera's exact QE curve, we fix the data here. If we
        don't know the camera, we just skip this step.
        """
        wavelengths_angstrom = [float(w) * 10.0 for w in result["wavelengths"]]
        intensities = result["intensities"]

        quantum_efficiency_corrected_intensities = None
        quantum_efficiency_curve = get_quantum_efficiency_curve(self.config.camera.name)
        if quantum_efficiency_curve is not None:
            quantum_efficiency_corrected_intensities = apply_quantum_efficiency_correction(
                wavelength_nm=np.array(result["wavelengths"]),
                intensity=np.array(result["intensities"]),
                curve=quantum_efficiency_curve,
            ).tolist()

        # Classify and test features on the QE-corrected spectrum when
        # available -- it better reflects the star's true color than raw
        # sensor counts.
        analysis = analyze_spectrum(
            np.array(wavelengths_angstrom),
            np.array(
                quantum_efficiency_corrected_intensities
                if quantum_efficiency_corrected_intensities is not None
                else intensities
            ),
            self.config.camera.name,
            is_quantum_efficiency_corrected=quantum_efficiency_corrected_intensities is not None,
            catalog_spectral_type=star.spectral_type,
            trail_width_px=result.get("trail_width_px"),
        )
        classification = analysis.classification
        probable_spectral_features = analysis.features

        # Compute the visual overlay rectangle and total rotated
        # dispersion angle
        rectangle, dispersion_angle = self._dispersion_overlay_geometry(
            result["target_pos"],
            self.config.extraction_radius,
            dispersion_angle_degrees=result["detected_angle"],
        )

        star.spectroscopy = SpectroscopyResult(
            wavelengths_angstrom=wavelengths_angstrom,
            intensities=intensities,
            quantum_efficiency_corrected_intensities=quantum_efficiency_corrected_intensities,
            self_determined_spectral_type=classification["spectral_type"],
            self_determined_spectral_type_confidence=classification["confidence"],
            self_determined_spectral_type_rms=classification["rms"],
            self_determined_spectral_type_note=classification.get("reason") or "",
            self_determined_spectral_type_candidates=classification["ranked_types"],
            probable_spectral_features=probable_spectral_features,
            star_position_px=[float(result["target_pos"][0]), float(result["target_pos"][1])],
            requested_wavelength_range_angstrom=(
                [float(value) * 10.0 for value in result["requested_wavelength_range_nm"]]
                if result.get("requested_wavelength_range_nm")
                else None
            ),
            valid_fraction=result.get("valid_fraction"),
            rectangle=rectangle,
            detected_angle=result["detected_angle"],
            dispersion_angle=dispersion_angle,
            trail_centerline_px=result.get("trail_centerline_px"),
            trail_width_px=result.get("trail_width_px"),
            resolution_element_angstrom=(
                analysis.resolution_element_angstrom if analysis.is_resolution_measured else None
            ),
        )

        # Records this extraction as one more epoch in the star's own
        # spectral history, so a caller can see how its spectrum has
        # changed across observing sessions -- not just the latest one
        # above. merge_spectroscopy_stellar_object folds this single
        # new entry into the catalog's running history for this star.
        observation_timestamp = image.timestamp
        star.spectra_history = [
            SpectralObservation(
                timestamp=(
                    datetime.fromtimestamp(observation_timestamp, tz=UTC)
                    if observation_timestamp is not None
                    else datetime.now(UTC)
                ),
                wavelengths=wavelengths_angstrom,
                intensities=intensities,
            )
        ]

        if isinstance(star.star_data, dict):
            star.star_data["xcentroid"] = result["target_pos"][0]
            star.star_data["ycentroid"] = result["target_pos"][1]

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

        # 2. Check whether this setup wants flare-masked extraction
        use_flare_mask = self.config.use_flare_mask_extraction
        is_traced = self.config.extraction_method == "traced"

        if use_flare_mask:
            wavelengths, intensities, target_pos, trail_centerline_px, trail_width_px = (
                self._extract_via_flare_mask(image, pos, detected_angle, is_traced)
            )
        else:
            wavelengths, intensities, target_pos, trail_centerline_px, trail_width_px = (
                self._extract_via_dispersion_line(image, pos, is_traced)
            )

        zero_order_saturated_pixel_fraction = self._measure_zero_order_saturation(image, target_pos)

        # Keep only the samples that were really measured: on the image and
        # inside the camera's sensitive range. The trail arrays line up with
        # the spectrum one-to-one, so they are trimmed the same way.
        wavelengths = np.asarray(wavelengths, dtype=float)
        intensities = np.asarray(intensities, dtype=float)
        if wavelengths.size == 0:
            return {
                "error": "The instrument model asked for a spectrum of zero length; check the camera config."
            }
        requested_wavelength_range_nm = [float(np.nanmin(wavelengths)), float(np.nanmax(wavelengths))]
        usable = keep_usable_samples(
            wavelengths,
            intensities,
            self.config.camera.sensor_min_wavelength,
            self.config.camera.sensor_max_wavelength,
        )
        if not usable.any():
            return {"error": "No part of the spectrum trail is on the image and inside the camera's range."}
        valid_fraction = float(usable.mean())
        wavelengths = wavelengths[usable]
        intensities = intensities[usable]
        if trail_centerline_px is not None and len(trail_centerline_px) == len(usable):
            trail_centerline_px = np.asarray(trail_centerline_px, dtype=float)[usable].tolist()
        if trail_width_px is not None and len(trail_width_px) == len(usable):
            trail_width_px = np.asarray(trail_width_px, dtype=float)[usable].tolist()

        return {
            "wavelengths": wavelengths.tolist(),
            "intensities": intensities.tolist(),
            "target_pos": target_pos,
            "detected_angle": detected_angle,
            "config_summary": self.instrument.config.model_dump(),
            "zero_order_saturated_pixel_fraction": zero_order_saturated_pixel_fraction,
            "trail_centerline_px": trail_centerline_px,
            "trail_width_px": trail_width_px,
            "valid_fraction": valid_fraction,
            "requested_wavelength_range_nm": requested_wavelength_range_nm,
        }

    def _extract_via_flare_mask(
        self, image: AstrometricsImage, pos: tuple[float, float], detected_angle: float, is_traced: bool
    ) -> tuple[np.ndarray, np.ndarray, tuple[float, float], list | None, list | None]:
        """Extract a spectrum using the flare-masking method.

        Returns
        -------
        wavelengths, intensities : `numpy.ndarray`
            The calibrated spectrum.
        target_pos : `tuple` [`float`, `float`]
            The zero-order anchor position the spectrum was calibrated
            from.
        trail_centerline_px, trail_width_px : `list` or `None`
            The traced extraction's per-column centerline and width, or
            both `None` when using the untraced extraction method.
        """
        flare_offset_pixels = (
            self.config.dispersion_start_px if self.config.dispersion_start_px is not None else 120.0
        )
        # The instrument's expected_length_px already reflects any
        # configured max_extraction_length_px cap, so re-deriving the
        # absolute offset from it here keeps this in sync with that cap
        # instead of hardcoding a second copy of it.
        max_offset_pixels = flare_offset_pixels + self.instrument.expected_length_px

        # Apply fine-tuning offsets from config
        base_pos = (pos[0] + self.config.dispersion_offset_x, pos[1] + self.config.dispersion_offset_y)

        # extract_with_flare_mask[_traced]'s per-column tilt tracking
        # (slope = -tan(angle_degrees)) is the negation of what
        # get_dispersion_vector() -- and therefore detect_dispersion_angle
        # -- uses for horizontal dispersion (they already agree for
        # vertical), so horizontal needs a sign flip here to actually
        # follow the star's real measured tilt instead of walking away
        # from it.
        flare_mask_angle = (
            -detected_angle if self.config.dispersion_orientation == "horizontal" else detected_angle
        )

        trail_centerline_px: list | None = None
        trail_width_px: list | None = None
        if is_traced:
            spectrum_1d, anchor_x, anchor_y, trail_centerline_px, trail_width_px = (
                self.extractor.extract_with_flare_mask_traced(
                    image,
                    base_pos,
                    flare_offset_pixels,
                    max_offset_pixels,
                    self.config.extraction_radius,
                    self.config.dispersion_orientation,
                    angle_degrees=flare_mask_angle,
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
                angle_degrees=flare_mask_angle,
            )

        # Calibrate wavelengths relative to the zero-order anchor
        # starting from flare_offset_pixels
        wavelengths, intensities = self.calibrator.calibrate(spectrum_1d, flare_offset_pixels)

        return wavelengths, intensities, (anchor_x, anchor_y), trail_centerline_px, trail_width_px

    def _extract_via_dispersion_line(
        self, image: AstrometricsImage, pos: tuple[float, float], is_traced: bool
    ) -> tuple[np.ndarray, np.ndarray, tuple[float, float], list | None, list | None]:
        """Extract a spectrum along the instrument's default dispersion line.

        Returns
        -------
        wavelengths, intensities : `numpy.ndarray`
            The calibrated spectrum.
        target_pos : `tuple` [`float`, `float`]
            The star's own position (unchanged from `pos`).
        trail_centerline_px, trail_width_px : `list` or `None`
            The traced extraction's per-column centerline and width, or
            both `None` when using the untraced extraction method.
        """
        # Default line extraction workflow
        vector = self.instrument.get_dispersion_vector()
        offset_px = self.instrument.zero_order_offset_px
        length = self.instrument.expected_length_px

        # Apply fine-tuning offsets from config
        base_pos = (pos[0] + self.config.dispersion_offset_x, pos[1] + self.config.dispersion_offset_y)

        # Calculate actual extraction start (at the edge of the
        # dispersion box)
        extraction_start = (base_pos[0] + offset_px * vector[0], base_pos[1] + offset_px * vector[1])

        trail_centerline_px: list | None = None
        trail_width_px: list | None = None
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

        # We start from offset_px relative to zero order
        wavelengths, intensities = self.calibrator.calibrate(spectrum_1d, offset_px)

        return wavelengths, intensities, pos, trail_centerline_px, trail_width_px

    def _measure_zero_order_saturation(
        self, image: AstrometricsImage, target_pos: tuple[float, float]
    ) -> float:
        """Measure what fraction of the star's center is saturated (maxed out).

        We need to make sure the center of the star isn't completely
        maxed out (saturated). If the center is just a flat white
        blob, we won't know exactly where the star is, which ruins
        the calibration for the spectrum. We check just the small box
        around the star for this problem.

        Returns
        -------
        zero_order_saturated_pixel_fraction : `float`
            The fraction of pixels in that box that are saturated.
        """
        aperture_radius = int(self.config.extraction_radius)
        data = image.data
        height, width = data.shape
        x_center, y_center = round(target_pos[0]), round(target_pos[1])
        y_start, y_end = max(0, y_center - aperture_radius), min(height, y_center + aperture_radius + 1)
        x_start, x_end = max(0, x_center - aperture_radius), min(width, x_center + aperture_radius + 1)
        zero_order_cutout = np.asarray(data[y_start:y_end, x_start:x_end], dtype=float)
        return compute_saturated_pixel_fraction(zero_order_cutout, DEFAULT_SATURATION_ADU_THRESHOLD)

    def _dispersion_overlay_geometry(
        self,
        center: tuple[float, float],
        extraction_radius: float,
        dispersion_angle_degrees: float | None = None,
    ) -> tuple[tuple[float, float, float, int], float]:
        """Map a center point into its spectrum trace's overlay rectangle.

        Both a processed star and a synthesized extended-target object
        need the same overlay: a rectangle spanning the dispersion trace,
        and the trace's rotated angle in image space.

        Parameters
        ----------
        center : `tuple` [`float`, `float`]
            The `(x, y)` pixel the trace is anchored to.
        extraction_radius : `float`
            The extraction aperture radius, in pixels.
        dispersion_angle_degrees : `float`, optional
            Overrides the instrument's current dispersion angle for this
            one calculation (restored afterward). Used when a star's own
            per-star auto-detected angle differs from whatever the
            instrument is currently configured with; omit it to use the
            instrument's angle as-is.

        Returns
        -------
        rectangle : `tuple` [`float`, `float`, `float`, `int`]
            `(mid_x, mid_y, length_px, aperture_px)` for the overlay box.
        dispersion_angle_deg : `float`
            The dispersion trace's angle in image space, in degrees.
        """
        offset_px = self.instrument.zero_order_offset_px
        len_px = self.instrument.expected_length_px
        mid_dist = offset_px + len_px / 2.0
        aperture_px = 2 * extraction_radius + 1

        if dispersion_angle_degrees is None:
            vec = self.instrument.get_dispersion_vector()
        else:
            old_angle = self.instrument.config.dispersion_angle_degrees
            self.instrument.config.dispersion_angle_degrees = dispersion_angle_degrees
            vec = self.instrument.get_dispersion_vector()
            self.instrument.config.dispersion_angle_degrees = old_angle

        cx = center[0] + self.config.dispersion_offset_x
        cy = center[1] + self.config.dispersion_offset_y
        mid_x = cx + mid_dist * vec[0]
        mid_y = cy + mid_dist * vec[1]

        rectangle = (float(mid_x), float(mid_y), float(len_px), int(aperture_px))
        dispersion_angle_deg = float(np.degrees(np.arctan2(vec[1], vec[0])))
        return rectangle, dispersion_angle_deg

    def detect_dispersion_angle(
        self,
        image: AstrometricsImage,
        star_pos: tuple[float, float],
        roi_half_width_px: float = DISPERSION_ANGLE_ROI_HALF_WIDTH_PX,
    ) -> float:
        """Figure out exactly how much the camera is tilted.

        It looks at the bright streak of the spectrum and calculates its exact
        angle.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to measure the streak in.
        star_pos : `tuple` [`float`, `float`]
            Where the star is, `(x, y)`.
        roi_half_width_px : `float`, optional
            How far to each side of `star_pos`, across the dispersion axis,
            the measuring strip reaches. Shrink this (see
            `_safe_dispersion_angle_roi_half_width_px`) when another star's
            own trail could otherwise fall inside the same strip and bias
            the fit.

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
            x_start = int(x_star - roi_half_width_px)
            x_end = int(x_star + roi_half_width_px)
            fit_axis = "y"
        else:
            x_start = int(x_star + (offset_px if direc == "positive" else -offset_px - length_px))
            x_end = int(x_start + length_px)
            y_start = int(y_star - roi_half_width_px)
            y_end = int(y_star + roi_half_width_px)
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
                # Vertical: slope is dx/dy. get_dispersion_vector()'s
                # 90-degree base angle for "vertical" means a positive
                # measured dx/dy slope must map to a *negative* angle
                # to reproduce that same slope -- unlike the horizontal
                # case below, this negation is required, not a bug.
                slope, _ = np.polyfit(y_indices, x_indices, 1)
                angle = -np.degrees(np.arctan(slope))
            else:
                # Horizontal: slope is dy/dx, and get_dispersion_vector()'s
                # 0-degree base angle for "horizontal" means the angle
                # must equal +arctan(slope) (no negation) to reproduce
                # this same measured slope when fed back through it.
                slope, _ = np.polyfit(x_indices, y_indices, 1)
                angle = np.degrees(np.arctan(slope))
            logger.debug(f"Auto-detected angle for star at {star_pos}: {angle:.2f} degrees")
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
        rectangle, dispersion_angle = self._dispersion_overlay_geometry(extraction_center, extraction_radius)

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
            spectroscopy=SpectroscopyResult(
                star_position_px=[float(extraction_center[0]), float(extraction_center[1])],
                rectangle=rectangle,
                dispersion_angle=dispersion_angle,
                extraction_radius=int(extraction_radius),
            ),
        )
