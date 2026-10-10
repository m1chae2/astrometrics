"""Data structures for judging one star's spectrum, not the whole pipeline run.

`quality_summary.py` records how a whole processing run went (how many
stars were found, how many frames were skipped). This module is about a
single star's own spectrum instead: how good the raw data behind it was,
what it looks like next to the star's catalog entry, and how much the
resulting classification should be trusted. These are attached directly
to `SpectroscopyResult` on `StellarObject`, one set per star, so a reader
can judge a single result without re-deriving any of this themselves.

The module also holds the common record for the pipeline's quality
checkpoints (`StageQualityCheckpoint`). The pipeline measures quality at four
points: the raw frame, the calibrated spectrum, the processing result and the
final result. Every checkpoint is a list of `StageQualityMetric` records, so
all four read the same way. `metric` builds one record and decides whether it
passed, which makes adding a metric to a checkpoint a one-line call.
"""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The four places the spectroscopy pipeline measures its own quality, in the
# order a spectrum passes through them: the raw frame (checkpoint 0), the
# calibrated spectrum after pre-processing (1), the result of core processing
# (2) and the final result after post-processing (3).
StageName = Literal["raw_frame", "pre_processing", "processing", "post_processing"]
STAGE_ORDER: tuple[StageName, ...] = ("raw_frame", "pre_processing", "processing", "post_processing")


class StageQualityMetric(BaseModel):
    """One measured number at a quality checkpoint, with its limit and verdict.

    Every checkpoint reports its numbers in this one shape. A reader can
    compare a metric with its limit without knowing which stage made it. Build
    one with `metric`, which fills in `passed`.
    """

    model_config = ConfigDict(populate_by_name=True)

    # A short snake_case name that is unique within its checkpoint, for
    # example "zero_order_saturated_fraction".
    name: str = Field(alias="name")
    # The measured value. `None` when it could not be measured for this
    # spectrum. A yes/no check is stored as 1.0 (yes) or 0.0 (no).
    value: float | None = Field(default=None, alias="value")
    # What `value` is measured in, for example "fraction", "angstrom" or
    # "relative RMS". Empty for a plain count.
    unit: str = Field(default="", alias="unit")
    # The value the metric is judged against. `None` for a metric that is
    # reported but has no limit yet.
    limit: float | None = Field(default=None, alias="limit")
    # `True` when the value is on the good side of the limit, `False` when it
    # is on the bad side. `None` when there is no limit or no value to judge.
    passed: bool | None = Field(default=None, alias="passed")
    # One sentence of context, such as where the limit came from. Empty when
    # there is nothing to add.
    note: str = Field(default="", alias="note")


class StageQualityCheckpoint(BaseModel):
    """All the quality numbers measured at one point of the pipeline.

    A spectrum passes four checkpoints, in order: ``raw_frame``,
    ``pre_processing``, ``processing`` and ``post_processing``. The
    checkpoints let a reader see where in the pipeline quality was lost.
    """

    model_config = ConfigDict(populate_by_name=True)

    stage: StageName = Field(alias="stage")
    metrics: list[StageQualityMetric] = Field(default_factory=list, alias="metrics")
    # Short snake_case labels for conditions worth a reader's attention at
    # this checkpoint, for example "zero_order_saturated". Empty when none
    # applies.
    flags: list[str] = Field(default_factory=list, alias="flags")

    @property
    def has_failed_metric(self) -> bool:
        """Say whether any metric at this checkpoint failed its limit.

        Returns
        -------
        has_failed_metric : `bool`
            `True` when at least one metric has ``passed`` equal to `False`.
            A metric with no limit or no value never counts.
        """
        return any(entry.passed is False for entry in self.metrics)


class StageQualityRollup(BaseModel):
    """How one checkpoint went across all the spectra of a run.

    Built from every spectrum's `StageQualityCheckpoint` for the same stage.
    It tells a reader how many spectra had a problem at this stage and what a
    typical spectrum measured.
    """

    model_config = ConfigDict(populate_by_name=True)

    stage: StageName = Field(alias="stage")
    # How many spectra have a checkpoint for this stage.
    spectrum_count: int = Field(default=0, alias="spectrumCount")
    # How many of those spectra have at least one failed metric.
    failed_spectrum_count: int = Field(default=0, alias="failedSpectrumCount")
    # The median of each numeric metric across the spectra, keyed by metric
    # name. A metric no spectrum could measure is left out.
    metric_medians: dict[str, float] = Field(default_factory=dict, alias="metricMedians")


def metric(
    name: str,
    value: float | None,
    unit: str,
    limit: float | None = None,
    higher_is_better: bool = True,
    note: str = "",
    *,
    limit_is_a_pass: bool = True,
) -> StageQualityMetric:
    """Build one checkpoint metric and decide whether it passed.

    This is the one call that adds a number to a checkpoint. It casts the
    value and limit to plain `float` (so a NumPy value never reaches the
    model), turns a NaN or infinite value into `None`, and sets ``passed``.

    Parameters
    ----------
    name : `str`
        The metric's name, unique within its checkpoint.
    value : `float` or `None`
        The measured value, or `None` when it could not be measured.
    unit : `str`
        What the value is measured in.
    limit : `float`, optional
        The value to judge against. Leave out for a metric that has no limit.
    higher_is_better : `bool`, optional
        `True` when a value above the limit is good (a signal-to-noise
        ratio), `False` when a value below it is good (a saturated fraction).
    note : `str`, optional
        One sentence of context, such as where the limit came from.
    limit_is_a_pass : `bool`, optional
        Whether a value exactly equal to the limit passes. `True` by default.
        Pass `False` for a limit that is a trigger, for example a saturated
        fraction that is flagged once it reaches the limit.

    Returns
    -------
    metric : `StageQualityMetric`
        The record. ``passed`` is `None` when there is no limit or no value.
    """
    clean_value = _plain_float(value)
    clean_limit = _plain_float(limit)
    passed: bool | None = None
    if clean_value is not None and clean_limit is not None:
        if clean_value == clean_limit:
            passed = limit_is_a_pass
        elif higher_is_better:
            passed = clean_value > clean_limit
        else:
            passed = clean_value < clean_limit
    return StageQualityMetric(
        name=name, value=clean_value, unit=unit, limit=clean_limit, passed=passed, note=note
    )


def _plain_float(number: float | None) -> float | None:
    """Turn a number into a plain `float`, or `None` if it is unusable.

    Parameters
    ----------
    number : `float` or `None`
        A Python or NumPy number, or `None`.

    Returns
    -------
    plain : `float` or `None`
        The number as a Python `float`. `None` for `None`, NaN and infinity.
    """
    if number is None:
        return None
    converted = float(number)
    return converted if math.isfinite(converted) else None


class CatalogComparison(BaseModel):
    """How a star's self-determined spectrum compares with its catalog entry.

    The catalog (SIMBAD/Gaia) already has a spectral type and a B-V colour
    for most stars, measured a different way. This is not used to help the
    classification along -- it is a check done afterward, to catch a
    spectrum that probably is not this star's at all (a bright neighbour's
    light, glare from a nearby bright star, or a name given to the wrong
    object).
    """

    model_config = ConfigDict(populate_by_name=True)

    # Whether the self-determined type is close enough to the catalog type
    # to call it agreement. `None` when there was nothing to compare (no
    # catalog type, or no self-determined type).
    spectral_type_agrees: bool | None = Field(default=None, alias="spectralTypeAgrees")
    # Why `spectral_type_agrees` is `False`, or an empty string otherwise.
    spectral_type_note: str = Field(default="", alias="spectralTypeNote")
    # True when the catalog calls this star a giant or supergiant. The
    # classifier only ever compares against main-sequence (dwarf)
    # references, so a giant's self-determined type is always "the dwarf
    # that looks most alike", not a contradiction to be flagged.
    is_luminosity_class_uncertain: bool = Field(default=False, alias="isLuminosityClassUncertain")
    # Explains the dwarf-vs-giant mismatch above, or an empty string when
    # `is_luminosity_class_uncertain` is `False`.
    luminosity_class_note: str = Field(default="", alias="luminosityClassNote")
    # The giant or supergiant reference that looks closest to this
    # spectrum, named alongside the ordinary (dwarf) self-determined type
    # when the catalog calls the star a giant. `None` when not looked up.
    closest_giant_type: str | None = Field(default=None, alias="closestGiantType")
    closest_giant_rms: float | None = Field(default=None, alias="closestGiantRms")
    # Whether the spectrum's own colour (see `synthetic_b_minus_v`) is
    # close enough to the catalog's B-V to call it agreement. `None` when
    # there was nothing to compare.
    colour_agrees: bool | None = Field(default=None, alias="colourAgrees")
    # Why `colour_agrees` is `False`, or an empty string otherwise.
    colour_note: str = Field(default="", alias="colourNote")

    def joined_note(self) -> str:
        """Join this comparison's notes into one line of text.

        Kept for callers that only want a short summary rather than the
        structured fields above -- for example a UI showing one line
        under a star's classification. The order (colour, then type, then
        luminosity class) matches how the note used to be built before
        this class existed, so it reads the same way it always has.

        Returns
        -------
        note : `str`
            The non-empty notes among colour, type and luminosity class,
            joined with "; ", or an empty string when none fired.
        """
        return "; ".join(
            note for note in (self.colour_note, self.spectral_type_note, self.luminosity_class_note) if note
        )


class InputQualityAssessment(BaseModel):
    """How good the raw data behind a spectrum was, before anything was found.

    Answers "was this spectrum even worth analyzing?" using only signals
    that come from the extraction itself -- the instrument's resolution,
    how much of the frame was saturated, how much of the requested
    spectrum actually landed on the image, and how far the signal stood
    out from noise. None of this depends on what the classifier or the
    feature tests concluded.
    """

    model_config = ConfigDict(populate_by_name=True)

    # How much the instrument blurred this spectrum, in Angstroms (see
    # `spectral_resolution`). A finer resolution means the classification
    # and feature tests can trust smaller differences between spectral
    # types; a coarser one means two nearby types are hard to tell apart
    # no matter how good the rest of the data is.
    resolution_element_angstrom: float = Field(alias="resolutionElementAngstrom")
    # `True` when the resolution above came from this spectrum's own
    # measured trail width, `False` when a fixed fallback was used instead
    # (a weaker basis for judging how sharp the data is).
    is_resolution_measured: bool = Field(alias="isResolutionMeasured")
    # The fraction of pixels at the star's zero-order position that were
    # saturated (pinned at the sensor's maximum), 0 to 1. `None` when not
    # measured. A high fraction means the brightest part of the trail may
    # be unreliable.
    zero_order_saturated_pixel_fraction: float | None = Field(
        default=None, alias="zeroOrderSaturatedPixelFraction"
    )
    # The fraction (0 to 1) of the requested spectrum that actually landed
    # on the image and inside the camera's sensitive range. Below 1.0,
    # part of the trail ran off the edge of the picture.
    valid_fraction: float | None = Field(default=None, alias="validFraction")
    # How strongly the spectrum stands out from its own noise (see
    # `estimate_spectrum_signal_to_noise`). `None` when it could not be
    # judged (for example too few usable samples).
    signal_to_noise: float | None = Field(default=None, alias="signalToNoise")


class OutputQualityAssessment(BaseModel):
    """How much to trust a spectrum's classification, given everything found.

    Combines the classifier's own match statistics (is the winning type a
    weak match, or nearly tied with the runner-up) with the catalog
    comparison above, into one overall verdict. Unlike
    `InputQualityAssessment`, this depends on what classifying the
    spectrum actually produced.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The winning type's relative RMS is above `NO_GOOD_MATCH_RMS`, so the
    # closest reference still fits badly (see `is_classification_poor_match`).
    # `False` for an unclassified star -- that is a separate "nothing to
    # compare" case, not a shaky match.
    is_poor_match: bool = Field(alias="isPoorMatch")
    # (Subtype level.) The best and second-best reference types are less than
    # `AMBIGUOUS_RMS_GAP` apart in relative RMS (see
    # `is_classification_ambiguous`). `False` when fewer than two references
    # were compared.
    is_ambiguous: bool = Field(alias="isAmbiguous")
    # The best reference and the best reference of a different spectral
    # class letter are less than `AMBIGUOUS_RMS_GAP` apart in relative RMS
    # (see `is_classification_class_ambiguous`), so the class letter itself
    # is uncertain. `False` when no other class was compared.
    is_class_ambiguous: bool = Field(alias="isClassAmbiguous")
    # Whether the classifier's own top two candidates are closer together
    # than the instrument's resolution can actually distinguish. `None`
    # until the Angstrom-per-subtype-step conversion this needs has been
    # calibrated (see `post_processing.assess_output_quality`) -- not
    # `False`, since "not computed" and "computed and found precise
    # enough" are different claims.
    is_subtype_finer_than_resolution: bool | None = Field(default=None, alias="isSubtypeFinerThanResolution")
    # `CatalogComparison.spectral_type_agrees`, repeated here so a reader
    # can see the overall trust verdict without also fetching the
    # comparison object. `None` when there was nothing to compare.
    catalog_agrees: bool | None = Field(default=None, alias="catalogAgrees")
    # The overall verdict: `False` when any of the above says this result
    # should be treated with caution, `True` when none of them do.
    is_trustworthy: bool = Field(alias="isTrustworthy")
