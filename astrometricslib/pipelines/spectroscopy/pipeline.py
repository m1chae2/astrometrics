"""The master controller for processing spectra.

This ties all the other tools together. It takes a raw picture and a list
of stars, and returns the final, cleaned-up color data (spectra) for each star.
"""

import logging
from typing import Any

import numpy as np

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.foundation.observatory_site import ObservatorySite
from astrometricslib.models.stellar_source import (
    DifferentialRefractionRecord,
    ExtinctionCorrectionRecord,
    SpectralExtractionDiagnostics,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.shared.analysis_context import AnalysisContext
from astrometricslib.pipelines.shared.quality.saturation import (
    compute_saturated_pixel_fraction,
    compute_stack_saturated_pixel_fraction,
    is_normalised_stack_scale,
)
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    assess_output_quality,
    output_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_input_quality import (
    assess_input_quality,
    input_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_raw_frame_quality import (
    assess_raw_frame_quality,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction import (
    apply_extinction_correction,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_refraction import (
    AtmosphericConditions,
    load_atmospheric_conditions,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.differential_refraction import (
    DifferentialRefraction,
    correct_differential_refraction,
    local_dispersion_angstrom_per_px,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    apply_instrument_response,
    load_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_scale import counts_per_second_factor
from astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_wing_correction import (
    STATUS_APPLIED,
    STATUS_NOT_NEEDED,
    StoredCrossTrailBlur,
    correct_neighbor_wings,
    load_cross_trail_blur,
    star_trace_from_result,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    apply_quantum_efficiency_correction,
    curve_from_profile_record,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import load_line_spread_profile
from astrometricslib.pipelines.spectroscopy.pre_processing.spectroscopy_instrument import (
    SpectroscopyInstrument,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_calibrator import SpectrumCalibrator
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import SpectrumExtractor
from astrometricslib.pipelines.spectroscopy.processing.assess_processing_quality import (
    assess_processing_quality,
)
from astrometricslib.pipelines.spectroscopy.processing.second_order_risk import (
    compute_second_order_blue_to_red_ratio,
)
from astrometricslib.pipelines.spectroscopy.processing.spectrum_analysis import (
    EXTENDED_TARGET_SPECTRAL_TYPE,
    analyze_spectrum,
)
from astrometricslib.utilities import SpectroscopyConfig
from astrometricslib.utilities.exceptions import DATA_ERRORS

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


def _project_onto_dispersion_axis(
    candidate_pos: tuple[float, float],
    origin_pos: tuple[float, float],
    dispersion_vector: np.ndarray,
) -> tuple[float, float]:
    """Split a position's offset from a point into along- and off-axis parts.

    Parameters
    ----------
    candidate_pos : `tuple` [`float`, `float`]
        The position being measured, `(x, y)`.
    origin_pos : `tuple` [`float`, `float`]
        The point to measure from, `(x, y)`.
    dispersion_vector : `numpy.ndarray`
        Unit vector `(x, y)` along the dispersion direction.

    Returns
    -------
    along_axis : `float`
        How far `candidate_pos` sits from `origin_pos`, measured along
        `dispersion_vector`. Negative means the opposite direction.
    off_axis : `float`
        How far `candidate_pos` sits from `origin_pos`, measured
        perpendicular to `dispersion_vector`. Always non-negative.
    """
    delta = np.array([candidate_pos[0] - origin_pos[0], candidate_pos[1] - origin_pos[1]])
    along_axis = float(np.dot(delta, dispersion_vector))
    off_axis = float(np.linalg.norm(delta - along_axis * dispersion_vector))
    return along_axis, off_axis


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
    along_trail, off_trail = _project_onto_dispersion_axis(candidate_pos, trail_owner_pos, dispersion_vector)
    return offset_px <= along_trail <= offset_px + length_px and off_trail <= perpendicular_tolerance_px


def _star_flux(star: Any) -> float | None:
    """Read a detected source's instrumental flux, if it has one.

    A plain `(x, y)` tuple (a caller-supplied position with no detection
    metadata behind it) has no flux at all, so this returns `None` for it
    rather than guessing.

    Returns
    -------
    flux : `float` or `None`
        The source detector's own (uncalibrated) flux estimate, or `None`
        if `star` carries no such measurement.
    """
    source = star.star_data if hasattr(star, "star_data") else star
    flux = source.get("flux") if hasattr(source, "get") else None
    return float(flux) if flux is not None else None


def _star_brightness_for_comparison(star: Any) -> float | None:
    """Read the best available brightness for comparing two stars.

    Prefers the star's own calibrated photometry (`photometry.mean_flux`,
    measured across many frames by the photometry stage, which the batch
    pipeline already runs before spectroscopy and which
    `_copy_identity` in `spectral_star_registration` carries onto a fresh
    spectroscopy detection) over the spectral image's own single-frame,
    uncalibrated detection flux (`_star_flux`). Falls back to the latter
    only when no photometry is on file for this star -- for example a
    star seen for the first time in this spectroscopy frame, with no
    prior photometry run.

    Returns
    -------
    brightness : `float` or `None`
        A brightness usable to compare two stars, or `None` when neither
        source has one.
    """
    photometry = getattr(star, "photometry", None)
    mean_flux = getattr(photometry, "mean_flux", None) if photometry is not None else None
    return float(mean_flux) if mean_flux is not None else _star_flux(star)


def find_neighbor_contamination_windows(
    star_pos: tuple[float, float],
    star_flux: float | None,
    neighbor_stars: list[Any],
    dispersion_vector: np.ndarray,
    offset_px: float,
    length_px: float,
    perpendicular_tolerance_px: float,
    instrument: Any,
) -> list[dict[str, Any]]:
    """Find wavelengths where another known star's position lands in this box.

    Two stars' trails run parallel (`_capped_extraction_radius_px` already
    keeps those apart), but a star sitting further along the *same*
    dispersion direction as this one is a different case: its own image
    (its zero order, or a bright point on its own trail) can fall inside
    this star's reading box at one specific position, adding light that
    belongs to a different star. This checks every other star astrometry
    already found and knows the position of -- it never guesses from the
    spectrum's own shape, so it can't mistake this star's own signal for a
    neighbour's the way a shape-based spike detector can (see
    `SpectroscopyConfig.reject_narrow_contaminants` for that failure).

    A neighbour's own image is treated as roughly as wide along the
    dispersion axis as this star's own box is across it
    (`perpendicular_tolerance_px`), since a star's image is close to
    circularly symmetric; there is no separate measurement of a neighbour's
    width to use instead.

    Parameters
    ----------
    star_pos : `tuple` [`float`, `float`]
        This star's own position, `(x, y)`.
    star_flux : `float`, optional
        This star's own brightness (see `_star_brightness_for_comparison`),
        for judging how much a neighbour's flux matters by comparison.
        `None` when unknown.
    neighbor_stars : `list`
        Every other star in the frame (not this one), in whatever form
        `_star_pixel_position` and `_star_brightness_for_comparison` accept.
    dispersion_vector : `numpy.ndarray`
        Unit vector `(x, y)` along the dispersion direction.
    offset_px : `float`
        How far this star's own trail starts from its position.
    length_px : `float`
        How long this star's own trail runs.
    perpendicular_tolerance_px : `float`
        This star's own box half-width, across the dispersion direction.
    instrument : `SpectroscopyInstrument`
        Used to turn a pixel offset into a wavelength
        (`wavelength_at_pixel_offset`).

    Returns
    -------
    windows : `list` [`dict`]
        One entry per neighbour whose position falls inside this box:
        ``"wavelength_low_angstrom"``, ``"wavelength_high_angstrom"``
        (the window a neighbour's own image width could reach),
        ``"neighbor_flux_ratio"`` (neighbour flux / this star's flux, or
        `None` when either flux is unknown).
    """
    windows: list[dict[str, Any]] = []
    for neighbor in neighbor_stars:
        _, neighbor_pos = _star_pixel_position(neighbor)
        if neighbor_pos[0] is None or neighbor_pos[1] is None:
            continue
        neighbor_pos = (float(neighbor_pos[0]), float(neighbor_pos[1]))
        along_trail, off_trail = _project_onto_dispersion_axis(neighbor_pos, star_pos, dispersion_vector)
        if not (offset_px <= along_trail <= offset_px + length_px):
            continue
        if off_trail > perpendicular_tolerance_px:
            continue
        low_wavelength_nm = instrument.wavelength_at_pixel_offset(along_trail - perpendicular_tolerance_px)
        high_wavelength_nm = instrument.wavelength_at_pixel_offset(along_trail + perpendicular_tolerance_px)
        neighbor_flux = _star_brightness_for_comparison(neighbor)
        windows.append({
            "wavelength_low_angstrom": min(low_wavelength_nm, high_wavelength_nm) * 10.0,
            "wavelength_high_angstrom": max(low_wavelength_nm, high_wavelength_nm) * 10.0,
            "neighbor_flux_ratio": (
                neighbor_flux / star_flux if neighbor_flux is not None and star_flux else None
            ),
        })
    return windows


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


def _frame_airmass(image: Any) -> float | None:
    """Read the airmass of a frame from its header.

    Parameters
    ----------
    image : `AstrometricsImage`
        The frame the spectrum was extracted from.

    Returns
    -------
    airmass : `float` or `None`
        The header's ``AIRMASS`` value, or `None` when the card is missing
        or is not a number.
    """
    header = getattr(image, "header", None)
    if header is None:
        return None
    try:
        return float(header.get("AIRMASS"))
    except TypeError, ValueError:
        return None


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
    observatory_site : `ObservatorySite` or `None`
        Where the observatory is. Without it, the refraction correction
        is not computed.
    atmospheric_conditions : `AtmosphericConditions` or `None`
        The air's pressure, temperature and humidity. `None` means the
        standard atmosphere at the site's elevation.
    """

    def __init__(
        self,
        config: SpectroscopyConfig | None = None,
        *,
        observatory_site: ObservatorySite | None = None,
        atmospheric_conditions: AtmosphericConditions | None = None,
    ) -> None:
        """Set up the master controller.

        Parameters
        ----------
        config : `SpectroscopyConfig`, optional
            The camera settings to use. If you leave this blank, it will
            load the default settings automatically.
        observatory_site : `ObservatorySite`, optional
            Where the observatory is, for the atmospheric refraction
            correction. When `config` is left blank and this is too, it is
            read from the loaded configuration. Otherwise, with no site,
            the correction is not computed.
        atmospheric_conditions : `AtmosphericConditions`, optional
            The air's pressure, temperature and humidity. When left blank
            and the configuration is loaded here, they are read from the
            ``[Observatory.Location]`` section. Otherwise the standard
            atmosphere at the site's elevation is used.
        """
        if config is None:
            from astrometricslib.foundation.config import get_configuration
            from astrometricslib.utilities import ConfigLoader

            app_config = get_configuration()
            config = ConfigLoader.load_spectroscopy_config(app_config=app_config)
            if observatory_site is None:
                observatory_site = app_config.get_observatory_site()
                if observatory_site is not None and atmospheric_conditions is None:
                    atmospheric_conditions = load_atmospheric_conditions(
                        app_config, observatory_site.elevation_m
                    )
        self.config = config
        self.observatory_site = observatory_site
        self.atmospheric_conditions = atmospheric_conditions
        # What we know about this camera model (for example, the value at
        # which its pixels count as saturated), looked up once here.
        self.camera_profile = resolve_camera_profile(config.camera.name)
        self.quantum_efficiency_curve = (
            curve_from_profile_record(self.camera_profile.quantum_efficiency)
            if self.camera_profile.quantum_efficiency is not None
            else None
        )
        # The stored correction for this setup's grating and sensor, or
        # `None` when none has been derived. Read once here, not per star.
        self.instrument_response = load_instrument_response(config.camera.name)
        self.line_spread_profile = load_line_spread_profile(config.camera.name)
        # The stored blur of this camera's streaks, measured on an isolated
        # bright star, or `None` when none has been derived. Only used when
        # `config.subtract_neighbor_wings` is on.
        self.cross_trail_blur: StoredCrossTrailBlur | None = load_cross_trail_blur(config.camera.name)
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
        self, context: AnalysisContext, limit: int | None = None, auto_detect_angle: bool = True
    ) -> list[StellarObject]:
        """Process all the stars we found in an image.

        Parameters
        ----------
        context : `AnalysisContext`
            The picture and the list of stars we found in it.
        limit : `int`, optional
            A cap on how many candidate stars to process. Left at `None`
            (the default), every candidate that survived detection and
            `_drop_spurious_trail_detections` is processed -- the real
            limit on how many stars get a spectrum is the detector's own
            5-sigma detection threshold (`source_detection.py`), not an
            arbitrary top-N count. A fixed count used to be the default
            here (10, brightness-ordered), which silently dropped real,
            fainter, already-identified stars once a field had more than
            10 detections; pass a number only to deliberately cap a run
            (for example a quick interactive check).
        auto_detect_angle : `bool`, optional
            Whether to try and figure out the exact tilt of the camera
            automatically (default is True).

        Returns
        -------
        processed_stellar_objects : `List[StellarObject]`
            The list of stars, now updated with their color data (spectra).
        """
        # If astrometry noticed the target is a large object like a nebula
        # instead of a star, turn that plain fact into a StellarObject
        # sized for our own dispersion geometry -- astrometry knows where
        # the object is and how big it looks, but not how wide a
        # measurement box our spectrograph needs for it. Done before the
        # trail-detection filter below, so the new object can be folded
        # into the same batch as everything else.
        if context.extended_target is None and context.extended_source_hint is not None:
            try:
                hint = context.extended_source_hint
                context.extended_target = self.create_extended_target_object(
                    extraction_center=hint.extraction_center,
                    object_name=hint.object_name,
                    otype=hint.otype,
                    extraction_radius=hint.extraction_radius_px,
                )
            except (ProcessingError, *DATA_ERRORS) as e:
                logger.warning("Failed to build extended target StellarObject from hint: %s", e)

        # Filter before slicing to `limit`: a spurious trail detection
        # sitting near the top of the brightness-sorted list would
        # otherwise take a slot a real, fainter star should have had. The
        # extended target is never a spurious-trail candidate itself (it
        # is a synthesized placeholder, not a detection), so it is added
        # only after this filter runs.
        candidate_stars = _drop_spurious_trail_detections(
            context.stellar_objects,
            self.instrument.get_dispersion_vector(),
            self.instrument.zero_order_offset_px,
            self.instrument.expected_length_px,
            self.config.extraction_radius,
        )

        # A target like a nebula or cluster is processed in the very same
        # batch as every ordinary star, not by spinning up a second
        # pipeline: it needs its own wider aperture and skips some
        # per-star steps that assume a point source (see
        # `_process_target_stars` and `_subtract_neighbor_wings`), but the
        # extraction, calibration and dispersion-angle code underneath is
        # exactly the same. Inserted at the front so the returned list's
        # order matches what callers have always seen.
        if context.extended_target is not None and context.extended_target not in candidate_stars:
            candidate_stars = [context.extended_target, *candidate_stars]
            if context.extended_target not in context.stellar_objects:
                context.stellar_objects.insert(0, context.extended_target)

        target_stars = candidate_stars if limit is None else candidate_stars[:limit]
        results = self.process_image(
            context.image, target_stars=target_stars, auto_detect_angle=auto_detect_angle
        )
        self.last_run_zero_order_saturation_fractions = [
            res["zero_order_saturated_pixel_fraction"]
            for res in results
            if "zero_order_saturated_pixel_fraction" in res
        ]
        valid_objects = [res["star_source"] for res in results if "error" not in res]

        return valid_objects

    def process_image(
        self,
        image: AstrometricsImage,
        target_stars: list[Any] | None = None,
        limit: int | None = None,
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
            A cap on how many of `target_stars` to process. `None` (the
            default) processes all of them; see `process` for why this
            is no longer a fixed count.
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

        # An extended target (a nebula or cluster) has no single streak to
        # measure -- its "trail" is a chain of ring images, not one line --
        # so it is never itself a candidate for the angle fit below, only
        # ever a recipient of whatever angle the real stars in the batch
        # resolve to.
        all_positions: list[tuple[float, float]] = []
        for star in target_stars:
            if getattr(star, "stellar_spectral_type", None) == EXTENDED_TARGET_SPECTRAL_TYPE:
                continue
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
            fallback_star = next(
                (
                    star
                    for star in target_stars
                    if getattr(star, "stellar_spectral_type", None) != EXTENDED_TARGET_SPECTRAL_TYPE
                ),
                target_stars[0],
            )
            _, fallback_pos = _star_pixel_position(fallback_star)
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
                "No star showed a clear dispersion streak (best contrast %.1f sigma < %.1f); "
                "keeping the configured angle %.2f degrees",
                contrast_sigma,
                DISPERSION_ANGLE_MINIMUM_TRAIL_CONTRAST_SIGMA,
                self.config.dispersion_angle_degrees,
            )
            return
        logger.info(
            "Globally resolved grating dispersion angle: %.2f degrees (from the star at %s, "
            "contrast %.1f sigma, angle-detection half-width %.1fpx)",
            global_angle,
            best_star_pos,
            contrast_sigma,
            roi_half_width_px,
        )
        self.config.dispersion_angle_degrees = global_angle
        self.instrument.config.dispersion_angle_degrees = global_angle

    def _process_target_stars(
        self,
        image: AstrometricsImage,
        target_stars: list[Any],
        limit: int | None,
        auto_detect_angle: bool,
    ) -> list[dict[str, Any]]:
        """Run single-star extraction over a batch of target stars.

        Returns
        -------
        results : `list` [`dict`]
            One result dict per successfully processed star, each also
            carrying its original `star_source` object.
        """
        extracted: list[tuple[Any, bool, dict[str, Any]]] = []
        batch = target_stars if limit is None else target_stars[:limit]
        batch_positions = [_star_pixel_position(star)[1] for star in batch]
        dispersion_vector = self.instrument.get_dispersion_vector()
        for star_index, star in enumerate(batch):
            is_stellar_obj, pos = _star_pixel_position(star)

            neighbor_positions = [
                (float(other[0]), float(other[1]))
                for other_index, other in enumerate(batch_positions)
                if other_index != star_index and other[0] is not None and other[1] is not None
            ]
            is_extended = getattr(star, "stellar_spectral_type", None) == EXTENDED_TARGET_SPECTRAL_TYPE
            if is_extended:
                # A nebula or cluster's own aperture is deliberately much
                # wider than a point source's (see
                # `create_extended_target_object`); shrinking it for a
                # nearby real star, the way `_capped_extraction_radius_px`
                # would, defeats the whole point of asking for a wide
                # aperture, so it is never applied here.
                configured_radius = star.spectroscopy.extraction_radius
                extraction_radius = (
                    int(configured_radius)
                    if configured_radius is not None
                    else int(self.config.extraction_radius)
                )
            else:
                extraction_radius = _capped_extraction_radius_px(
                    (float(pos[0]), float(pos[1])),
                    neighbor_positions,
                    dispersion_vector,
                    self.instrument.expected_length_px,
                    int(self.config.extraction_radius),
                )
            try:
                result = self._process_single_star(
                    image,
                    pos,
                    auto_detect_angle=auto_detect_angle,
                    extraction_radius=extraction_radius,
                    reject_narrow_contaminants=True if is_extended else None,
                )
            except ProcessingError as error:
                # One star without a usable spectrum does not stop the rest.
                logger.info("Skipped the star at %s: %s", pos, error)
            else:
                # Attach the original star object if possible for reference
                result["star_source"] = star
                neighbor_stars = [
                    other for other_index, other in enumerate(batch) if other_index != star_index
                ]
                result["possible_neighbor_contamination"] = find_neighbor_contamination_windows(
                    (float(pos[0]), float(pos[1])),
                    _star_brightness_for_comparison(star),
                    neighbor_stars,
                    dispersion_vector,
                    self.instrument.zero_order_offset_px,
                    self.instrument.expected_length_px,
                    extraction_radius,
                    self.instrument,
                )
                extracted.append((star, is_stellar_obj, result))

        # Every star is read first, so each one's neighbours' light can be
        # taken out of it before the results are applied.
        if self.config.subtract_neighbor_wings and len(extracted) > 1:
            self._subtract_neighbor_wings(image, [result for _, _, result in extracted])

        results = []
        for star, is_stellar_obj, result in extracted:
            # If it's a StellarObject, enrich it with results
            if is_stellar_obj:
                self._apply_result_to_stellar_object(star, result, image)
            results.append(result)

        return results

    def _subtract_neighbor_wings(self, image: AstrometricsImage, results: list[dict[str, Any]]) -> None:
        """Take each star's neighbours' blurred light out of its spectrum.

        Two stars close together give side-by-side streaks, and the bright
        one's blur reaches into the faint one's box (see
        `neighbor_trail_deblending`). For every star with such a neighbour the
        stored blur is fitted to the pair, and the neighbour's share is taken
        out of the star's intensities. What was done, and how much was taken
        out at each sample, is recorded in the star's result.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture the spectra were read from.
        results : `list` [`dict`]
            Every star's extraction result. Changed in place: a corrected
            star's ``"intensities"`` are replaced, and every star with a
            neighbour gets ``"neighbor_wing_status"`` and, when it was
            corrected, ``"neighbor_wing_fraction"``. An extended target
            (a nebula or cluster) is left out entirely, in both roles:
            its own spectrum is never corrected, and it is never treated
            as a "neighbour" whose blur reaches into a real star's box --
            the blur-fit math here assumes a point source's narrow streak,
            which a diffuse, wide-aperture target does not have.
        """
        correctable = [
            result
            for result in results
            if getattr(result.get("star_source"), "stellar_spectral_type", None)
            != EXTENDED_TARGET_SPECTRAL_TYPE
        ]
        traces = [star_trace_from_result(result) for result in correctable]
        first_vector = next(
            (result["dispersion_vector"] for result in correctable if result.get("dispersion_vector")),
            None,
        )
        if first_vector is None:
            return
        outcomes = correct_neighbor_wings(
            image.data, traces, self.cross_trail_blur, (float(first_vector[0]), float(first_vector[1]))
        )
        for result, outcome in zip(correctable, outcomes, strict=True):
            if outcome.status == STATUS_NOT_NEEDED:
                continue
            result["neighbor_wing_status"] = outcome.status
            if (
                outcome.status == STATUS_APPLIED
                and outcome.corrected_flux is not None
                and outcome.wing_fraction is not None
            ):
                result["intensities"] = outcome.corrected_flux.tolist()
                result["neighbor_wing_fraction"] = outcome.wing_fraction.tolist()

    def _apply_result_to_stellar_object(
        self, star: StellarObject, result: dict[str, Any], image: AstrometricsImage
    ) -> None:
        """Copy a single star's extraction result onto its `StellarObject`.

        "Quantum Efficiency" (QE) corrects for the fact that camera
        sensors see some colors of light better than others. If we
        know the camera's exact QE curve, we fix the data here. If we
        don't know the camera, we just skip this step.
        """
        intensities = result["intensities"]

        # Compute the visual overlay rectangle and total rotated
        # dispersion angle
        rectangle, dispersion_angle = self._dispersion_overlay_geometry(
            result["target_pos"],
            result.get("extraction_radius", self.config.extraction_radius),
            dispersion_angle_degrees=result["detected_angle"],
        )

        # Atmospheric differential refraction (DAR). The wavelength scale is
        # already computed (`_process_single_star` calibrated every sample
        # from its distance to the zero order). The air has displaced each
        # wavelength's light along the trail, so each sample's wavelength is
        # shifted back here, before every step below that reads a wavelength:
        # the QE correction, the instrument response and the airmass
        # correction. `result["wavelengths"]` keeps the unshifted scale.
        corrected_wavelengths, differential_refraction = self._correct_differential_refraction(
            result, image, dispersion_angle
        )
        wavelengths_angstrom = corrected_wavelengths.tolist()

        quantum_efficiency_corrected_intensities = None
        if self.quantum_efficiency_curve is not None:
            quantum_efficiency_corrected_intensities = apply_quantum_efficiency_correction(
                wavelength_nm=corrected_wavelengths / 10.0,
                intensity=np.array(result["intensities"]),
                curve=self.quantum_efficiency_curve,
            ).tolist()

        # The instrument response was derived from QE-corrected spectra, so
        # it is only ever applied to one -- same rule the classifier used
        # to apply internally, now applied here alongside QE correction,
        # right next to it, as one coherent equipment-calibration step.
        best_available_intensity = (
            quantum_efficiency_corrected_intensities
            if quantum_efficiency_corrected_intensities is not None
            else intensities
        )
        response = self.instrument_response if quantum_efficiency_corrected_intensities is not None else None
        response_corrected_intensity = (
            apply_instrument_response(
                np.array(wavelengths_angstrom), np.array(best_available_intensity), response
            )
            if response is not None
            else None
        )
        # The response includes the air's dimming at the standard star's
        # airmass. Scale the spectrum to that airmass, using the airmass of
        # this frame, so a target observed higher or lower does not keep a
        # leftover blue-red tilt.
        extinction_record = None
        if response_corrected_intensity is not None and response is not None:
            response_corrected_intensity, extinction_record = apply_extinction_correction(
                np.array(wavelengths_angstrom),
                response_corrected_intensity,
                _frame_airmass(image),
                response.reference_airmass,
            )
            logger.debug("Extinction correction: %s", extinction_record.as_dict())

        # Classify and test features on the response-corrected spectrum
        # when available -- it better reflects the star's true color than
        # QE-corrected or raw sensor counts.
        analysis = analyze_spectrum(
            np.array(wavelengths_angstrom),
            np.array(best_available_intensity),
            response_corrected_intensity,
            catalog_spectral_type=star.spectral_type,
            is_extended_target=star.stellar_spectral_type == EXTENDED_TARGET_SPECTRAL_TYPE,
            catalog_b_minus_v=star.b_minus_v,
            trail_width_px=result.get("trail_width_px"),
            extraction_box_width_px=float(rectangle[3]) if rectangle is not None else None,
            resolution_profile=self.line_spread_profile,
            possible_neighbor_contamination=result.get("possible_neighbor_contamination"),
            extinction_correction=extinction_record,
        )
        classification = analysis.classification
        probable_spectral_features = analysis.features

        input_quality = assess_input_quality(
            resolution_element_angstrom=analysis.resolution_element_angstrom,
            is_resolution_measured=analysis.is_resolution_measured,
            zero_order_saturated_pixel_fraction=result.get("zero_order_saturated_pixel_fraction"),
            valid_fraction=result.get("valid_fraction"),
            signal_to_noise=analysis.signal_to_noise,
        )
        output_quality = assess_output_quality(
            classification, analysis.catalog_comparison, analysis.resolution_element_angstrom
        )
        second_order_blue_to_red_ratio = compute_second_order_blue_to_red_ratio(
            np.array(wavelengths_angstrom), np.array(intensities)
        ).tolist()

        # The four quality checkpoints, in the order a spectrum passes them.
        # Checkpoint 0 reuses the numbers the extraction already measured for
        # this star. `frame_check` is the optional frame-level result of
        # `measure_spectral_frame_file`; the pipeline does not run it, so it
        # is normally absent.
        stage_quality = [
            assess_raw_frame_quality(
                zero_order_saturated_pixel_fraction=result.get("zero_order_saturated_pixel_fraction"),
                valid_fraction=result.get("valid_fraction"),
                trail_width_px=result.get("trail_width_px"),
                extraction_diagnostics=result.get("extraction_diagnostics"),
                frame_check=result.get("frame_check"),
                differential_refraction=differential_refraction,
            ),
            input_quality_checkpoint(input_quality),
            assess_processing_quality(
                classification=classification,
                features=probable_spectral_features,
                synthetic_b_minus_v=analysis.synthetic_b_minus_v,
                emission_lines=analysis.emission_lines,
                is_emission_line_source=analysis.is_emission_line_source,
                second_order_blue_to_red_ratio=second_order_blue_to_red_ratio,
            ),
            output_quality_checkpoint(
                output_quality,
                own_spectral_type=str(classification["spectral_type"]),
                catalog_spectral_type=star.spectral_type,
                catalog_comparison=analysis.catalog_comparison,
            ),
        ]

        star.spectroscopy = SpectroscopyResult(
            wavelengths_angstrom=wavelengths_angstrom,
            intensities=intensities,
            quantum_efficiency_corrected_intensities=quantum_efficiency_corrected_intensities,
            response_corrected_intensities=analysis.response_corrected_intensity,
            self_determined_spectral_type=classification["spectral_type"],
            self_determined_spectral_type_rms=classification["classification_rms"],
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
            second_order_blue_to_red_ratio=second_order_blue_to_red_ratio,
            resolution_element_angstrom=(
                analysis.resolution_element_angstrom if analysis.is_resolution_measured else None
            ),
            neighbor_wing_fraction=result.get("neighbor_wing_fraction"),
            neighbor_wing_status=result.get("neighbor_wing_status"),
            possible_neighbor_contamination=result.get("possible_neighbor_contamination"),
            counts_per_second_factor=counts_per_second_factor(getattr(image, "header", None)),
            catalog_comparison=analysis.catalog_comparison,
            input_quality=input_quality,
            output_quality=output_quality,
            stage_quality=stage_quality,
            extraction_diagnostics=(
                SpectralExtractionDiagnostics.model_validate(result["extraction_diagnostics"])
                if result.get("extraction_diagnostics") is not None
                else None
            ),
            extinction_correction=(
                ExtinctionCorrectionRecord.model_validate(analysis.extinction_correction)
                if analysis.extinction_correction is not None
                else None
            ),
            differential_refraction=DifferentialRefractionRecord.model_validate(
                differential_refraction.as_dict()
            ),
        )

        if isinstance(star.star_data, dict):
            star.star_data["xcentroid"] = result["target_pos"][0]
            star.star_data["ycentroid"] = result["target_pos"][1]

    def _correct_differential_refraction(
        self, result: dict[str, Any], image: AstrometricsImage, dispersion_image_angle_degrees: float
    ) -> tuple[np.ndarray, DifferentialRefraction]:
        """Shift one star's wavelengths for atmospheric refraction.

        The correction needs the observatory site, the frame's WCS and the
        exposure time. When any is missing, or the star is below an
        altitude of 20 degrees, the wavelengths come back unchanged and the
        record says why. The zero order's effective wavelength is the mean
        wavelength of the raw counts. The counts already carry the
        camera's sensitivity and the star's spectrum, so no second
        weighting by the quantum efficiency curve is needed.

        Parameters
        ----------
        result : `dict`
            The star's extraction result from `_process_single_star`.
        image : `AstrometricsImage`
            The frame the spectrum was extracted from.
        dispersion_image_angle_degrees : `float`
            The image direction in which wavelength increases along the
            trail, from the +x axis toward the +y axis.

        Returns
        -------
        wavelengths_angstrom : `numpy.ndarray`
            The corrected wavelengths, in Angstroms.
        record : `DifferentialRefraction`
            What was computed and whether it was applied.
        """
        config = self.config

        def dispersion(wavelength_angstrom: np.ndarray) -> np.ndarray:
            """Give the Angstroms per pixel at some wavelengths.

            Parameters
            ----------
            wavelength_angstrom : `numpy.ndarray`
                The wavelengths, in Angstroms.

            Returns
            -------
            dispersion : `numpy.ndarray`
                The Angstroms per pixel along the trail.
            """
            return local_dispersion_angstrom_per_px(
                wavelength_angstrom,
                config.grating_distance_mm,
                config.grating_lines_per_mm,
                config.camera.pixel_size_um,
            )

        return correct_differential_refraction(
            np.asarray(result["wavelengths"], dtype=float) * 10.0,
            np.asarray(result["intensities"], dtype=float),
            header=getattr(image, "header", None),
            wcs=getattr(image, "wcs", None),
            target_pixel_xy=(float(result["target_pos"][0]), float(result["target_pos"][1])),
            dispersion_image_angle_degrees=dispersion_image_angle_degrees,
            site=self.observatory_site,
            conditions=self.atmospheric_conditions,
            dispersion_angstrom_per_px=dispersion,
        )

    def _process_single_star(
        self,
        image: AstrometricsImage,
        pos: tuple[float, float],
        auto_detect_angle: bool = True,
        extraction_radius: int | None = None,
        reject_narrow_contaminants: bool | None = None,
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
            trail, or larger (for an extended target). Defaults to
            `config.extraction_radius`.
        reject_narrow_contaminants : `bool`, optional
            Overrides `config.reject_narrow_contaminants` for this one
            star, when given. An extended target's much wider box is more
            likely to catch a genuine narrow contaminant worth rejecting
            than an ordinary point source's is.

        Returns
        -------
        result : `dict`
            A dictionary containing the color data (wavelengths and
            intensities)
            and other math details about the extraction. The key
            `sample_distances_px` holds the distance of each kept sample
            from the zero order, in pixels, measured along the dispersion
            direction. It has one value per wavelength.

        Raises
        ------
        ProcessingError
            The instrument model gives a spectrum of zero length, or no part
            of the trail is on the image and inside the camera's range.
        """
        radius = (
            int(extraction_radius) if extraction_radius is not None else int(self.config.extraction_radius)
        )
        extractor = self.extractor
        if radius != self.extractor.radius or reject_narrow_contaminants is not None:
            extractor = SpectrumExtractor(
                radius=radius,
                subtract_sky_background=self.config.subtract_sky_background,
                reject_narrow_contaminants=(
                    self.config.reject_narrow_contaminants
                    if reject_narrow_contaminants is None
                    else reject_narrow_contaminants
                ),
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
            wavelengths, intensities, target_pos, trail_centerline_px, trail_width_px, sample_distances_px = (
                self._extract_via_flare_mask(image, pos, detected_angle, is_traced, extractor, radius)
            )
        else:
            wavelengths, intensities, target_pos, trail_centerline_px, trail_width_px, sample_distances_px = (
                self._extract_via_dispersion_line(image, pos, is_traced, extractor)
            )

        zero_order_saturated_pixel_fraction = self._measure_zero_order_saturation(image, target_pos)

        # Keep only the samples that were really measured: on the image and
        # inside the camera's sensitive range. The trail arrays line up with
        # the spectrum one-to-one, so they are trimmed the same way.
        wavelengths = np.asarray(wavelengths, dtype=float)
        intensities = np.asarray(intensities, dtype=float)
        if wavelengths.size == 0:
            raise ProcessingError(
                "The instrument model asked for a spectrum of zero length; check the camera config."
            )
        requested_wavelength_range_nm = [float(np.nanmin(wavelengths)), float(np.nanmax(wavelengths))]
        # `sample_distances_px` holds the distance of every sample from the
        # zero order, measured along the dispersion direction. The sample
        # arrays and the trail arrays are trimmed below with the same mask, so
        # a distance stays attached to its own sample.
        sample_distances_px = np.asarray(sample_distances_px, dtype=float)
        usable = keep_usable_samples(
            wavelengths,
            intensities,
            self.config.camera.sensor_min_wavelength,
            self.config.camera.sensor_max_wavelength,
        )
        if not usable.any():
            raise ProcessingError(
                "No part of the spectrum trail is on the image and inside the camera's range.",
                details={"position": [float(pos[0]), float(pos[1])]},
            )
        valid_fraction = float(usable.mean())
        wavelengths = wavelengths[usable]
        intensities = intensities[usable]
        sample_distances_px = sample_distances_px[usable]
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
            # What the extractor did for this star: sky modes and the width of
            # the reading box. Taken now because the extractor resets it at
            # the start of its next extraction.
            "extraction_diagnostics": extractor.last_diagnostics.as_dict(),
            # Both extraction paths record this, so the calibration tuner can
            # place each sample on the physical model after leading samples
            # were dropped.
            "sample_distances_px": sample_distances_px.tolist(),
            # The neighbour-wing stage needs the plain dispersion-line
            # geometry to place every sample on the image again. The
            # flare-mask extraction reads a different path and records none.
            **(
                {}
                if use_flare_mask
                else {
                    "distances_from_zero_order_px": sample_distances_px.tolist(),
                    "base_position_px": (
                        float(pos[0]) + self.config.dispersion_offset_x,
                        float(pos[1]) + self.config.dispersion_offset_y,
                    ),
                    "dispersion_vector": tuple(float(v) for v in self.instrument.get_dispersion_vector()),
                }
            ),
        }

    def _extract_via_flare_mask(
        self,
        image: AstrometricsImage,
        pos: tuple[float, float],
        detected_angle: float,
        is_traced: bool,
        extractor: SpectrumExtractor,
        extraction_radius: int,
    ) -> tuple[np.ndarray, np.ndarray, tuple[float, float], list | None, list | None, np.ndarray]:
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
        sample_distances_px : `numpy.ndarray`
            The distance of each sample from the zero order, in pixels,
            measured along the dispersion direction. The extractor steps one
            pixel along the dispersion axis per sample, and a tilted trail
            is longer than that by a factor of 1 / cos(tilt). The distance
            of a sample is therefore its offset from the anchor along the
            axis, divided by the cosine of the detected tilt.
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

        # The extractor starts at the whole pixel nearest to
        # `anchor + flare_offset_pixels`, so the first sample is not exactly
        # `flare_offset_pixels` from the anchor. Take each sample's real
        # distance from the anchor along the dispersion direction, and
        # calibrate from that distance. The anchor is the extractor's
        # centroid of the zero order with the spectrum trail removed. A
        # centroid that kept the trail would sit about 0.06 to 0.09 pixel
        # toward it, and every recorded distance would be short by that much.
        anchor_along_axis = anchor_x if self.config.dispersion_orientation == "horizontal" else anchor_y
        first_step = round(anchor_along_axis + flare_offset_pixels)
        axis_offsets = first_step + np.arange(len(spectrum_1d), dtype=float) - anchor_along_axis
        sample_distances_px = axis_offsets / np.cos(np.radians(detected_angle))
        wavelengths, intensities = self.calibrator.calibrate_at_distances(spectrum_1d, sample_distances_px)

        return (
            wavelengths,
            intensities,
            (anchor_x, anchor_y),
            trail_centerline_px,
            trail_width_px,
            sample_distances_px,
        )

    def _extract_via_dispersion_line(
        self,
        image: AstrometricsImage,
        pos: tuple[float, float],
        is_traced: bool,
        extractor: SpectrumExtractor,
    ) -> tuple[np.ndarray, np.ndarray, tuple[float, float], list | None, list | None, np.ndarray]:
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
        sample_distances_px : `numpy.ndarray`
            The distance of each sample from the zero order, in pixels,
            measured along the dispersion direction. The extractor steps one
            pixel along the dispersion vector per sample, so this is
            `offset_px + i` for sample `i`.
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
        sample_distances_px = offset_px + np.arange(len(spectrum_1d), dtype=float)

        return wavelengths, intensities, pos, trail_centerline_px, trail_width_px, sample_distances_px

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
        if is_normalised_stack_scale(data):
            # Siril stacks run 0 to 1, below any ADU level.
            return compute_stack_saturated_pixel_fraction(data, zero_order_cutout)
        return compute_saturated_pixel_fraction(
            zero_order_cutout, self.camera_profile.saturation_threshold_adu.value
        )

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
            except DATA_ERRORS:
                # A fit that does not converge gives no trail.
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
        logger.debug("Auto-detected angle for star at %s: %.2f degrees", star_pos, angle)
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
