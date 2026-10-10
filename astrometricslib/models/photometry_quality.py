"""Data structures for judging one star's light curve, not the whole run.

`quality_summary.py` records how a whole processing run went (how many
frames were rejected, how many stars were found). This module is about
a single star's own light curve instead: how good the raw per-frame
measurements behind it were, and how much its variability verdict
should be trusted. These are attached directly to `PhotometryResult` on
`StellarObject`, one set per star, so a reader can judge a single
result without re-deriving any of this themselves.
"""

from pydantic import BaseModel, ConfigDict, Field


class InputQualityAssessment(BaseModel):
    """How good the raw per-frame measurements behind a light curve were.

    Answers "was this star's light curve even worth judging for
    variability?" using only signals from measurement and normalization
    themselves -- how many of the session's frames actually contributed
    a usable point, how often this star was saturated, and how far the
    frame-to-frame alignment drifted while it was being tracked. None of
    this depends on the variability verdict itself.
    """

    model_config = ConfigDict(populate_by_name=True)

    # How many frames this star has a usable (unrejected) flux
    # measurement in -- the final length of its light curve.
    frames_measured: int = Field(alias="framesMeasured")
    # How many frames were in the session this star was measured across,
    # regardless of whether this star was usable in each one. The
    # denominator `coverage_fraction` is built from.
    frames_available: int = Field(alias="framesAvailable")
    # `frames_measured / frames_available`. The same ratio
    # `_score_reference_star_candidates` already computes to judge a
    # star as a comparison-ensemble candidate, surfaced here onto the
    # star's own record instead of being thrown away after selection.
    coverage_fraction: float = Field(alias="coverageFraction")
    # The fraction of this star's own measurements flagged as saturated,
    # 0 to 1.
    saturated_fraction: float = Field(alias="saturatedFraction")
    # The largest frame-to-frame alignment offset, in pixels, seen
    # across every frame this star was measured in. `None` when no
    # per-frame drift was recorded for any of the star's timestamps (for
    # example, a single-frame session never reaches the parallel-worker
    # alignment step). A large drift means the telescope lost tracking
    # or the frame-to-frame alignment failed, not that the star's
    # brightness actually changed -- see `pre_processing.assess_input_quality`.
    max_registration_drift_px: float | None = Field(default=None, alias="maxRegistrationDriftPx")
    # `coverage_fraction` fell below the threshold this codebase already
    # treats as too sparse for a reliable comparison star (see
    # `pre_processing.assess_input_quality`).
    is_low_coverage: bool = Field(alias="isLowCoverage")
    # `max_registration_drift_px` was large enough that the star's own
    # tracking, not its brightness, is the more likely explanation for
    # any apparent variation (see `pre_processing.assess_input_quality`).
    is_tracking_unstable: bool = Field(alias="isTrackingUnstable")


class OutputQualityAssessment(BaseModel):
    """How much to trust a star's variability verdict, given its own CV.

    A star is flagged as variable when its coefficient of variation (CV)
    exceeds the observing run's adaptive cutoff -- a single boolean that,
    on its own, does not say whether a star sits far past that line or
    right on top of it. This turns the same comparison into a continuous
    margin, so a confident call is distinguishable from a borderline one.
    """

    model_config = ConfigDict(populate_by_name=True)

    # This star's own coefficient of variation, re-surfaced here so a
    # reader of the assessment does not also need to fetch it from
    # `PhotometryResult.coefficient_of_variation`.
    coefficient_of_variation: float = Field(alias="coefficientOfVariation")
    # The population-wide cutoff this star's CV was compared against
    # (see `_adaptive_cv_cutoff`).
    adaptive_cutoff: float = Field(alias="adaptiveCutoff")
    # How far this star's CV sits from the cutoff, in the same
    # MAD-scaled units the cutoff itself is built from (see
    # `post_processing.assess_output_quality`). Positive means flagged
    # variable; a value near zero means the call was close either way.
    margin_in_mad_units: float = Field(alias="marginInMadUnits")
    # `abs(margin_in_mad_units)` was small enough that one noisy
    # measurement could flip this star's verdict either way (see
    # `post_processing.assess_output_quality`).
    is_low_confidence: bool = Field(alias="isLowConfidence")
    # `not is_low_confidence`. Kept as its own field (rather than making
    # callers negate `is_low_confidence` themselves) to match
    # `SpectroscopyResult`'s equivalent field, and to leave room for a
    # future factor to combine in without every caller having to change.
    is_trustworthy: bool = Field(alias="isTrustworthy")
