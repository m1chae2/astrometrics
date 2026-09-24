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
from astrometricslib.pipelines.spectroscopy.second_order_risk import compute_second_order_blue_to_red_ratio
from astrometricslib.pipelines.spectroscopy.spectroscopy_instrument import (
    SpectroscopyInstrument,
)
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import (
    EXTENDED_TARGET_SPECTRAL_TYPE,
    analyze_spectrum,
)
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


# The steepest tilt, in degrees, the angle fit will accept. The strip that
# follows a streak can only reach this far from the star's own line, and a
# fit that wants more is following something other than the trail (a
# neighbour, or noise). Trails measured so far lean between 2 and 3 degrees
# (Vega 2.1, Albireo 2.7, M 57 about 2.5), so 10 leaves a wide margin.
# Not validated beyond those three stacks.
DISPERSION_ANGLE_MAXIMUM_TILT_DEGREES = 10.0

# How many times the angle fit re-centres its strip on the previous fit
# before stopping. The fit moves less each time; on the Albireo, Vega and
# M 57 stacks it settled within 3 rounds, so 8 is a safe upper limit.
DISPERSION_ANGLE_MAXIMUM_REFIT_COUNT = 8

# How many pixels along the trail each band spans when the streak's sideways
# position is measured. 40 rows average out pixel noise (a bright streak's
# centre moves by well under a pixel per band) while keeping about 15 bands
# along a 630 px trail so the tilt is well constrained. Chosen by judgement,
# then checked against direct centroids on the Albireo, Vega and M 57 stacks.
DISPERSION_ANGLE_BAND_LENGTH_PX = 40

# The brightness, in noise sigmas of a band's averaged profile, its peak must
# reach for the band to count. Pure noise peaks at about 2 sigma across the
# few dozen pixels of a profile, so 3 keeps noise bands out. A judgement, not
# tuned on data.
DISPERSION_ANGLE_MINIMUM_BAND_SIGMA = 3.0

# How many candidate tilts the search tries before it starts following the
# trail. 41 across plus or minus 10 degrees is a step of 0.5 degrees, well
# inside what one strip half-width of 3 px can still follow over the first
# bands. Chosen by judgement.
DISPERSION_ANGLE_SEED_SLOPE_COUNT = 41

# The fewest bands that must show the streak for an angle to be reported.
# Fewer than this and one odd band could set the whole slope.
DISPERSION_ANGLE_MINIMUM_BAND_COUNT = 4

# The change in slope (across-pixels per along-pixel) below which the fit
# counts as settled. 0.001 is 0.06 degrees, far below the 0.1 degree scatter
# seen between stars in the same stack. Chosen by judgement.
DISPERSION_ANGLE_REFIT_TOLERANCE = 0.001

# The least trail contrast, in noise sigmas, a star's angle measurement needs
# before the batch-wide dispersion angle will trust it. Contrast is the
# typical height of the streak above the background across the bands that
# show it, in units of the background noise of a band's averaged profile
# (see `DISPERSION_ANGLE_MINIMUM_BAND_SIGMA`, which a band must reach just
# to count, and which pure noise almost never does over four bands). On the
# Albireo stack the star the old first-in-the-list rule picked had no visible
# trail and scored 2.8 by the previous, differently defined contrast, while
# the star with a real trail scored 150 on that scale. On the current scale
# the trails on the Albireo, Vega and M 57 stacks score in the hundreds to
# thousands, and pure noise returns no measurement at all. 5.0 is a wide
# margin either way. Validated on those three stacks only.
DISPERSION_ANGLE_MINIMUM_TRAIL_CONTRAST_SIGMA = 5.0


# The smallest extraction radius a star's aperture is ever shrunk to when a
# neighbour's trail runs alongside. Below this the aperture (2 * radius + 1
# wide) is too narrow to hold a trail whose own width is a few pixels.
# Not tuned beyond that geometric reasoning.
EXTRACTION_RADIUS_MINIMUM_PX = 3


def _capped_extraction_radius_px(
    star_pos: tuple[float, float],
    neighbor_positions: list[tuple[float, float]],
    dispersion_vector: np.ndarray,
    trail_length_px: float,
    default_radius_px: int,
) -> int:
    """Shrink a star's extraction radius so its box cannot touch a neighbour's.

    Every star's box is `2 * radius + 1` pixels wide, laid along the same
    dispersion direction. Two stars whose trails run side by side closer
    than that width share pixels, so the brighter star's light leaks into
    the fainter star's spectrum (on Albireo, two stars 16.7 px apart with
    21 px boxes overlapped by about 4 px). Trails are parallel, so the
    distance between them is measured across the dispersion direction, and
    only a neighbour whose trail overlaps this one along the dispersion
    direction (closer than one trail length) can matter.

    Parameters
    ----------
    star_pos : `tuple` [`float`, `float`]
        The star being extracted, `(x, y)`.
    neighbor_positions : `list` [`tuple` [`float`, `float`]]
        Every other star extracted alongside it.
    dispersion_vector : `numpy.ndarray`
        Unit vector `(x, y)` along the dispersion direction.
    trail_length_px : `float`
        How long a dispersed trail is, along the dispersion direction.
    default_radius_px : `int`
        The configured radius, used when no neighbour is close enough.

    Returns
    -------
    radius_px : `int`
        `default_radius_px`, or less when a neighbour's trail is closer
        than the full box width, never below `EXTRACTION_RADIUS_MINIMUM_PX`.
        A box `2 * radius + 1` wide just fits when `2 * radius + 1` is at
        most the separation.
    """
    along = np.asarray(dispersion_vector, dtype=float)
    across = np.array([-along[1], along[0]])
    nearest_separation = float("inf")
    for neighbor in neighbor_positions:
        offset = np.array([neighbor[0] - star_pos[0], neighbor[1] - star_pos[1]], dtype=float)
        if abs(float(offset @ along)) >= trail_length_px:
            continue
        nearest_separation = min(nearest_separation, abs(float(offset @ across)))
    if not np.isfinite(nearest_separation):
        return default_radius_px
    fitting_radius = int(np.floor((nearest_separation - 1.0) / 2.0))
    return max(EXTRACTION_RADIUS_MINIMUM_PX, min(default_radius_px, fitting_radius))


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
            radius=config.extraction_radius,
            subtract_sky_background=config.subtract_sky_background,
            reject_narrow_contaminants=config.reject_narrow_contaminants,
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
                    config=self.config.with_overrides(
                        extraction_radius=ext_radius, reject_narrow_contaminants=True
                    )
                )
                # The tilt is never re-detected here: a nebula has no single
                # streak to measure (its "trail" is a chain of ring images), so
                # a fit on it returns a meaningless angle (on M 57, -3 degrees
                # where the real tilt is about +2). `wide_pipeline` already
                # holds the angle measured from the stars above, or the
                # configured one when no star gave a clear streak.
                ext_results = wide_pipeline.process_image(
                    context.image, target_stars=[context.extended_target], auto_detect_angle=False
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

        Measures the streak angle from every target star whose full
        dispersion box fits inside the image (or from the first star if
        none do) and keeps the measurement with the clearest streak, then
        updates `self.config` / `self.instrument.config` in place so every
        star in this batch uses the same angle. Choosing by streak
        contrast, not list order, matters: the first star in a list can
        have no visible trail at all (a real Albireo run picked such a
        star, fitted noise, and drew every box tilted the wrong way).
        If no candidate shows a streak clearer than
        `DISPERSION_ANGLE_MINIMUM_TRAIL_CONTRAST_SIGMA`, the configured
        angle is left unchanged instead of adopting a noise fit.
        """
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

        candidate_positions: list[tuple[float, float]] = []
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
                    candidate_positions.append((x_star, y_star))
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
                    candidate_positions.append((x_star, y_star))

        if not candidate_positions:
            _, fallback_pos = _star_pixel_position(target_stars[0])
            if fallback_pos[0] is not None and fallback_pos[1] is not None:
                candidate_positions.append((float(fallback_pos[0]), float(fallback_pos[1])))

        best_measurement: tuple[float, float, tuple[float, float], float] | None = None
        for candidate_pos in candidate_positions:
            neighbor_positions = [pos for pos in all_positions if pos != candidate_pos]
            roi_half_width_px = _safe_dispersion_angle_roi_half_width_px(
                candidate_pos, neighbor_positions, orient, length_px
            )
            angle, contrast_sigma = self.measure_dispersion_trail(image, candidate_pos, roi_half_width_px)
            if best_measurement is None or contrast_sigma > best_measurement[1]:
                best_measurement = (angle, contrast_sigma, candidate_pos, roi_half_width_px)

        if best_measurement is None:
            return

        global_angle, contrast_sigma, best_star_pos, roi_half_width_px = best_measurement
        if contrast_sigma < DISPERSION_ANGLE_MINIMUM_TRAIL_CONTRAST_SIGMA:
            logger.warning(
                f"No star showed a clear dispersion streak (best contrast {contrast_sigma:.1f} sigma "
                f"< {DISPERSION_ANGLE_MINIMUM_TRAIL_CONTRAST_SIGMA:.1f}); keeping the configured angle "
                f"{self.config.dispersion_angle_degrees:.2f} degrees"
            )
            return
        logger.info(
            f"Globally resolved grating dispersion angle: {global_angle:.2f} degrees "
            f"(from the star at {best_star_pos}, contrast {contrast_sigma:.1f} sigma, "
            f"angle-detection half-width {roi_half_width_px:.1f}px)"
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
        batch = target_stars[:limit]
        batch_positions = [_star_pixel_position(star)[1] for star in batch]
        dispersion_vector = self.instrument.get_dispersion_vector()
        for star_index, star in enumerate(batch):
            is_stellar_obj, pos = _star_pixel_position(star)

            neighbor_positions = [
                (float(other[0]), float(other[1]))
                for other_index, other in enumerate(batch_positions)
                if other_index != star_index and other[0] is not None and other[1] is not None
            ]
            extraction_radius = _capped_extraction_radius_px(
                (float(pos[0]), float(pos[1])),
                neighbor_positions,
                dispersion_vector,
                self.instrument.expected_length_px,
                int(self.config.extraction_radius),
            )
            result = self._process_single_star(
                image, pos, auto_detect_angle=auto_detect_angle, extraction_radius=extraction_radius
            )
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

        # Compute the visual overlay rectangle and total rotated
        # dispersion angle
        rectangle, dispersion_angle = self._dispersion_overlay_geometry(
            result["target_pos"],
            result.get("extraction_radius", self.config.extraction_radius),
            dispersion_angle_degrees=result["detected_angle"],
        )

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
            is_extended_target=star.stellar_spectral_type == EXTENDED_TARGET_SPECTRAL_TYPE,
            catalog_b_minus_v=star.b_minus_v,
            trail_width_px=result.get("trail_width_px"),
            extraction_box_width_px=float(rectangle[3]) if rectangle is not None else None,
        )
        classification = analysis.classification
        probable_spectral_features = analysis.features

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
            emission_lines=analysis.emission_lines,
            is_emission_line_source=analysis.is_emission_line_source,
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
            second_order_blue_to_red_ratio=compute_second_order_blue_to_red_ratio(
                np.array(wavelengths_angstrom), np.array(intensities)
            ).tolist(),
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
        self,
        image: AstrometricsImage,
        pos: tuple[float, float],
        auto_detect_angle: bool = True,
        extraction_radius: int | None = None,
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
        extraction_radius : `int`, optional
            This star's own aperture radius, in pixels, when it must be
            smaller than the configured one to keep clear of a neighbour's
            trail. Defaults to `config.extraction_radius`.

        Returns
        -------
        result : `dict`
            A dictionary containing the color data (wavelengths and
            intensities)
            and other math details about the extraction.
        """
        radius = (
            int(extraction_radius) if extraction_radius is not None else int(self.config.extraction_radius)
        )
        extractor = self.extractor
        if radius != self.extractor.radius:
            extractor = SpectrumExtractor(
                radius=radius,
                subtract_sky_background=self.config.subtract_sky_background,
                reject_narrow_contaminants=self.config.reject_narrow_contaminants,
            )

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
                self._extract_via_flare_mask(image, pos, detected_angle, is_traced, extractor, radius)
            )
        else:
            wavelengths, intensities, target_pos, trail_centerline_px, trail_width_px = (
                self._extract_via_dispersion_line(image, pos, is_traced, extractor)
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
            "extraction_radius": radius,
            "config_summary": self.instrument.config.model_dump(),
            "zero_order_saturated_pixel_fraction": zero_order_saturated_pixel_fraction,
            "trail_centerline_px": trail_centerline_px,
            "trail_width_px": trail_width_px,
            "valid_fraction": valid_fraction,
            "requested_wavelength_range_nm": requested_wavelength_range_nm,
        }

    def _extract_via_flare_mask(
        self,
        image: AstrometricsImage,
        pos: tuple[float, float],
        detected_angle: float,
        is_traced: bool,
        extractor: SpectrumExtractor,
        extraction_radius: int,
    ) -> tuple[np.ndarray, np.ndarray, tuple[float, float], list | None, list | None]:
        """Extract a spectrum using the flare-masking method.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        pos : `tuple` [`float`, `float`]
            Where the star is `(x, y)`.
        detected_angle : `float`
            The dispersion tilt to follow, in degrees.
        is_traced : `bool`
            Whether to use the traced extraction method.
        extractor : `SpectrumExtractor`
            The extractor to use, built for this star's aperture radius.
        extraction_radius : `int`
            This star's aperture radius, in pixels.

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
                extractor.extract_with_flare_mask_traced(
                    image,
                    base_pos,
                    flare_offset_pixels,
                    max_offset_pixels,
                    extraction_radius,
                    self.config.dispersion_orientation,
                    angle_degrees=flare_mask_angle,
                    centerline_polynomial_degree=self.config.centerline_polynomial_degree,
                )
            )
        else:
            spectrum_1d, anchor_x, anchor_y = extractor.extract_with_flare_mask(
                image,
                base_pos,
                flare_offset_pixels,
                max_offset_pixels,
                extraction_radius,
                self.config.dispersion_orientation,
                angle_degrees=flare_mask_angle,
            )

        # Calibrate wavelengths relative to the zero-order anchor
        # starting from flare_offset_pixels
        wavelengths, intensities = self.calibrator.calibrate(spectrum_1d, flare_offset_pixels)

        return wavelengths, intensities, (anchor_x, anchor_y), trail_centerline_px, trail_width_px

    def _extract_via_dispersion_line(
        self,
        image: AstrometricsImage,
        pos: tuple[float, float],
        is_traced: bool,
        extractor: SpectrumExtractor,
    ) -> tuple[np.ndarray, np.ndarray, tuple[float, float], list | None, list | None]:
        """Extract a spectrum along the instrument's default dispersion line.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        pos : `tuple` [`float`, `float`]
            Where the star is `(x, y)`.
        is_traced : `bool`
            Whether to use the traced extraction method.
        extractor : `SpectrumExtractor`
            The extractor to use, built for this star's aperture radius.

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
            spectrum_1d, trail_centerline_px, trail_width_px = extractor.extract_line_traced(
                image,
                extraction_start,
                vector,
                length,
                centerline_polynomial_degree=self.config.centerline_polynomial_degree,
            )
        else:
            spectrum_1d = extractor.extract_line(image, extraction_start, vector, length)

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

    def measure_dispersion_trail(
        self,
        image: AstrometricsImage,
        star_pos: tuple[float, float],
        roi_half_width_px: float = DISPERSION_ANGLE_ROI_HALF_WIDTH_PX,
    ) -> tuple[float, float]:
        """Measure the tilt of a star's spectrum streak and how clear it is.

        It looks at the bright streak of the spectrum and calculates its exact
        angle, plus a contrast score saying whether there was really a
        streak there to measure.

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
            The tilt of the camera, in degrees (0.0 when it could not be
            measured).
        contrast_sigma : `float`
            The typical height of the streak above the background, in noise
            sigmas, across the bands along the trail that show it. A visible
            streak scores in the hundreds; 0.0 when no streak could be
            followed (pure noise returns 0.0).
        """
        data = image.data
        x_star, y_star = star_pos

        offset_px = self.instrument.zero_order_offset_px
        length_px = self.instrument.expected_length_px
        orient = self.config.dispersion_orientation
        direc = self.config.dispersion_direction

        # Work in "along the trail" and "across the trail" coordinates so the
        # vertical and horizontal cases share one code path: for a vertical
        # trail rows run along it, for a horizontal one the image is
        # transposed so its columns do.
        if orient == "vertical":
            image_array = data
            along_star, across_star = y_star, x_star
        else:
            image_array = data.T
            along_star, across_star = x_star, y_star
        along_start = int(along_star + (offset_px if direc == "positive" else -offset_px - length_px))
        along_end = int(along_start + length_px)

        along_start, along_end = max(0, along_start), min(image_array.shape[0], along_end)
        if along_end <= along_start:
            return 0.0, 0.0

        # The strip that follows the trail can wander this far from the star's
        # own line, so the pixel block is cut wide enough to hold any of them.
        maximum_slope = float(np.tan(np.radians(DISPERSION_ANGLE_MAXIMUM_TILT_DEGREES)))
        reach_px = int(np.ceil(roi_half_width_px + maximum_slope * length_px))
        across_start = max(0, int(across_star - reach_px))
        across_end = min(image_array.shape[1], int(across_star + reach_px) + 1)
        if across_end <= across_start:
            return 0.0, 0.0

        along_coordinates = np.arange(along_start, along_end)
        pixel_block = image_array[along_start:along_end, across_start:across_end]

        # Follow the streak with a line (across = slope * along + intercept).
        # The block is cut into bands along the trail; in each band the
        # streak's
        # sideways position is the brightness-weighted centre of the pixels
        # within `roi_half_width_px` of the current line, and the line is then
        # refitted through those centres and the strips re-centred on it, until
        # it stops moving. A strip fixed on the star's own vertical line loses
        # a leaning trail (on M 57 it gave 1.6 degrees where the trail leans
        # about 2.9), and picking the brightest 5% of pixels in a strip that
        # follows the trail is biased by which pixels happen to be brightest
        # (+0.2 to +0.4 degrees on the Albireo and Vega stacks).
        background_level = float(np.median(pixel_block))
        noise_sigma = float(np.median(np.abs(pixel_block - background_level))) * 1.4826
        if noise_sigma <= 0.0:
            noise_sigma = float(np.std(pixel_block))
        if noise_sigma <= 0.0:
            return 0.0, 0.0

        band_edges = np.arange(0, along_coordinates.size, DISPERSION_ANGLE_BAND_LENGTH_PX)
        offsets = np.arange(-int(np.ceil(roi_half_width_px)), int(np.ceil(roi_half_width_px)) + 1)
        # A narrow strip can only follow a trail it already overlaps, so the
        # search starts from the best of a few lines through the star, one per
        # candidate tilt (a real trail leans about the star's zero order).
        candidate_slopes = np.linspace(-maximum_slope, maximum_slope, DISPERSION_ANGLE_SEED_SLOPE_COUNT)
        seed_offsets = np.arange(-1, 2)
        seed_scores = []
        for candidate_slope in candidate_slopes:
            seed_centre = np.rint(across_star + candidate_slope * (along_coordinates - along_star)).astype(
                int
            )
            seed_columns = seed_centre[:, None] + seed_offsets[None, :] - across_start
            is_seed_inside = ((seed_columns >= 0) & (seed_columns < pixel_block.shape[1])).all(axis=1)
            if is_seed_inside.sum() < along_coordinates.size // 2:
                seed_scores.append(-np.inf)
                continue
            seed_rows = np.arange(along_coordinates.size)[is_seed_inside]
            seed_scores.append(float(pixel_block[seed_rows[:, None], seed_columns[is_seed_inside]].mean()))
        slope = float(candidate_slopes[int(np.argmax(seed_scores))])
        intercept = float(across_star - slope * along_star)
        band_contrasts: list[float] = []
        for _ in range(DISPERSION_ANGLE_MAXIMUM_REFIT_COUNT):
            band_middles = []
            band_centres = []
            band_signals = []
            band_contrasts = []
            for band_start in band_edges:
                rows = np.arange(
                    band_start, min(band_start + DISPERSION_ANGLE_BAND_LENGTH_PX, along_coordinates.size)
                )
                if rows.size < DISPERSION_ANGLE_BAND_LENGTH_PX // 2:
                    continue
                line_centre = np.rint(intercept + slope * along_coordinates[rows]).astype(int)
                columns = line_centre[:, None] + offsets[None, :] - across_start
                is_inside_block = (columns >= 0) & (columns < pixel_block.shape[1])
                if not is_inside_block.all():
                    continue
                profile = pixel_block[rows[:, None], columns].mean(axis=0) - background_level
                band_noise = noise_sigma / np.sqrt(rows.size)
                if profile.max() < DISPERSION_ANGLE_MINIMUM_BAND_SIGMA * band_noise:
                    continue
                weights = np.clip(profile, 0.0, None)
                sideways_position = float((offsets * weights).sum() / weights.sum())
                band_middles.append(float(along_coordinates[rows].mean()))
                band_centres.append(float(line_centre.mean() + sideways_position))
                band_signals.append(float(weights.sum()))
                band_contrasts.append(float(profile.max() / band_noise))
            if len(band_middles) < DISPERSION_ANGLE_MINIMUM_BAND_COUNT:
                return 0.0, 0.0
            try:
                fitted_slope, fitted_intercept = np.polyfit(
                    band_middles, band_centres, 1, w=np.sqrt(band_signals)
                )
            except Exception:
                return 0.0, 0.0
            fitted_slope = float(np.clip(fitted_slope, -maximum_slope, maximum_slope))
            middle = float(along_coordinates.mean())
            centre_shift = abs((fitted_slope * middle + fitted_intercept) - (slope * middle + intercept))
            has_converged = (
                abs(fitted_slope - slope) < DISPERSION_ANGLE_REFIT_TOLERANCE and centre_shift < 0.5
            )
            slope, intercept = fitted_slope, float(fitted_intercept)
            if has_converged:
                break

        contrast_sigma = float(np.median(band_contrasts)) if band_contrasts else 0.0

        if orient == "vertical":
            # Vertical: slope is dx/dy. get_dispersion_vector()'s
            # 90-degree base angle for "vertical" means a positive
            # measured dx/dy slope must map to a *negative* angle
            # to reproduce that same slope -- unlike the horizontal
            # case below, this negation is required, not a bug.
            angle = -np.degrees(np.arctan(slope))
        else:
            # Horizontal: slope is dy/dx, and get_dispersion_vector()'s
            # 0-degree base angle for "horizontal" means the angle
            # must equal +arctan(slope) (no negation) to reproduce
            # this same measured slope when fed back through it.
            angle = np.degrees(np.arctan(slope))
        logger.debug(f"Auto-detected angle for star at {star_pos}: {angle:.2f} degrees")
        return float(angle), contrast_sigma

    def detect_dispersion_angle(
        self,
        image: AstrometricsImage,
        star_pos: tuple[float, float],
        roi_half_width_px: float = DISPERSION_ANGLE_ROI_HALF_WIDTH_PX,
    ) -> float:
        """Figure out exactly how much the camera is tilted.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to measure the streak in.
        star_pos : `tuple` [`float`, `float`]
            Where the star is, `(x, y)`.
        roi_half_width_px : `float`, optional
            How far to each side of `star_pos` the measuring strip reaches;
            see `measure_dispersion_trail`.

        Returns
        -------
        angle_degrees : `float`
            The tilt of the camera, in degrees.
        """
        return self.measure_dispersion_trail(image, star_pos, roi_half_width_px)[0]

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
