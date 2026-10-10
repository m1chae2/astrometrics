"""Data structures for stars, light curves, and spectroscopy.

This module defines the pure data classes used to track individual stars,
measure how their brightness changes over time, and analyze their light
spectrums.
"""

import math
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from astrometricslib.models.astrometry_quality import CatalogMatchQuality
from astrometricslib.models.gaia_xp_comparison import GaiaXpComparison
from astrometricslib.models.gate_result import GateResult
from astrometricslib.models.known_variability import (
    KnownVariability,
    catalogs_consulted,
    combine_known_variability,
    is_confirmed_constant,
)
from astrometricslib.models.photometry_quality import (
    InputQualityAssessment as PhotometryInputQuality,
)
from astrometricslib.models.photometry_quality import (
    OutputQualityAssessment as PhotometryOutputQuality,
)
from astrometricslib.models.spectral_cross_checks import LineIndexClassification, ReddeningRecord
from astrometricslib.models.spectroscopy_quality import (
    CatalogComparison,
    InputQualityAssessment,
    OutputQualityAssessment,
    StageQualityCheckpoint,
)
from astrometricslib.models.wavelength_scale import WavelengthZeroPointRecord

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "AnalysisResult",
    "ExtinctionCorrectionRecord",
    "FileItem",
    "GroupedFrameStat",
    "PeriodogramResult",
    "PhotometryResult",
    "PlotData",
    "SpectralExtractionDiagnostics",
    "StellarObject",
    "StellarSessionMatch",
    "TargetFilesResponse",
    "TransitCandidate",
    "VariableCandidate",
    "has_catalog_magnitude",
    "is_rms_gap_ambiguous",
    "ladder_position",
    "ladder_steps_between",
    "rms_gap_to_next_class",
    "rms_gap_to_second_best",
    "types_differ_on_ladder",
]

BRIGHTEST_CATALOG_MAGNITUDE = -2.0
"""The brightest value a real catalog magnitude can have. Real apparent
magnitudes stop near -1.5 (Sirius). Photometry stores instrumental magnitudes
(about -10 to -17) in the same field, and those say nothing about how bright
a star looks. Must match BRIGHTEST_CATALOG_MAGNITUDE in
ui/planetariumDisplay/layers/StarOverlay.ts."""


MINIMUM_POINTS_FOR_PERIOD_SEARCH = 5
"""Fewest brightness measurements the smooth-cycle (Lomb-Scargle) search
accepts. A star with fewer gives no result, so it is not worth searching."""

MINIMUM_POINTS_FOR_TRANSIT_SEARCH = 8
"""Fewest brightness measurements the repeating-dip (box least squares)
search accepts."""

NO_GOOD_MATCH_RMS = 0.15
"""Poor-match limit. Unit: relative RMS (the root-mean-square difference as a
fraction of the spectrum's average brightness; 0.15 means a typical sample is
15% off). A spectrum is a poor match ("no good match") when its closest
reference differs from it by more than this. Measured on 14 stars with a
known type or a standard: stars matched within three spectral-type steps of
the catalog scored 0.046 to 0.105, and stars matched to the wrong type scored
0.181 to 0.283. A low RMS means the reference is close to the spectrum, not
that the type is right: two known-wrong matches (K2 matched as M0 at 0.086,
and A2 matched as F2 at 0.048) score below this limit. The model field, the
run gate and the star summary all read this one value."""

UNRELIABLE_MATCH_RMS = 0.45
"""No-match limit. Unit: relative RMS, as for `NO_GOOD_MATCH_RMS`. When even
the closest reference is further away than this, the classifier names no type
at all, because the least-bad reference would only invent one. Of 55 stars
classified on 2026-09-24, 19 scored above 0.45, and 18 of those had no catalog
type (they were matched to the wrong part of the sky)."""

AMBIGUOUS_RMS_GAP = 0.02
"""Ambiguity limit. Unit: relative RMS (a difference between two RMS values,
not a probability). The best match is ambiguous when the second-best
reference is closer to the best one than this, that is, when
`rms_gap_to_second_best` is below this value. 0.02 is the smallest RMS
distance between two neighbouring rungs of the reference ladder (A0V and A2V
are 0.019 apart; the median neighbouring pair is 0.043 apart and the 10th
percentile is 0.024, at resolutions from 25 to 70 Angstroms). A gap below it
means the spectrum cannot prefer one rung over its neighbour even by the
margin that a perfect match to the closest-spaced rung would show. The same
limit is applied to two gaps: `rms_gap_to_second_best` (the subtype level,
`is_ambiguous`) and `rms_gap_to_next_class` (the best reference of another
spectral class letter, `is_class_ambiguous`). The run gate fails only on the
class-level one."""

DIFFERS_FROM_CATALOG_SUBTYPES = 20.0
"""Catalog-disagreement limit. Unit: subtype steps on the O-to-M ladder (ten
steps to a letter class, so 20 is two whole classes, such as B to F). A
measured type more than this many steps from the catalog type is called
different. Of the 29 stars with a catalog type classified on 2026-09-24, 27
were within 12 steps (F8 matched as K0 was the worst), and the two beyond
that were both wrong for reasons other than the templates (34.5 and 45
steps). A limit of 8 steps has no measurement behind it, and it would call
the F8-as-K0 match a disagreement."""

LADDER_ORDER = "OBAFGKM"
"""Spectral classes from hottest to coolest."""


def ladder_position(spectral_type: str | None) -> float | None:
    """Place a spectral type on the O-to-M ladder, ten steps to a class.

    Parameters
    ----------
    spectral_type : `str`, optional
        A type such as ``"A3V"``, ``"B9.5V"`` or ``"A5V+M3-4V"``. Only the
        first letter and subtype number are read.

    Returns
    -------
    position : `float` or `None`
        Steps from the start of O (B0 is 10 and M0 is 60), for example 23.0
        for ``"A3V"``. A type with no subtype number counts as subtype 5,
        the middle of its class. `None` for a type that is not on the ladder
        (such as a carbon star).
    """
    trimmed = (spectral_type or "").strip().upper()
    if not trimmed or trimmed[0] not in LADDER_ORDER:
        return None
    digits = ""
    for character in trimmed[1:]:
        if character.isdigit() or (character == "." and digits and "." not in digits):
            digits += character
        else:
            break
    subtype = float(digits.rstrip(".")) if digits else 5.0
    return LADDER_ORDER.index(trimmed[0]) * 10.0 + subtype


def ladder_steps_between(first_type: str | None, second_type: str | None) -> float | None:
    """Measure how far apart two spectral types are on the O-to-M ladder.

    Parameters
    ----------
    first_type, second_type : `str`, optional
        Spectral types such as ``"A3V"`` or ``"K5"`` (see `ladder_position`).

    Returns
    -------
    steps : `float` or `None`
        The signed distance, second position minus first position, in
        subtype steps (ten per letter class). Negative means the second type
        is hotter. `None` when either type is missing or not on the ladder.
    """
    first_position, second_position = ladder_position(first_type), ladder_position(second_type)
    if first_position is None or second_position is None:
        return None
    return second_position - first_position


def types_differ_on_ladder(own_type: str | None, catalog_type: str | None) -> bool | None:
    """Say whether two spectral types are further apart than the limit.

    This is the one place that applies `DIFFERS_FROM_CATALOG_SUBTYPES`.

    Parameters
    ----------
    own_type : `str`, optional
        The type measured from the spectrum.
    catalog_type : `str`, optional
        The type the catalog gives.

    Returns
    -------
    differ : `bool` or `None`
        `True` when the types are more than `DIFFERS_FROM_CATALOG_SUBTYPES`
        steps apart. `None` when either type is missing or not on the
        ladder, so there is nothing to compare.
    """
    own_position, catalog_position = ladder_position(own_type), ladder_position(catalog_type)
    if own_position is None or catalog_position is None:
        return None
    return abs(own_position - catalog_position) > DIFFERS_FROM_CATALOG_SUBTYPES


def rms_gap_to_second_best(rms_values: Iterable[float]) -> float | None:
    """Measure how much better the best reference fits than the runner-up.

    Parameters
    ----------
    rms_values : `Iterable` [`float`]
        The relative RMS of each reference that was compared. Lower is a
        closer match.

    Returns
    -------
    gap : `float` or `None`
        The second-smallest RMS minus the smallest, in relative RMS units.
        This is a difference between two RMS values, not a probability. It
        is 0.0 for a tie. `None` when fewer than two references were
        compared.
    """
    ordered = sorted(float(value) for value in rms_values)
    if len(ordered) < 2:
        return None
    return ordered[1] - ordered[0]


def rms_gap_to_next_class(scores: Iterable[tuple[str, float]]) -> float | None:
    """Measure how much better the best reference fits than the next class.

    The next class is the closest reference whose spectral class letter (the
    O, B, A, F, G, K or M at the start of its label) differs from the best
    reference's letter.

    Parameters
    ----------
    scores : `Iterable` [`tuple` [`str`, `float`]]
        Each compared reference's label (such as ``"G0V"``) and its relative
        RMS. Lower is a closer match.

    Returns
    -------
    gap : `float` or `None`
        The smallest RMS among references of another class, minus the best
        reference's RMS, in relative RMS units. This is a difference between
        two RMS values, not a probability. `None` when no reference of
        another class was compared.
    """
    ordered = sorted((float(rms), label.strip().upper()[:1]) for label, rms in scores)
    if not ordered:
        return None
    best_rms, best_letter = ordered[0]
    other_rms = [rms for rms, letter in ordered[1:] if letter != best_letter]
    return min(other_rms) - best_rms if other_rms else None


def is_rms_gap_ambiguous(gap: float | None) -> bool | None:
    """Say whether an RMS gap is too small to prefer the best reference.

    This is the one place that applies `AMBIGUOUS_RMS_GAP`.

    Parameters
    ----------
    gap : `float`, optional
        The result of `rms_gap_to_second_best`, in relative RMS units.

    Returns
    -------
    is_ambiguous : `bool` or `None`
        `True` when the gap is below `AMBIGUOUS_RMS_GAP`. `None` when there
        is no gap (fewer than two references), which is not the same as
        "clearly separated".
    """
    return None if gap is None else gap < AMBIGUOUS_RMS_GAP


def has_catalog_magnitude(magnitude: object) -> bool:
    """Tell whether a star's magnitude is a real catalog magnitude.

    Parameters
    ----------
    magnitude : `object`
        The star's raw magnitude field. It may be a number, `None`, or an
        empty string.

    Returns
    -------
    bool
        `True` for a finite number at or above `BRIGHTEST_CATALOG_MAGNITUDE`
        that is not exactly zero. `False` for a missing value (`None`,
        ``""``), an instrumental (very negative) value, or zero. Zero is what
        is saved for a star whose catalog gave no magnitude, so it means
        "unknown", not "very bright".
    """
    return bool(
        isinstance(magnitude, int | float)
        and not isinstance(magnitude, bool)
        and math.isfinite(magnitude)
        and magnitude >= BRIGHTEST_CATALOG_MAGNITUDE
        and magnitude != 0
    )


class PeriodogramResult(BaseModel):
    """The result of searching a star's brightness for repeating cycles.

    A "periodogram" tests many possible repeat lengths (periods) against
    a star's brightness history and reports which one fits best -- the
    way you might try different guesses for a song's beat until one
    lines up.
    """

    model_config = ConfigDict(populate_by_name=True)

    best_period_days: float = Field(default=0.0, alias="bestPeriodDays")
    # How strong/clear the best repeating pattern is. Higher means the
    # star more clearly brightens and dims on a regular schedule.
    power: float = Field(default=0.0, alias="power")
    # The chance this pattern is just random noise instead of a real
    # repeating cycle. Lower is more trustworthy.
    false_alarm_probability: float = Field(default=1.0, alias="falseAlarmProbability")
    # What the search concluded: "detected", "possible", "not_detected" or
    # "insufficient_data" (the measurements are too few or too short to
    # test any repeat). Only "detected" and "possible" results say anything
    # about the star; "best_period_days" of the others is just the
    # strongest of many chance peaks.
    verdict: str = Field(default="", alias="verdict")
    # A sentence explaining a verdict that needs it (for example why the
    # data was insufficient).
    note: str = Field(default="", alias="note")
    # How many full cycles of the best period fit in the observed time.
    cycles_observed: float | None = Field(default=None, alias="cyclesObserved")
    # The shortest and longest period the search could test.
    searched_min_period_days: float | None = Field(default=None, alias="searchedMinPeriodDays")
    searched_max_period_days: float | None = Field(default=None, alias="searchedMaxPeriodDays")
    # How many noise-only versions of the light curve the false-alarm
    # probability was measured against, and how many consecutive measurements
    # moved together in them (1 is a plain shuffle; more keeps correlated
    # noise). The smallest probability that can be reported is 1/(count + 1).
    shuffle_count: int | None = Field(default=None, alias="shuffleCount")
    null_block_length: int | None = Field(default=None, alias="nullBlockLength")
    # The false-alarm probability for the whole family of searches made on
    # this target: the chance that at least one of `searches_in_family`
    # searches would look this strong by chance (see
    # `family_wise_correction`). `verdict` is judged on this; the verdict
    # before the correction is kept in `uncorrected_verdict`. All empty for a
    # result that was not part of a family.
    family_wise_false_alarm_probability: float | None = Field(
        default=None, alias="familyWiseFalseAlarmProbability"
    )
    searches_in_family: int | None = Field(default=None, alias="searchesInFamily")
    uncorrected_verdict: str = Field(default="", alias="uncorrectedVerdict")
    # The checks made on a "detected" or "possible" result against data it
    # was not found from (see `period_checks`): each failed check lowered the
    # verdict one level. `unconfirmed` is true when a check could not be made
    # (for example too few nights to leave one out); that does not lower the
    # verdict, but the result has not been confirmed on held-out data.
    verdict_checks: list[GateResult] = Field(default_factory=list, alias="verdictChecks")
    unconfirmed: bool = Field(default=False, alias="unconfirmed")


class TransitCandidate(BaseModel):
    """Data for a brief, repeating dip in a star's brightness.

    This "transit" pattern is how astronomers find planets around other
    stars, but the same box-shaped dip also shows up when the "star" is
    actually two stars and one passes in front of the other (an
    eclipsing binary) -- the detection math (see
    `VariabilityAnalyzer.run_bls_transit_search`) doesn't know which
    caused it, so this model doesn't assume either.
    """

    model_config = ConfigDict(populate_by_name=True)

    period_days: float = Field(default=0.0, alias="periodDays")
    transit_depth_mag: float = Field(default=0.0, alias="transitDepthMag")
    transit_duration_hours: float = Field(default=0.0, alias="transitDurationHours")
    # The exact time of the middle of one transit, used as a reference
    # point for predicting when the next ones will happen.
    epoch_t0: float = Field(default=0.0, alias="epochT0")
    # Signal-to-noise ratio: how clearly the dip stands out from normal
    # measurement noise. Higher means a more convincing detection.
    transit_snr: float = Field(default=0.0, alias="transitSnr")
    # transit_snr run through the same significance-to-confidence
    # heuristic saturation used for spectral feature detection, so 0
    # means noise and confidence approaches 1 as the dip's SNR grows --
    # not a calibrated detection probability.
    transit_confidence: float = Field(default=0.0, alias="transitConfidence")
    # The chance that shuffling the same measurements gives a dip pattern
    # at least this strong. Lower is more trustworthy.
    false_alarm_probability: float = Field(default=1.0, alias="falseAlarmProbability")
    # How many separate dips were seen, and how many measurements fell
    # inside them. One event is not a repeating pattern.
    transit_count: int = Field(default=0, alias="transitCount")
    points_in_transit: int = Field(default=0, alias="pointsInTransit")
    # "detected", "possible", "not_detected" or "insufficient_data". Only
    # "detected" and "possible" results say anything about the star.
    verdict: str = Field(default="", alias="verdict")
    note: str = Field(default="", alias="note")
    searched_min_period_days: float | None = Field(default=None, alias="searchedMinPeriodDays")
    searched_max_period_days: float | None = Field(default=None, alias="searchedMaxPeriodDays")
    # How many noise-only versions of the light curve the false-alarm
    # probability was measured against, and how many consecutive measurements
    # moved together in them (1 is a plain shuffle; more keeps correlated
    # noise). The smallest probability that can be reported is 1/(count + 1).
    shuffle_count: int | None = Field(default=None, alias="shuffleCount")
    null_block_length: int | None = Field(default=None, alias="nullBlockLength")
    # The false-alarm probability for the whole family of searches made on
    # this target: the chance that at least one of `searches_in_family`
    # searches would look this strong by chance (see
    # `family_wise_correction`). `verdict` is judged on this; the verdict
    # before the correction is kept in `uncorrected_verdict`. All empty for a
    # result that was not part of a family.
    family_wise_false_alarm_probability: float | None = Field(
        default=None, alias="familyWiseFalseAlarmProbability"
    )
    searches_in_family: int | None = Field(default=None, alias="searchesInFamily")
    uncorrected_verdict: str = Field(default="", alias="uncorrectedVerdict")
    # The checks made on a "detected" or "possible" result against data it
    # was not found from (see `period_checks`): each failed check lowered the
    # verdict one level. `unconfirmed` is true when a check could not be made
    # (for example too few nights to leave one out); that does not lower the
    # verdict, but the result has not been confirmed on held-out data.
    verdict_checks: list[GateResult] = Field(default_factory=list, alias="verdictChecks")
    unconfirmed: bool = Field(default=False, alias="unconfirmed")


class SessionPhotometrySummary(BaseModel):
    """What one observing session contributed to a merged light curve.

    A star seen on several nights has one merged light curve. Each night
    is normalized against its own group of comparison stars, so the
    levels of the nights are only comparable if those groups behave
    alike. This record keeps what a reader needs to judge that: which
    session, how many comparison stars it used, how bright their
    typical member was, and where the star's own normalized level sat.
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    # How many usable (positive) normalized measurements the session gave.
    point_count: int = Field(default=0, alias="pointCount")
    # The middle value of the star's normalized flux in this session. A
    # normalized flux is the star's flux divided by the comparison
    # ensemble's median flux in the same frame, so it is unitless.
    median_normalized_flux: float | None = Field(default=None, alias="medianNormalizedFlux")
    # The spread of the star's normalized flux within the session, as a
    # robust standard deviation (1.4826 x the median absolute deviation),
    # in the same unitless scale as the normalized flux.
    normalized_flux_scatter: float | None = Field(default=None, alias="normalizedFluxScatter")
    # The number of comparison stars used per frame (the same for every
    # frame, because the set is fixed), and the median over frames of the
    # ensemble level (the divisor of the normalization), in the session's
    # raw flux units (ADU per second).
    comparison_star_count: int | None = Field(default=None, alias="comparisonStarCount")
    ensemble_median_flux: float | None = Field(default=None, alias="ensembleMedianFlux")
    # The ids of the comparison stars the session used. The set was chosen
    # once and is the same for every frame of the session. Empty for a
    # session recorded before the set was fixed.
    comparison_star_ids: list[str] = Field(default_factory=list, alias="comparisonStarIds")
    # How much the comparison stars' own normalized light curves scatter, in
    # magnitudes: the median over the set of 2.5 log10(1 + CV), with CV the
    # standard deviation of a star's normalized flux over its mean. `None`
    # when the session has no usable set.
    comparison_scatter_mag: float | None = Field(default=None, alias="comparisonScatterMag")
    # How many stars were turned away as comparison stars: candidates whose
    # scatter exceeded what their errors allow, and stars a catalog lists as
    # variable. `None` for a session recorded before the set was vetted.
    comparison_rejected_count: int | None = Field(default=None, alias="comparisonRejectedCount")


class PhotometryResult(BaseModel):
    """A record of how a star's brightness changes over time: a light curve."""

    model_config = ConfigDict(populate_by_name=True)

    timestamps: list[datetime] = Field(default_factory=list, alias="timestamps")
    fluxes: list[float] = Field(default_factory=list, alias="fluxes")
    fluxes_normalized: list[float] = Field(default_factory=list, alias="fluxesNormalized")
    # Brightness values with any slow, gradual drift removed (like from
    # clouds or the star slowly rising and setting), leaving just the
    # short-term ups and downs.
    fluxes_detrended: list[float] = Field(default_factory=list, alias="fluxesDetrended")
    airmasses: list[float] = Field(default_factory=list, alias="airmasses")
    magnitudes: list[float] = Field(default_factory=list, alias="magnitudes")
    is_saturated: list[bool] = Field(default_factory=list, alias="isSaturated")
    # The 1-sigma uncertainty of each entry of `fluxes`, in ADU per second
    # (the same units), from the CCD equation (see
    # `pre_processing.frame_photometry.aperture_flux_error_adu`). Empty, or
    # one entry per timestamp. Empty for a light curve saved before
    # uncertainties were recorded.
    flux_errors: list[float] = Field(default_factory=list, alias="fluxErrors")
    # The 1-sigma uncertainty of each entry of `fluxes_normalized`, with no
    # unit (the normalized flux is a ratio). It adds the uncertainty of the
    # comparison-star ensemble to the star's own. Empty, or one entry per
    # normalized flux.
    fluxes_normalized_errors: list[float] = Field(default_factory=list, alias="fluxesNormalizedErrors")
    # The 1-sigma uncertainty of each entry of `fluxes_detrended`, with no
    # unit. The airmass trend is treated as exact, so this is the normalized
    # uncertainty scaled by the same factor as the flux. Empty, or one entry
    # per detrended flux.
    fluxes_detrended_errors: list[float] = Field(default_factory=list, alias="fluxesDetrendedErrors")
    # `True` when the gain of the camera was unknown and the errors above
    # assume 1 electron per ADU, so their size is only a guide. `False` when
    # the gain came from the camera profile or the FITS header. `None` when
    # no errors were recorded.
    errors_assume_unit_gain: bool | None = Field(default=None, alias="errorsAssumeUnitGain")
    # `True` when the read noise of the camera was unknown and the errors
    # above assume none. `None` when no errors were recorded.
    errors_assume_zero_read_noise: bool | None = Field(default=None, alias="errorsAssumeZeroReadNoise")
    # The mid-exposure time of each entry of `timestamps`, as a Barycentric
    # Julian Date in Barycentric Dynamical Time (BJD_TDB), in days.
    # `timestamps` stays the exposure start in UTC. Empty, or one entry per
    # timestamp. Empty for a light curve saved before this was recorded, or
    # when the target's position was unknown.
    time_bjd_tdb: list[float] = Field(default_factory=list, alias="timeBjdTdb")
    # How `time_bjd_tdb` was found: with the observatory's position or from
    # Earth's center (see `pre_processing.observation_times`). `None` when
    # `time_bjd_tdb` is empty.
    time_basis: str | None = Field(default=None, alias="timeBasis")
    periodogram: PeriodogramResult | None = Field(default=None, alias="periodogram")
    transit_candidate: TransitCandidate | None = Field(default=None, alias="transitCandidate")
    mean_flux: float | None = Field(default=None, alias="meanFlux")
    # How spread out this star's brightness measurements are relative to
    # their average -- a standard way to compare "noisiness" between
    # stars of different brightness. Higher can mean the star is
    # actually variable, or just noisily measured. The single stored
    # source of truth for this star's variability; StellarObject's own
    # variability_score below is just this same number on a different
    # scale, computed rather than stored so the two can never drift apart.
    coefficient_of_variation: float | None = Field(default=None, alias="coefficientOfVariation")
    # How good the raw per-frame measurements behind this light curve
    # were (see `pre_processing.assess_input_quality`). `None` for a
    # light curve saved before this was recorded.
    input_quality: PhotometryInputQuality | None = Field(default=None, alias="inputQuality")
    # How much to trust this star's variability verdict, given its own
    # CV against the population cutoff (see
    # `post_processing.assess_output_quality`). `None` for a light
    # curve saved before this was recorded, or one that was never
    # evaluated for variability.
    output_quality: PhotometryOutputQuality | None = Field(default=None, alias="outputQuality")
    # The id of the job (see astrometricslib.models.provenance.Activity)
    # that last wrote this light curve, so its exact pipeline version can
    # be looked up. `None` for a light curve saved before this was
    # recorded, or written outside a tracked job.
    generated_by_job_id: str | None = Field(default=None, alias="generatedByJobId")
    # One entry per observing session merged into this light curve, in the
    # order they were merged. Empty for a light curve from a single
    # session, or one saved before this was recorded.
    session_summaries: list[SessionPhotometrySummary] = Field(default_factory=list, alias="sessionSummaries")
    # The change between the sessions' median normalized levels, as the
    # brightest session over the faintest one, in magnitudes, and how many
    # times its expected error that difference is (see
    # `identify_long_term_variable_candidates`). `None` until the
    # long-term search has run on this light curve, or when fewer than two
    # sessions have enough points.
    between_session_amplitude_mag: float | None = Field(default=None, alias="betweenSessionAmplitudeMag")
    between_session_significance: float | None = Field(default=None, alias="betweenSessionSignificance")
    # The variability indices of the light curve (see
    # `pipelines.photometry.processing.variability_indices`). All are `None`
    # until the variability search has run, and when the field had too few
    # stars to fit a noise model.
    #
    # The star's mean instrumental magnitude, -2.5 log10 of its mean raw flux
    # in ADU per second, and its scatter in magnitudes (1.0857 times the
    # sample standard deviation over the mean). The noise model of the field
    # is fitted to these two numbers.
    instrumental_mag: float | None = Field(default=None, alias="instrumentalMag")
    rms_mag: float | None = Field(default=None, alias="rmsMag")
    # Measured scatter over the scatter the field's noise model expects at
    # this brightness. No unit; a constant star is near 1.
    excess_scatter: float | None = Field(default=None, alias="excessScatter")
    # Chi-square of the light curve against a constant, divided by the points
    # minus one. No unit; a constant star with correct errors is near 1.
    reduced_chi_square: float | None = Field(default=None, alias="reducedChiSquare")
    # The Stetson (1996) J index: the average signed square root of the
    # products of neighbouring residuals, in units of the errors. No unit;
    # a constant star is near 0, a smoothly changing one is above 0.
    stetson_j: float | None = Field(default=None, alias="stetsonJ")
    # The smallest of the three indices divided by its threshold. Above 1
    # exactly when the star passes all three, so it ranks candidates.
    variability_score: float | None = Field(default=None, alias="variabilityScore")


class StellarSessionMatch(BaseModel):
    """Tracks when a star was detected during a specific observing session.

    If a star is observed on 5 different nights, it will have 5 of
    these records combined into its final light curve.
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    angular_separation_arcsec: float = Field(alias="angularSeparationArcsec")


class SpectralExtractionDiagnostics(BaseModel):
    """What the extractor did while it read one star's spectrum.

    A short summary of the extractor's `ExtractionDiagnostics`. It shows
    whether the sky background was read cleanly and how wide the reading
    box was, so a reader can judge the spectrum without re-running the
    extraction.
    """

    model_config = ConfigDict(populate_by_name=True)

    # How many sky readings used each mode, keyed by mode name (for example
    # "both_bands" or "upper_band_contaminated"). Each step along the
    # spectrum makes one reading.
    sky_mode_counts: dict[str, int] = Field(default_factory=dict, alias="skyModeCounts")
    # The mode used by the most readings. `None` when no sky was read.
    dominant_sky_mode: str | None = Field(default=None, alias="dominantSkyMode")
    # The share of sky readings (0 to 1) that had to drop one sky band
    # because a neighbouring star's light fell in it. 0.0 when no sky was read.
    contaminated_sky_fraction: float = Field(default=0.0, alias="contaminatedSkyFraction")
    # The median half-width of the reading box, in pixels. A box reaches this
    # far each side of its centre. `None` for an extraction that does not
    # trace the spectrum, which uses one fixed radius.
    aperture_half_width_median_px: float | None = Field(default=None, alias="apertureHalfWidthMedianPx")
    # How much the half-width varied along the spectrum, as a standard
    # deviation in pixels. `None` for an untraced extraction.
    aperture_half_width_spread_px: float | None = Field(default=None, alias="apertureHalfWidthSpreadPx")


class ExtinctionCorrectionRecord(BaseModel):
    """Whether the airmass correction was applied to a spectrum, and with what.

    Mirrors `ExtinctionCorrection` in the spectroscopy pipeline. Airmass is
    how much air the light crossed (1.0 straight overhead, larger nearer the
    horizon). The instrument response removes the air's dimming at the
    standard star's airmass, and this correction rescales the spectrum for
    the airmass of the target's frame.
    """

    model_config = ConfigDict(populate_by_name=True)

    # True when the correction scaled the spectrum.
    is_applied: bool = Field(alias="isApplied")
    # The airmass of the target's frame. `None` when unknown.
    target_airmass: float | None = Field(default=None, alias="targetAirmass")
    # The airmass of the standard star the instrument response was fitted
    # to. `None` when unknown.
    reference_airmass: float | None = Field(default=None, alias="referenceAirmass")
    # The extinction curve used (or that would have been used).
    curve_name: str = Field(alias="curveName")
    # Why the correction was skipped. `None` when it was applied.
    reason: str | None = Field(default=None, alias="reason")


class DifferentialRefractionRecord(BaseModel):
    """What the refraction correction did to a spectrum's wavelengths.

    Mirrors `DifferentialRefraction` in the spectroscopy pipeline. Air bends
    blue light toward the zenith (the point straight overhead) more than red
    light. The zero-order image is a white-light image, so each wavelength of
    the spectrum lands slightly off the place the wavelength scale gives it.
    The part of that shift along the dispersion changes the wavelength. The
    part across it only widens the trail. Every number is `None` when it
    could not be computed.
    """

    model_config = ConfigDict(populate_by_name=True)

    # True when the displacement was computed. False without an observatory
    # site, a WCS or a time, and for a target below 20 degrees altitude.
    is_computed: bool = Field(alias="isComputed")
    # True when the wavelengths were shifted (`dar_correction_applied`).
    is_applied: bool = Field(alias="isApplied")
    # Why the displacement was not computed or the shift was not made.
    reason: str | None = Field(default=None, alias="reason")
    # The time of mid-exposure, UTC, as an ISO string.
    mid_exposure_utc: str | None = Field(default=None, alias="midExposureUtc")
    # The target's altitude above the horizon, in degrees.
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    # The position angle of the zenith as seen from the target, in degrees
    # from north through east.
    parallactic_angle_degrees: float | None = Field(default=None, alias="parallacticAngleDegrees")
    # The position angle of the direction of increasing wavelength along
    # the trail, in degrees from north through east.
    dispersion_position_angle_degrees: float | None = Field(
        default=None, alias="dispersionPositionAngleDegrees"
    )
    # The dispersion position angle minus the parallactic angle, wrapped to
    # -180 to 180 degrees. 0 means the red end of the spectrum points at the
    # zenith.
    parallactic_to_dispersion_angle_degrees: float | None = Field(
        default=None, alias="parallacticToDispersionAngleDegrees"
    )
    # The sky angle of one pixel along the dispersion, in arcseconds.
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    # The wavelength of the zero-order image, in Angstroms: the mean
    # wavelength weighted by the camera's sensitivity and the star's spectrum.
    effective_wavelength_angstrom: float | None = Field(default=None, alias="effectiveWavelengthAngstrom")
    # The air's conditions and where they came from ("config", or the
    # standard atmosphere scaled to the site's elevation).
    pressure_hpa: float | None = Field(default=None, alias="pressureHpa")
    temperature_c: float | None = Field(default=None, alias="temperatureC")
    relative_humidity_percent: float | None = Field(default=None, alias="relativeHumidityPercent")
    atmosphere_source: str | None = Field(default=None, alias="atmosphereSource")
    # The displacement of the light along the dispersion, relative to the
    # zero order, in arcseconds. Positive is toward longer wavelengths.
    along_dispersion_arcsec_at_4200: float | None = Field(default=None, alias="alongDispersionArcsecAt4200")
    along_dispersion_arcsec_at_8000: float | None = Field(default=None, alias="alongDispersionArcsecAt8000")
    # The same displacement as a wavelength error before the correction (the
    # labelled wavelength minus the true one), in Angstroms.
    along_dispersion_angstrom_at_4200: float | None = Field(
        default=None, alias="alongDispersionAngstromAt4200"
    )
    along_dispersion_angstrom_at_8000: float | None = Field(
        default=None, alias="alongDispersionAngstromAt8000"
    )
    # The displacement across the dispersion, in arcseconds. It widens the
    # trail and changes no wavelength. Positive is toward the dispersion
    # position angle plus 90 degrees.
    across_dispersion_arcsec_at_4200: float | None = Field(default=None, alias="acrossDispersionArcsecAt4200")
    across_dispersion_arcsec_at_8000: float | None = Field(default=None, alias="acrossDispersionArcsecAt8000")
    # The absolute difference between the wavelength errors at 4200 and
    # 8000 Angstroms.
    along_dispersion_span_angstrom: float | None = Field(default=None, alias="alongDispersionSpanAngstrom")
    # The absolute difference between the across-dispersion displacements at
    # 4200 and 8000 Angstroms, in pixels: how much the trail widens.
    across_dispersion_span_px: float | None = Field(default=None, alias="acrossDispersionSpanPx")


class SpectroscopyResult(BaseModel):
    """A star's own extracted spectrum, and what it suggests about the star.

    Bundles spectroscopy's results the same way `PhotometryResult` bundles
    photometry's: the processed measurement itself alongside what was
    derived from it, in one place on `StellarObject`, instead of as
    several same-topic fields scattered directly on the star.
    """

    model_config = ConfigDict(populate_by_name=True)

    wavelengths_angstrom: list[float] = Field(default_factory=list, alias="wavelengthsAngstrom")
    intensities: list[float] = Field(default_factory=list, alias="intensities")
    # Only set for a camera with a known quantum-efficiency curve on
    # file -- see quantum_efficiency_correction.py.
    quantum_efficiency_corrected_intensities: list[float] | None = Field(
        default=None, alias="quantumEfficiencyCorrectedIntensities"
    )
    # The spectrum with the instrument's full response (grating, optics,
    # atmosphere -- not just the sensor's QE) removed, over the wavelength
    # range that response is valid for; NaN outside it. See
    # instrument_response.apply_instrument_response. Only set for a camera
    # with a derived response on file. This is the physically meaningful
    # normalized flux; quantum_efficiency_corrected_intensities alone is
    # not, since it leaves the grating/optics/atmosphere tilt in place.
    response_corrected_intensities: list[float] | None = Field(
        default=None, alias="responseCorrectedIntensities"
    )
    # A spectral type guessed from this star's own extracted spectrum,
    # via template matching against a reference library -- independent
    # of StellarObject.spectral_type, which comes from a catalog
    # lookup. "Unknown" when no spectrum has been classified yet.
    self_determined_spectral_type: str = Field(default="", alias="selfDeterminedSpectralType")
    # How far the winning reference is from this spectrum: the root-mean-
    # square difference between the spectrum and the reference scaled to
    # its brightness, as a fraction of the spectrum's average brightness
    # (relative RMS). Lower is better; above `NO_GOOD_MATCH_RMS` the match
    # is poor. This is the classification's own score, not a probability.
    # `None` when no type was found.
    self_determined_spectral_type_rms: float | None = Field(
        default=None, alias="selfDeterminedSpectralTypeRms"
    )
    # Why no spectral type was determined (for example the trail left the
    # image), or empty when one was.
    self_determined_spectral_type_note: str = Field(default="", alias="selfDeterminedSpectralTypeNote")
    # Every reference type compared, closest first -- each entry has
    # "spectral_type", "rms" (relative RMS; lower is closer) and
    # "correlation". Lets a caller see close calls, not just the winner.
    self_determined_spectral_type_candidates: list[dict[str, Any]] = Field(
        default_factory=list, alias="selfDeterminedSpectralTypeCandidates"
    )
    # Named absorption features (Balmer series, Ca II H&K, etc.) found in
    # this star's own spectrum, most confident first -- see
    # spectral_feature_detector.detect_named_features for what "confidence"
    # means here.
    probable_spectral_features: list[dict[str, Any]] = Field(
        default_factory=list, alias="probableSpectralFeatures"
    )
    # Named emission lines (or blends of lines) found in this spectrum, most
    # convincing first -- see emission_line_detector.detect_emission_lines.
    emission_lines: list[dict[str, Any]] = Field(default_factory=list, alias="emissionLines")
    # True when at least two emission lines or blends were detected, so the
    # spectrum looks like glowing gas rather than a star.
    is_emission_line_source: bool = Field(default=False, alias="isEmissionLineSource")
    # Where the star's zero-order image sits, as an (x, y) pixel pair, in
    # the spectroscopy image. `StellarObject.star_data` holds the star's
    # position in the normal (astrometry) image, and the two pictures
    # are different pixel grids, so the spectroscopy position is kept
    # here instead. `rectangle` and `trail_centerline_px` below are in
    # this same spectroscopy-image grid.
    star_position_px: list[float] | None = Field(default=None, alias="starPositionPx")
    # The wavelength range, [lowest, highest] in Angstroms, the extraction
    # asked for before any samples were dropped. The spectrum arrays above
    # only hold the part of it that was on the image and inside the
    # camera's sensitive range, so comparing the two shows how much was lost.
    requested_wavelength_range_angstrom: list[float] | None = Field(
        default=None, alias="requestedWavelengthRangeAngstrom"
    )
    # The fraction (0 to 1) of the requested samples that were on the image
    # and inside the camera's range. Below 1.0, part of the spectrum trail
    # ran off the edge of the picture. `None` for a spectrum saved before
    # this was recorded.
    valid_fraction: float | None = Field(default=None, alias="validFraction")
    # The pixel box drawn around the star's spectrum trail in the
    # picture, used to redraw that box later without redetecting it.
    rectangle: Any | None = Field(default=None, alias="rectangle")
    # The raw tilt angle measured straight off the detected trail, before
    # any cleanup. dispersion_angle below is the value actually used
    # downstream.
    detected_angle: float | None = Field(default=None, alias="detectedAngle")
    # The angle, in degrees, that this star's spectrum "rainbow" streak
    # is tilted at (see SpectroscopyPipelineQualityMetrics for more on
    # this streak, called the "trail").
    dispersion_angle: float | None = Field(default=None, alias="dispersionAngle")
    # The pixel coordinates running down the middle of that trail, and
    # how wide the trail is at each point.
    trail_centerline_px: list[float] | None = Field(default=None, alias="trailCenterlinePx")
    trail_width_px: list[float] | None = Field(default=None, alias="trailWidthPx")
    # An audit warning, one value per sample of the spectrum above: how many
    # times brighter this star is at half that wavelength. Second-order light
    # from the blue can add to a red wavelength, and a large ratio means
    # even a small amount would matter (see second_order_risk). It changes
    # nothing in the spectrum. 0.0 where half the wavelength was not
    # measured. `None` for a spectrum saved before this was recorded.
    second_order_blue_to_red_ratio: list[float] | None = Field(
        default=None, alias="secondOrderBlueToRedRatio"
    )
    # How much the instrument blurred this spectrum, in Angstroms, worked
    # out from the trail width above (see spectral_resolution). The
    # classification and the feature tests were run at this width. `None`
    # when the trail width was not available, in which case they used the
    # fixed fallback resolution instead.
    resolution_element_angstrom: float | None = Field(default=None, alias="resolutionElementAngstrom")
    # How many pixels out from the star's center to gather light from
    # when measuring its spectrum.
    extraction_radius: int | None = Field(default=None, alias="extractionRadius")
    # An audit record for the neighbour-wing correction (see
    # neighbor_trail_deblending). For each sample of the spectrum above, the
    # share of this star's box light that came from a neighbouring star's
    # blur and was taken out, from 0 to 1. `None` when the correction was not
    # run (the switch is off, or there was no neighbour to correct for).
    neighbor_wing_fraction: list[float] | None = Field(default=None, alias="neighborWingFraction")
    # What the neighbour-wing correction did for this star, in words:
    # "applied", or "skipped: ..." with the reason (for example the fit was
    # not trustworthy). `None` when the correction was not run.
    neighbor_wing_status: str | None = Field(default=None, alias="neighborWingStatus")
    # An audit warning: wavelength windows where a different, independently
    # detected star's own known position lands inside this star's reading
    # box (see `find_neighbor_contamination_windows`), each with the
    # neighbour's flux relative to this star's own (`None` when either is
    # unknown). Geometric, not a guess from this spectrum's shape, so it
    # cannot mistake this star's own signal for a neighbour's. Changes
    # nothing in the spectrum; a reader (or `classify_spectral_type`'s
    # `excluded_windows_angstrom`) decides what to do with a flagged
    # window. Empty when no neighbour's position falls inside the box.
    possible_neighbor_contamination: list[dict[str, float | None]] | None = Field(
        default=None, alias="possibleNeighborContamination"
    )
    # What the intensities above are measured in (see intensity_scale).
    # Multiply an intensity by this to get detector counts per second, so
    # spectra from different images and exposures can be compared for
    # brightness. `None` when the image did not say enough (every spectrum
    # stored before this field existed), in which case the scale is unknown.
    counts_per_second_factor: float | None = Field(default=None, alias="countsPerSecondFactor")
    # How this spectrum's self-determined type and colour compare with the
    # star's catalog entry (see `post_processing.compare_to_catalog`).
    # `None` for a spectrum saved before this was recorded, or one that was
    # never classified.
    catalog_comparison: CatalogComparison | None = Field(default=None, alias="catalogComparison")
    # How good the raw data behind this spectrum was, before anything was
    # found in it (see `pre_processing.assess_input_quality`). `None` for a
    # spectrum saved before this was recorded.
    input_quality: InputQualityAssessment | None = Field(default=None, alias="inputQuality")
    # How much to trust the classification above, given everything found
    # (see `post_processing.assess_output_quality`). `None` for a spectrum
    # saved before this was recorded, or one that was never classified.
    output_quality: OutputQualityAssessment | None = Field(default=None, alias="outputQuality")
    # The four quality checkpoints this spectrum passed, in stage order: raw
    # frame, pre-processing, processing and post-processing (see
    # `StageQualityCheckpoint`). Each lists its measured numbers against their
    # limits in one common shape. Empty for a spectrum saved before this was
    # recorded.
    stage_quality: list[StageQualityCheckpoint] = Field(default_factory=list, alias="stageQuality")
    # The wavelength zero-point offset measured from known lines, and whether
    # the pipeline removed it from `wavelengths_angstrom` (see
    # `WavelengthZeroPointRecord`). `None` for a spectrum saved before this
    # was recorded.
    wavelength_zero_point: WavelengthZeroPointRecord | None = Field(default=None, alias="wavelengthZeroPoint")
    # The reddening correction applied before the classification: the catalog
    # E(B-V) used, its source, and the best type with and without dereddening
    # (see `ReddeningRecord`). `None` when the star had no catalog E(B-V), and
    # for a spectrum saved before this was recorded.
    reddening: ReddeningRecord | None = Field(default=None, alias="reddening")
    # The type that the strengths of a few spectral lines point to, and how far
    # it is from the template-fit type above (see `LineIndexClassification`).
    # A cross-check that ignores the continuum slope. `None` when it could not
    # be made, and for a spectrum saved before this was recorded.
    line_index_classification: LineIndexClassification | None = Field(
        default=None, alias="lineIndexClassification"
    )
    # How the extractor read the sky and how wide its reading box was (see
    # `SpectralExtractionDiagnostics`). `None` for a spectrum saved before
    # this was recorded.
    extraction_diagnostics: SpectralExtractionDiagnostics | None = Field(
        default=None, alias="extractionDiagnostics"
    )
    # Whether the airmass correction was applied to the response-corrected
    # spectrum, and with which airmasses (see `ExtinctionCorrectionRecord`).
    # `None` when no response correction was run, and for a spectrum saved
    # before this was recorded.
    extinction_correction: ExtinctionCorrectionRecord | None = Field(
        default=None, alias="extinctionCorrection"
    )
    # What the atmospheric differential refraction correction did to the
    # wavelengths (see `DifferentialRefractionRecord`). `None` for a spectrum
    # saved before this was recorded.
    differential_refraction: DifferentialRefractionRecord | None = Field(
        default=None, alias="differentialRefraction"
    )
    # How this spectrum compares with the star's Gaia DR3 XP spectrum, an
    # outside reference of similar resolution (see
    # `post_processing.compare_to_gaia_xp`). Holds the reason when the check
    # could not run. `None` for a spectrum saved before this was recorded.
    gaia_xp_comparison: GaiaXpComparison | None = Field(default=None, alias="gaiaXpComparison")
    # The id of the job (see astrometricslib.models.provenance.Activity)
    # that last wrote this spectrum, so its exact pipeline version can be
    # looked up. `None` for a spectrum saved before this was recorded, or
    # written outside a tracked job.
    generated_by_job_id: str | None = Field(default=None, alias="generatedByJobId")

    @computed_field(alias="isPoorMatch")
    @property
    def is_poor_match(self) -> bool:
        """Check if even the closest reference spectrum fits badly.

        `True` when the best reference differs from the spectrum by more
        than `NO_GOOD_MATCH_RMS` (0.15), so its type should not be claimed.
        `False` when the fit is good or no type was matched.
        """
        rms = self.self_determined_spectral_type_rms
        return rms is not None and rms > NO_GOOD_MATCH_RMS

    @computed_field(alias="rmsGapToSecondBest")
    @property
    def rms_gap_to_second_best(self) -> float | None:
        """Measure how much closer the best reference is than the runner-up.

        Compares the candidates' relative RMS values (see the module function
        `rms_gap_to_second_best`). The gap is a difference between two RMS
        values, not a probability.

        Returns
        -------
        gap : `float` or `None`
            The second-smallest RMS minus the smallest, in relative RMS
            units. `None` when fewer than two candidates have an RMS.
        """
        return rms_gap_to_second_best(
            float(c["rms"])
            for c in self.self_determined_spectral_type_candidates
            if isinstance(c.get("rms"), int | float)
        )

    @computed_field(alias="isAmbiguous")
    @property
    def is_ambiguous(self) -> bool | None:
        """Check if the best reference barely beats the runner-up.

        Returns
        -------
        is_ambiguous : `bool` or `None`
            `True` when `rms_gap_to_second_best` is below `AMBIGUOUS_RMS_GAP`.
            `None` when there is no gap to judge. The runner-up is usually a
            neighbouring subtype of the same class, so this says the subtype
            is uncertain, not necessarily the class.
        """
        return is_rms_gap_ambiguous(self.rms_gap_to_second_best)

    @computed_field(alias="rmsGapToNextClass")
    @property
    def rms_gap_to_next_class(self) -> float | None:
        """Measure how much closer the best reference is than the next class.

        Compares the best candidate with the closest candidate whose spectral
        class letter differs (see the module function `rms_gap_to_next_class`).
        The gap is a difference between two RMS values, not a probability.

        Returns
        -------
        gap : `float` or `None`
            The RMS difference in relative RMS units. `None` when no
            candidate of another class has an RMS.
        """
        return rms_gap_to_next_class(
            (str(c.get("spectral_type", "")), float(c["rms"]))
            for c in self.self_determined_spectral_type_candidates
            if isinstance(c.get("rms"), int | float)
        )

    @computed_field(alias="isClassAmbiguous")
    @property
    def is_class_ambiguous(self) -> bool | None:
        """Check if another spectral class fits almost as well as the best.

        Returns
        -------
        is_class_ambiguous : `bool` or `None`
            `True` when `rms_gap_to_next_class` is below `AMBIGUOUS_RMS_GAP`,
            so the class letter itself is uncertain. `None` when there is no
            gap to judge.
        """
        return is_rms_gap_ambiguous(self.rms_gap_to_next_class)


class StellarObject(BaseModel):
    """The main record for an individual star found in an image."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str = Field(default="", alias="id")
    name: str = Field(default="", alias="name")
    # Where the star is in the sky (right ascension is like longitude,
    # declination is like latitude, but for the sky instead of Earth).
    # `None` until identification/resolution sets it -- e.g. the
    # extended-target "Cluster" entry has no single point position.
    # Stays `Any` (not `float`) rather than `""` -- see the note on
    # flux below; the same tolerance is needed here too.
    right_ascension: Any = Field(default=None, alias="ra")
    declination: Any = Field(default=None, alias="dec")
    # How much light the star gives off (a raw brightness reading).
    # `Any`, not `float`: pipeline code transiently stashes numpy/
    # astropy values here before a save normalizes them (see
    # `safe_json_dumps` in astrometricslib.foundation.storage) -- `None` is
    # just the "not yet known" default, not the field's only valid shape.
    flux: Any = Field(default=None, alias="flux")
    # The star's brightness on the standard astronomical scale, where
    # LOWER numbers mean a BRIGHTER star (the opposite of most scales).
    # `None` means not yet known, not "magnitude zero" -- same `Any`
    # tolerance as flux above.
    magnitude: Any = Field(default=None, alias="magnitude")
    # The star's catalog colour: its blue (B) magnitude minus its visual (V)
    # magnitude, from SIMBAD. Bluer (hotter) stars have lower values. It comes
    # from a different instrument than ours, so it is an independent check on
    # a spectrum (see `synthetic_colour`). `None` when the catalog has no B or
    # no V, or the star was identified from Gaia, which is not read for this.
    b_minus_v: Any = Field(default=None, alias="bMinusV")
    # spectral_type and stellar_spectral_type are normally kept equal --
    # both hold the star's classification (like "G2V" for a Sun-like
    # star). The one exception is a synthetic entry used to represent a
    # star cluster, where stellar_spectral_type is set to the fixed
    # label "Cluster" while spectral_type keeps the more general object
    # type from the catalog it came from.
    spectral_type: str = Field(default="", alias="spectralType")
    # This star's brightness measured over time -- see PhotometryResult.
    # Mirrors spectroscopy below: one nested result per domain, instead
    # of that domain's fields loose on the star.
    photometry: PhotometryResult | None = Field(default_factory=PhotometryResult, alias="photometry")
    # The star's raw pixel position and shape info from source detection
    # (e.g. its centroid coordinates), used to relocate it in later
    # pictures. Defaults to an empty dict, not a list -- every real
    # consumer treats this as a dict (`.get("xcentroid", ...)`).
    star_data: Any = Field(default_factory=dict, alias="starData")
    # How big the star looks in the image it was detected in, as a radius in
    # pixels (measured by source detection). Used to size the on-screen
    # circle drawn around the star. `None` until detection has measured it.
    radius_px: float | None = Field(default=None, alias="radiusPx")
    # This star's own extracted spectrum and what it suggests about the
    # star -- see SpectroscopyResult. Mirrors photometry above: one
    # nested result per domain, instead of that domain's fields loose
    # on the star.
    spectroscopy: SpectroscopyResult | None = Field(default_factory=SpectroscopyResult, alias="spectroscopy")
    # See the comment on spectral_type above -- this is normally the
    # same value, kept as a separate field for the cluster-entry case.
    stellar_spectral_type: str = Field(default="", alias="stellarSpectralType")
    target_ids: list[str] = Field(default_factory=list, alias="targetIds")
    session_matches: list[StellarSessionMatch] = Field(default_factory=list, alias="sessionMatches")
    is_catalog_identified: bool = Field(default=False, alias="isCatalogIdentified")
    # Every object type SIMBAD lists for this star, joined with "|" (for
    # example "*|**|EB*|SB*|V*"). The full list is kept, not just SIMBAD's
    # single main type: the main type of Algol, a textbook eclipsing binary,
    # is "SB*" (spectroscopic binary). Empty when the star has no SIMBAD
    # record, was named from Gaia alone, or was saved before this was
    # recorded. See `known_variability`.
    simbad_object_types: str = Field(default="", alias="simbadObjectTypes")
    # What Gaia DR3's `phot_variable_flag` says about this star ("VARIABLE",
    # "CONSTANT", "NOT_AVAILABLE"), or "NO_GAIA_MATCH" when Gaia was asked and
    # has no entry (the brightest stars are not in Gaia DR3). Empty when Gaia
    # was never asked. See `known_variability`.
    gaia_variable_flag: str = Field(default="", alias="gaiaVariableFlag")
    # The star's Gaia DR3 source id (the long number in "Gaia DR3 132..."),
    # read from SIMBAD's list of names for the star, or from the star's own
    # Gaia name. `None` when Gaia has no entry (the brightest stars are not in
    # Gaia DR3) or the star was saved before this was recorded. The XP
    # spectrum check needs it (see `post_processing.compare_to_gaia_xp`).
    gaia_dr3_source_id: int | None = Field(default=None, alias="gaiaDr3SourceId")
    # The AAVSO Variable Star Index type of this star ("EA/SD", "DCEP", or
    # "CST" for a star checked and found constant), "NOT_IN_VSX", or
    # "LISTED_WITHOUT_TYPE". Empty when VSX was never asked.
    vsx_variability_type: str = Field(default="", alias="vsxVariabilityType")
    # How confidently this star was matched to its SIMBAD/Gaia entry --
    # see CatalogMatchQuality. `None` for a star that was never matched
    # (a FIELD_J... position-only id).
    catalog_match_quality: CatalogMatchQuality | None = Field(default=None, alias="catalogMatchQuality")

    @property
    def known_variability(self) -> KnownVariability:
        """Say whether the catalogs already list this star as a variable star.

        Joins SIMBAD, Gaia DR3 and VSX (see `combine_known_variability`), using
        whichever have been asked. Worked out each time, so it is not stored.

        Returns
        -------
        known_variability : `KnownVariability`
            ``UNKNOWN`` when no catalog has been asked. This says what the
            catalogs record, not whether the star varies.
        """
        return combine_known_variability(
            self.simbad_object_types, self.gaia_variable_flag, self.vsx_variability_type
        )

    @property
    def known_variability_catalogs(self) -> list[str]:
        """Name the catalogs that have been asked about this star.

        Returns
        -------
        catalogs : `list` [`str`]
            Any of ``"SIMBAD"``, ``"Gaia DR3"``, ``"VSX"``.
        """
        return catalogs_consulted(
            self.simbad_object_types, self.gaia_variable_flag, self.vsx_variability_type
        )

    @property
    def is_confirmed_constant(self) -> bool:
        """Say whether a catalog positively calls this star constant.

        Returns
        -------
        is_constant : `bool`
            True for Gaia's ``CONSTANT`` or a VSX ``CST`` entry, with no
            catalog listing or suspecting the star as variable.
        """
        return is_confirmed_constant(
            self.simbad_object_types, self.gaia_variable_flag, self.vsx_variability_type
        )

    @computed_field(alias="variabilityScore")
    @property
    def variability_score(self) -> float | None:
        """How much this star's brightness jumps around, on a display scale.

        Returns
        -------
        variability_score : `float` or `None`
            `photometry.coefficient_of_variation` multiplied by 100, or
            `None` before any variability has been measured for this star.
        """
        cv = self.photometry.coefficient_of_variation if self.photometry else None
        return cv * 100.0 if cv is not None else None

    @computed_field(alias="hasSpectra")
    @property
    def has_spectra(self) -> bool:
        """Check if this star's light spectrum has been measured."""
        return bool(self.spectroscopy and self.spectroscopy.wavelengths_angstrom)

    @computed_field(alias="hasCatalogMagnitude")
    @property
    def has_catalog_magnitude(self) -> bool:
        """Check if the star's magnitude is a real catalog magnitude.

        See the module function `has_catalog_magnitude` for the rule.
        """
        return has_catalog_magnitude(self.magnitude)

    @computed_field(alias="differsFromCatalog")
    @property
    def differs_from_catalog(self) -> bool | None:
        """Check if the spectrum's matched type disagrees with the catalog.

        `True` when the two types are more than
        `DIFFERS_FROM_CATALOG_SUBTYPES` (20) steps apart on the O-to-M
        ladder. `None` when either type is missing or not on the ladder.
        """
        own_type = self.spectroscopy.self_determined_spectral_type if self.spectroscopy else None
        return types_differ_on_ladder(own_type, self.spectral_type)

    @computed_field(alias="canRunPeriodSearch")
    @property
    def can_run_period_search(self) -> bool:
        """Check if the light curve has enough points for the cycle search.

        `True` with at least `MINIMUM_POINTS_FOR_PERIOD_SEARCH` (5)
        measurements.
        """
        points = len(self.photometry.timestamps) if self.photometry else 0
        return points >= MINIMUM_POINTS_FOR_PERIOD_SEARCH

    @computed_field(alias="canRunTransitSearch")
    @property
    def can_run_transit_search(self) -> bool:
        """Check if the light curve has enough points for the dip search.

        `True` with at least `MINIMUM_POINTS_FOR_TRANSIT_SEARCH` (8)
        measurements.
        """
        points = len(self.photometry.timestamps) if self.photometry else 0
        return points >= MINIMUM_POINTS_FOR_TRANSIT_SEARCH

    @computed_field(alias="hasPhotometry")
    @property
    def has_photometry(self) -> bool:
        """Check if this star's brightness has been tracked over time."""
        return bool(
            self.photometry
            and (
                (self.photometry.timestamps and len(self.photometry.timestamps) > 0)
                or (self.photometry.magnitudes and len(self.photometry.magnitudes) > 0)
                or (self.photometry.fluxes and len(self.photometry.fluxes) > 0)
            )
        )

    @computed_field(alias="plotData")
    @property
    def plot_data(self) -> dict[str, list[float]]:
        """The star's spectrum, formatted so it's easy to draw on a graph."""
        return self.get_plot_data()

    def get_plot_data(self) -> dict[str, list[float]]:
        """Convert whatever format the spectrum is in to a standard graph one.

        Returns
        -------
        plot_data : `dict`
            A dictionary with ``"wavelengths"`` (x-axis) and ``"intensities"``
            (y-axis).
        """

        def normalize(wls: Any, flux: Any) -> dict[str, list[float]]:
            if wls and len(wls) > 0 and max(wls) < 2000:
                wls = [float(w) * 10 for w in wls]
            return {"wavelengths": [float(w) for w in wls], "intensities": [float(f) for f in flux]}

        if self.spectroscopy and self.spectroscopy.wavelengths_angstrom and self.spectroscopy.intensities:
            return normalize(self.spectroscopy.wavelengths_angstrom, self.spectroscopy.intensities)

        return {"wavelengths": [], "intensities": []}

    def serialize(self) -> dict[str, Any]:
        """Package the star's data into a basic dictionary format.

        Returns
        -------
        data : `dict`
            The star's fields, plus the ready-to-graph ``"plotData"``.
        """
        data = self.model_dump(mode="python", by_alias=True)
        raw_plot = self.get_plot_data()
        data["plotData"] = {
            "wavelengths": raw_plot.get("wavelengths", []),
            "intensities": raw_plot.get("intensities", []),
        }
        return data


class VariableCandidate(BaseModel):
    """A star that might be changing brightness over time."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    mean_flux: float = Field(..., alias="meanFlux", ge=0.0)
    # How spread out this star's brightness measurements are relative to
    # their average. A higher number is one sign the star might really
    # be variable. The single stored source of truth here too -- see
    # PhotometryResult.coefficient_of_variation.
    coefficient_of_variation: float = Field(..., alias="coefficientOfVariation", ge=0.0)
    ra: float = Field(..., ge=0.0, le=360.0, alias="ra")
    dec: float = Field(..., ge=-90.0, le=90.0, alias="dec")
    # Whether the catalogs already list this star as variable (SIMBAD, Gaia
    # DR3 and VSX, whichever have been asked): a `KnownVariability` value.
    # "unknown" means no catalog has been asked, which is not the same as
    # "not listed". A candidate listed as variable is not a discovery.
    known_variability: str = Field(default="unknown", alias="knownVariability")
    # One sentence saying what that answer rests on, naming the catalogs.
    known_variability_note: str = Field(default="", alias="knownVariabilityNote")
    # The indices the candidate rule used (see the same-named fields of
    # `PhotometryResult`). `None` when the field had no noise model and the
    # star was flagged by its CV alone.
    excess_scatter: float | None = Field(default=None, alias="excessScatter")
    reduced_chi_square: float | None = Field(default=None, alias="reducedChiSquare")
    stetson_j: float | None = Field(default=None, alias="stetsonJ")
    variability_score: float | None = Field(default=None, alias="variabilityScore")

    @computed_field(alias="score")
    @property
    def score(self) -> float:
        """How confident the code is that this star is truly variable.

        Returns
        -------
        score : `float`
            `coefficient_of_variation`, capped at 1.0 so it reads as a
            0 (not confident) to 1 (very confident) score.
        """
        return min(1.0, self.coefficient_of_variation)


class AnalysisResult(BaseModel):
    """A summary of what happened when a processing job was run."""

    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(..., pattern="^(started|running|completed|failed|pruned)$")
    target_id: str = Field(..., alias="targetId")
    job_id: str | None = Field(default=None, alias="jobId")
    total_images: int = Field(default=0, alias="totalImages")
    analysis_mode: str = Field(..., alias="analysisMode")
    stars_processed: int = Field(default=0, alias="starsProcessed")
    spectra_extracted: int = Field(default=0, alias="spectraExtracted")
    stars_found: int = Field(default=0, alias="starsFound")
    frames_processed: int = Field(default=0, alias="framesProcessed")
    rejected_count: int = Field(default=0, alias="rejectedCount")
    rejected_files: list[str] = Field(default_factory=list, alias="rejectedFiles")
    variable_candidates: list[VariableCandidate] = Field(default_factory=list, alias="variableCandidates")
    error: str | None = Field(default=None, alias="error")
    message: str | None = Field(default=None, alias="message")


class PlotData(BaseModel):
    """Holds the X and Y coordinates needed to draw a spectrum graph."""

    model_config = ConfigDict(populate_by_name=True)

    wavelengths: list[float] = Field(default_factory=list, alias="wavelengths")
    intensities: list[float] = Field(default_factory=list, alias="intensities")


class FileItem(BaseModel):
    """A single image file ready to be shown in a UI list."""

    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="path")
    name: str = Field(alias="name")
    camera: str = Field(default="Unknown", alias="camera")
    iso: str = Field(default="800", alias="iso")
    exposure: str = Field(default="1.0", alias="exposure")
    filter: str = Field(default="None", alias="filter")
    date: str = Field(default="Unknown", alias="date")


class GroupedFrameStat(BaseModel):
    """A count of how many images share the same filter and exposure time."""

    model_config = ConfigDict(populate_by_name=True)

    filter: str = Field(alias="filter")
    iso: str = Field(alias="iso")
    exposure: str = Field(alias="exposure")
    count: int = Field(..., gt=0, alias="count")
    # Whether matching "dark" calibration frames are available -- these
    # are pictures taken with the lens capped, used to subtract out the
    # camera sensor's own background noise.
    darks: str | None = Field(default=None, alias="darks")
    camera: str | None = Field(default=None, alias="camera")


class TargetFilesResponse(BaseModel):
    """All the files and summary statistics that belong to a single target."""

    model_config = ConfigDict(populate_by_name=True)

    files: list[FileItem] = Field(default_factory=list, alias="files")
    stacked_image: str | None = Field(None, alias="stackedImage")
    stacked_spectral_target: str | None = Field(None, alias="stackedSpectralTarget")
    total_exposure: float = Field(default=0.0, alias="totalExposure", ge=0.0)
    exposure_counts: dict[str, int] = Field(default_factory=dict, alias="exposureCounts")
