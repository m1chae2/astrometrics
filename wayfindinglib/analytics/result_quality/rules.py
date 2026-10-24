"""Purpose: The table of rules that turn measurements into statements.

Description: Every warning, recommendation and constraint that the library
makes about a target's data has one row in `RESULT_QUALITY_RULES`. A row
names the metrics it reads, each pinned to one definition version, so a
change in what a metric means makes the rule say "remeasure" instead of
reading numbers of another meaning. A row also names its action class (the
kind of change it asks for), its limits, the validation register entry
behind it, and a wording template.

Two kinds of row exist:

* A plain row (``computed_by`` is ``None``) compares a measurement with a
  limit. `judge_results` in ``judge_results.py`` evaluates it.
* A row with ``computed_by`` set is made by another module, named in that
  field, because the statement needs more than one comparison (a forecast,
  a fit, an efficiency, a join with the calibration library). The row is
  still here so the whole rule set can be read, tested and audited in one
  place.

`DEFERRED_RULES` lists the items of the decision catalog (plan section
6.6) that have no row yet, each with the reason. A rule never reads an
exploratory metric (decision S12), so items whose metrics are exploratory
wait there until the metric is promoted.

The library never advises hardware (decision 11). When only new hardware
would help, a row makes a ``constraint`` statement and proposes nothing.

Every number below carries the place it comes from. Numbers that are design
estimates are listed in the validation register (``WAY-D05`` to ``WAY-D12``)
with a way to measure them on your own data.
"""

from pydantic import BaseModel, ConfigDict

from astrometricslib import LimitTier, QualityLimit
from wayfindinglib.models.quality.rule import MetricRequirement, QualityRule
from wayfindinglib.models.quality.statements import ActionClass, Confidence, Severity, StatementKind

# Metric names the rules read. Each is a registered metric name; the
# conformance test (`wayfindinglib/test/test_quality_rule_conformance.py`)
# checks that each exists at the pinned version.
PEAK_FRACTION_METRIC = "stacking.group_brightest_star_peak_fraction"
SKY_NOISE_METRIC = "frame.sky_noise_e"
READ_NOISE_METRIC = "frame.read_noise_e"
SKY_RATE_METRIC = "frame.sky_rate_e_per_s_per_px"
ELONGATION_METRIC = "frame.star_elongation"
RAW_FRAME_CLIPPING_METRIC = "stacking.gate_raw_frame_clipping"
FLAT_NOISE_IN_APERTURE_METRIC = "stacking.flat_noise_in_aperture_stack_unit"
EMPTY_APERTURE_NOISE_METRIC = "stacking.empty_aperture_noise_stack_unit"
FLAT_NOISE_FRACTION_METRIC = "stacking.flat_noise_fraction"
FLAT_FRAME_COUNT_METRIC = "stacking.flat_frame_count"
EFFECTIVE_TIME_METRIC = "stacking.effective_time_s"
STAR_WIDTH_PIXELS_METRIC = "frame.star_width_moffat_px"
STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC = "frame.star_width_effective_arcsec"
FOCUSER_TEMPERATURE_METRIC = "frame.focuser_temperature_degc"
STAR_WIDTH_MOFFAT_ARCSEC_METRIC = "frame.star_width_moffat_arcsec"
QUARANTINE_ROUNDNESS_METRIC = "frame.quarantine_roundness"
QUARANTINE_SKY_MEDIAN_METRIC = "frame.quarantine_sky_median_adu"
DETECTED_REGION_COUNT_METRIC = "frame.detected_region_count"

# The brightest star's peak, as a fraction of the room between the sky and the
# ceiling, at which the star clips. 1.0 is the ceiling by definition, so the
# value needs no measurement (tier: geometry).
CLIPPING_PEAK_FRACTION = 1.0

# The fraction of the room that a clipped exposure is scaled back to. It is
# the value `TARGET_PEAK_FRACTION` that astrometricslib used for its old
# "recommended exposure" (0.8: bright enough for a good signal, with a margin
# of 20% for a change in seeing or sky). It is a design estimate, register
# entry WAY-D05. Measure it by taking one star at several exposures and
# finding the one where the peak sits at 0.8 of the room.
TARGET_PEAK_FRACTION = 0.8

# The least ratio of the sky's photon noise to the camera's read noise in one
# pixel. At 3 the read noise adds 1/9 to the variance, so the total noise is
# 5.4% above the sky noise alone, an exposure where the sky dominates (the
# same derivation as the efficiency batch: eta of the read-noise factor at
# least 0.9 means sky variance at least 9 times the read-noise variance).
# Design estimate, register entry WAY-D06.
MINIMUM_SKY_TO_READ_NOISE_RATIO = 3.0

# The most stretched the stars may be (long axis divided by short axis; 1 is
# round) before the rule warns. Noise alone pushes the value of a round star
# a little above 1, most for faint stars. 1.2 is the point where a star is
# plainly oval to the eye. Design estimate, register entry WAY-D07.
TRAILED_STAR_ELONGATION_LIMIT = 1.2

# The share of the stack's noise variance that the master flat may add before
# the rule warns. 0.03 is the figure of STK-05: a flat that adds under 3% to
# the variance leaves a fixed pattern too small to see. Design estimate (the
# stack-level reading is new), register entry WAY-D08.
FLAT_VARIANCE_SHARE_LIMIT = 0.03

# The share of variance the master flat should add after the extra flats are
# taken: one third of the limit, so the fix leaves a clear margin. Design
# estimate, register entry WAY-D08.
FLAT_VARIANCE_SHARE_TARGET = 0.01

# The relative noise of a master flat above which the stacker blurs the flat.
# It equals astrometricslib's `MAXIMUM_FLAT_NOISE_FRACTION` (0.5%), derived
# in STK-05: a flat at 0.5% adds under 3% to the variance of a typical stack.
MASTER_FLAT_NOISE_FRACTION_LIMIT = 0.005


def _design_limit(
    value: float, unit: str, derivation: str, register_entry: str, sample_count: int | None = None
) -> QualityLimit:
    """Build a design-estimate limit.

    Parameters
    ----------
    value : `float`
        The limit.
    unit : `str`
        Its unit.
    derivation : `str`
        How the value was found, in plain words.
    register_entry : `str`
        The design-estimate row that lists it, such as ``"WAY-D05"``.
    sample_count : `int`, optional
        The number of samples behind it (``None`` for a reasoned choice).

    Returns
    -------
    limit : `QualityLimit`
        A limit of tier ``design_estimate``.
    """
    return QualityLimit(
        value=value,
        unit=unit,
        tier=LimitTier.DESIGN_ESTIMATE,
        derivation=derivation,
        sample_count=sample_count,
        register_entry=register_entry,
    )


class DeferredRule(BaseModel):
    """An item of the decision catalog that has no rule yet.

    Parameters
    ----------
    name : `str`
        A short stable name, such as ``"polar_alignment"``.
    catalog_item : `str`
        The item's name in the decision catalog (plan section 6.6).
    reason : `str`
        Why there is no rule yet, in plain words.
    unblocked_by : `str`
        What has to exist before the rule can be written.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    catalog_item: str
    reason: str
    unblocked_by: str


RESULT_QUALITY_RULES: tuple[QualityRule, ...] = (
    QualityRule(
        name="overexposed_setting",
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.CAPTURE_SETTINGS,
        severity=Severity.WARNING,
        requires=[MetricRequirement(name=PEAK_FRACTION_METRIC, definition_version=1)],
        limits=[
            QualityLimit(
                value=CLIPPING_PEAK_FRACTION,
                unit="fraction",
                tier=LimitTier.GEOMETRY,
                derivation=(
                    "A peak at the ceiling is clipped: 1 is the room between the sky and the "
                    "camera's ceiling, by definition."
                ),
            ),
            _design_limit(
                TARGET_PEAK_FRACTION,
                "fraction",
                (
                    "The value astrometricslib used for its recommended exposure before issue 94: "
                    "bright enough for a good signal, with a margin of 20% for seeing and sky changes."
                ),
                "WAY-D05",
            ),
        ],
        register_entry="WAY-D05",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "{exposure_text} frames clip the brightest star: its peak is {peak_fraction:.1f} times the "
            "room below the ceiling. {advice_text}"
        ),
        purpose="Clipped stars lose their brightness, so photometry and star colors from them are wrong.",
    ),
    QualityRule(
        name="underexposed_setting",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.CAPTURE_SETTINGS,
        requires=[
            MetricRequirement(name=SKY_NOISE_METRIC, definition_version=2),
            MetricRequirement(name=READ_NOISE_METRIC, definition_version=1),
        ],
        limits=[
            _design_limit(
                MINIMUM_SKY_TO_READ_NOISE_RATIO,
                "",
                (
                    "At a sky noise of 3 times the read noise, the read noise adds 1/9 to the "
                    "variance, so the noise is 5.4% above the sky noise alone."
                ),
                "WAY-D06",
            )
        ],
        register_entry="WAY-D06",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "{exposure_text} frames are limited by read noise: the sky noise is {ratio:.1f} times the "
            "read noise. {advice_text}"
        ),
        purpose=(
            "Short frames spend part of their noise on the camera instead of the sky, so more of "
            "them are needed."
        ),
    ),
    QualityRule(
        name="offset_too_low",
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.CAPTURE_SETTINGS,
        severity=Severity.WARNING,
        requires=[MetricRequirement(name=RAW_FRAME_CLIPPING_METRIC, definition_version=1)],
        limits=[],
        register_entry="STK-22",
        confidence=Confidence.PARTLY_MEASURED,
        wording=(
            "Raise the offset: {zero_percent:.0f}% of the pixels read exactly zero in the worst frame group."
        ),
        purpose="Pixels clipped at zero read the faint sky too high and make the measured noise too low.",
    ),
    QualityRule(
        name="trailed_stars",
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.SESSION_PROCEDURE,
        severity=Severity.ADVICE,
        requires=[MetricRequirement(name=ELONGATION_METRIC, definition_version=1)],
        limits=[
            _design_limit(
                TRAILED_STAR_ELONGATION_LIMIT,
                "",
                "A star whose long axis is 20% longer than its short axis is plainly oval to the eye.",
                "WAY-D07",
            )
        ],
        register_entry="WAY-D07",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "Stars are {elongation:.2f} times longer than they are wide ({exposure_text} frames). "
            "Check the guiding."
        ),
        purpose="Stretched stars lose sharpness and depth, and the cause is usually guiding or tracking.",
    ),
    QualityRule(
        name="flat_noise_limits_stack",
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.CALIBRATION,
        severity=Severity.ADVICE,
        requires=[
            MetricRequirement(name=FLAT_NOISE_IN_APERTURE_METRIC, definition_version=1),
            MetricRequirement(name=EMPTY_APERTURE_NOISE_METRIC, definition_version=3),
            MetricRequirement(name=FLAT_FRAME_COUNT_METRIC, definition_version=1),
        ],
        limits=[
            _design_limit(
                FLAT_VARIANCE_SHARE_LIMIT,
                "fraction",
                "STK-05: a flat that adds under 3% to the stack's noise variance leaves no visible pattern.",
                "WAY-D08",
            ),
            _design_limit(
                FLAT_VARIANCE_SHARE_TARGET,
                "fraction",
                "One third of the limit, so the fix leaves a clear margin.",
                "WAY-D08",
            ),
        ],
        register_entry="WAY-D08",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "The master flat adds {noise_percent:.0f}% to the stack's noise ({variance_percent:.1f}% of its "
            "variance). {advice_text}"
        ),
        purpose=(
            "A noisy master flat puts its noise into every frame as a fixed pattern that stacking "
            "cannot average out."
        ),
    ),
    QualityRule(
        name="master_flat_noisy",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.CALIBRATION,
        requires=[
            MetricRequirement(name=FLAT_NOISE_FRACTION_METRIC, definition_version=1),
            MetricRequirement(name=FLAT_FRAME_COUNT_METRIC, definition_version=1),
        ],
        limits=[
            QualityLimit(
                value=MASTER_FLAT_NOISE_FRACTION_LIMIT,
                unit="fraction",
                tier=LimitTier.VALIDATED_DATASET,
                derivation=(
                    "STK-05: a master flat at 0.5% relative noise adds under 3% to the variance of a "
                    "typical stack."
                ),
                register_entry="STK-05",
            )
        ],
        register_entry="STK-05",
        confidence=Confidence.PARTLY_MEASURED,
        wording=(
            "The master flat's noise is {noise_percent:.2f}%, above the {limit_percent:.1f}% limit. "
            "{advice_text}"
        ),
        purpose=(
            "The stacker blurs a flat that is noisier than the limit, which loses dust and vignetting detail."
        ),
    ),
    QualityRule(
        name="frames_lost",
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.DATA_SELECTION,
        severity=Severity.ADVICE,
        # The stacker's frame quarantine moves a frame out of the stack when
        # its stars are few (cloud) or smeared (trailing). The report cites
        # the stored quarantine rows of each set-aside frame as the evidence
        # of its statement.
        requires=[
            MetricRequirement(name=QUARANTINE_ROUNDNESS_METRIC, definition_version=1),
            MetricRequirement(name=QUARANTINE_SKY_MEDIAN_METRIC, definition_version=1),
            MetricRequirement(name=DETECTED_REGION_COUNT_METRIC, definition_version=1),
        ],
        limits=[],
        register_entry="WAY-D60",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="{lost_count} of {frame_count} frames were set aside ({reason_text}).",
        purpose="Frames set aside for cloud or trailing are time that did not reach the stack.",
        computed_by="report_rules",
    ),
    QualityRule(
        name="more_frames",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.CAPTURE_SETTINGS,
        requires=[
            MetricRequirement(name=EMPTY_APERTURE_NOISE_METRIC, definition_version=3),
            MetricRequirement(name=EFFECTIVE_TIME_METRIC, definition_version=1),
            MetricRequirement(name=SKY_RATE_METRIC, definition_version=2),
            MetricRequirement(name=READ_NOISE_METRIC, definition_version=1),
        ],
        limits=[],
        register_entry="WAY-D61",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "Take {frame_count} more at {exposure_text} (about {wall_clock_text}) to cut the noise "
            "by about {noise_percent:.0f}%."
        ),
        purpose=(
            "Noise falls as the square root of the effective time, so the next step shows how much "
            "more data is worth."
        ),
        computed_by="report_rules",
    ),
    QualityRule(
        name="more_frames_will_not_help",
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.CAPTURE_SETTINGS,
        severity=Severity.ADVICE,
        requires=[
            MetricRequirement(name=EMPTY_APERTURE_NOISE_METRIC, definition_version=3),
            MetricRequirement(name=EFFECTIVE_TIME_METRIC, definition_version=1),
        ],
        limits=[],
        register_entry="WAY-D62",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="Noise stopped falling after about {effective_time_text}. {advice_text}",
        purpose=(
            "A noise floor means more frames do not help until the cause (flats, dither, a fixed "
            "pattern) is fixed."
        ),
        computed_by="report_rules",
    ),
    QualityRule(
        name="calibration_gap",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.CALIBRATION,
        requires=[],
        limits=[],
        register_entry="WAY-D63",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="Take {frame_type_text} at {setting_text}: none in the library matches.",
        purpose=(
            "A calibration frame taken at other settings does not remove the fixed pattern of the "
            "light frames."
        ),
        computed_by="report_rules",
    ),
    QualityRule(
        name="focus_drift",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.SESSION_PROCEDURE,
        requires=[
            MetricRequirement(name=STAR_WIDTH_PIXELS_METRIC, definition_version=1),
            MetricRequirement(name=FOCUSER_TEMPERATURE_METRIC, definition_version=1),
        ],
        limits=[],
        register_entry="WAY-D09",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "Stars widen by {slope:.2f} px per degree C. To keep the width within {allowed_percent:.0f}% of "
            "best focus, refocus every {interval:.1f} degrees C (give or take {tolerance:.1f})."
        ),
        purpose=(
            "Focus moves with temperature, so a refocus interval keeps the stars sharp through the night."
        ),
        computed_by="focus_drift",
    ),
    QualityRule(
        name="sky_cost",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.SCHEDULING,
        requires=[MetricRequirement(name=SKY_RATE_METRIC, definition_version=2)],
        limits=[],
        register_entry="WAY-D64",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "Frames with a bright sky ({condition_text}) lost about {loss_percent:.0f}% "
            "(model-derived). {advice_text}"
        ),
        purpose=(
            "A bright sky (Moon, twilight, light pollution) raises the noise, so the sky's cost "
            "says which nights to avoid."
        ),
        computed_by="report_rules",
    ),
    QualityRule(
        name="altitude_cost",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.SCHEDULING,
        requires=[
            MetricRequirement(name=STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, definition_version=1),
            MetricRequirement(name=SKY_RATE_METRIC, definition_version=2),
        ],
        limits=[],
        register_entry="WAY-D65",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "Frames below {altitude_degrees:.0f} degrees lost {loss_percent:.0f}%. Image above "
            "{advice_altitude_degrees:.0f} degrees."
        ),
        purpose="Low targets are seen through more air, which widens stars and brightens the sky.",
        computed_by="report_rules",
    ),
    QualityRule(
        name="sharpness_tradeoff",
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.DATA_SELECTION,
        requires=[MetricRequirement(name=STAR_WIDTH_MOFFAT_ARCSEC_METRIC, definition_version=1)],
        limits=[],
        register_entry="WAY-D66",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "Keeping the sharpest {keep_percent:.0f}% of frames sharpens stars by {sharper_percent:.0f}% "
            "and raises the noise by {noisier_percent:.0f}%."
        ),
        purpose=(
            "Stacking only the sharpest frames trades noise for sharper stars, so the user can choose "
            "which matters more for the target."
        ),
        computed_by="report_rules",
    ),
    QualityRule(
        name="seeing_limits_resolution",
        statement_kind=StatementKind.CONSTRAINT,
        requires=[MetricRequirement(name=STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, definition_version=1)],
        limits=[],
        register_entry="WAY-D12",
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="Seeing limits the detail on this target; more time adds depth, not detail.",
        purpose=(
            "When the star width is already at the seeing limit, only better nights (not more "
            "frames) add detail. No advice follows."
        ),
        computed_by="efficiency",
    ),
)

DEFERRED_RULES: tuple[DeferredRule, ...] = (
    DeferredRule(
        name="guiding_limits_exposure",
        catalog_item="Guiding limits exposure",
        reason=(
            "The share of frames that stay sharp at each exposure comes from the guiding analysis of "
            "Control (session_analysis), which Planning may not import. The guide exposure ladder works "
            "on guide-camera test frames, not on the stored guide logs, so it cannot give an exposure "
            "limit for a capture setting in the target report."
        ),
        unblocked_by=(
            "A Foundation function that turns the stored guide records into the longest sharp exposure, "
            "with a register entry."
        ),
    ),
    DeferredRule(
        name="best_window",
        catalog_item="Best window",
        reason=(
            "The best nights need a model that combines each night's visibility of the target, the Moon "
            "and the sky history. That nights-by-visibility model is not built. Tonight's ranking "
            "(get_advisory, kind tonight) covers tonight only."
        ),
        unblocked_by=(
            "A nights, visibility and Moon model that scores the coming nights, with a register entry."
        ),
    ),
    DeferredRule(
        name="polar_alignment",
        catalog_item="Polar alignment",
        reason=(
            "Its input, the field rotation of each frame (stacking.frame_registration_rotation_deg), is an "
            "exploratory metric (STK-43), and an exploratory metric never drives a rule (decision S12). "
            "The polar error is shown to the user as an observing value, labeled exploratory."
        ),
        unblocked_by="Promoting the field rotation metric after the review of STK-43.",
    ),
    DeferredRule(
        name="transparency",
        catalog_item="Transparency (sky clarity)",
        reason=(
            "A transparency rule needs a photometric zero point that is not exploratory. The catalog zero "
            "points (photometry.catalog_zero_point_*) are exploratory (PHO-15), and no night is known to be "
            "photometric (decision S12)."
        ),
        unblocked_by="Promoting a zero point metric after the review of PHO-15.",
    ),
    DeferredRule(
        name="period_alias",
        catalog_item="Period alias",
        reason=(
            "The window-alias metrics (photometry.window_alias_*, PHO-18) exist, but no model says which "
            "extra nights remove an alias, so a rule could not name the nights to add."
        ),
        unblocked_by="A model of how added nights change the window function, with a register entry.",
    ),
    DeferredRule(
        name="asteroid_linking",
        catalog_item="Asteroid linking",
        reason="No metric of the arc length or the rate of motion of a candidate is registered.",
        unblocked_by="Registering the arc length and the motion rate in the asteroid detection pipeline.",
    ),
    DeferredRule(
        name="spectrum_snr",
        catalog_item="Spectrum SNR",
        reason=(
            "The class gap against the signal-to-noise ratio has not been calibrated (plan section 6.4), "
            "so the rule could not say how many more spectra reach a subtype."
        ),
        unblocked_by="Degrading high-SNR spectra with added noise to measure the class gap against SNR.",
    ),
    DeferredRule(
        name="photometry_precision",
        catalog_item="Photometry precision (natural goal)",
        reason=(
            "The natural finish line of photometry is a precision fine enough to detect the target's known "
            "variation. The amplitude is stored (photometry.target_catalog_amplitude_mag) and so is the "
            "detectable amplitude of a run, but no calibration turns the gap into a number of frames."
        ),
        unblocked_by="Calibrating the detectable amplitude against frame count on real photometry runs.",
    ),
    DeferredRule(
        name="tilt",
        catalog_item="Tilt",
        reason=(
            "Its inputs, the star widths at the center and the four corners (frame.star_width_center_px "
            "and the corner metrics), are exploratory metrics (AST-18), and an exploratory metric never "
            "drives a rule (decision S12)."
        ),
        unblocked_by="Promoting the corner widths after the review of AST-18.",
    ),
)


def rule_by_name(name: str) -> QualityRule:
    """Find a rule of the table by its name.

    Parameters
    ----------
    name : `str`
        The rule's name.

    Returns
    -------
    rule : `QualityRule`
        The rule with that name.

    Raises
    ------
    KeyError
        If the table has no rule of that name.
    """
    for rule in RESULT_QUALITY_RULES:
        if rule.name == name:
            return rule
    raise KeyError(f"No quality rule is named {name!r}.")
