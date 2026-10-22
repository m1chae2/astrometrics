"""The frame metrics the library already measures, for the registry.

Each `MetricSpec` here describes one per-frame measurement that existing code
computes today. The name says which estimator it is, so a number from another
estimator never shares a name with it. Nothing here measures anything: the
`computed_by` field points at the function that does.

The registry (`metric_registry.py`) collects these with the metrics that each
pipeline declares in its own ``metrics.py``.
"""

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)

FRAME_BACKGROUND_LEVEL = "frame.background_level_adu"
FRAME_SATURATED_PIXEL_FRACTION = "frame.saturated_pixel_fraction"
FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX = "frame.star_width_gaussian_brightest_px"
FRAME_DETECTED_REGION_COUNT = "frame.detected_region_count"
FRAME_SENSOR_TEMPERATURE = "frame.sensor_temperature_degc"
FRAME_FOCUSER_TEMPERATURE = "frame.focuser_temperature_degc"
FRAME_STAR_WIDTH_MOFFAT_PX = "frame.star_width_moffat_px"
FRAME_STAR_WIDTH_MOFFAT_ARCSEC = "frame.star_width_moffat_arcsec"
FRAME_STAR_ELONGATION = "frame.star_elongation"
FRAME_STAR_ELONGATION_ANGLE_DEG = "frame.star_elongation_angle_deg"
FRAME_SKY_RATE_E_PER_S_PER_PX = "frame.sky_rate_e_per_s_per_px"
FRAME_SKY_NOISE_E = "frame.sky_noise_e"
FRAME_READ_NOISE_E = "frame.read_noise_e"

_FRAME_ONLY = (SubjectLevel.FRAME,)

# The band text of the metrics that are measured on each color plane of a frame
# stored as planes, and on a single plane otherwise.
_PER_PLANE_BAND = (
    "The frame's own filter. A frame stored as color planes is measured plane by plane, with the plane "
    "named on the subject. A raw color mosaic is measured as it is, with its color pixels mixed."
)

# How the star metrics choose and fit their stars, shared by the four star
# declarations so they cannot drift apart.
_MOFFAT_SAMPLE = (
    "Up to about 196 stars, one from each cell of a 14 x 14 grid laid over the plane (the brightest "
    "detected star of the cell that has no other detected source within 14 px, a whole 21 x 21 px fit "
    "box inside the frame, and no pixel in the box at or above 0.9 of the camera's usable top level). "
    "When a cell's star fails the fit, up to two fainter stars of the cell are tried. No star is "
    "dropped for being stretched. At least 5 stars are needed."
)

FRAME_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name=FRAME_BACKGROUND_LEVEL,
        definition_version=1,
        unit="adu",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning="The brightness of the sky background in one frame.",
        decisions=("frame quarantine", "background homogeneity"),
        # `raw_frame_check` measures its own plain-median sky level
        # (`sky_median_adu`). That is a different estimator and must never
        # be emitted under this name.
        estimator=(
            "Median of the pixels left after astropy's `sigma_clipped_stats` (3 sigma, "
            "the default maximum of 5 clipping passes), on the frame with its color planes "
            "collapsed to one."
        ),
        sample_definition="Every pixel of the frame.",
        band=(
            "The frame's own filter. A color image is first collapsed to the mean of its color planes; "
            "an undebayered one-shot-color mosaic is measured as it is, with its color pixels mixed."
        ),
        computed_by="pipelines.shared.quality.background_measurement.measure_frame_background_level",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "The stacking checks compare each frame's sky level with its session to find cloud and gradients."
        ),
        register_entry="STK-04",
    ),
    MetricSpec(
        name=FRAME_SATURATED_PIXEL_FRACTION,
        definition_version=1,
        unit="fraction",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning="The share of pixels at or above the camera's saturation level.",
        decisions=("raw frame clipping",),
        estimator=(
            "Count of pixels at or above the camera-profile saturation threshold, divided by the pixel count."
        ),
        sample_definition="Every pixel of the frame, after a color image is collapsed to one plane.",
        band=(
            "The frame's own filter. A color image is first collapsed to the mean of its color planes; "
            "an undebayered one-shot-color mosaic is measured as it is, with its color pixels mixed."
        ),
        computed_by="pipelines.shared.quality.background_measurement.measure_frame_saturated_pixel_fraction",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame with many saturated pixels has lost the brightness of those pixels, "
            "and the checks warn about it. No register entry yet backs the limit it is compared with."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
        definition_version=1,
        unit="px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=(
            "The width (FWHM) of the brightest stars in one frame, from 2-D Gaussian fits. "
            "On a trailed frame the narrow-axis width is used, so the value can read sharp."
        ),
        decisions=("stack sharpness",),
        estimator=(
            "Median of the fitted star widths from `measure_image_fwhm`. Each star gets a tilted 2-D "
            "Gaussian fit that must reach R squared 0.8 and land within 2.5 px of the detected center. "
            "The width is 2.355 times the geometric mean of the two fitted sigmas; when the long axis "
            "is more than 1.5 times the short axis, it is 2.355 times the short-axis sigma instead, "
            "and that star stays in the median. A star whose box reaches 0.95 of the frame's "
            "plateau level (only when 3 or more pixels sit at the frame maximum) is skipped."
        ),
        sample_definition=(
            "Up to 400 detected sources with flux at least 5% of the brightest are tried, brightest "
            "first, until 15 fits succeed. The frame is collapsed to one plane first. The optional "
            "saturated-core mask is not passed by the callers."
        ),
        band=(
            "The frame's own filter. A color image is first collapsed to the mean of its color planes; "
            "an undebayered one-shot-color mosaic is measured as it is."
        ),
        computed_by="pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=("The stack sharpness check compares star width between frames and stacks."),
        register_entry="AST-07",
    ),
    MetricSpec(
        name=FRAME_DETECTED_REGION_COUNT,
        definition_version=1,
        unit="count",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning=(
            "The number of connected bright regions in one raw frame. Hot pixels, nebulosity and "
            "pieces of a trail count too, and merged stars count once, so it is not a star count."
        ),
        decisions=("frame quarantine", "raw frame check"),
        estimator=(
            "`ndimage.label` count of regions above 40 noise units (`DETECTION_SIGMA`) after "
            "background removal and a 1.5 px Gaussian smooth, from `_detect_stars`."
        ),
        sample_definition="Every connected bright region in the frame.",
        band="The frame's own filter; a color image is first collapsed to one plane.",
        computed_by="pipelines.shared.quality.raw_frame_check.measure_raw_frame",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame with far fewer regions than its batch usually has cloud or fog, and "
            "the quarantine and the raw frame check flag it."
        ),
        register_entry="STK-01",
    ),
    MetricSpec(
        name=FRAME_SENSOR_TEMPERATURE,
        definition_version=1,
        unit="degC",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning="The camera sensor temperature recorded when the frame was taken.",
        decisions=(),
        estimator="Read from the FITS header card CCD-TEMP; not computed from pixels.",
        sample_definition="One header value per frame.",
        band="not applicable",
        computed_by="pipelines.shared.frame_scanning.create_frame_record_from_fits",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="The camera's reading is taken as given.",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "Dark current depends on sensor temperature, so it helps explain noise "
            "differences between frames."
        ),
    ),
    MetricSpec(
        name=FRAME_FOCUSER_TEMPERATURE,
        definition_version=1,
        unit="degC",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning="The focuser temperature recorded when the frame was taken.",
        decisions=(),
        estimator="Read from the FITS header card FOCUSTEM; not computed from pixels.",
        sample_definition="One header value per frame.",
        band="not applicable",
        computed_by="pipelines.shared.frame_scanning.create_frame_record_from_fits",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="The focuser's reading is taken as given.",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "Focus drifts with temperature, so it helps explain a change in star width through a night."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_WIDTH_MOFFAT_PX,
        definition_version=1,
        unit="px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=(
            "The width (FWHM) of stars spread over the whole frame, from pixel-integrated Moffat fits. "
            "A trailed or out-of-focus frame reads wide."
        ),
        decisions=("focus", "guiding"),
        estimator=(
            "Median over the stars of the full width at half maximum of an elliptical Moffat profile "
            "integrated over each pixel (3 x 3 sub-samples), fitted in a 21 x 21 px box with a free "
            "constant background. A first pass fits the power index beta for each star; the median beta "
            "is then held fixed for a second pass. A fit is kept with R squared of at least 0.8, a "
            "center within 2.5 px of the detected one, and a width between 0.8 and 25 px. The star's "
            "width is the geometric mean of the FWHM along its two axes."
        ),
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_star_profiles",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Standard deviation of the median over 1000 bootstrap resamples of the stars (fixed seed). "
            "It covers the star-to-star scatter, not a bias of the Moffat model."
        ),
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "The width in pixels is stored beside the arcsecond value so a wrong pixel scale can be "
            "told from a change in the stars."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        definition_version=1,
        unit="arcsec",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=(
            "The width (FWHM) of stars spread over the whole frame, in arcseconds, from "
            "pixel-integrated Moffat fits. A trailed or out-of-focus frame reads wide."
        ),
        decisions=("focus", "guiding", "seeing"),
        estimator=(
            "The pixel width of `frame.star_width_moffat_px` times the frame's pixel scale "
            "(`FrameRecord.pixel_scale_arcsec`, which comes from the header's SCALE card or the focal "
            "length and pixel size). The measurement's reason names the scale used."
        ),
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_star_profiles",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "The bootstrap error of the pixel width times the pixel scale. The pixel scale is taken as exact."
        ),
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "The efficiency model's seeing factor and the focus and guiding advice compare star widths "
            "between frames and nights, which needs a unit that does not depend on the pixel scale."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_ELONGATION,
        definition_version=1,
        unit="",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning="How stretched the stars are: the long axis divided by the short axis (1 is round).",
        decisions=("guiding", "tracking"),
        estimator=(
            "Median over the stars of the square root of the ratio of the larger to the smaller "
            "eigenvalue of the star's flux-weighted second-moment matrix. The pixels are weighted by "
            "their light above the fitted background, inside an ellipse of 1.5 FWHM along each fitted "
            "axis (at least 3 px). The ratio is at least 1; noise pushes a round star slightly above 1."
        ),
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_star_profiles",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method="Standard deviation of the median over 1000 bootstrap resamples of the stars.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "Stars stretched by the mount, the guiding or the optics lose resolution and depth, and the "
            "guiding advice reads the stretch. Today's width keeps a stretched star at its narrow axis, "
            "so only this metric shows the stretch."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_ELONGATION_ANGLE_DEG,
        definition_version=1,
        unit="deg",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning=(
            "The direction of the stars' long axis, in degrees from the image x axis toward the y axis "
            "(0 up to but not including 180). It means little when the stars are round."
        ),
        decisions=("guiding", "tracking"),
        estimator=(
            "Circular mean of the stars' long-axis angles, where an angle and the same angle plus 180 "
            "degrees are one direction (the angles are doubled, averaged as unit vectors, and halved). "
            "Each angle comes from the same second moments as `frame.star_elongation`."
        ),
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_star_profiles",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Standard deviation of the circular mean over 1000 bootstrap resamples of the stars. The "
            "measurement's reason gives the mean resultant length (0 to 1); a low value means the "
            "stars do not share a direction."
        ),
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "The direction of the stretch separates a right-ascension drift from a declination one, "
            "which explains an elongation value."
        ),
    ),
    MetricSpec(
        name=FRAME_SKY_RATE_E_PER_S_PER_PX,
        definition_version=1,
        unit="e-/s/px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning="How bright the sky background is, in electrons per second per pixel.",
        decisions=("efficiency", "exposure length"),
        estimator=(
            "(sigma-clipped median of the plane, 3 sigma, minus the bias level of the frame's setting) "
            "times the gain of the frame's setting in electrons per ADU, divided by the exposure time. "
            "The gain comes from the camera profile entry that matches the frame's gain setting, offset "
            "and binning exactly. The bias level must be given by the caller: the raw frame holds it "
            "inside its pixels and does not state it. Stored as assumed when the gain is not measured."
        ),
        sample_definition="Every pixel of the plane.",
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_sky_electrons",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "The efficiency model's sky factor and the noise regime need the sky in electrons, which "
            "does not depend on the camera's gain setting."
        ),
    ),
    MetricSpec(
        name=FRAME_SKY_NOISE_E,
        definition_version=1,
        unit="e-",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning="The noise of the sky background in one pixel of one exposure, in electrons.",
        decisions=("exposure length", "noise regime"),
        estimator=(
            "The square root of the sky electrons in one pixel of one exposure (photon counting "
            "noise): the square root of (background minus bias level, in ADU) times the gain. It does "
            "not include the read noise, which is stored apart as `frame.read_noise_e`; the ratio of the "
            "two is never stored."
        ),
        sample_definition="Every pixel of the plane (through its sigma-clipped median).",
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_sky_electrons",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "Sky noise against read noise decides whether an exposure is long enough for the sky to dominate."
        ),
    ),
    MetricSpec(
        name=FRAME_READ_NOISE_E,
        definition_version=1,
        unit="e-",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning="The camera's read noise at the frame's setting, in electrons (RMS).",
        decisions=("exposure length", "noise regime"),
        estimator=(
            "Read from the camera profile entry that matches the frame's gain setting, offset and "
            "binning exactly; not computed from the frame's pixels. Stored as assumed unless the "
            "profile's value was measured."
        ),
        sample_definition="One profile value per frame.",
        band="not applicable",
        computed_by="pipelines.shared.quality.frame_measurement.look_up_frame_sensor_noise",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="The profile value is taken as given.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "Read noise against sky noise decides whether an exposure is long enough for the sky to dominate."
        ),
    ),
)
