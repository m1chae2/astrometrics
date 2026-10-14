"""Purpose: Session Analysis Models.

Description: What the session-quality analysis reports for one observing
night. The analysis follows the same three stages the science library's
pipelines use:

* Pre-processing asks whether the data is good. It reports how complete and
  trustworthy the night's records are and lists problems in how the data was
  gathered.
* Processing measures what the night's data shows, such as the guiding error.
* Post-processing turns those measurements into recommendations, each tied to
  the evidence and the equipment-derived limit it rests on.

Every result is computed on demand from stored samples and the equipment's
current limits, and none is stored. A stored recommendation would go stale as
soon as the equipment, and so every limit, changed.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GUIDING_ANALYSIS_VERSION = "1.0.0"
"""Bumped whenever the guiding analysis changes what it measures or reports."""


class RecommendationSeverity(StrEnum):
    """How much attention a recommendation needs.

    `INFO` states a finding with nothing to do. `ADVICE` suggests a change
    that would improve results. `WARNING` names a problem that makes the
    night's data unreliable or the equipment misconfigured.
    """

    INFO = "info"
    ADVICE = "advice"
    WARNING = "warning"


class RecommendationKind(StrEnum):
    """What a recommendation is about."""

    INSUFFICIENT_DATA = "insufficient_data"
    RECALIBRATE_GUIDER = "recalibrate_guider"
    CHECK_GUIDE_SIGNAL = "check_guide_signal"
    UPDATE_GUIDE_OPTICS_CONFIGURATION = "update_guide_optics_configuration"
    GUIDING_ABOVE_LIMIT = "guiding_above_limit"
    GUIDING_WITHIN_LIMIT = "guiding_within_limit"
    CAPTURES_NOT_IN_LIBRARY = "captures_not_in_library"
    FRAMES_MISSING_MEASUREMENTS = "frames_missing_measurements"
    FREQUENT_ABORTED_CAPTURES = "frequent_aborted_captures"
    SENSOR_TEMPERATURE_DRIFT = "sensor_temperature_drift"
    SPECTRAL_STAR_CLIPPED = "spectral_star_clipped"
    STAR_WIDTH_ABOVE_BASELINE = "star_width_above_baseline"
    STARS_ELONGATED = "stars_elongated"
    CAPTURE_WITHIN_LIMITS = "capture_within_limits"
    SKY_REGION_POOR = "sky_region_poor"
    MINIMUM_ALTITUDE_SUGGESTED = "minimum_altitude_suggested"
    SKY_COVERAGE_GAP = "sky_coverage_gap"
    SKY_WITHIN_LIMITS = "sky_within_limits"
    RECURRING_ISSUE = "recurring_issue"
    EXPOSURE_LENGTH_LIMITED = "exposure_length_limited"


class Recommendation(BaseModel):
    """One thing the analysis recommends, with the reasons for it.

    Attributes
    ----------
    kind : `RecommendationKind`
        What the recommendation is about.
    severity : `RecommendationSeverity`
        How much attention it needs.
    message : `str`
        What was found and what to do, in plain language.
    evidence : `dict` [`str`, `float` or `str` or `None`]
        The measured values the recommendation rests on.
    compared_to : `dict` [`str`, `float` or `str` or `None`]
        The equipment-derived limits the evidence was compared with.
    confidence : `str`
        ``"high"``, ``"medium"`` or ``"low"``: how far the evidence alone
        supports the conclusion. A recommendation that depends on a proxy,
        or on limits from different equipment, is never ``"high"``.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: RecommendationKind
    severity: RecommendationSeverity
    message: str
    evidence: dict[str, float | str | None] = Field(default_factory=dict)
    compared_to: dict[str, float | str | None] = Field(default_factory=dict, alias="comparedTo")
    confidence: str


class GuidingInputQuality(BaseModel):
    """Pre-processing: how good the night's guiding data is.

    Attributes
    ----------
    runs : `int`
        Continuous stretches of guiding in the night.
    guided_seconds : `float`
        Total time spent guiding.
    frames_total : `int`
        Guide frames the guider took.
    frames_lost : `int`
        Guide frames the guider lost (for example a lost star).
    lost_fraction : `float` or `None`
        Share of frames lost, or `None` if no frames were recorded.
    lost_fraction_limit : `float` or `None`
        The share above which a night is unusual for this equipment, or
        `None` if not enough earlier nights exist to say.
    has_high_loss : `bool` or `None`
        Whether `lost_fraction` exceeds `lost_fraction_limit`.
    samples_analyzed : `int`
        Measured guide samples available.
    cadence_seconds : `float` or `None`
        Median time between guide samples.
    excursion_count : `int` or `None`
        Samples whose error exceeds the excursion limit: events such as a
        lock on the wrong star, not noise. `None` if the limit is unknown.
    excursion_fraction : `float` or `None`
        Share of samples that are excursions.
    excursion_fraction_limit : `float` or `None`
        The share above which a night is unusual for this equipment.
    has_frequent_excursions : `bool` or `None`
        Whether `excursion_fraction` exceeds `excursion_fraction_limit`.
    median_snr : `float` or `None`
        Median signal-to-noise ratio of the guide star.
    snr_limit : `float` or `None`
        The signal below which a night is unusual for this equipment.
    has_low_signal : `bool` or `None`
        Whether `median_snr` is below `snr_limit`.
    median_star_mass : `float` or `None`
        Median brightness of the guide star, in camera counts. Unlike the
        SNR it does not depend on the noise, so it shows directly how much
        light reached the guide camera.
    star_mass_limit : `float` or `None`
        The brightness below which a night is unusual for this equipment.
    typical_star_mass : `float` or `None`
        The equipment's typical guide-star brightness on earlier nights.
    has_dim_star : `bool` or `None`
        Whether `median_star_mass` is below `star_mass_limit`.
    typical_cadence_seconds : `float` or `None`
        The equipment's typical guide cycle. A guide star that is dim because
        the guide exposure was shortened has a shorter cycle than this.
    calibration_problems : `list` [`str`]
        Runs whose calibrated mount speed is impossible (above the sidereal
        rate, or not positive), each described in words.
    guide_scale_matches_configuration : `bool` or `None`
        Whether the plate scale in the guide log agrees with the configured
        guide optics. `None` if either is unknown.
    has_enough_samples : `bool`
        Whether there are enough samples to analyse the night at all.
    limits_equipment_match : `str`
        How well the night's equipment matches the equipment the limits were
        worked out for: ``"exact"``, ``"guide_optics_only"`` (the guide
        optics match and the imaging equipment is unknown or different), or
        ``"none"`` (no limits apply).
    """

    model_config = ConfigDict(populate_by_name=True)

    runs: int = 0
    guided_seconds: float = Field(default=0.0, alias="guidedSeconds")
    frames_total: int = Field(default=0, alias="framesTotal")
    frames_lost: int = Field(default=0, alias="framesLost")
    lost_fraction: float | None = Field(default=None, alias="lostFraction")
    lost_fraction_limit: float | None = Field(default=None, alias="lostFractionLimit")
    has_high_loss: bool | None = Field(default=None, alias="hasHighLoss")
    samples_analyzed: int = Field(default=0, alias="samplesAnalyzed")
    cadence_seconds: float | None = Field(default=None, alias="cadenceSeconds")
    excursion_count: int | None = Field(default=None, alias="excursionCount")
    excursion_fraction: float | None = Field(default=None, alias="excursionFraction")
    excursion_fraction_limit: float | None = Field(default=None, alias="excursionFractionLimit")
    has_frequent_excursions: bool | None = Field(default=None, alias="hasFrequentExcursions")
    median_snr: float | None = Field(default=None, alias="medianSnr")
    snr_limit: float | None = Field(default=None, alias="snrLimit")
    has_low_signal: bool | None = Field(default=None, alias="hasLowSignal")
    median_star_mass: float | None = Field(default=None, alias="medianStarMass")
    star_mass_limit: float | None = Field(default=None, alias="starMassLimit")
    typical_star_mass: float | None = Field(default=None, alias="typicalStarMass")
    has_dim_star: bool | None = Field(default=None, alias="hasDimStar")
    typical_cadence_seconds: float | None = Field(default=None, alias="typicalCadenceSeconds")
    calibration_problems: list[str] = Field(default_factory=list, alias="calibrationProblems")
    guide_scale_matches_configuration: bool | None = Field(
        default=None, alias="guideScaleMatchesConfiguration"
    )
    has_enough_samples: bool = Field(default=False, alias="hasEnoughSamples")
    limits_equipment_match: str = Field(default="none", alias="limitsEquipmentMatch")


class GuidingRunPerformance(BaseModel):
    """Processing: the guiding error of one run.

    Attributes
    ----------
    run_id : `str`
        The run's identifier.
    started_at : `float`
        When the run began, in seconds since the Unix epoch.
    duration_seconds : `float` or `None`
        Length of the run.
    samples : `int`
        Samples in the run that were not excursions.
    rms_per_axis_arcsec : `float` or `None`
        Robust guiding error per axis, in arcseconds.
    lost_fraction : `float` or `None`
        Share of the run's frames that were lost.
    net_dec_correction_arcsec_per_minute : `float` or `None`
        The run's net Dec correction rate (see `GuidingPerformance`).
    altitude_degrees : `float` or `None`
        Altitude when the run began.
    azimuth_degrees : `float` or `None`
        Azimuth when the run began.
    declination_degrees : `float` or `None`
        Declination when the run began.
    pier_side : `str` or `None`
        Side of the pier the telescope was on.
    """

    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    started_at: float = Field(alias="startedAt")
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")
    samples: int = 0
    rms_per_axis_arcsec: float | None = Field(default=None, alias="rmsPerAxisArcsec")
    lost_fraction: float | None = Field(default=None, alias="lostFraction")
    net_dec_correction_arcsec_per_minute: float | None = Field(
        default=None, alias="netDecCorrectionArcsecPerMinute"
    )
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    azimuth_degrees: float | None = Field(default=None, alias="azimuthDegrees")
    declination_degrees: float | None = Field(default=None, alias="declinationDegrees")
    pier_side: str | None = Field(default=None, alias="pierSide")


class ExposureFeasibility(BaseModel):
    """How often guiding stayed clean for a whole exposure of one length.

    Every possible start time during the night's guiding is tried. An
    exposure that starts then is clean if, for its whole length, the guide star
    was never lost, the guider never jumped, and the error stayed within what
    this equipment can absorb. The share of clean start times is the chance
    that an exposure of that length, started at a random moment while guiding,
    would have had clean guiding throughout.

    Attributes
    ----------
    exposure_seconds : `float`
        The exposure length.
    windows : `int`
        Start times tried, counting only times when guiding was running.
    clean_fraction : `float` or `None`
        Share of windows with clean guiding. `None` if no window was tried.
    lost_fraction : `float` or `None`
        Share of windows in which the star was lost, meaning no guide sample
        for more than four guide cycles in a row.
    jump_fraction : `float` or `None`
        Share of windows with a jump, a sample whose error exceeds the
        excursion limit.
    wobble_fraction : `float` or `None`
        Share of windows whose error scatter exceeds the acceptable guiding
        error. The scatter is about the window's own mean, so a steady offset
        from the lock position, which does not blur a star, is not counted.
    """

    model_config = ConfigDict(populate_by_name=True)

    exposure_seconds: float = Field(alias="exposureSeconds")
    windows: int = 0
    clean_fraction: float | None = Field(default=None, alias="cleanFraction")
    lost_fraction: float | None = Field(default=None, alias="lostFraction")
    jump_fraction: float | None = Field(default=None, alias="jumpFraction")
    wobble_fraction: float | None = Field(default=None, alias="wobbleFraction")


class GuidingPerformance(BaseModel):
    """Processing: what the night's guiding data shows.

    Attributes
    ----------
    rms_ra_arcsec : `float` or `None`
        Robust guiding error along RA, in arcseconds.
    rms_dec_arcsec : `float` or `None`
        Robust guiding error along Dec, in arcseconds.
    rms_per_axis_arcsec : `float` or `None`
        The two combined, per axis. This is the number compared with the
        acceptable guiding error.
    rms_including_excursions_arcsec : `float` or `None`
        The ordinary root-mean-square of every sample, excursions included,
        per axis. It shows how much the excursions matter.
    expected_star_widening_fraction : `float` or `None`
        How much this guiding error widens a star, as a fraction of its
        width, given the equipment's measured star width. `None` if the star
        width is unknown.
    net_dec_correction_arcsec_per_minute : `float` or `None`
        How far, on average, the guider moved the mount along Dec per minute
        (positive is north), from the Dec corrections it issued. It is a
        description of what the guider did, not a polar alignment estimate:
        on this observatory's real nights it does not follow the pattern a
        polar misalignment would give, so backlash and guide-rate effects
        must contribute. Only runs with a possible calibrated Dec speed and
        at least ten minutes of guiding are used. `None` if none qualify.
    runs : `list` [`GuidingRunPerformance`]
        Per-run results, for comparing sky position with guiding quality
        across nights.
    exposure_feasibility : `list` [`ExposureFeasibility`]
        For each exposure length in use, how often guiding stayed clean for
        the whole exposure. Empty when the limits or exposure lengths are not
        known.
    longest_reliable_exposure_seconds : `float` or `None`
        The longest of those lengths for which at least half the windows were
        clean, or `None` if none reached half.
    """

    model_config = ConfigDict(populate_by_name=True)

    rms_ra_arcsec: float | None = Field(default=None, alias="rmsRaArcsec")
    rms_dec_arcsec: float | None = Field(default=None, alias="rmsDecArcsec")
    rms_per_axis_arcsec: float | None = Field(default=None, alias="rmsPerAxisArcsec")
    rms_including_excursions_arcsec: float | None = Field(default=None, alias="rmsIncludingExcursionsArcsec")
    expected_star_widening_fraction: float | None = Field(default=None, alias="expectedStarWideningFraction")
    net_dec_correction_arcsec_per_minute: float | None = Field(
        default=None, alias="netDecCorrectionArcsecPerMinute"
    )
    runs: list[GuidingRunPerformance] = Field(default_factory=list)
    exposure_feasibility: list[ExposureFeasibility] = Field(default_factory=list, alias="exposureFeasibility")
    longest_reliable_exposure_seconds: float | None = Field(
        default=None, alias="longestReliableExposureSeconds"
    )


class GuidingSessionAnalysis(BaseModel):
    """The full three-stage guiding analysis of one observing night.

    Attributes
    ----------
    pipeline_name : `str`
        Always ``"guiding"``.
    pipeline_version : `str`
        Version of the analysis that produced this result.
    session_id : `str`
        The observing night.
    equipment_fingerprint : `str`
        The equipment the limits were worked out for.
    flagged : `bool`
        `True` if any recommendation is a warning.
    flag_reasons : `list` [`str`]
        The kinds of the warnings.
    resolved_parameters : `dict` [`str`, `float` or `str` or `None`]
        The limits this analysis used, so a result can be traced to the
        numbers behind it.
    input_quality : `GuidingInputQuality`
        Pre-processing: is the data good?
    performance : `GuidingPerformance`
        Processing: what does it show?
    recommendations : `list` [`Recommendation`]
        Post-processing: what to do about it.
    created_at : `datetime`
        When the analysis was run.
    """

    model_config = ConfigDict(populate_by_name=True)

    pipeline_name: str = Field(default="guiding", alias="pipelineName")
    pipeline_version: str = Field(default=GUIDING_ANALYSIS_VERSION, alias="pipelineVersion")
    session_id: str = Field(alias="sessionId")
    equipment_fingerprint: str = Field(alias="equipmentFingerprint")
    flagged: bool = False
    flag_reasons: list[str] = Field(default_factory=list, alias="flagReasons")
    resolved_parameters: dict[str, Any] = Field(default_factory=dict, alias="resolvedParameters")
    input_quality: GuidingInputQuality = Field(alias="inputQuality")
    performance: GuidingPerformance
    recommendations: list[Recommendation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
