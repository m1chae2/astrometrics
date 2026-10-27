"""The frame metrics the library already measures, for the registry.

Each `MetricSpec` here describes one per-frame measurement that existing code
computes today. The name says which estimator it is, so a number from another
estimator never shares a name with it. Nothing here measures anything: the
`computed_by` field points at the function that does.

The registry (`metric_registry.py`) collects these with the metrics that each
pipeline declares in its own ``metrics.py``.
"""

import datetime

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
FRAME_STAR_WIDTH_EFFECTIVE_PX = "frame.star_width_effective_px"
FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC = "frame.star_width_effective_arcsec"
FRAME_STAR_WIDTH_CENTER_PX = "frame.star_width_center_px"
# The names of the five regions of the field. A corner is named by whether it
# lies at the low or the high end of the x axis (the columns) and of the y axis
# (the array rows), counted from the first pixel: x is the column and y is the
# array row, with row 0 first. A viewer that draws row 0 at the bottom (as
# Matplotlib's lower origin does) shows the same corner at the other side, so
# the name does not depend on how an image is displayed.
REGION_CENTER = "center"
REGION_XLOW_YLOW = "xlow_ylow"
REGION_XHIGH_YLOW = "xhigh_ylow"
REGION_XLOW_YHIGH = "xlow_yhigh"
REGION_XHIGH_YHIGH = "xhigh_yhigh"

# The center and the four corner regions of the field, by region name.
FRAME_STAR_WIDTH_REGION_PX = {
    REGION_CENTER: FRAME_STAR_WIDTH_CENTER_PX,
    REGION_XLOW_YLOW: "frame.star_width_corner_xlow_ylow_px",
    REGION_XHIGH_YLOW: "frame.star_width_corner_xhigh_ylow_px",
    REGION_XLOW_YHIGH: "frame.star_width_corner_xlow_yhigh_px",
    REGION_XHIGH_YHIGH: "frame.star_width_corner_xhigh_yhigh_px",
}
FRAME_SKY_RATE_E_PER_S_PER_PX = "frame.sky_rate_e_per_s_per_px"
FRAME_SKY_NOISE_E = "frame.sky_noise_e"
FRAME_READ_NOISE_E = "frame.read_noise_e"

_FRAME_ONLY = (SubjectLevel.FRAME,)

# The band text of the metrics that are measured on each color plane of a frame
# stored as planes, and on a single plane otherwise.
_PER_PLANE_BAND = (
    "The frame's own filter. A frame stored as color planes is measured plane by plane, with the plane "
    "named on the subject. A raw color mosaic is measured as it is, with its color pixels mixed; the "
    "star shape metrics of a mosaic are the exception, see the sample definition."
)

# How the star metrics choose and fit their stars, shared by the four star
# declarations so they cannot drift apart.
_MOFFAT_SAMPLE = (
    "Up to about 196 stars, one from each cell of a 14 x 14 grid laid over the plane (the brightest "
    "detected star of the cell that has no other detected source within 14 px, a whole 21 x 21 px fit "
    "box inside the frame, and no pixel in the box at or above 0.9 of the camera's usable top level). "
    "When a cell's star fails the fit, up to two fainter stars of the cell are tried. No star is "
    "dropped for being stretched, but the cuts above fall hardest on the most trailed stars (a "
    "trailed star reaches a neighbor and the saturation margin sooner), so the reason of each row "
    "states how many detected sources each cut refused (at the frame edge, with a neighbor, near "
    "saturation, and by the fit). At least 5 stars are needed. On a raw color mosaic every 2 x 2 "
    "cell (one red, two green and one blue pixel) is first summed into one super-pixel, and the "
    "stars are found and fitted on those, so the color filter pattern does not bend the second "
    "moments or the angle; the widths are multiplied by 2 to give original pixels, and the "
    "elongation and angle need no factor. The sum rounds the stars a little, so a mosaic's "
    "elongation reads a few percent low (1.39 for a true 1.5 at a width of 4 px)."
)

# The review date of the exploratory field-shape metrics: six months after they
# were first measured (2026-10-23), the plan's default.
_FIELD_SHAPE_REVIEW_DATE = datetime.date(2027, 4, 30)

_EFFECTIVE_WIDTH_ESTIMATOR = (
    "sqrt(FWHM^2 + c p^2) with FWHM the frame's median Moffat width (`frame.star_width_moffat_px`), "
    "p one original pixel and c = 2 ln 2 / 3 = 0.4621. A star is the sky's image smeared over the "
    "pixel's square sensitive area, which adds a variance p^2 / 12 along each axis; for a Gaussian "
    "core, FWHM^2 is 8 ln 2 times the variance, which gives c. The Moffat fit integrates its profile "
    "over each pixel, so its width is the width before that smearing and the term is added back. c is "
    "theory, not fitted (user decision of 2026-10-23). It assumes a Gaussian core; a Moffat core with "
    "wings differs a little, and a star near the Nyquist limit (about 2 px) is where the pixel term "
    "matters most. For a raw color mosaic the fit runs on 2 x 2 super-pixels, but p is still the "
    "original pixel of the frame's pixel scale, not the super-pixel and not the 2 px pitch of one "
    "color."
)


_REGION_SAMPLE = (
    "The stars of `frame.star_width_moffat_px` that lie in one of five equal regions of the field, "
    "each a block of 4 x 4 of the 14 x 14 grid cells the stars are chosen from (8 percent of the "
    "frame): the center, and the four corners. A star between the regions is left out. Because a "
    "star's fit box must lie inside the frame, no star sits within 10 px of the edge. At least 5 "
    "stars are needed in a region. The reason of each row gives the count. For a raw color mosaic "
    "the positions are given back in original pixels."
)


def _region_spec(region: str, meaning: str) -> MetricSpec:
    """Declare the star width of one region of the field.

    Parameters
    ----------
    region : `str`
        A key of `FRAME_STAR_WIDTH_REGION_PX`.
    meaning : `str`
        One line saying which region it is.

    Returns
    -------
    spec : `MetricSpec`
        The declaration, which differs between regions only in its name and
        meaning.
    """
    return MetricSpec(
        name=FRAME_STAR_WIDTH_REGION_PX[region],
        definition_version=1,
        unit="px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=meaning,
        decisions=("tilt", "field curvature", "collimation"),
        estimator=(
            "Median over the stars in the region of the pixel-integrated Moffat FWHM, the same fit and "
            "the same per-star width as `frame.star_width_moffat_px` (no second fit). The ratio of a "
            "corner to the center is not stored; the parts are."
        ),
        sample_definition=_REGION_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_field_region_widths",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Standard deviation of the median over 1000 bootstrap resamples of the region's stars (fixed "
            "seed). It covers the star-to-star scatter, not a bias of the Moffat model."
        ),
        register_entry="AST-18",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=(
            "Do the star widths in the four corners differ from the center by more than their "
            "bootstrap errors, and does that pattern stay the same from night to night (tilt, field "
            "curvature or collimation) rather than follow the seeing?"
        ),
        review_date=_FIELD_SHAPE_REVIEW_DATE,
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
            "background removal and a 1.5 px Gaussian smooth, from `detect_stars`."
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
        register_entry="AST-17",
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
        register_entry="AST-17",
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
        meaning=(
            "How stretched the stars are: the long axis divided by the short axis (1 is round). "
            "Noise pushes the value of a round star above 1, most for faint stars."
        ),
        decisions=("guiding", "tracking"),
        estimator=(
            "Median over the stars of the square root of the ratio of the larger to the smaller "
            "eigenvalue of the star's flux-weighted second-moment matrix. The pixels are weighted by "
            "their light above the fitted background, inside an ellipse of 1.5 FWHM along each fitted "
            "axis (at least 3 px). The ratio is at least 1, so noise can only push it up: the measured "
            "axis ratio is biased above 1 for faint stars. On synthetic round stars (3 px wide, sky "
            "noise 29 ADU per pixel) the median reads 1.02 for stars with peaks near 14000 ADU, 1.03 "
            "near 4000 ADU, and 1.05 for stars with peaks of about 150 to 1400 ADU. Read a value "
            "within a few hundredths of 1 as round."
        ),
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_star_profiles",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method="Standard deviation of the median over 1000 bootstrap resamples of the stars.",
        register_entry="AST-17",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "Stars stretched by the mount, the guiding or the optics lose resolution and depth, and the "
            "guiding advice reads the stretch. The Gaussian width (`frame.star_width_gaussian_brightest_px`) "
            "falls back to the narrow axis of a stretched star and so hides a trail, and the Moffat width "
            "widens with it; this metric is the one that shows the stretch itself."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_ELONGATION_ANGLE_DEG,
        definition_version=2,
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
            "Half the angle of the average ellipticity vector of the stars. Each star gives the two "
            "components (Ixx - Iyy) / (Ixx + Iyy) and 2 Ixy / (Ixx + Iyy) of its second moments (the "
            "same moments as `frame.star_elongation`); their lengths grow with the stretch, so a round "
            "star, whose direction is only noise, counts for little and a stretched one for much. The "
            "averaged components are not angles, so an angle and the same angle plus 180 degrees are "
            "one direction by construction. Version 1 averaged the stars' angles with equal weight."
        ),
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.measure_star_profiles",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Standard deviation of the direction over 1000 bootstrap resamples of the stars. The "
            "measurement's reason gives the coherence (0 to 1: the length of the mean ellipticity "
            "vector divided by the mean ellipticity of the stars); a low value means the stars do not "
            "share a direction."
        ),
        register_entry="AST-17",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "The direction of the stretch separates a right-ascension drift from a declination one, "
            "which explains an elongation value."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_WIDTH_EFFECTIVE_PX,
        definition_version=1,
        unit="px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=(
            "The width (FWHM) of the stars as the sensor records them: the fitted width with the blur of "
            "the square pixel added. Model-derived."
        ),
        decisions=("seeing", "undersampling"),
        estimator=_EFFECTIVE_WIDTH_ESTIMATOR,
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.effective_star_width_px",
        uncertainty_kind=UncertaintyKind.MODEL_DERIVED,
        uncertainty_method=(
            "The bootstrap error of `frame.star_width_moffat_px` carried through the formula (times "
            "FWHM / FWHM_eff). The value adds a theoretical term, and that term's own error (the Gaussian "
            "core assumption) is not in the uncertainty. That error is a bias, not a scatter: the formula "
            "reads low for a star with wings, by about 3 percent at 2 px for a Moffat beta of 3 and up to "
            "6 percent at 1.6 px for a beta of 2 (validation register AST-18)."
        ),
        register_entry="AST-18",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "The efficiency model's seeing factor (phase 4) reads the width in this form, and the "
            "pixel scale it was recorded at travels with it."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
        definition_version=1,
        unit="arcsec",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=(
            "The effective star width in arcseconds: the fitted width with the blur of the square pixel "
            "added, times the frame's pixel scale. Model-derived."
        ),
        decisions=("seeing", "undersampling"),
        estimator=(
            "`frame.star_width_effective_px` times the frame's pixel scale "
            "(`FrameRecord.pixel_scale_arcsec`). " + _EFFECTIVE_WIDTH_ESTIMATOR
        ),
        sample_definition=_MOFFAT_SAMPLE,
        band=_PER_PLANE_BAND,
        computed_by="pipelines.shared.quality.frame_measurement.effective_star_width_px",
        uncertainty_kind=UncertaintyKind.MODEL_DERIVED,
        uncertainty_method=(
            "The error of `frame.star_width_effective_px` times the pixel scale. The pixel scale is taken "
            "as exact, and the theoretical term's own error is not included. That error is a bias of "
            "about -3 percent at 2 px for a Moffat beta of 3 and up to -6 percent at 1.6 px for a beta "
            "of 2 (validation register AST-18)."
        ),
        register_entry="AST-18",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "A width in arcseconds that includes the pixel's blur lets nights and cameras at different "
            "pixel scales be compared; the efficiency model's seeing factor (phase 4) will read it."
        ),
    ),
    MetricSpec(
        name=FRAME_SKY_RATE_E_PER_S_PER_PX,
        definition_version=3,
        unit="e-/s/px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=(
            "How bright the sky background is, in electrons per second per pixel. It includes the "
            "dark current and amp glow (light from the sensor's own electronics), because no dark "
            "frame is subtracted."
        ),
        decisions=("efficiency", "exposure length"),
        estimator=(
            "(sigma-clipped mean of the plane, clipped at 3 sigma with astropy's default of up to 5 "
            "passes, minus the bias level of the frame's setting) times the gain of the frame's setting "
            "in electrons per ADU, divided by the exposure time. The mean is used, not the median, "
            "because a 14-bit sensor stores steps of 4 ADU and a median takes only a few values (on 61 "
            "real frames the clipped median took 13 values). The bias level is found with the same "
            "estimator, from the bias frames of the exact setting, and the caller passes it: the raw "
            "frame holds it inside its pixels and does not state it. The gain comes from the camera "
            "profile entry that matches the frame's gain setting, offset and binning exactly. Stored as "
            "assumed when the gain is not measured, and when the frame records no sensor temperature "
            "(no CCD-TEMP card, as for a DSLR): that camera has no set-point cooling, so its dark "
            "current and amp glow, which are inside the value, are unknown and no dark was subtracted. "
            "Version 1 used the clipped median. Version 3 changed no formula: the offset fallback "
            "(decision D7) lets a frame at another offset use a profile entry, so frames that had no "
            "gain are measured again."
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
        definition_version=3,
        unit="e-",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning=(
            "The expected Poisson (photon counting) noise of the sky background in one pixel of one "
            "exposure, in electrons. It is computed from the sky level, not measured as the scatter "
            "of the pixels."
        ),
        decisions=("exposure length", "noise regime"),
        estimator=(
            "The square root of the sky electrons in one pixel of one exposure (photon counting "
            "noise): the square root of (clipped-mean background minus bias level, in ADU) times the "
            "gain, with the same estimators and the same assumed status as "
            "`frame.sky_rate_e_per_s_per_px`. "
            "Version 3 changed no formula: the offset fallback (decision D7) makes frames measurable that "
            "had no gain entry. It does not include the read noise, which is stored apart as "
            "`frame.read_noise_e`, or the dark current's own noise; the ratio of the two is never stored."
        ),
        sample_definition="Every pixel of the plane (through its sigma-clipped mean).",
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
        definition_version=2,
        unit="e-",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning="The camera's read noise at the frame's setting, in electrons (RMS).",
        decisions=("exposure length", "noise regime"),
        estimator=(
            "Read from the camera profile entry that matches the frame's gain setting, offset and "
            "binning; not computed from the frame's pixels. When no entry has the frame's offset and "
            "the frame is not clipped at zero, the entry of the same gain and binning at the nearest "
            "other offset is used and the source says 'offset fallback'. Stored as assumed unless the "
            "profile's value was measured. Version 2 changed no formula: the offset fallback (decision D7) "
            "makes frames measurable that had no entry."
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
    _region_spec(
        REGION_CENTER, "The median star width in the center of the field (the middle block of cells)."
    ),
    _region_spec(
        REGION_XLOW_YLOW,
        "The median star width in the corner at the low x (first columns) and low y (first array rows, "
        "row 0 first) end of the field.",
    ),
    _region_spec(
        REGION_XHIGH_YLOW,
        "The median star width in the corner at the high x (last columns) and low y (first array rows, "
        "row 0 first) end of the field.",
    ),
    _region_spec(
        REGION_XLOW_YHIGH,
        "The median star width in the corner at the low x (first columns) and high y (last array rows, "
        "row 0 first) end of the field.",
    ),
    _region_spec(
        REGION_XHIGH_YHIGH,
        "The median star width in the corner at the high x (last columns) and high y (last array rows, "
        "row 0 first) end of the field.",
    ),
)
